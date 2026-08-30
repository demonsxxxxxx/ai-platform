from __future__ import annotations

from dataclasses import replace
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request
from http.client import BadStatusLine
from pathlib import Path

import pytest

from tools import sandbox_quickstart as quickstart


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
OLD_COMMIT = "2" * 40
BACKEND = quickstart.BACKEND_REPOSITORY + "@sha256:" + "3" * 64
FRONTEND = quickstart.FRONTEND_REPOSITORY + "@sha256:" + "4" * 64


def _container(
    repo: Path,
    service: str,
    *,
    commit: str = "",
    image: str = BACKEND,
    health: str = "healthy",
    status: str = "running",
) -> quickstart.RuntimeContainer:
    app_service = service in {"api", "worker", "frontend"}
    return quickstart.RuntimeContainer(
        container_id="a" * 64,
        commit=commit,
        image=image,
        restart_count=0,
        status=status,
        health=health,
        project=quickstart.PROJECT,
        service=service,
        config_files=",".join(str(repo / path) for path in quickstart.COMPOSE_FILES),
        release_owner="repo-local-compose" if app_service else "",
        release_role=service if app_service else "",
        source_dirty="false" if app_service else "",
        working_dir=str(repo / quickstart.COMPOSE_FILES[0].parent),
        one_off="False",
        config_hash=f"config-{service}",
    )


def _subject_file(path: Path, **changes: object) -> Path:
    value = {
        "source_commit": COMMIT,
        "backend_image": BACKEND,
        "frontend_image": FRONTEND,
        "env_file": "/data/ai-platform-internal-test/config/stable/.env",
        "ci_success": True,
        **changes,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize(
    "changes",
    [
        {"ci_success": False},
        {"source_commit": OLD_COMMIT},
        {"backend_image": quickstart.BACKEND_REPOSITORY + ":main"},
        {"frontend_image": BACKEND},
    ],
)
def test_subject_requires_ci_success_exact_main_and_role_digests(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = _subject_file(tmp_path / "latest-main.json", **changes)
    if changes == {"source_commit": OLD_COMMIT}:
        assert quickstart._load_subject(path).commit == OLD_COMMIT
    else:
        with pytest.raises(quickstart.QuickstartError, match="subject is invalid"):
            quickstart._load_subject(path)


def test_compose_command_has_only_internal_test_files_and_exact_overrides(tmp_path: Path) -> None:
    subject = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    command = quickstart._compose_command(
        ["sudo", "-n", "docker", "--context", "default"],
        tmp_path, tmp_path / ".env", subject,
    )

    assert command[:4] == ["sudo", "-n", "env", "-i"]
    assert command[command.index("docker") + 1:command.index("compose")] == [
        "--context", "default",
    ]
    assert [command[index + 1] for index, value in enumerate(command) if value == "-f"] == [
        str(tmp_path / path) for path in quickstart.COMPOSE_FILES
    ]
    assert "docker-compose.sandbox.yml" not in " ".join(command)
    assert "docker-compose.opensandbox.yml" not in " ".join(command)
    assert "docker-compose.s72-colocation.yml" not in " ".join(command)
    rendered = " ".join(command)
    assert BACKEND in rendered and FRONTEND in rendered and COMMIT in rendered
    assert f"OPENSANDBOX_EXECUTOR_IMAGE={BACKEND}" in command
    assert f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST=sha256:{'3' * 64}" in command


def test_managed_env_is_only_checked_for_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "managed"
    env_file = root / "config" / OLD_COMMIT / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("SECRET=value\n", encoding="utf-8")
    env_file.chmod(0o600)
    release = quickstart.Quickstart(tmp_path, root)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("env read"))

    assert release._validate_env(env_file) == env_file


def test_fresh_main_mismatch_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "managed" / "releases" / COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")

    class SourceRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            joined = " ".join(command)
            if "remote.origin.url" in joined:
                return quickstart.ORIGIN_URL
            if "rev-parse" in joined:
                return COMMIT
            if "status" in joined:
                return ""
            return OLD_COMMIT + "\trefs/heads/main"

    release = quickstart.Quickstart(repo, tmp_path / "managed", runner=SourceRunner())
    with pytest.raises(quickstart.QuickstartError, match="not fresh origin/main"):
        release._verify_source(quickstart.Subject(COMMIT, BACKEND, FRONTEND))


def test_invalid_origin_is_rejected_before_network_access(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    repo = root / "releases" / COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")

    class InvalidOriginRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            joined = " ".join(command)
            if "rev-parse" in joined:
                return COMMIT
            if "status" in joined:
                return ""
            if "remote.origin.url" in joined:
                return "ssh://unapproved.invalid/repository"
            pytest.fail("invalid origin was contacted")

    release = quickstart.Quickstart(repo, root, runner=InvalidOriginRunner())
    with pytest.raises(quickstart.QuickstartError, match="invalid origin"):
        release._verify_source(quickstart.Subject(COMMIT, BACKEND, FRONTEND))


def test_git_main_check_uses_canonical_url_and_sanitized_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "managed"
    repo = root / "releases" / COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")
    network_calls: list[tuple[list[str], dict[str, str]]] = []
    local_environments: list[dict[str, str]] = []

    class CanonicalRunner(quickstart.Runner):
        def run(self, command: object, **kwargs: object) -> str:
            command = list(command)
            joined = " ".join(command)
            if "rev-parse" in joined:
                local_environments.append(kwargs["environment"])
                return COMMIT
            if "status" in joined:
                local_environments.append(kwargs["environment"])
                return ""
            if "remote.origin.url" in joined:
                local_environments.append(kwargs["environment"])
                return quickstart.ORIGIN_URL
            network_calls.append((command, kwargs["environment"]))
            return COMMIT + "\trefs/heads/main"

    monkeypatch.setenv("GIT_SSH_COMMAND", "untrusted-command")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "other"))
    release = quickstart.Quickstart(repo, root, runner=CanonicalRunner())
    release._verify_source(quickstart.Subject(COMMIT, BACKEND, FRONTEND))

    command, environment = network_calls[0]
    assert quickstart.ORIGIN_URL in command and "origin" not in command
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == quickstart.os.devnull
    assert "GIT_SSH_COMMAND" not in environment
    assert all(
        "GIT_DIR" not in environment and "GIT_WORK_TREE" not in environment
        for environment in local_environments
    )


def test_docker_environment_keeps_proxy_but_rejects_daemon_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("DOCKER_HOST", "tcp://unapproved.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "unapproved")

    environment = quickstart._docker_environment()

    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert "DOCKER_HOST" not in environment and "DOCKER_CONTEXT" not in environment


def test_runner_keeps_default_trimmed_output_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(*_args: object, **kwargs: object) -> quickstart.subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return quickstart.subprocess.CompletedProcess(
            ["command"], 0, stdout="  value \r\n"
        )

    monkeypatch.setattr(
        quickstart.subprocess,
        "run",
        run,
    )

    assert quickstart.Runner().run(["command"], output=True) == "value"
    assert captured["start_new_session"] is (os.name == "posix")


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_inspect_preserves_empty_source_commit_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, line_ending: str
) -> None:
    root = tmp_path / "managed"
    expected_config = ",".join(
        str(root / "releases" / COMMIT / path) for path in quickstart.COMPOSE_FILES
    )
    stdout = "\t".join(
        (
            "a" * 64,
            "",
            "postgres:16",
            "0",
            "running",
            "healthy",
            quickstart.PROJECT,
            "postgres",
            expected_config,
            "",
            "",
            "",
            str(root / "releases" / COMMIT / quickstart.COMPOSE_FILES[0].parent),
            "False",
            "config-postgres",
        )
    ) + line_ending
    calls: list[list[str]] = []

    def run(command: object, **_kwargs: object) -> quickstart.subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return quickstart.subprocess.CompletedProcess(list(command), 0, stdout=stdout)

    monkeypatch.setattr(quickstart.subprocess, "run", run)
    release = quickstart.Quickstart(tmp_path, root)
    release.docker = ["docker"]

    assert release._inspect("postgres") == quickstart.RuntimeContainer(
        container_id="a" * 64,
        commit="",
        image="postgres:16",
        restart_count=0,
        status="running",
        health="healthy",
        project=quickstart.PROJECT,
        service="postgres",
        config_files=expected_config,
        release_owner="",
        release_role="",
        source_dirty="",
        working_dir=str(
            root / "releases" / COMMIT / quickstart.COMPOSE_FILES[0].parent
        ),
        one_off="False",
        config_hash="config-postgres",
    )
    assert len(calls) == 1
    assert calls[0][:4] == [
        "docker",
        "container",
        "inspect",
        "ai-platform-postgres",
    ]
    assert calls[0][-2] == "--format"
    assert calls[0][-1] == "\t".join((
        "{{.Id}}",
        '{{index .Config.Labels "ai-platform.source-commit"}}',
        "{{.Config.Image}}",
        "{{.RestartCount}}",
        "{{.State.Status}}",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        '{{index .Config.Labels "com.docker.compose.project"}}',
        '{{index .Config.Labels "com.docker.compose.service"}}',
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
        '{{index .Config.Labels "ai-platform.release-owner"}}',
        '{{index .Config.Labels "ai-platform.release-role"}}',
        '{{index .Config.Labels "ai-platform.source-dirty"}}',
        '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
        '{{index .Config.Labels "com.docker.compose.oneoff"}}',
        '{{index .Config.Labels "com.docker.compose.config-hash"}}',
    ))


@pytest.mark.parametrize("extra_service", [False, True])
def test_current_runtime_requires_exact_internal_test_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_service: bool
) -> None:
    root = tmp_path / "managed"
    release = quickstart.Quickstart(tmp_path, root)
    release.docker = ["docker"]
    expected_config = ",".join(
        str(root / "releases" / COMMIT / path) for path in quickstart.COMPOSE_FILES
    )
    services = list(quickstart.PROJECT_SERVICES)
    if extra_service:
        services.append("old-proxy")
    monkeypatch.setattr(
        release.runner, "run", lambda *_args, **_kwargs: "\n".join(services)
    )

    runtime_repo = root / "releases" / COMMIT

    def inspect(service: str) -> quickstart.RuntimeContainer:
        image = FRONTEND if service == "frontend" else BACKEND
        commit = COMMIT if service in {"api", "worker", "frontend"} else ""
        container = _container(runtime_repo, service, commit=commit, image=image)
        assert container.config_files == expected_config
        return container

    monkeypatch.setattr(release, "_inspect", inspect)
    if extra_service:
        with pytest.raises(quickstart.QuickstartError, match="runtime subject is invalid"):
            release._current_runtime()
    else:
        assert release._current_runtime() == quickstart.Subject(COMMIT, BACKEND, FRONTEND)


def test_runtime_rejects_wrong_compose_file_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = quickstart.Quickstart(tmp_path, tmp_path / "managed")
    release.docker = ["docker"]
    monkeypatch.setattr(
        release.runner,
        "run",
        lambda *_args, **_kwargs: "\n".join(quickstart.PROJECT_SERVICES),
    )
    monkeypatch.setattr(
        release,
        "_inspect",
        lambda service: _container(
            Path("/data/ai-platform-internal-test/releases/other"),
            service,
            commit=COMMIT if service in {"api", "worker", "frontend"} else "",
            image=FRONTEND if service == "frontend" else BACKEND,
        ),
    )

    with pytest.raises(quickstart.QuickstartError, match="runtime subject is invalid"):
        release._current_runtime()


def _release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]) -> quickstart.Quickstart:
    release = quickstart.Quickstart(tmp_path, tmp_path / "managed", health_timeout=0)
    release.docker = ["docker"]
    subject = quickstart.Subject(COMMIT, BACKEND, FRONTEND, tmp_path / ".env")
    previous = quickstart.Subject(OLD_COMMIT, BACKEND.replace("3", "5"), FRONTEND.replace("4", "6"))
    monkeypatch.setattr(quickstart, "_load_subject", lambda *_: subject)
    monkeypatch.setattr(release, "_detect_docker", lambda: None)
    monkeypatch.setattr(release, "_verify_source", lambda _: events.append("source"))
    monkeypatch.setattr(release, "_current_runtime", lambda: previous)
    monkeypatch.setattr(release, "_validate_env", lambda _: tmp_path / ".env")
    monkeypatch.setattr(release, "_compose", lambda _env, _subject, *args: events.append("compose:" + " ".join(args)))
    monkeypatch.setattr(release, "_wait_health", lambda _: events.append("health"))
    monkeypatch.setattr(
        release,
        "_wait_worker_runtime",
        lambda *_args, **_kwargs: events.append("worker-heartbeat"),
    )
    monkeypatch.setattr(
        release.runner,
        "run",
        lambda command, **_: events.append(
            f"inspect:{list(command)[-1]}"
            if list(command)[-3:-1] == ["image", "inspect"]
            else "pull"
        )
        or "",
    )
    monkeypatch.setattr(release, "_rollback", lambda *_: events.append("rollback"))
    monkeypatch.setattr(release, "_preflight_rollback", lambda *_: events.append("rollback-preflight"))
    return release


def test_quickstart_orders_config_before_pull_and_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)

    release.run()

    assert events == [
        "source", "compose:config --quiet", "pull", "pull", f"inspect:{BACKEND}", "source",
        "rollback-preflight",
        "compose:up -d --no-build --pull never", "health", "worker-heartbeat",
    ]


def test_pull_failure_never_runs_up_or_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)
    monkeypatch.setattr(
        release.runner,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(quickstart.QuickstartError("pull")),
    )

    with pytest.raises(quickstart.QuickstartError, match="pull"):
        release.run()

    assert not any("up" in event for event in events)
    assert "rollback" not in events


def test_unavailable_rollback_blocks_target_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)
    monkeypatch.setattr(
        release,
        "_preflight_rollback",
        lambda *_: (_ for _ in ()).throw(quickstart.QuickstartError("rollback unavailable")),
    )

    with pytest.raises(quickstart.QuickstartError, match="rollback unavailable"):
        release.run()

    assert not any(event.startswith("compose:up") for event in events)


def test_runtime_change_after_pull_blocks_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)
    previous = quickstart.Subject(
        OLD_COMMIT, BACKEND.replace("3", "5"), FRONTEND.replace("4", "6")
    )
    changed = quickstart.Subject("7" * 40, previous.backend_image, previous.frontend_image)
    runtimes = iter((previous, changed))
    monkeypatch.setattr(release, "_current_runtime", lambda: next(runtimes))

    with pytest.raises(quickstart.QuickstartError, match="runtime changed"):
        release.run()

    assert not any(event.startswith("compose:up") for event in events)
    assert "rollback" not in events


def test_http_protocol_error_is_normalized_for_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = quickstart.Quickstart(tmp_path, health_timeout=0)
    monkeypatch.setattr(
        release,
        "_health",
        lambda _subject: (_ for _ in ()).throw(BadStatusLine("untrusted response")),
    )

    with pytest.raises(quickstart.QuickstartError, match="health did not converge"):
        release._wait_health(quickstart.Subject(COMMIT, BACKEND, FRONTEND))


def test_health_probes_configured_opensandbox_from_api_and_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = quickstart.Quickstart(tmp_path)
    release.docker = ["docker"]
    subject = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    probe_commands: list[list[str]] = []

    monkeypatch.setattr(
        release,
        "_http_json",
        lambda path: (
            {"status": "ok"}
            if path.endswith("/health")
            else {"status": "ready", "runtime_commit": COMMIT}
        ),
    )
    monkeypatch.setattr(
        release,
        "_inspect",
        lambda service: _container(
            tmp_path,
            service,
            commit=COMMIT if service in {"api", "worker", "frontend"} else "",
            image=FRONTEND if service == "frontend" else BACKEND,
            health="none" if service == "worker" else "healthy",
        ),
    )

    def run(command, **_kwargs):
        if command[:2] == ["systemctl", "is-active"]:
            return "active"
        probe_commands.append(list(command))
        return ""

    monkeypatch.setattr(release.runner, "run", run)

    release._health(subject)

    assert [command[2] for command in probe_commands] == [
        "ai-platform-api",
        "ai-platform-worker",
    ]
    assert all("OPENSANDBOX_BASE_URL" in command[-1] for command in probe_commands)
    assert all("OPENSANDBOX_PROTOCOL" in command[-1] for command in probe_commands)
    assert all("OPENSANDBOX_DOMAIN" in command[-1] for command in probe_commands)
    assert all("ProxyHandler({})" in command[-1] for command in probe_commands)


def test_health_rejects_incomplete_worker_compose_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = quickstart.Quickstart(tmp_path)
    subject = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    bad_worker = replace(
        _container(
            tmp_path,
            "worker",
            commit=COMMIT,
            image=BACKEND,
            health="none",
        ),
        config_hash="",
    )
    monkeypatch.setattr(
        release,
        "_http_json",
        lambda path: (
            {"status": "ok"}
            if path.endswith("/health")
            else {"status": "ready", "runtime_commit": COMMIT}
        ),
    )
    monkeypatch.setattr(
        release,
        "_inspect",
        lambda service: (
            bad_worker
            if service == "worker"
            else _container(
                tmp_path,
                service,
                commit=COMMIT if service in {"api", "frontend"} else "",
                image=FRONTEND if service == "frontend" else BACKEND,
            )
        ),
    )

    with pytest.raises(quickstart.QuickstartError, match="container identity failed"):
        release._health(subject)


def test_health_probe_uses_supported_protocol_domain_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class Response:
        status = 200

        def read(self, _limit: int) -> bytes:
            return b"{}"

    class Opener:
        def open(self, url: str, *, timeout: int) -> Response:
            assert timeout == 10
            requested_urls.append(url)
            return Response()

    monkeypatch.setenv("OPENSANDBOX_BASE_URL", "")
    monkeypatch.setenv("OPENSANDBOX_PROTOCOL", "http")
    monkeypatch.setenv("OPENSANDBOX_DOMAIN", "opensandbox.internal:8080")
    monkeypatch.setattr(urllib.request, "build_opener", lambda _handler: Opener())

    with pytest.raises(SystemExit) as exc_info:
        exec(quickstart.OPENSANDBOX_HEALTH_PROBE, {})

    assert exc_info.value.code == 0
    assert requested_urls == ["http://opensandbox.internal:8080/health"]


def test_configured_lifecycle_probe_failure_is_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = quickstart.Quickstart(tmp_path)
    release.docker = ["docker"]

    def fail(*_args, **_kwargs):
        raise quickstart.QuickstartError("command failed")

    monkeypatch.setattr(release.runner, "run", fail)

    with pytest.raises(quickstart.QuickstartError, match="command failed"):
        release._probe_opensandbox_lifecycle()


def test_up_failure_runs_one_small_rollback_without_destructive_compose_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)

    def compose(_env: Path, _subject: object, *args: str) -> None:
        events.append("compose:" + " ".join(args))
        if args and args[0] == "up":
            raise quickstart.QuickstartError("up")

    monkeypatch.setattr(release, "_compose", compose)
    with pytest.raises(quickstart.QuickstartError, match="previous images are healthy again"):
        release.run()

    assert events.count("rollback") == 1
    assert all("down" not in event and "-v" not in event for event in events)


def test_keyboard_interrupt_after_up_runs_small_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)

    def compose(_env: Path, _subject: object, *args: str) -> None:
        events.append("compose:" + " ".join(args))
        if args and args[0] == "up":
            raise KeyboardInterrupt

    monkeypatch.setattr(release, "_compose", compose)
    with pytest.raises(quickstart.QuickstartError, match="previous images are healthy again"):
        release.run()

    assert events.count("rollback") == 1


def test_protocol_v2_lease_probe_reads_processing_and_retry_metadata(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    class ProbeRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            commands.append(list(command))
            return json.dumps(
                {"status": "ok", "processing": 1, "retry": 2, "total": 2}
            )

    release = quickstart.Quickstart(tmp_path, runner=ProbeRunner())
    release.docker = ["docker", "--context", "default"]

    assert release._protocol_v2_lease_count() == 2
    assert commands == [
        [
            "docker",
            "--context",
            "default",
            "exec",
            "ai-platform-redis",
            "redis-cli",
            "--raw",
            "EVAL",
            quickstart.ROLLBACK_PROTOCOL_V2_LEASE_PROBE,
            "2",
            quickstart.ROLLBACK_PROCESSING_META_KEY,
            quickstart.ROLLBACK_RETRY_META_KEY,
            str(quickstart.ROLLBACK_LEASE_SCAN_LIMIT),
        ]
    ]


@pytest.mark.parametrize(
    "response",
    [
        '{"status":"unproven","reason":"invalid_metadata"}',
        '{"status":"ok","processing":1,"retry":0,"total":2}',
        "not-json",
    ],
)
def test_protocol_v2_lease_probe_fails_closed(
    tmp_path: Path,
    response: str,
) -> None:
    class ProbeRunner(quickstart.Runner):
        def run(self, _command: object, **_: object) -> str:
            return response

    release = quickstart.Quickstart(tmp_path, runner=ProbeRunner())
    release.docker = ["docker"]

    with pytest.raises(quickstart.QuickstartError, match="lease state could not be proven"):
        release._protocol_v2_lease_count()


def test_worker_runtime_sample_proves_exact_runtime_and_live_heartbeat(
    tmp_path: Path,
) -> None:
    container = _container(
        tmp_path,
        "worker",
        commit=COMMIT,
        image=BACKEND,
        health="none",
    )
    commands: list[list[str]] = []

    class RecoveryRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            command = list(command)
            commands.append(command)
            if "inspect" in command:
                return "\t".join(
                    (
                        container.container_id,
                        container.commit,
                        container.image,
                        str(container.restart_count),
                        container.status,
                        container.health,
                        container.project,
                        container.service,
                        container.config_files,
                        container.release_owner,
                        container.release_role,
                        container.source_dirty,
                        container.working_dir,
                        container.one_off,
                        container.config_hash,
                    )
                )
            return json.dumps(
                {
                    "status": "ok",
                    "worker_id": "worker-a",
                    "pid": 17,
                    "observed_at": 101.0,
                }
            )

    release = quickstart.Quickstart(tmp_path, runner=RecoveryRunner())
    release.docker = ["docker"]

    sample = release._worker_runtime_sample(
        quickstart.Subject(COMMIT, BACKEND, FRONTEND),
        not_before=100.0,
    )

    assert sample == quickstart.WorkerRuntimeSample(
        container_id=container.container_id,
        restart_count=0,
        config_hash="config-worker",
        worker_id="worker-a",
        pid=17,
        observed_at=101.0,
    )
    assert commands[1][2:6] == [container.container_id, "python", "-I", "-c"]
    assert commands[1][-2:] == [COMMIT, "100.0"]


def test_worker_runtime_wait_requires_stable_identity_and_advancing_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = quickstart.Quickstart(tmp_path, health_timeout=10)
    samples = iter(
        (
            quickstart.WorkerRuntimeSample(
                "a" * 64, 0, "config-worker", "worker-a", 17, 101.0
            ),
            quickstart.WorkerRuntimeSample(
                "a" * 64, 0, "config-worker", "worker-a", 17, 101.0
            ),
            quickstart.WorkerRuntimeSample(
                "a" * 64, 0, "config-worker", "worker-a", 17, 106.0
            ),
        )
    )
    calls = 0

    def sample(*_args: object, **_kwargs: object) -> quickstart.WorkerRuntimeSample:
        nonlocal calls
        calls += 1
        return next(samples)

    monkeypatch.setattr(release, "_worker_runtime_sample", sample)
    monkeypatch.setattr(quickstart.time, "sleep", lambda _seconds: None)

    release._wait_worker_runtime(
        quickstart.Subject(COMMIT, BACKEND, FRONTEND),
        not_before=100.0,
    )

    assert calls == 3


def test_worker_runtime_wait_fails_closed_without_heartbeat_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = quickstart.Quickstart(tmp_path, health_timeout=0)
    sample = quickstart.WorkerRuntimeSample(
        "a" * 64, 0, "config-worker", "worker-a", 17, 101.0
    )
    monkeypatch.setattr(
        release,
        "_worker_runtime_sample",
        lambda *_args, **_kwargs: sample,
    )

    with pytest.raises(quickstart.QuickstartError, match="did not converge"):
        release._wait_worker_runtime(
            quickstart.Subject(COMMIT, BACKEND, FRONTEND),
            not_before=100.0,
        )


def test_rollback_with_v2_lease_keeps_api_stopped_and_target_worker_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    target = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    release = quickstart.Quickstart(tmp_path, tmp_path / "managed")
    monkeypatch.setattr(
        release,
        "_compose",
        lambda _env, used_subject, *args: events.append(
            (used_subject.commit, " ".join(args))
        ),
    )
    monkeypatch.setattr(release, "_protocol_v2_lease_count", lambda: 1)
    verified: list[tuple[str, float]] = []
    monkeypatch.setattr(
        release,
        "_wait_worker_runtime",
        lambda used_subject, *, not_before: verified.append(
            (used_subject.commit, not_before)
        ),
    )

    with pytest.raises(quickstart.RollbackBlockedError, match="active protocol-v2 leases"):
        release._rollback(target, previous, tmp_path / ".env")

    assert events == [
        (COMMIT, "stop api worker"),
        (COMMIT, "up -d --no-build --pull never worker"),
    ]
    assert len(verified) == 1
    assert verified[0][0] == COMMIT
    assert release.repo == tmp_path.resolve()


def test_rollback_switches_images_only_after_proving_no_v2_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    previous_repo = root / "releases" / OLD_COMMIT
    target = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    release = quickstart.Quickstart(tmp_path, root)
    events: list[tuple[str, str, Path]] = []
    monkeypatch.setattr(release, "_verify_checkout", lambda *_: None)
    monkeypatch.setattr(release, "_protocol_v2_lease_count", lambda: 0)
    monkeypatch.setattr(
        release,
        "_compose",
        lambda _env, used_subject, *args: events.append(
            (used_subject.commit, " ".join(args), release.repo)
        ),
    )
    monkeypatch.setattr(
        release,
        "_wait_health",
        lambda used_subject: events.append((used_subject.commit, "health", release.repo)),
    )
    monkeypatch.setattr(
        release,
        "_wait_worker_runtime",
        lambda used_subject, **_kwargs: events.append(
            (used_subject.commit, "worker-heartbeat", release.repo)
        ),
    )

    release._rollback(target, previous, tmp_path / ".env")

    assert events == [
        (COMMIT, "stop api worker", tmp_path.resolve()),
        (OLD_COMMIT, "config --quiet", previous_repo),
        (OLD_COMMIT, "up -d --no-build --pull never", previous_repo),
        (OLD_COMMIT, "health", previous_repo),
        (OLD_COMMIT, "worker-heartbeat", previous_repo),
    ]
    assert release.repo == tmp_path.resolve()


def test_interrupt_after_target_stop_restores_and_verifies_target_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    target = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    release = quickstart.Quickstart(tmp_path, tmp_path / "managed")
    monkeypatch.setattr(
        release,
        "_compose",
        lambda _env, used_subject, *args: events.append(
            (used_subject.commit, " ".join(args))
        ),
    )
    monkeypatch.setattr(
        release,
        "_protocol_v2_lease_count",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    monkeypatch.setattr(release, "_wait_worker_runtime", lambda *_args, **_kwargs: None)

    with pytest.raises(quickstart.RollbackInterruptedError, match="worker was verified"):
        release._rollback(target, previous, tmp_path / ".env")

    assert events == [
        (COMMIT, "stop api worker"),
        (COMMIT, "stop api worker"),
        (COMMIT, "up -d --no-build --pull never worker"),
    ]


def test_interrupt_during_previous_up_restores_and_verifies_target_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    previous_repo = root / "releases" / OLD_COMMIT
    target = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    release = quickstart.Quickstart(tmp_path, root)
    events: list[tuple[str, str, Path]] = []
    interrupted = False

    def compose(_env: Path, used_subject: quickstart.Subject, *args: str) -> None:
        nonlocal interrupted
        events.append((used_subject.commit, " ".join(args), release.repo))
        if used_subject == previous and args and args[0] == "up" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(release, "_verify_checkout", lambda *_: None)
    monkeypatch.setattr(release, "_protocol_v2_lease_count", lambda: 0)
    monkeypatch.setattr(release, "_compose", compose)
    monkeypatch.setattr(release, "_wait_worker_runtime", lambda *_args, **_kwargs: None)

    with pytest.raises(quickstart.RollbackInterruptedError, match="worker was verified"):
        release._rollback(target, previous, tmp_path / ".env")

    assert events == [
        (COMMIT, "stop api worker", tmp_path.resolve()),
        (OLD_COMMIT, "config --quiet", previous_repo),
        (OLD_COMMIT, "up -d --no-build --pull never", previous_repo),
        (COMMIT, "stop api worker", tmp_path.resolve()),
        (COMMIT, "up -d --no-build --pull never worker", tmp_path.resolve()),
    ]
    assert release.repo == tmp_path.resolve()


def test_signal_during_rollback_is_deferred_until_previous_runtime_is_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    previous_repo = root / "releases" / OLD_COMMIT
    target = quickstart.Subject(COMMIT, BACKEND, FRONTEND)
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    release = quickstart.Quickstart(tmp_path, root)
    events: list[str] = []
    handlers: dict[int, object] = {
        signal.SIGINT: object(),
        signal.SIGTERM: object(),
    }

    def install(signum: int, handler: object) -> object:
        old = handlers[signum]
        handlers[signum] = handler
        return old

    def probe() -> int:
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        events.append("signal-latched")
        return 0

    monkeypatch.setattr(quickstart.signal, "signal", install)
    monkeypatch.setattr(release, "_verify_checkout", lambda *_: None)
    monkeypatch.setattr(release, "_protocol_v2_lease_count", probe)
    monkeypatch.setattr(
        release,
        "_compose",
        lambda _env, used_subject, *args: events.append(
            f"{used_subject.commit}:{' '.join(args)}:{release.repo}"
        ),
    )
    monkeypatch.setattr(
        release,
        "_wait_health",
        lambda used_subject: events.append(
            f"{used_subject.commit}:health:{release.repo}"
        ),
    )
    monkeypatch.setattr(
        release,
        "_wait_worker_runtime",
        lambda used_subject, **_kwargs: events.append(
            f"{used_subject.commit}:worker-heartbeat:{release.repo}"
        ),
    )

    with pytest.raises(quickstart.RollbackInterruptedError, match="runtime was verified"):
        with release.termination:
            release.termination.protect_runtime_transition()
            release._rollback(target, previous, tmp_path / ".env")

    assert events[-1] == f"{OLD_COMMIT}:worker-heartbeat:{previous_repo}"
    assert not callable(handlers[signal.SIGTERM])
    assert release.repo == tmp_path.resolve()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
def test_two_process_group_signals_are_deferred_through_previous_runtime_recovery(
    tmp_path: Path,
) -> None:
    harness = textwrap.dedent(
        f"""
        import sys
        import time
        from pathlib import Path

        sys.path.insert(0, {str(ROOT)!r})
        from tools import sandbox_quickstart as quickstart

        target = quickstart.Subject({COMMIT!r}, {BACKEND!r}, {FRONTEND!r})
        previous = quickstart.Subject({OLD_COMMIT!r}, {BACKEND!r}, {FRONTEND!r})
        release = quickstart.Quickstart(Path({str(tmp_path)!r}), Path({str(tmp_path / 'managed')!r}))
        release._verify_checkout = lambda *_args: None
        release._protocol_v2_lease_count = lambda: 0

        def compose(_env, subject, *arguments):
            if subject == previous and arguments and arguments[0] == "up":
                print("previous-up", flush=True)
                release.runner.run(
                    [sys.executable, "-c", "import time; time.sleep(0.5)"],
                    timeout=5,
                )

        release._compose = compose
        release._wait_health = lambda _subject: print("previous-health", flush=True)
        release._wait_worker_runtime = (
            lambda _subject, **_kwargs: print("previous-worker", flush=True)
        )
        release._restore_target_recovery_worker = (
            lambda *_args: print("target-worker", flush=True)
        )

        try:
            with release.termination:
                release.termination.protect_runtime_transition()
                release._rollback(target, previous, Path({str(tmp_path / '.env')!r}))
        except quickstart.RollbackInterruptedError:
            print(f"safe-interrupted:{{release.termination.pending_count}}", flush=True)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", harness],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    first_line = process.stdout.readline().strip()
    assert first_line == "previous-up"
    process_group = os.getpgid(process.pid)
    os.killpg(process_group, signal.SIGTERM)
    time.sleep(0.05)
    os.killpg(process_group, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)
    output = "\n".join((first_line, stdout))

    assert process.returncode == 0, stderr
    assert "previous-health" in output
    assert "previous-worker" in output
    assert "target-worker" not in output
    assert "safe-interrupted:2" in output


@pytest.mark.parametrize(
    "status", [" M deploy/ai-platform/docker-compose.yml", "?? tools/json.py"]
)
def test_dirty_exact_checkout_is_rejected(tmp_path: Path, status: str) -> None:
    root = tmp_path / "managed"
    repo = root / "releases" / COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")

    class DirtyRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            return status if "status" in command else COMMIT

    release = quickstart.Quickstart(repo, root, runner=DirtyRunner())
    with pytest.raises(quickstart.QuickstartError, match="not clean"):
        release._verify_checkout(repo, COMMIT)


def test_rollback_preflight_checks_checkout_images_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "managed"
    previous_repo = root / "releases" / OLD_COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = previous_repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")
    commands: list[list[str]] = []

    class PreflightRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            command = list(command)
            commands.append(command)
            return "" if "status" in command else OLD_COMMIT

    release = quickstart.Quickstart(tmp_path, root, runner=PreflightRunner())
    release.docker = ["docker"]
    previous = quickstart.Subject(OLD_COMMIT, BACKEND, FRONTEND)
    config_repos: list[Path] = []
    monkeypatch.setattr(
        release, "_compose", lambda *_args: config_repos.append(release.repo)
    )

    release._preflight_rollback(previous, tmp_path / ".env")

    assert config_repos == [previous_repo]
    assert [command[-1] for command in commands if command[:3] == ["docker", "image", "inspect"]] == [
        BACKEND, FRONTEND
    ]
    assert release.repo == tmp_path.resolve()


def test_runbook_exposes_internal_test_latest_and_retry_commands() -> None:
    runbook = (ROOT / "docs/operations/release-operations-runbook.md").read_text(
        encoding="utf-8"
    )
    command = "./scripts/deploy-latest.sh --profile internal-test"
    assert command in runbook
    assert f"{command} --latest" in runbook
    assert "incoming/latest-main.json" in runbook
    assert "never runs `down`, `down -v`, or volume deletion" in runbook
    assert "across two advancing fresh runtime heartbeats" in runbook
    assert "saved previous binary's exact" in runbook


def test_existing_shell_entry_forwards_to_the_canonical_profile() -> None:
    entry = (ROOT / "scripts/quickstart-s72.sh").read_text(encoding="utf-8")
    assert "scripts/deploy-latest.sh" in entry
    assert "--profile internal-test" in entry
    assert '"$@"' in entry

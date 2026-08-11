from __future__ import annotations

import json
from http.client import BadStatusLine
from pathlib import Path

import pytest

from tools import s72_quickstart as quickstart


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
OLD_COMMIT = "2" * 40
BACKEND = quickstart.BACKEND_REPOSITORY + "@sha256:" + "3" * 64
FRONTEND = quickstart.FRONTEND_REPOSITORY + "@sha256:" + "4" * 64


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
        ["sudo", "-n", "docker"], tmp_path, tmp_path / ".env", subject
    )

    assert command[:4] == ["sudo", "-n", "env", "-i"]
    assert [command[index + 1] for index, value in enumerate(command) if value == "-f"] == [
        str(tmp_path / path) for path in quickstart.COMPOSE_FILES
    ]
    assert "docker-compose.sandbox.yml" not in " ".join(command)
    assert "docker-compose.opensandbox.yml" not in " ".join(command)
    assert "docker-compose.s72-colocation.yml" not in " ".join(command)
    rendered = " ".join(command)
    assert BACKEND in rendered and FRONTEND in rendered and COMMIT in rendered


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

    def inspect(service: str) -> list[str]:
        image = FRONTEND if service == "frontend" else BACKEND
        commit = COMMIT if service in {"api", "worker", "frontend"} else ""
        return [
            commit, image, "0", "running", "healthy",
            quickstart.PROJECT, service, expected_config,
        ]

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
        lambda service: [
            COMMIT if service in {"api", "worker", "frontend"} else "",
            FRONTEND if service == "frontend" else BACKEND,
            "0", "running", "healthy", quickstart.PROJECT, service,
            "/data/ai-platform-internal-test/releases/other/docker-compose.yml",
        ],
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
    monkeypatch.setattr(release.runner, "run", lambda command, **_: events.append("pull") or "")
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
        "source", "compose:config --quiet", "pull", "pull", "source", "rollback-preflight",
        "compose:up -d --no-build --pull never", "health",
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


def test_runbook_exposes_the_zero_argument_quickstart() -> None:
    runbook = (ROOT / "docs/operations/211-release-operations-runbook.md").read_text(
        encoding="utf-8"
    )
    assert "./scripts/quickstart-s72.sh" in runbook
    assert "incoming/latest-main.json" in runbook
    assert "never runs `down`, `down -v`, or volume deletion" in runbook


def test_shell_entry_uses_python_isolated_mode() -> None:
    entry = (ROOT / "scripts/quickstart-s72.sh").read_text(encoding="utf-8")
    assert "python3 -I" in entry

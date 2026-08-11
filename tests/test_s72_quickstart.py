from __future__ import annotations

import json
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

    assert command[:3] == ["sudo", "-n", "env"]
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
                return "https://github.com/demonsxxxxxx/ai-platform.git"
            if "rev-parse" in joined:
                return COMMIT
            if "status" in joined:
                return ""
            return OLD_COMMIT + "\trefs/heads/main"

    release = quickstart.Quickstart(repo, tmp_path / "managed", runner=SourceRunner())
    with pytest.raises(quickstart.QuickstartError, match="not fresh origin/main"):
        release._verify_source(quickstart.Subject(COMMIT, BACKEND, FRONTEND))


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
    return release


def test_quickstart_orders_config_before_pull_and_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    release = _release(tmp_path, monkeypatch, events)

    release.run()

    assert events == [
        "source", "compose:config --quiet", "pull", "pull", "source",
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


def test_dirty_exact_checkout_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    repo = root / "releases" / COMMIT
    for relative in quickstart.COMPOSE_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")

    class DirtyRunner(quickstart.Runner):
        def run(self, command: object, **_: object) -> str:
            return " M deploy/ai-platform/docker-compose.yml" if "status" in command else COMMIT

    release = quickstart.Quickstart(repo, root, runner=DirtyRunner())
    with pytest.raises(quickstart.QuickstartError, match="not clean"):
        release._verify_checkout(repo, COMMIT)


def test_runbook_exposes_the_zero_argument_quickstart() -> None:
    runbook = (ROOT / "docs/operations/211-release-operations-runbook.md").read_text(
        encoding="utf-8"
    )
    assert "python3 tools/s72_quickstart.py" in runbook
    assert "incoming/latest-main.json" in runbook
    assert "never runs `down`, `down -v`, or volume deletion" in runbook

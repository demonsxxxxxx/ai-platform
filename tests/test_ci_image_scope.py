from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from tools.ci_image_scope import (
    changed_paths,
    image_inputs_affected,
    image_validation_disposition,
)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("backend", "Dockerfile"),
        ("backend", "app/main.py"),
        ("backend", "tools/release.py"),
        ("backend", "docs/release-evidence/schema.json"),
        ("frontend", "frontend/web/src/main.tsx"),
        ("frontend", "frontend/web/Dockerfile"),
        ("frontend", "tools/frontend_release_traceability.py"),
        ("frontend", "tests/test_frontend_linux_contracts.py"),
    ],
)
def test_image_inputs_affected_for_packaged_inputs(role: str, path: str) -> None:
    assert image_inputs_affected(role, [path]) is True


@pytest.mark.parametrize("role", ["backend", "frontend"])
def test_policy_only_pull_request_does_not_affect_images(role: str) -> None:
    assert image_inputs_affected(role, ["architecture-policy.json"]) is False
    assert image_validation_disposition(
        event_name="pull_request",
        role=role,
        changed_paths=["architecture-policy.json"],
    ) == (False, "not_affected")


@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("role", ["backend", "frontend"])
def test_non_pull_request_image_builds_are_owned_by_packaging(
    event_name: str, role: str
) -> None:
    assert image_validation_disposition(
        event_name=event_name,
        role=role,
        changed_paths=(),
    ) == (False, "packaging_owned")


def test_unknown_event_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported image validation event"):
        image_validation_disposition(
            event_name="schedule",
            role="backend",
            changed_paths=(),
        )


def _local_docker_copy_sources(dockerfile: Path) -> tuple[str, ...]:
    sources: list[str] = []
    for raw_line in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)
        if any(token.startswith("--from=") for token in tokens[1:]):
            continue
        operands = [token for token in tokens[1:] if not token.startswith("--")]
        sources.extend(operands[:-1])
    return tuple(sources)


@pytest.mark.parametrize(
    ("role", "dockerfile"),
    [("backend", "Dockerfile"), ("frontend", "frontend/web/Dockerfile")],
)
def test_classifier_covers_every_local_docker_copy_source(
    role: str, dockerfile: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    sources = _local_docker_copy_sources(root / dockerfile)
    assert sources
    for source in sources:
        source_path = root / source
        probe = f"{source.rstrip('/')}/__image_scope_probe__" if source_path.is_dir() else source
        assert image_inputs_affected(role, [probe]), source


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def test_changed_paths_uses_exact_ancestor_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    policy = repo / "architecture-policy.json"
    policy.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "architecture-policy.json")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    policy.write_text('{"version": 1}\n', encoding="utf-8")
    _git(repo, "add", "architecture-policy.json")
    _git(repo, "commit", "-m", "candidate")
    head = _git(repo, "rev-parse", "HEAD")

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base,
            head,
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    assert completed.stdout == b"architecture-policy.json\0"

    monkeypatch.chdir(repo)
    assert changed_paths(base, head) == ("architecture-policy.json",)

    with pytest.raises(ValueError, match="exact lowercase commit SHA"):
        changed_paths("main", head)


def test_changed_paths_compares_diverged_exact_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "common base")
    common_base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "--create", "candidate", common_base)
    app_dir = repo / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app/main.py")
    _git(repo, "commit", "-m", "candidate change")
    head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "main")
    (repo / "base-only.txt").write_text("advanced base\n", encoding="utf-8")
    _git(repo, "add", "base-only.txt")
    _git(repo, "commit", "-m", "advance base")
    base = _git(repo, "rev-parse", "HEAD")

    monkeypatch.chdir(repo)
    assert changed_paths(base, head) == ("app/main.py", "base-only.txt")

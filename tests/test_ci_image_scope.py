from __future__ import annotations

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
def test_non_pull_request_events_always_build_images(
    event_name: str, role: str
) -> None:
    assert image_validation_disposition(
        event_name=event_name,
        role=role,
        changed_paths=(),
    ) == (True, "required_event")


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

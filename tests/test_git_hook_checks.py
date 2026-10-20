from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tools import git_hook_checks as hooks


def git(root, *args, check=True, data=None):
    return subprocess.run(["git", *args], cwd=root, input=data, capture_output=True,
                          text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Hook Fixture")
    git(root, "config", "user.email", "hook@example.test")
    for relative in (
        "tools/git_hook_checks.py", "tools/check_documentation.py", "tools/run_test_stage.py",
        "scripts/analysis/source_quality_inventory.py", ".githooks/pre-commit", ".githooks/pre-push",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hooks.ROOT / relative, target)
        if relative.startswith(".githooks/"):
            target.chmod(0o755)
    (root / ".gitignore").write_text(".venv\n.pytest-tmp/\n__pycache__/\n.pytest_cache/\n")
    (root / "README.md").write_text("# Fixture\n")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(sys.prefix, str(root / ".venv"))
    else:
        (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    git(root, "add", ".")
    assert not git(root, "ls-files", ".venv").stdout
    git(root, "commit", "-m", "fixture base")
    return root


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_precommit_handles_deep_snapshot_paths(repo):
    path = "docs/" + "/".join(["evidence-contract-" + "x" * 24] * 6) + "/readiness.md"
    write(repo, path, "# Synthetic contract\n")
    git(repo, "-c", "core.longpaths=true", "add", "docs")
    result = git(
        repo, "-c", "core.longpaths=true", "-c", "core.hooksPath=.githooks",
        "commit", "-m", "deep snapshot", check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "-c", "core.longpaths=true", "show", "HEAD:" + path).stdout == "# Synthetic contract\n"


def test_precommit_starts_frontend_entry_from_snapshot(repo):
    write(repo, "frontend/web/src/check.js", "const value = 1;\n")
    write(
        repo, "frontend/web/node_modules/eslint/bin/eslint.js",
        "require('node:fs').accessSync(process.argv.at(-1));\n"
        "console.log('synthetic-eslint-entry');\n",
    )
    git(repo, "add", "frontend/web/src/check.js")
    result = git(repo, "-c", "core.hooksPath=.githooks", "commit", "-m", "frontend snapshot", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "synthetic-eslint-entry" in result.stdout + result.stderr


def test_precommit_checks_staged_source_and_does_not_change_drafts(repo):
    write(repo, "app/rate.py", "import unused_module\n")
    git(repo, "add", "app/rate.py")
    write(repo, "app/rate.py", "RATE = 2\n")
    before = git(repo, "status", "--porcelain=v1").stdout
    original_head = git(repo, "rev-parse", "HEAD").stdout
    result = git(repo, "-c", "core.hooksPath=.githooks", "commit", "-m", "must fail", check=False)
    assert result.returncode != 0
    assert "F401" in result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout == original_head
    assert git(repo, "status", "--porcelain=v1").stdout == before
    assert (repo / "app/rate.py").read_text() == "RATE = 2\n"


def test_precommit_document_failure_uses_the_staged_target(repo):
    write(repo, "docs/contract.md", "# Contract\n[missing](absent.md)\n")
    git(repo, "add", "docs/contract.md")
    write(repo, "docs/contract.md", "# Worktree draft without a link\n")
    result = git(repo, "-c", "core.hooksPath=.githooks", "commit", "-m", "must fail", check=False)
    assert result.returncode != 0
    assert "link_target_missing" in result.stdout + result.stderr


def test_deleted_document_checks_its_unchanged_consumers(repo):
    write(repo, "docs/target.md", "# Target\n")
    write(repo, "docs/consumer.md", "# Consumer\n[target](target.md)\n")
    git(repo, "add", "docs")
    git(repo, "commit", "-m", "documents")
    git(repo, "rm", "docs/target.md")
    result = git(repo, "-c", "core.hooksPath=.githooks", "commit", "-m", "must fail", check=False)
    assert result.returncode != 0
    assert "consumer.md" in result.stdout + result.stderr


def test_affected_tests_follow_imports_and_deleted_modules(tmp_path):
    write(tmp_path, "app/runs/domain/rate.py", "RATE = 1\n")
    write(tmp_path, "app/runs/api.py", "from app.runs.domain.rate import RATE\n")
    write(tmp_path, "tests/test_rate.py", "from app.runs.api import RATE\n")
    write(tmp_path, "tests/test_other.py", "def test_other(): pass\n")
    changed = ["app/runs/domain/rate.py"]
    assert hooks.affected_tests(tmp_path, changed) == ["tests/test_rate.py"]
    (tmp_path / changed[0]).unlink()
    assert hooks.affected_tests(tmp_path, changed) == ["tests/test_rate.py"]
    assert hooks.affected_tests(tmp_path, ["uv.lock"]) == ["tests/test_other.py", "tests/test_rate.py"]


def test_local_prepush_checks_commit_not_uncommitted_worktree(repo, tmp_path):
    write(repo, "app/rate.py", "RATE = 1\n")
    write(repo, "tests/test_rate.py", "from app.rate import RATE\ndef test_rate():\n    assert RATE == 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base for hook")
    git(tmp_path, "init", "--bare", "remote.git")
    git(repo, "remote", "add", "origin", "../remote.git")
    git(repo, "push", "origin", "main")
    write(repo, "app/rate.py", "RATE = 2\n")
    git(repo, "add", "app/rate.py")
    git(repo, "commit", "-m", "fixed rate")
    write(repo, "app/rate.py", "RATE = 999\n")
    before = git(repo, "status", "--porcelain=v1").stdout
    result = git(repo, "-c", "core.hooksPath=.githooks", "push", "origin", "main", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests/test_rate.py" in result.stdout
    assert "1 passed" in result.stdout
    assert git(repo, "status", "--porcelain=v1").stdout == before
    assert (repo / "app/rate.py").read_text() == "RATE = 999\n"


def test_frontend_deletion_still_runs_typecheck(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(hooks, "link_frontend_dependencies", lambda *_: None)
    monkeypatch.setattr(hooks.subprocess, "run", lambda args, **_kwargs: commands.append(args))
    hooks.checks(tmp_path, tmp_path, ["frontend/web/src/deleted.tsx"], push=True)
    assert commands == [["node", "node_modules/typescript/bin/tsc", "-b"]]


@pytest.mark.parametrize("record", ["bad", "main nope main " + "a" * 40, "main " + "a" * 40 + " main nope"])
def test_invalid_push_records_fail_without_running_git(tmp_path, record):
    with pytest.raises(ValueError, match="invalid Git"):
        hooks.push_ranges(tmp_path, record)

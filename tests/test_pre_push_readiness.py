from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READINESS_TOOL = REPO_ROOT / "tools" / "pre_push_readiness.py"
GOVERNANCE_TOOL = REPO_ROOT / "tools" / "code_governance.py"
ISSUE_WORKFLOW = REPO_ROOT / "docs" / "agent-rules" / "github-issue-pr-workflow.md"


def _run(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git(repo: Path, *arguments: str) -> str:
    return _run(repo, "git", *arguments).stdout.strip()


def _write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def readiness_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "readiness@example.test")
    _git(repo, "config", "user.name", "Readiness Test")
    _write(repo, "README.md", "fixture\n")
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/billing.py", "RATE = 2\n")
    _write(repo, "tools/code_governance.py", GOVERNANCE_TOOL.read_text(encoding="utf-8"))
    base = _commit(repo, "base")
    return repo, base


def _check(repo: Path, base: str, head: str, *, output_format: str = "json") -> subprocess.CompletedProcess[str]:
    return _run(
        repo,
        sys.executable,
        str(READINESS_TOOL),
        "check",
        "--base-ref",
        base,
        "--head-ref",
        head,
        "--format",
        output_format,
        check=False,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_stale_base_fails_before_any_local_checks(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _git(repo, "checkout", "-b", "candidate", base)
    _write(repo, "docs/candidate.md", "candidate\n")
    head = _commit(repo, "candidate")
    _git(repo, "checkout", "main")
    _write(repo, "docs/main.md", "main\n")
    stale_base = _commit(repo, "main advances")

    result = _check(repo, stale_base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "stale_base"
    assert payload["failure"]["code"] == "non_ancestor_range"
    assert payload["stages"] == []


def test_malformed_exact_ref_is_rejected_as_a_governance_violation(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, head = readiness_repo

    result = _check(repo, "main", head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_ref"
    assert "40-hex" in payload["failure"]["message"]


def test_deterministic_product_failure_preserves_pytest_identity(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(
        repo,
        "tests/test_deterministic_failure.py",
        "from app import billing\n\n\ndef test_deterministic_failure():\n    assert billing.RATE == 3\n",
    )
    head = _commit(repo, "failing responsibility test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "product_test_failure"
    assert payload["failure"]["code"] == "pytest_failed"
    assert payload["failure"]["test_identity"] == "tests/test_deterministic_failure.py::test_deterministic_failure"


def test_governance_failure_keeps_rule_and_path(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "app/billing_rules.py", "RATE = 2\n")
    head = _commit(repo, "missing responsibility mirror")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "test_responsibility_mirror"
    assert payload["failure"]["path"] == "app/billing_rules.py"


def test_success_uses_the_exact_resolved_range_and_stable_taxonomy(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "docs only")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"
    assert payload["base_ref"] == base
    assert payload["head_ref"] == head
    assert payload["category"] is None
    assert set(payload["taxonomy"]) == {
        "external_check",
        "governance_violation",
        "infrastructure_failure",
        "product_test_failure",
        "stale_base",
    }
    assert {stage["name"] for stage in payload["stages"]} == {
        "compileall",
        "diff_check",
        "governance",
        "responsibility_tests",
    }


def test_docs_only_range_does_not_run_an_unrelated_existing_test(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _initial = readiness_repo
    _write(repo, "tests/test_repositories.py", "def test_unrelated_failure():\n    assert False\n")
    base = _commit(repo, "unrelated legacy test")
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "docs only")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "not_applicable"
    assert responsibility_stage["tests"] == []


def test_text_output_is_human_readable_and_uses_the_stable_category(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _git(repo, "checkout", "-b", "candidate", base)
    _write(repo, "docs/candidate.md", "candidate\n")
    head = _commit(repo, "candidate")
    _git(repo, "checkout", "main")
    _write(repo, "docs/main.md", "main\n")
    stale_base = _commit(repo, "main advances")

    result = _check(repo, stale_base, head, output_format="text")

    assert result.returncode == 2
    assert "pre-push-readiness: FAIL" in result.stdout
    assert "category: stale_base" in result.stdout
    assert "code: non_ancestor_range" in result.stdout


def test_pr_workflow_requires_the_exact_ref_gate_before_push_and_after_merge_up() -> None:
    workflow = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    normalized = workflow.lower()

    assert "tools/pre_push_readiness.py check" in workflow
    assert "before the first push" in normalized
    assert "after every ordinary merge-up" in normalized
    assert "stale_base" in workflow
    assert "product_test_failure" in workflow
    assert "governance_violation" in workflow
    assert "infrastructure_failure" in workflow
    assert "external_check" in workflow
    assert "positive infrastructure evidence on the same SHA" in workflow

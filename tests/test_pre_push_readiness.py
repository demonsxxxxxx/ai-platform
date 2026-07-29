from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READINESS_TOOL = REPO_ROOT / "tools" / "pre_push_readiness.py"
GOVERNANCE_TOOL = REPO_ROOT / "tools" / "code_governance.py"
CODE_GOVERNANCE_TEST = REPO_ROOT / "tests" / "test_code_governance.py"
ISSUE_WORKFLOW = REPO_ROOT / "docs" / "agent-rules" / "github-issue-pr-workflow.md"


def _run(
    repo: Path,
    *arguments: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
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
    _write(repo, "tools/pre_push_readiness.py", READINESS_TOOL.read_text(encoding="utf-8"))
    _write(repo, "tests/test_code_governance.py", CODE_GOVERNANCE_TEST.read_text(encoding="utf-8"))
    base = _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


def _check(
    repo: Path,
    base: str,
    head: str,
    *,
    output_format: str = "json",
    authority_ref: str | None = None,
    shared_test_suites: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    authority = authority_ref or _git(repo, "rev-parse", "refs/remotes/origin/main")
    temporary_root = Path(tempfile.mkdtemp(prefix="pre-push-readiness-test-authority-"))
    authority_worktree = temporary_root / "authority"
    _git(repo, "worktree", "add", "--detach", str(authority_worktree), authority)
    try:
        arguments = [
            sys.executable,
            "-P",
            str(authority_worktree / "tools" / "pre_push_readiness.py"),
            "check",
            "--authority-ref",
            authority,
            "--base-ref",
            base,
            "--head-ref",
            head,
            "--format",
            output_format,
        ]
        for suite in shared_test_suites:
            arguments.extend(("--shared-test-suite", suite))
        return _run(authority_worktree, *arguments, check=False, env=env)
    finally:
        _run(repo, "git", "worktree", "remove", "--force", str(authority_worktree), check=False)
        shutil.rmtree(temporary_root)


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def _governance_exception(*, reason: str) -> str:
    return json.dumps(
        {
            "schema_version": "ai-platform.code-governance-exception.v1",
            "expires_on": "2099-01-01",
            "owner": "platform-governance",
            "reason": reason,
            "violations": [{"code": "functional_hot_file_growth", "path": "app/billing.py"}],
        }
    ) + "\n"


def _python_assignments(count: int) -> str:
    return "".join(f"VALUE_{index} = {index}\n" for index in range(count))


def _fake_corepack_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    if os.name == "nt":
        (fake_bin / "corepack.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        corepack = fake_bin / "corepack"
        corepack.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        corepack.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    return environment


def _readiness_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pre_push_readiness_cleanup_test", READINESS_TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _CleanupRunner:
    def __init__(self, module: ModuleType, *, remove_returncode: int = 0) -> None:
        self.module = module
        self.remove_returncode = remove_returncode
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        del cwd, env
        self.commands.append(command)
        if command[:3] == ("git", "worktree", "remove"):
            return self.module._CommandResult(self.remove_returncode, "", "remove failed")
        return self.module._CommandResult(0, "", "")


def test_worktree_cleanup_records_successful_remove_and_absent_registration(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    base = temporary_root / "base"
    head.mkdir(parents=True)
    base.mkdir()
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True), ("base", base, True)))

    assert failure is None
    cleanup = result["stages"][-1]
    assert cleanup["name"] == "worktree_cleanup"
    assert cleanup["status"] == "pass"
    assert all(record["remove_returncode"] == 0 for record in cleanup["worktrees"])
    assert all(record["registered_after"] is False for record in cleanup["worktrees"])
    assert temporary_root.exists() is False


def test_cleanup_only_failure_is_an_infrastructure_failure(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module, remove_returncode=1)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True),))

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert result["stages"][-1]["status"] == "failed"


def test_primary_product_failure_is_preserved_when_cleanup_also_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _readiness_module()
    readiness = module.PrePushReadiness(tmp_path)
    temporary_root = tmp_path / "temporary"
    primary = module.ReadinessError("product_test_failure", "pytest_failed", "deterministic test failed")
    cleanup = module.ReadinessError("infrastructure_failure", "worktree_cleanup_failed", "cleanup failed")

    monkeypatch.setattr(readiness, "_assert_repository", lambda: None)
    monkeypatch.setattr(readiness, "_resolve_full_commit", lambda value, label: value)
    monkeypatch.setattr(readiness, "_assert_accepted_authority", lambda authority: None)
    monkeypatch.setattr(readiness, "_assert_authority_provenance", lambda authority: None)
    monkeypatch.setattr(readiness, "_assert_ancestor", lambda base, head: None)
    monkeypatch.setattr(readiness, "_create_temporary_root", lambda: temporary_root)
    monkeypatch.setattr(readiness, "_add_worktree", lambda path, commit: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(readiness, "_run_diff_check", lambda result, base, head: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(readiness, "_cleanup_worktrees", lambda result, root, worktrees: cleanup)

    with pytest.raises(module.ReadinessError) as raised:
        readiness.check("a" * 40, "b" * 40, "c" * 40)

    assert raised.value is primary
    assert raised.value.category == "product_test_failure"
    assert raised.value.cleanup_failure is cleanup


def test_authority_worktree_never_executes_a_candidate_tool_replacement(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    marker = tmp_path / "candidate-tool-executed.txt"
    _write(
        repo,
        "tools/pre_push_readiness.py",
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "raise RuntimeError('candidate tool executed')\n",
    )
    _write(repo, "tests/test_pre_push_readiness.py", "def test_pre_push_readiness():\n    assert True\n")
    head = _commit(repo, "replace candidate readiness tool")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    assert marker.exists() is False
    assert payload["authority_ref"] == base
    assert payload["authority"]["status"] == "verified"


def test_candidate_authority_governance_tamper_cannot_change_the_sealed_result(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(
        repo,
        "tests/test_authority_tamper.py",
        "import subprocess\n"
        "from pathlib import Path\n\n\n"
        "def test_tamper_authority_governance():\n"
        "    worktrees = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], text=True)\n"
        "    authority = next(\n"
        "        Path(line.removeprefix('worktree '))\n"
        "        for line in worktrees.splitlines()\n"
        "        if line.startswith('worktree ') and Path(line.removeprefix('worktree ')).name == 'authority'\n"
        "    )\n"
        "    (authority / 'tools' / 'code_governance.py').write_text(\n"
        "        \"raise RuntimeError('candidate changed authority governance')\\n\", encoding='utf-8'\n"
        "    )\n",
    )
    head = _commit(repo, "tamper authority governance from candidate test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "authority_post_candidate_integrity_mismatch"
    governance_index = next(index for index, stage in enumerate(payload["stages"]) if stage["name"] == "governance")
    tests_index = next(index for index, stage in enumerate(payload["stages"]) if stage["name"] == "responsibility_tests")
    assert governance_index < tests_index
    governance_stage = payload["stages"][governance_index]
    assert governance_stage["status"] == "pass"
    assert Path(governance_stage["command"][2]).name == "authority-governance.py"
    assert next(stage for stage in payload["stages"] if stage["name"] == "authority_integrity")["status"] == "failed"


def test_frontend_typescript_change_runs_the_repository_native_frontend_suite(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write(repo, "frontend/web/package.json", "{\"scripts\": {\"ci:verify\": \"true\"}}\n")
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    _write(repo, "frontend/web/src/App.test.tsx", "export const appTest = true;\n")
    head = _commit(repo, "frontend responsibility")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path))
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    frontend_stage = next(stage for stage in payload["stages"] if stage["name"] == "frontend_responsibility")
    assert frontend_stage["command"] == ["corepack.cmd" if os.name == "nt" else "corepack", "pnpm", "run", "ci:verify"]


def test_shared_test_fixture_requires_an_explicit_bounded_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/conftest.py", "VALUE = True\n")
    head = _commit(repo, "shared test fixture")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "shared_test_suite_required"
    assert payload["failure"]["path"] == "tests/conftest.py"


def test_shared_test_fixture_runs_the_explicit_bounded_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/conftest.py", "VALUE = True\n")
    _write(repo, "tests/test_shared_fixture.py", "def test_shared_fixture():\n    assert True\n")
    head = _commit(repo, "shared fixture with suite")

    result = _check(repo, base, head, shared_test_suites=("tests/test_shared_fixture.py",))
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["tests"] == ["tests/test_shared_fixture.py"]


def test_shared_suite_requires_a_changed_shared_fixture(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/test_explicit_suite.py", "def test_explicit_suite():\n    assert True\n")
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "unrelated suite flag")

    result = _check(repo, base, head, shared_test_suites=("tests/test_explicit_suite.py",))
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "unexpected_shared_test_suite"


def test_unowned_production_change_remains_external_with_an_unrelated_suite(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(repo, "unowned-policy.json", "{\"enabled\": true}\n")
    _write(repo, "tests/test_explicit_suite.py", "def test_explicit_suite():\n    assert True\n")
    head = _commit(repo, "unowned path with unrelated suite")

    result = _check(repo, base, head, shared_test_suites=("tests/test_explicit_suite.py",))
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == "unowned-policy.json"


@pytest.mark.parametrize("existing_exception", (False, True), ids=("added", "modified"))
def test_changed_code_governance_exception_runs_its_exact_bounded_suite(
    readiness_repo: tuple[Path, str],
    existing_exception: bool,
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "app/billing.py", _python_assignments(3_001))
    if existing_exception:
        _write(repo, ".code-governance-exception.json", _governance_exception(reason="initial exception"))
    base = _commit(repo, "governance exception baseline")
    _write(repo, "app/billing.py", _python_assignments(3_001) + "NEW_VALUE = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    _write(repo, ".code-governance-exception.json", _governance_exception(reason="updated exception"))
    head = _commit(repo, "change governance exception")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "pass"
    assert responsibility_stage["tests"] == ["tests/test_billing.py", "tests/test_code_governance.py"]


def test_deleted_code_governance_exception_follows_the_deleted_path_policy(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _authority = readiness_repo
    _write(repo, ".code-governance-exception.json", _governance_exception(reason="initial exception"))
    base = _commit(repo, "governance exception baseline")
    (repo / ".code-governance-exception.json").unlink()
    head = _commit(repo, "delete governance exception")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "not_applicable"
    assert responsibility_stage["tests"] == []


def test_deleted_test_file_is_not_sent_to_pytest(readiness_repo: tuple[Path, str]) -> None:
    repo, authority = readiness_repo
    _write(repo, "tests/test_deleted.py", "def test_deleted():\n    assert False\n")
    base = _commit(repo, "test scheduled for deletion")
    (repo / "tests" / "test_deleted.py").unlink()
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "delete stale test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert "tests/test_deleted.py" not in responsibility_stage["tests"]
    assert authority == payload["authority_ref"]


def test_unclassifiable_production_change_requires_external_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "config/policy.json", "{\"enabled\": true}\n")
    head = _commit(repo, "unclassifiable production change")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == "config/policy.json"


def test_mixed_backend_and_frontend_changes_run_both_responsibility_suites(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write(repo, "app/invoice.py", "TOTAL = 1\n")
    _write(repo, "tests/test_invoice.py", "def test_invoice():\n    assert True\n")
    _write(repo, "frontend/web/package.json", "{\"scripts\": {\"ci:verify\": \"true\"}}\n")
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    _write(repo, "frontend/web/src/App.test.tsx", "export const appTest = true;\n")
    head = _commit(repo, "mixed responsibilities")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path))
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    stages = {stage["name"]: stage for stage in payload["stages"]}
    assert stages["governance"]["status"] == "failed"
    assert "responsibility_tests" not in stages
    assert "frontend_responsibility" not in stages


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


def test_malformed_authority_ref_is_rejected_before_candidate_checks(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo

    result = _check(repo, base, base, authority_ref="origin/main")
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_ref"
    assert payload["failure"]["message"] == "authority_ref must be a full 40-hex commit id"
    assert payload["stages"] == []


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
    repo, _authority = readiness_repo
    _write(
        repo,
        "app/billing.py",
        "\n".join(f"VALUE_{index} = {index}" for index in range(3_001)) + "\nRATE = 2\n",
    )
    base = _commit(repo, "large billing module")
    _write(repo, "app/billing.py", (repo / "app" / "billing.py").read_text(encoding="utf-8") + "EXTRA = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    head = _commit(repo, "grow functional hot file")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "functional_hot_file_growth"
    assert payload["failure"]["path"] == "app/billing.py"


def test_governance_ruff_ignores_a_head_root_shadow_module(readiness_repo: tuple[Path, str]) -> None:
    repo, _authority = readiness_repo
    _write(repo, "ruff.py", "raise RuntimeError('head ruff module was imported')\n")
    base = _commit(repo, "shadow ruff in candidate base")
    _write(repo, "tests/test_ruff.py", "def test_ruff():\n    assert True\n")
    head = _commit(repo, "shadow ruff module")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    governance_stage = next(stage for stage in payload["stages"] if stage["name"] == "governance")
    assert governance_stage["ruff"]["status"] == "pass"


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
        "authority_integrity",
        "compileall",
        "diff_check",
        "governance",
        "responsibility_tests",
        "worktree_cleanup",
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

    assert "tools/pre_push_readiness.py\") check" in workflow
    assert "python tools/pre_push_readiness.py" not in workflow
    assert "--authority-ref $authority" in workflow
    assert "detached temporary worktree" in normalized
    assert "never execute" in normalized
    assert "one-time bootstrap boundary" in normalized
    assert "cannot run this normal gate or certify itself" in normalized
    assert "PYTHONSAFEPATH=1" in workflow
    assert "before the first push" in normalized
    assert "after every ordinary merge-up" in normalized
    assert "corepack pnpm run ci:verify" in workflow
    assert "--shared-test-suite" in workflow
    assert "or modified `.code-governance-exception.json`" in workflow
    assert "`tests/test_code_governance.py` suite" in workflow
    assert "deletion follows the deleted-path" in normalized
    assert "every other unowned root configuration or json path remains" in normalized
    assert "before candidate compile, pytest," in normalized
    assert "frontend, or candidate configuration executes" in normalized
    assert "immutable authority git object" in normalized
    assert "cannot discharge an" in normalized
    assert "unowned production path" in normalized
    assert "stale_base" in workflow
    assert "product_test_failure" in workflow
    assert "governance_violation" in workflow
    assert "infrastructure_failure" in workflow
    assert "external_check" in workflow
    assert "positive infrastructure evidence on the same SHA" in workflow

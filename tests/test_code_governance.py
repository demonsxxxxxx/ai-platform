from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "code_governance.py"
SPEC = importlib.util.spec_from_file_location("code_governance_under_test", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
code_governance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = code_governance
SPEC.loader.exec_module(code_governance)


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
def governance_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "governance@example.test")
    _git(repo, "config", "user.name", "Governance Test")
    _write(repo, "README.md", "fixture\n")
    base = _commit(repo, "base")
    return repo, base


class _RuffPassingRunner(code_governance._CommandRunner):
    def run(self, command: list[str], *, cwd: Path) -> Any:
        if len(command) >= 4 and command[1:4] == ["-m", "ruff", "check"]:
            return code_governance._CommandResult(0, "All checks passed!\n", "")
        return super().run(command, cwd=cwd)


def _evaluate(
    repo: Path,
    base: str,
    head: str,
    *,
    ruff_passes: bool = True,
) -> Any:
    runner = _RuffPassingRunner() if ruff_passes else code_governance._CommandRunner()
    if ruff_passes:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(code_governance.importlib.util, "find_spec", lambda name: object())
            return code_governance.CodeGovernanceEvaluator(
                repo,
                runner=runner,
                today=date(2026, 7, 25),
            ).evaluate(base, head)
    return code_governance.CodeGovernanceEvaluator(
        repo,
        runner=runner,
        today=date(2026, 7, 25),
    ).evaluate(base, head)


def _codes(evaluation: Any) -> set[str]:
    return {item.code for item in evaluation.violations}


def _payload(evaluation: Any) -> dict[str, Any]:
    return evaluation.as_dict()


def _python_lines(count: int, *, prefix: str = "value") -> str:
    return "".join(f"{prefix}_{index} = {index}\n" for index in range(count))


def test_small_non_python_change_passes(governance_repo: tuple[Path, str]) -> None:
    repo, base = governance_repo
    _write(repo, "frontend/web/src/session.ts", "export const session = true;\n")
    _write(repo, "frontend/web/src/session.test.ts", "export const sessionTest = true;\n")
    head = _commit(repo, "small change")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.exit_code == 0
    assert evaluation.status == "pass"
    assert evaluation.mode == "behavior_fix"
    assert evaluation.ruff["status"] == "not_applicable"
    assert _payload(evaluation)["reserved_gates"] == {
        "error_taxonomy": "phase_2b_not_enforced",
        "typed_payloads": "phase_2b_not_enforced",
    }


def test_size_and_subsystem_violations_are_reported(governance_repo: tuple[Path, str]) -> None:
    repo, base = governance_repo
    for index in range(13):
        _write(repo, f"app/domain_{index}.json", "{\"enabled\": true}\n")
    _write(repo, "deploy/ai-platform/policy.json", "{\"enabled\": true}\n")
    _write(repo, "config/bulk.json", "".join("item\n" for _ in range(801)))
    head = _commit(repo, "oversized")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.exit_code == 2
    assert {
        "production_file_count",
        "production_net_loc",
        "production_subsystem_count",
    } <= _codes(evaluation)


def test_pure_rename_is_separate_from_behavior_fix_and_delete_is_safe(
    governance_repo: tuple[Path, str],
) -> None:
    repo, initial = governance_repo
    _write(repo, "app/original.json", "{\"stable\": true}\n")
    source = _commit(repo, "source")
    _git(repo, "mv", "app/original.json", "app/renamed.json")
    renamed = _commit(repo, "rename")

    rename_evaluation = _evaluate(repo, source, renamed)

    assert rename_evaluation.status == "pass"
    assert rename_evaluation.mode == "move_only"
    assert rename_evaluation.metrics["behavior_production_files"] == 0
    assert rename_evaluation.metrics["move_only_production_files"] == 1

    _git(repo, "rm", "app/renamed.json")
    deleted = _commit(repo, "delete")
    delete_evaluation = _evaluate(repo, renamed, deleted)

    assert delete_evaluation.status == "pass"
    assert delete_evaluation.metrics["production_net_loc"] < 0
    assert initial != source


def test_test_to_production_rename_is_behavior_change_with_soft_loc_metrics(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "tests/test_billing_rules.py", "RATE = 2\n")
    base = _commit(repo, "test source")
    (repo / "app").mkdir()
    _git(repo, "mv", "tests/test_billing_rules.py", "app/billing_rules.py")
    head = _commit(repo, "promote test code")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.exit_code == 0
    assert evaluation.mode == "behavior_fix"
    assert evaluation.metrics["behavior_production_files"] == 1
    assert evaluation.metrics["move_only_production_files"] == 0
    assert evaluation.metrics["production_net_loc"] == 1
    assert evaluation.metrics["production_added_loc"] == 1
    assert evaluation.metrics["test_net_loc"] == -1
    assert evaluation.metrics["test_to_production_added_loc_ratio"] == 0.0
    assert evaluation.metrics["production_subsystems"] == ["app"]
    assert _payload(evaluation)["changes"][0]["role"] == "behavior_production"


def test_production_to_test_rename_counts_production_exit_and_test_entry(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/billing_rules.py", "RATE = 2\n")
    base = _commit(repo, "production source")
    (repo / "tests").mkdir()
    _git(repo, "mv", "app/billing_rules.py", "tests/test_billing_rules.py")
    head = _commit(repo, "demote production code")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.exit_code == 0
    assert evaluation.mode == "behavior_fix"
    assert evaluation.metrics["behavior_production_files"] == 1
    assert evaluation.metrics["move_only_production_files"] == 0
    assert evaluation.metrics["production_net_loc"] == -1
    assert evaluation.metrics["test_added_loc"] == 1
    assert evaluation.metrics["test_net_loc"] == 1
    assert evaluation.metrics["test_to_production_added_loc_ratio"] is None
    assert evaluation.metrics["test_loc_review_explanation_recommended"] is True
    assert evaluation.metrics["production_subsystems"] == ["app"]
    assert _payload(evaluation)["changes"][0]["role"] == "behavior_production"


def test_hot_functional_file_cannot_grow(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/hot.py", _python_lines(3001))
    _write(repo, "tests/test_hot.py", "def test_hot():\n    assert True\n")
    base = _commit(repo, "large base")
    with (repo / "app" / "hot.py").open("a", encoding="utf-8") as handle:
        handle.write("extra = True\n")
    with (repo / "tests" / "test_hot.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef test_extra():\n    assert True\n")
    head = _commit(repo, "grow functional hot file")

    evaluation = _evaluate(repo, base, head)

    assert "functional_hot_file_growth" in _codes(evaluation)
    assert "hot_file_growth" not in _codes(evaluation)


def test_hot_production_file_growth_is_limited(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "config/hot.json", "".join(f"line-{index}\n" for index in range(1501)))
    base = _commit(repo, "hot base")
    with (repo / "config" / "hot.json").open("a", encoding="utf-8") as handle:
        handle.write("".join(f"extra-{index}\n" for index in range(101)))
    head = _commit(repo, "grow hot file")

    evaluation = _evaluate(repo, base, head)

    assert _codes(evaluation) == {"hot_file_growth"}


def test_hot_production_file_growth_at_limit_passes(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "config/hot.json", "".join(f"line-{index}\n" for index in range(1501)))
    base = _commit(repo, "hot limit base")
    with (repo / "config" / "hot.json").open("a", encoding="utf-8") as handle:
        handle.write("".join(f"extra-{index}\n" for index in range(100)))
    head = _commit(repo, "grow hot file at limit")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"


def test_production_net_loc_boundary_is_exclusive(governance_repo: tuple[Path, str]) -> None:
    repo, base = governance_repo
    _write(repo, "config/bulk.json", "".join(f"line-{index}\n" for index in range(800)))
    head = _commit(repo, "net loc boundary")

    evaluation = _evaluate(repo, base, head)

    assert "production_net_loc" in _codes(evaluation)


def test_test_hot_file_growth_is_limited(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "tests/test_large.py", _python_lines(2501, prefix="test_value"))
    base = _commit(repo, "large test base")
    with (repo / "tests" / "test_large.py").open("a", encoding="utf-8") as handle:
        handle.write(_python_lines(101, prefix="new_test_value"))
    head = _commit(repo, "grow large test")

    evaluation = _evaluate(repo, base, head)

    assert _codes(evaluation) == {"test_hot_file_growth"}


def test_test_hot_file_growth_at_limit_passes(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "tests/test_large.py", _python_lines(2501, prefix="test_value"))
    base = _commit(repo, "large test limit base")
    with (repo / "tests" / "test_large.py").open("a", encoding="utf-8") as handle:
        handle.write(_python_lines(100, prefix="new_test_value"))
    head = _commit(repo, "grow large test at limit")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"


def test_behavior_change_reports_test_loc_ratio_as_a_soft_review_signal(
    governance_repo: tuple[Path, str],
) -> None:
    repo, base = governance_repo
    _write(repo, "app/billing_rules.py", "RATE = 2\n")
    _write(repo, "tests/test_cross_layer.py", "def test_behavior():\n    result = 2\n    assert result == 2\n")
    head = _commit(repo, "cross-layer behavior regression")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"
    assert evaluation.metrics["production_added_loc"] == 1
    assert evaluation.metrics["test_added_loc"] == 3
    assert evaluation.metrics["test_to_production_added_loc_ratio"] == 3.0
    assert evaluation.metrics["test_loc_review_explanation_recommended"] is True
    assert _payload(evaluation)["policy"]["test_loc_review"]["enforcement"] == "soft"


def test_invalid_exception_schema_is_an_error(governance_repo: tuple[Path, str]) -> None:
    repo, base = governance_repo
    _write(repo, "app/billing_rules.py", "RATE = 2\n")
    _write(
        repo,
        code_governance.EXCEPTION_PATH,
        json.dumps(
            {
                "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
                "expires_on": "2026-08-01",
                "owner": "platform",
                "reason": "bounded migration",
                "violations": [{"code": "production_net_loc", "path": None}],
                "unexpected": True,
            }
        ),
    )
    head = _commit(repo, "invalid exception")

    with pytest.raises(code_governance.GovernanceError, match="keys must be exactly") as caught:
        _evaluate(repo, base, head)

    assert caught.value.code == "invalid_exception"


def test_valid_exact_exception_exempts_only_requested_violation(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/billing_rules.py", _python_lines(3001))
    base = _commit(repo, "hot functional base")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\n")
    _write(
        repo,
        code_governance.EXCEPTION_PATH,
        json.dumps(
            {
                "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
                "expires_on": "2026-08-01",
                "owner": "platform-governance",
                "reason": "temporary hot-file migration",
                "violations": [{"code": "functional_hot_file_growth", "path": "app/billing_rules.py"}],
            }
        ),
    )
    head = _commit(repo, "valid exception")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"
    assert evaluation.exit_code == 0
    assert [item.code for item in evaluation.exempted_violations] == ["functional_hot_file_growth"]
    assert evaluation.exception["status"] == "applied"


def test_missing_ruff_fails_closed_and_command_is_deterministic(
    governance_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = governance_repo
    _write(repo, "app/zeta.py", "ZETA = True\n")
    _write(repo, "app/alpha.py", "ALPHA = True\n")
    _write(repo, "tests/test_alpha.py", "def test_alpha():\n    assert True\n")
    _write(repo, "tests/test_zeta.py", "def test_zeta():\n    assert True\n")
    head = _commit(repo, "python change")
    monkeypatch.setattr(code_governance.importlib.util, "find_spec", lambda name: None)

    evaluation = code_governance.CodeGovernanceEvaluator(
        repo,
        today=date(2026, 7, 25),
    ).evaluate(base, head)

    assert evaluation.status == "violation"
    assert "ruff_unavailable" in _codes(evaluation)
    assert evaluation.ruff["command"] == [
        "python",
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--",
        "app/alpha.py",
        "app/zeta.py",
        "tests/test_alpha.py",
        "tests/test_zeta.py",
    ]


def test_ruff_failure_is_reported(governance_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base = governance_repo
    _write(repo, "app/alpha.py", "ALPHA = True\n")
    _write(repo, "tests/test_alpha.py", "def test_alpha():\n    assert True\n")
    head = _commit(repo, "ruff failure")
    monkeypatch.setattr(code_governance.importlib.util, "find_spec", lambda name: object())

    class _RuffFailingRunner(code_governance._CommandRunner):
        def run(self, command: list[str], *, cwd: Path) -> Any:
            if len(command) >= 4 and command[1:4] == ["-m", "ruff", "check"]:
                return code_governance._CommandResult(1, "F401 unused import\n", "")
            return super().run(command, cwd=cwd)

    evaluation = code_governance.CodeGovernanceEvaluator(
        repo,
        runner=_RuffFailingRunner(),
        today=date(2026, 7, 25),
    ).evaluate(base, head)

    assert evaluation.ruff["status"] == "failed"
    assert "ruff_failed" in _codes(evaluation)


@pytest.mark.parametrize(
    ("config_path", "config"),
    [
        ("pyproject.toml", "[tool.ruff.lint]\nignore = [\"F401\"]\n"),
        ("ruff.toml", "[lint]\nignore = [\"F401\"]\n"),
        (".ruff.toml", "[lint]\nignore = [\"F401\"]\n"),
    ],
)
def test_ruff_isolated_ignores_head_config_suppression(
    governance_repo: tuple[Path, str], config_path: str, config: str
) -> None:
    repo, base = governance_repo
    _write(repo, "tests/test_alpha.py", "import os\n")
    _write(repo, config_path, config)
    head = _commit(repo, f"suppress F401 through {config_path}")

    configured = _run(
        repo,
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--",
        "tests/test_alpha.py",
        check=False,
    )
    evaluation = code_governance.CodeGovernanceEvaluator(repo, today=date(2026, 7, 25)).evaluate(base, head)

    assert configured.returncode == 0
    assert "ruff_failed" in _codes(evaluation)
    assert evaluation.ruff["command"] == [
        "python",
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--",
        "tests/test_alpha.py",
    ]


def test_json_is_deterministic(governance_repo: tuple[Path, str]) -> None:
    repo, base = governance_repo
    _write(repo, "frontend/web/src/zeta.ts", "export const zeta = true;\n")
    _write(repo, "frontend/web/src/alpha.ts", "export const alpha = true;\n")
    _write(repo, "frontend/web/src/alpha.test.ts", "export const alphaTest = true;\n")
    _write(repo, "frontend/web/src/zeta.test.ts", "export const zetaTest = true;\n")
    head = _commit(repo, "deterministic")

    first = json.dumps(_payload(_evaluate(repo, base, head)), ensure_ascii=False, indent=2, sort_keys=True)
    second = json.dumps(_payload(_evaluate(repo, base, head)), ensure_ascii=False, indent=2, sort_keys=True)

    assert first == second
    assert [item["path"] for item in json.loads(first)["changes"]] == sorted(
        item["path"] for item in json.loads(first)["changes"]
    )


def test_cli_exit_codes_are_zero_two_and_three(
    governance_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = governance_repo
    _write(repo, "README.md", "fixture updated\n")
    passing_head = _commit(repo, "docs only")

    monkeypatch.chdir(repo)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert code_governance.main(
            ["check", "--base-ref", base, "--head-ref", passing_head, "--format", "json"]
        ) == 0
    assert json.loads(stdout.getvalue())["status"] == "pass"

    _write(repo, "app/new_module.py", "VALUE = 1\n")
    violating_head = _commit(repo, "violation")
    monkeypatch.setattr(code_governance.importlib.util, "find_spec", lambda name: None)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert code_governance.main(
            ["check", "--base-ref", passing_head, "--head-ref", violating_head, "--format", "json"]
        ) == 2
    assert json.loads(stdout.getvalue())["status"] == "violation"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert code_governance.main(
            ["check", "--base-ref", "main", "--head-ref", violating_head, "--format", "json"]
        ) == 3
    error = json.loads(stdout.getvalue())
    assert error["status"] == "error"
    assert error["error"]["code"] == "invalid_ref"

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


def _run_bytes(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(arguments),
        cwd=repo,
        check=True,
        capture_output=True,
        input=input_bytes,
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


def _advisory_codes(evaluation: Any) -> set[str]:
    return {item.code for item in evaluation.advisories}


def _payload(evaluation: Any) -> dict[str, Any]:
    return evaluation.as_dict()


def _python_lines(count: int, *, prefix: str = "value") -> str:
    return "".join(f"{prefix}_{index} = {index}\n" for index in range(count))


def _candidate_binding(repo: Path, base: str, scope_head: str) -> dict[str, str]:
    reader = code_governance._GitChangeReader(repo, code_governance._CommandRunner())
    return {
        "base_ref": base,
        "scope_sha256": reader.exception_scope_sha256(base, scope_head),
    }


def _review_event(head: str, *, body: str) -> str:
    return json.dumps({"pull_request": {"body": body, "head": {"sha": head}}})


def _review_body(head: str) -> str:
    record = {
        "schema_version": "ai-platform.review-findings.v1",
        "review_subject": {
            "head_sha": head,
            "scope": "exact candidate governance review",
        },
        "no_material_findings": {
            "reviewer_handle": "@reviewer",
            "reviewer_role": "independent test reviewer",
            "evidence_id": "test-review-evidence",
        },
        "findings": [],
    }
    return "<!-- ai-platform.review-findings.v1 -->\n```json\n" + json.dumps(record) + "\n```\n"


def _write_fake_review_validator(
    path: Path,
    *,
    returncode: int,
    stderr: str = "",
) -> Path:
    path.write_text(
        "import sys\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    return path


def test_pull_request_review_record_runs_from_code_governance_authority(
    governance_repo: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, head = governance_repo
    event_path = tmp_path / "event.json"
    event_path.write_text(_review_event(head, body=_review_body(head)), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GOVERNANCE_HEAD_REF", head)
    monkeypatch.setenv("GOVERNANCE_PR_NUMBER", "1200")

    validator_path = _write_fake_review_validator(
        tmp_path / "validate_pr_review_record.py",
        returncode=0,
    )
    code_governance._validate_pull_request_review_record(
        head,
        validator_path=validator_path,
    )


def test_pull_request_review_record_failure_is_closed_and_redacted(
    governance_repo: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, head = governance_repo
    event_path = tmp_path / "event.json"
    event_path.write_text(_review_event(head, body="missing structured record"), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GOVERNANCE_HEAD_REF", head)
    monkeypatch.setenv("GOVERNANCE_PR_NUMBER", "1200")

    validator_path = _write_fake_review_validator(
        tmp_path / "validate_pr_review_record.py",
        returncode=2,
        stderr="review_record_marker_missing: $: missing or invalid review record\n",
    )
    with pytest.raises(code_governance.GovernanceError) as captured:
        code_governance._validate_pull_request_review_record(
            head,
            validator_path=validator_path,
        )

    assert captured.value.code == "review_record_invalid"
    assert "review_record_marker_missing" in str(captured.value)
    assert "missing structured record" not in str(captured.value)


def test_review_record_requires_event_path_only_for_pull_requests(
    governance_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, head = governance_repo
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GOVERNANCE_HEAD_REF", head)
    monkeypatch.setenv("GOVERNANCE_PR_NUMBER", "1200")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    with pytest.raises(code_governance.GovernanceError) as captured:
        code_governance._validate_pull_request_review_record(head)
    assert captured.value.code == "review_record_event_missing"

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    code_governance._validate_pull_request_review_record(head)


def test_ambient_pull_request_environment_does_not_enable_authority_validation(
    governance_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, head = governance_repo
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.delenv("GOVERNANCE_HEAD_REF", raising=False)
    monkeypatch.delenv("GOVERNANCE_PR_NUMBER", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    code_governance._validate_pull_request_review_record(head)


@pytest.mark.parametrize(
    ("governance_head", "governance_pr_number"),
    [
        ("0" * 40, "1200"),
        (None, "1200"),
        ("HEAD", "1200"),
        ("exact", None),
        ("exact", "0"),
    ],
)
def test_incomplete_or_mismatched_authority_context_fails_closed(
    governance_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    governance_head: str | None,
    governance_pr_number: str | None,
) -> None:
    _repo, head = governance_repo
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    if governance_head is None:
        monkeypatch.delenv("GOVERNANCE_HEAD_REF", raising=False)
    else:
        monkeypatch.setenv(
            "GOVERNANCE_HEAD_REF",
            head if governance_head == "exact" else governance_head,
        )
    if governance_pr_number is None:
        monkeypatch.delenv("GOVERNANCE_PR_NUMBER", raising=False)
    else:
        monkeypatch.setenv("GOVERNANCE_PR_NUMBER", governance_pr_number)

    with pytest.raises(code_governance.GovernanceError) as captured:
        code_governance._validate_pull_request_review_record(head)

    assert captured.value.code == "review_record_authority_context_invalid"


def _commit_raw_path(repo: Path, base: str, path: bytes) -> str:
    blob = _run_bytes(repo, "git", "hash-object", "-w", "--stdin", input_bytes=b"same content\n").stdout.strip()
    tree_input = b"100644 blob " + blob + b"\t" + path + b"\0"
    tree = _run_bytes(repo, "git", "mktree", "-z", input_bytes=tree_input).stdout.decode("ascii").strip()
    return _git(repo, "commit-tree", tree, "-p", base, "-m", "raw path candidate")


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


def test_v1_report_keeps_legacy_fields_and_adds_advisories(
    governance_repo: tuple[Path, str],
) -> None:
    repo, base = governance_repo
    _write(repo, "docs/governance.md", "additive report\n")
    head = _commit(repo, "additive report")

    payload = _payload(_evaluate(repo, base, head))

    assert {
        "base_ref",
        "changes",
        "exception",
        "exempted_violations",
        "head_ref",
        "metrics",
        "mode",
        "policy",
        "reserved_gates",
        "ruff",
        "schema_version",
        "status",
        "violations",
    } <= payload.keys()
    assert payload["schema_version"] == "ai-platform.code-governance-report.v1"
    assert payload["advisories"] == []


def test_size_signals_are_advisory_across_multiple_responsibilities(
    governance_repo: tuple[Path, str],
) -> None:
    repo, base = governance_repo
    for index in range(13):
        _write(repo, f"app/domain_{index}.json", "{\"enabled\": true}\n")
    _write(repo, "deploy/ai-platform/policy.json", "{\"enabled\": true}\n")
    _write(repo, "config/bulk.json", "".join("item\n" for _ in range(801)))
    head = _commit(repo, "oversized")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.exit_code == 0
    assert evaluation.status == "pass"
    assert _codes(evaluation) == set()
    assert {"production_file_count", "production_net_loc"} <= _advisory_codes(evaluation)
    assert evaluation.metrics["production_subsystem_count"] == 3
    assert evaluation.metrics["production_subsystems"] == ["app", "config", "deploy/ai-platform"]
    assert [item["name"] for item in evaluation.metrics["changed_responsibilities"]] == [
        "app",
        "config",
        "deploy/ai-platform",
    ]
    assert _payload(evaluation)["policy"]["size_and_hot_file_gates"] == "advisory"


def test_multiple_production_subsystems_are_reported_without_blocking(
    governance_repo: tuple[Path, str],
) -> None:
    repo, base = governance_repo
    _write(repo, "app/settings.py", "ENABLED = True\n")
    _write(repo, "deploy/ai-platform/policy.json", "{\"enabled\": true}\n")
    _write(repo, "frontend/web/src/services/client.ts", "export const enabled = true;\n")
    head = _commit(repo, "coherent cross-subsystem change")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"
    assert evaluation.exit_code == 0
    assert evaluation.metrics["production_subsystem_count"] == 3
    assert evaluation.metrics["production_subsystems"] == [
        "app",
        "deploy/ai-platform",
        "frontend/web/src/services",
    ]
    assert "production_subsystem_count_max_exclusive" not in _payload(evaluation)["policy"]
    assert evaluation.metrics["changed_responsibilities"] == [
        {"name": "app", "paths": ["app/settings.py"]},
        {"name": "deploy/ai-platform", "paths": ["deploy/ai-platform/policy.json"]},
        {
            "name": "frontend/web/src/services",
            "paths": ["frontend/web/src/services/client.ts"],
        },
    ]
    rendered = code_governance._render_text(evaluation)
    assert "production subsystem count: 3" in rendered
    assert "production subsystems: app, deploy/ai-platform, frontend/web/src/services" in rendered
    assert "changed responsibilities:" in rendered
    assert "- app: app/settings.py" in rendered


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


def test_hot_functional_file_growth_is_advisory(governance_repo: tuple[Path, str]) -> None:
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

    assert evaluation.status == "pass"
    assert "functional_hot_file_growth" in _advisory_codes(evaluation)
    assert "hot_file_growth" not in _advisory_codes(evaluation)
    assert evaluation.metrics["hot_files"] == [
        {
            "kind": "functional_production",
            "net_loc": 1,
            "path": "app/hot.py",
            "peak_lines": 3002,
        }
    ]


def test_hot_production_file_growth_is_limited(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "config/hot.json", "".join(f"line-{index}\n" for index in range(1501)))
    base = _commit(repo, "hot base")
    with (repo / "config" / "hot.json").open("a", encoding="utf-8") as handle:
        handle.write("".join(f"extra-{index}\n" for index in range(101)))
    head = _commit(repo, "grow hot file")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"
    assert _advisory_codes(evaluation) == {"hot_file_growth"}


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

    assert evaluation.status == "pass"
    assert "production_net_loc" in _advisory_codes(evaluation)


def test_test_hot_file_growth_is_limited(governance_repo: tuple[Path, str]) -> None:
    repo, _initial = governance_repo
    _write(repo, "tests/test_large.py", _python_lines(2501, prefix="test_value"))
    base = _commit(repo, "large test base")
    with (repo / "tests" / "test_large.py").open("a", encoding="utf-8") as handle:
        handle.write(_python_lines(101, prefix="new_test_value"))
    head = _commit(repo, "grow large test")

    evaluation = _evaluate(repo, base, head)

    assert evaluation.status == "pass"
    assert _advisory_codes(evaluation) == {"test_hot_file_growth", "test_loc_review"}


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
    assert evaluation.metrics["review_recommendations"] == ["explain_test_loc_mix"]
    assert "test_loc_review" in _advisory_codes(evaluation)
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
                "candidate": {"base_ref": "0" * 40, "scope_sha256": "0" * 64},
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


def test_valid_exact_exception_exempts_only_requested_hard_violation(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _base = governance_repo
    evaluator = code_governance.CodeGovernanceEvaluator(repo)
    violation = code_governance.Violation(
        "custom_hard_gate",
        "hard gate failed",
        path="app/billing_rules.py",
    )
    payload = {
        "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
        "candidate": {"base_ref": "0" * 40, "scope_sha256": "0" * 64},
        "expires_on": "2026-08-01",
        "owner": "platform-governance",
        "reason": "temporary hard-gate migration",
        "violations": [
            {"code": "custom_hard_gate", "path": "app/billing_rules.py"}
        ],
    }

    active, exempted, summary = evaluator._apply_exception([violation], [], payload)

    assert active == []
    assert exempted == [violation]
    assert summary["status"] == "applied"
    assert summary["acknowledged_advisories"] == []


def test_exact_legacy_size_exception_acknowledges_advisory_without_hiding_it(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/billing_rules.py", _python_lines(3001))
    base = _commit(repo, "hot functional base")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\n")
    scope_head = _commit(repo, "exception scope")
    _write(
        repo,
        code_governance.EXCEPTION_PATH,
        json.dumps(
            {
                "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
                "candidate": _candidate_binding(repo, base, scope_head),
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
    assert [item.code for item in evaluation.advisories] == ["functional_hot_file_growth"]
    assert evaluation.exempted_violations == ()
    assert evaluation.exception["status"] == "applied"
    assert [
        item["code"] for item in evaluation.exception["acknowledged_advisories"]
    ] == ["functional_hot_file_growth"]


def test_inherited_exception_cannot_authorize_a_later_candidate(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/billing_rules.py", _python_lines(3001))
    scope_head = _commit(repo, "exception scope")
    _write(
        repo,
        code_governance.EXCEPTION_PATH,
        json.dumps(
            {
                "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
                "candidate": _candidate_binding(repo, _initial, scope_head),
                "expires_on": "2026-08-01",
                "owner": "platform-governance",
                "reason": "candidate-bound hot-file migration",
                "violations": [{"code": "functional_hot_file_growth", "path": "app/billing_rules.py"}],
            }
        ),
    )
    base = _commit(repo, "exception-bearing base")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\n")
    head = _commit(repo, "later candidate inherits exception")

    with pytest.raises(code_governance.GovernanceError, match="candidate binding does not match") as caught:
        _evaluate(repo, base, head)

    assert caught.value.code == "invalid_exception"


def test_exception_cannot_authorize_scope_appended_under_the_same_base(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _initial = governance_repo
    _write(repo, "app/billing_rules.py", _python_lines(3001))
    base = _commit(repo, "hot functional base")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\n")
    scope_head = _commit(repo, "authorized scope")
    candidate = _candidate_binding(repo, base, scope_head)
    reader = code_governance._GitChangeReader(
        repo,
        code_governance._CommandRunner(),
    )
    code_governance._validate_exception_candidate(
        candidate,
        base_ref=base,
        scope_sha256=reader.exception_scope_sha256(base, scope_head),
    )

    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\nLATER = True\n")
    later_head = _commit(repo, "append later scope")

    with pytest.raises(code_governance.GovernanceError, match="candidate binding does not match") as caught:
        code_governance._validate_exception_candidate(
            candidate,
            base_ref=base,
            scope_sha256=reader.exception_scope_sha256(base, later_head),
        )

    assert caught.value.code == "invalid_exception"


def test_semantically_unchanged_exception_rewrite_cannot_renew_authority(
    governance_repo: tuple[Path, str],
) -> None:
    repo, initial = governance_repo
    _write(repo, "app/billing_rules.py", _python_lines(3001))
    base = _commit(repo, "hot functional base")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\n")
    scope_head = _commit(repo, "authorized scope")
    payload = {
        "schema_version": code_governance.EXCEPTION_SCHEMA_VERSION,
        "candidate": _candidate_binding(repo, base, scope_head),
        "expires_on": "2026-08-01",
        "owner": "platform-governance",
        "reason": "candidate-bound hot-file migration",
        "violations": [{"code": "functional_hot_file_growth", "path": "app/billing_rules.py"}],
    }
    _write(repo, code_governance.EXCEPTION_PATH, json.dumps(payload))
    inherited_base = _commit(repo, "authorize exact scope")
    _write(repo, "app/billing_rules.py", _python_lines(3001) + "EXTRA = True\nLATER = True\n")
    _write(repo, code_governance.EXCEPTION_PATH, json.dumps(payload, indent=2))
    later_head = _commit(repo, "rewrite inherited exception")

    with pytest.raises(code_governance.GovernanceError, match="candidate binding does not match") as caught:
        _evaluate(repo, inherited_base, later_head)

    assert initial != base
    assert caught.value.code == "invalid_exception"


def test_candidate_scope_hash_preserves_non_utf8_git_path_bytes(
    governance_repo: tuple[Path, str],
) -> None:
    repo, base = governance_repo
    first_head = _commit_raw_path(repo, base, b"raw-\xff.py")
    second_head = _commit_raw_path(repo, base, b"raw-\xfe.py")
    reader = code_governance._GitChangeReader(repo, code_governance._CommandRunner())

    assert reader.exception_scope_sha256(base, first_head) != reader.exception_scope_sha256(base, second_head)


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

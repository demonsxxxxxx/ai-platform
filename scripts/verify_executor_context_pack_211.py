"""Verify 211 executor context-pack source-probe evidence.

This verifier accepts only a redacted evidence payload that proves the worker
prompt consumed the bounded executor context pack without exposing private
storage identifiers, sandbox runtime paths, or executor-private content. It is
not a live worker-run acceptance verifier by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


EVIDENCE_SCHEMA_VERSION = "ai-platform.executor-context-pack-211.v1"
SOURCE_SCHEMA_VERSION = "ai-platform.executor-context-pack.v1"

REQUIRED_SOURCE_FUNCTIONS = [
    "app.repositories.get_context_snapshot_for_worker",
    "app.context_builder.executor_context_pack_from_snapshot",
    "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
    "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
    "app.worker._context_snapshot_ref_from_row",
]
REQUIRED_PROMPT_CHECKS = [
    "bounded_summary_present",
    "context_pack_version_present",
    "context_pack_generated_at_present",
    "raw_storage_identifiers_absent",
    "sandbox_runtime_paths_absent",
    "executor_private_content_absent",
    "long_term_memory_read_false",
]
REQUIRED_SCOPE_CHECKS = [
    "tenant_id_scoped",
    "workspace_id_scoped",
    "user_id_scoped",
    "session_id_scoped",
    "source_run_artifact_count_positive",
    "source_run_artifact_scope_verified",
]
REQUIRED_NON_EXPANSION_INVARIANTS = {
    "ordinary_user_multi_agent_allowed": False,
    "ordinary_user_high_risk_sandbox_allowed": False,
    "lightweight_office_tasks_start_sandbox_by_default": False,
    "long_term_cross_session_memory_enabled": False,
    "public_projection_only_for_ordinary_users": True,
}
REQUIRED_LIVE_RUN_CHECKS = [
    "run_row_loaded",
    "context_snapshot_id_present",
    "scoped_context_snapshot_loaded",
    "worker_context_ref_rebuilt_from_db_snapshot",
    "context_pack_schema_present",
]
REQUIRED_RUNTIME_EVIDENCE = [
    "live_worker_run_payload",
    "run_row_loaded",
    "context_snapshot_id_present",
    "scoped_context_snapshot_loaded",
    "worker_context_ref_rebuilt_from_db_snapshot",
    "prompt_includes_bounded_summary",
    "prompt_includes_context_pack_version",
    "prompt_includes_context_pack_generated_at",
    "raw_storage_identifiers_absent",
    "sandbox_runtime_paths_absent",
    "executor_private_content_absent",
    "long_term_memory_read_false",
    "source_run_artifact_scope_tenant_workspace_user_session",
    "source_run_artifact_count_positive",
    "fresh_generated_at",
    "source_functions_bound_to_current_runtime",
]
FORBIDDEN_PUBLIC_CONTEXT_INPUT_KEY_ALIASES = {
    "copiedfromrunid",
    "parentrunid",
    "runid",
    "runids",
    "sourcerunid",
    "sourcerunids",
}
SENSITIVE_PATTERNS = [
    re.compile(r"/home/[^\s\"']*", re.IGNORECASE),
    re.compile(r"/tmp/[^\s\"']*", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\[^\s\"']*"),
    re.compile(r"s3://[^\s\"']+", re.IGNORECASE),
    re.compile(r"oss://[^\s\"']+", re.IGNORECASE),
    re.compile(r"callback[_-]?token", re.IGNORECASE),
    re.compile(r"\btoken\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"ANTHROPIC_AUTH_TOKEN", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
]


class CheckResult:
    def __init__(self, name: str, passed: bool, message: str) -> None:
        self.name = name
        self.passed = passed
        self.message = sanitize_message(message)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
        }


def sanitize_message(message: object) -> str:
    text = str(message)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _read_evidence(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return None, "evidence file missing"
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "evidence file is not valid JSON"
    if not isinstance(data, dict):
        return None, "evidence root must be an object"
    return data, None


def _require_true(section: dict[str, Any], fields: list[str], *, section_name: str) -> str | None:
    for field in fields:
        if section.get(field) is not True:
            return f"{section_name} missing or false: {field}"
    return None


def _freshness_error(evidence: dict[str, Any], *, max_age_seconds: int = 900) -> str | None:
    generated_at = evidence.get("generated_at")
    if not isinstance(generated_at, str):
        return "fresh evidence timestamp missing"
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return "fresh evidence timestamp invalid"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > max_age_seconds:
        return "fresh evidence timestamp stale"
    return None


def _source_functions_error(evidence: dict[str, Any]) -> str | None:
    source_functions = evidence.get("source_functions")
    if not isinstance(source_functions, list):
        return "source_functions evidence missing"
    missing = [name for name in REQUIRED_SOURCE_FUNCTIONS if name not in source_functions]
    if missing:
        return f"source_functions missing: {', '.join(missing)}"
    unexpected = [name for name in source_functions if not isinstance(name, str) or name not in REQUIRED_SOURCE_FUNCTIONS]
    if unexpected:
        return "source_functions include unexpected entries"
    return None


def _non_expansion_error(evidence: dict[str, Any]) -> str | None:
    invariants = evidence.get("non_expansion_invariants")
    if not isinstance(invariants, dict):
        return "non_expansion_invariants evidence missing"
    for field, expected in REQUIRED_NON_EXPANSION_INVARIANTS.items():
        if invariants.get(field) is not expected:
            return f"non_expansion_invariants mismatch: {field}"
    return None


def _live_run_error(evidence: dict[str, Any], *, require_live_run_payload: bool) -> str | None:
    strength = evidence.get("evidence_strength")
    if strength == "source_probe_on_target_runtime":
        if evidence.get("does_not_close_211_acceptance") is not True:
            return "211 acceptance boundary missing"
        if evidence.get("runtime_acceptance_requires_real_run_payload") is not True:
            return "live run-payload requirement missing"
        if evidence.get("runtime_run_payload_verified") is not False:
            return "source-probe must not claim live run payload verification"
        return "live worker run payload evidence required" if require_live_run_payload else None
    if strength != "live_worker_run_payload":
        return "executor context evidence strength missing"
    if evidence.get("does_not_close_211_acceptance") is not False:
        return "live evidence must be allowed to close 211 acceptance"
    if evidence.get("runtime_acceptance_requires_real_run_payload") is not False:
        return "live evidence must satisfy run-payload requirement"
    if evidence.get("runtime_run_payload_verified") is not True:
        return "live run-payload verification missing"
    live_checks = evidence.get("live_run_checks")
    if not isinstance(live_checks, dict):
        return "live_run_checks evidence missing"
    missing = [field for field in REQUIRED_LIVE_RUN_CHECKS if live_checks.get(field) is not True]
    if missing:
        return f"live_run_checks missing or false: {', '.join(missing)}"
    runtime_evidence = evidence.get("runtime_evidence")
    if not isinstance(runtime_evidence, dict):
        return "runtime_evidence evidence missing"
    missing_runtime = [
        field
        for field in REQUIRED_RUNTIME_EVIDENCE
        if runtime_evidence.get(field) is not True
    ]
    if missing_runtime:
        return f"runtime_evidence missing or false: {', '.join(missing_runtime)}"
    return None


def _source_run_artifact_scope_error(evidence: dict[str, Any]) -> str | None:
    if evidence.get("evidence_strength") != "live_worker_run_payload":
        return None
    public_summary = evidence.get("public_context_summary")
    if not isinstance(public_summary, dict):
        return "public_context_summary evidence missing"
    counts = public_summary.get("referenced_material_counts")
    if not isinstance(counts, dict):
        return "referenced material counts evidence missing"
    artifact_count = counts.get("artifact_count")
    if not isinstance(artifact_count, int) or isinstance(artifact_count, bool) or artifact_count <= 0:
        return "source-run artifact scope requires a positive artifact_count"
    input_keys = public_summary.get("input_keys")
    if not isinstance(input_keys, list) or any(not isinstance(item, str) for item in input_keys):
        return "public context input_keys evidence missing or invalid"
    leaked_keys = [
        item
        for item in input_keys
        if "".join(ch for ch in item if ch.isalnum()).lower()
        in FORBIDDEN_PUBLIC_CONTEXT_INPUT_KEY_ALIASES
    ]
    if leaked_keys:
        return "public context input_keys expose source run identifiers"
    return None


def check_executor_context_pack_evidence(
    evidence_path: str | Path,
    *,
    run_id: str = "",
    require_live_run_payload: bool = False,
) -> CheckResult:
    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_executor_context_pack_evidence", False, error)
    if not run_id:
        return CheckResult("check_executor_context_pack_evidence", False, "run_id argument required")
    if evidence.get("run_id") != run_id:
        return CheckResult("check_executor_context_pack_evidence", False, "run_id evidence mismatch")
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        return CheckResult("check_executor_context_pack_evidence", False, "executor context evidence schema mismatch")
    if evidence.get("source_schema_version") != SOURCE_SCHEMA_VERSION:
        return CheckResult("check_executor_context_pack_evidence", False, "source schema mismatch")
    if evidence.get("runtime_mode") != "worker":
        return CheckResult("check_executor_context_pack_evidence", False, "worker runtime evidence missing")
    live_run_error = _live_run_error(evidence, require_live_run_payload=require_live_run_payload)
    if live_run_error:
        return CheckResult("check_executor_context_pack_evidence", False, live_run_error)
    freshness_error = _freshness_error(evidence)
    if freshness_error:
        return CheckResult("check_executor_context_pack_evidence", False, freshness_error)
    source_functions_error = _source_functions_error(evidence)
    if source_functions_error:
        return CheckResult("check_executor_context_pack_evidence", False, source_functions_error)
    prompt_checks = evidence.get("prompt_checks")
    if not isinstance(prompt_checks, dict):
        return CheckResult("check_executor_context_pack_evidence", False, "prompt_checks evidence missing")
    prompt_error = _require_true(prompt_checks, REQUIRED_PROMPT_CHECKS, section_name="prompt_checks")
    if prompt_error:
        return CheckResult("check_executor_context_pack_evidence", False, prompt_error)
    scope_checks = evidence.get("scope_checks")
    if not isinstance(scope_checks, dict):
        return CheckResult("check_executor_context_pack_evidence", False, "scope_checks evidence missing")
    scope_error = _require_true(scope_checks, REQUIRED_SCOPE_CHECKS, section_name="scope_checks")
    if scope_error:
        return CheckResult("check_executor_context_pack_evidence", False, scope_error)
    source_artifact_error = _source_run_artifact_scope_error(evidence)
    if source_artifact_error:
        return CheckResult("check_executor_context_pack_evidence", False, source_artifact_error)
    invariants_error = _non_expansion_error(evidence)
    if invariants_error:
        return CheckResult("check_executor_context_pack_evidence", False, invariants_error)
    message = (
        "executor context-pack live worker-run evidence present"
        if evidence.get("evidence_strength") == "live_worker_run_payload"
        else "executor context-pack source-probe evidence present"
    )
    return CheckResult("check_executor_context_pack_evidence", True, message)


def check_no_secret_leakage(evidence_path: str | Path) -> CheckResult:
    evidence_path = Path(evidence_path)
    if not evidence_path.exists():
        return CheckResult("check_no_secret_leakage", False, "evidence file missing")
    try:
        content = evidence_path.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult("check_no_secret_leakage", False, f"evidence read failed: {exc}")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(content):
            return CheckResult("check_no_secret_leakage", False, "sensitive evidence detected")
    return CheckResult("check_no_secret_leakage", True, "no sensitive evidence detected")


def run_checks(checks: list[Callable[[], CheckResult]]) -> tuple[int, list[CheckResult]]:
    results = [check() for check in checks]
    return (0 if all(result.passed for result in results) else 1), results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify ai-platform executor context pack evidence on 211")
    parser.add_argument(
        "--evidence-file",
        default=os.environ.get(
            "AI_PLATFORM_EXECUTOR_CONTEXT_PACK_EVIDENCE",
            "/tmp/ai-platform-executor-context-pack-evidence.json",
        ),
    )
    parser.add_argument("--run-id", default=os.environ.get("AI_PLATFORM_EXECUTOR_CONTEXT_PACK_RUN_ID", ""))
    parser.add_argument(
        "--require-live-run-payload",
        action="store_true",
        help="Fail source-probe evidence and require live_worker_run_payload evidence for 211 acceptance.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code, results = run_checks(
        [
            lambda: check_executor_context_pack_evidence(
                args.evidence_file,
                run_id=args.run_id,
                require_live_run_payload=args.require_live_run_payload,
            ),
            lambda: check_no_secret_leakage(args.evidence_file),
        ]
    )
    if args.json_output:
        print(json.dumps({"checks": [result.to_dict() for result in results]}, ensure_ascii=True, indent=2))
    else:
        failed = [result.name for result in results if not result.passed]
        print(
            "PASSED: executor context-pack source-probe verifier checks passed"
            if not failed
            else "FAILED: " + ", ".join(failed)
        )
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"{status} {result.name}: {result.message}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

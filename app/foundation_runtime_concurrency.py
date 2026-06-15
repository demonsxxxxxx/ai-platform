from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from tools.verify_poc_gate import CONTEXT_FORBIDDEN_PROJECTION_MARKERS


FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA = "ai-platform.foundation-runtime-concurrency.v1"

_REQUIRED_SCENARIOS = [
    "run_creation",
    "execution",
    "cancel",
    "retry",
]
_REQUIRED_CHECKS = [
    "queue_admission",
    "sandbox_workspace",
    "memory_context",
    "artifact_acl",
    "tool_permission",
    "skill_snapshots",
    "run_playback",
]
_MINIMUM_CONCURRENT_REQUESTS = 10
_MINIMUM_TENANTS = 2
_DEFAULT_INVARIANTS = {
    "production_concurrency_increase_allowed": False,
    "ordinary_user_multi_agent_allowed": False,
    "docker_sandbox_hardened_claim_allowed": False,
    "department_rollout_allowed": False,
    "long_term_cross_session_memory_enabled": False,
}
_DENIED_HTTP_STATUSES = {401, 403, 404}
_SUCCESS_HTTP_STATUSES = {200, 202, 204, 409}
_CANCEL_EFFECT_STATUSES = {"cancel_requested", "cancelled", "canceled"}
_CONCURRENCY_PROBE_SOURCES = {"client_case_timestamps"}
_QUEUE_PROBE_SOURCES = {"redis_metadata", "admin_runtime_queue"}
_SANDBOX_LEASE_PROBE_SOURCES = {"runtime_run_detail"}
_MINIMUM_TOOL_PERMISSION_NEGATIVE_REUSE_PROBES_PER_RUN = 4
_REVISION_REF_RE = re.compile(r"^[0-9a-f]{40}(?:[-A-Za-z0-9_.:]*)?$")


def _requirements() -> dict[str, Any]:
    return {
        "minimum_concurrent_requests": _MINIMUM_CONCURRENT_REQUESTS,
        "minimum_tenants": _MINIMUM_TENANTS,
        "required_scenarios": list(_REQUIRED_SCENARIOS),
        "required_checks": list(_REQUIRED_CHECKS),
        "evidence_status": "required_before_foundation_runtime_poc_closure",
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "tenant_count": 0,
        "user_count": 0,
        "session_count": 0,
        "run_count": 0,
        "concurrent_request_count": 0,
        "max_observed_concurrency": 0,
        "concurrency_probe_source": "missing",
        "concurrency_window_sample_count": 0,
    }


def _safe_int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _safe_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _all_denied(values: Any) -> bool:
    statuses = [item for item in _safe_list(values) if type(item) is int]
    return bool(statuses) and all(status in _DENIED_HTTP_STATUSES for status in statuses)


def _has_success_sample(values: Any) -> bool:
    statuses = [item for item in _safe_list(values) if type(item) is int]
    return bool(statuses) and all(status in _SUCCESS_HTTP_STATUSES for status in statuses)


def _cancel_effect_sample_count(values: Any) -> int:
    return sum(
        1
        for item in _safe_list(values)
        if isinstance(item, str) and item.strip().lower() in _CANCEL_EFFECT_STATUSES
    )


def _summary_from_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    summary = _safe_dict(evidence.get("summary"))
    return {
        "tenant_count": _safe_int(summary.get("tenant_count")),
        "user_count": _safe_int(summary.get("user_count")),
        "session_count": _safe_int(summary.get("session_count")),
        "run_count": _safe_int(summary.get("run_count")),
        "concurrent_request_count": _safe_int(summary.get("concurrent_request_count")),
        "max_observed_concurrency": _safe_int(summary.get("max_observed_concurrency")),
        "concurrency_probe_source": str(summary.get("concurrency_probe_source") or "missing"),
        "concurrency_window_sample_count": _safe_int(summary.get("concurrency_window_sample_count")),
    }


def _scenario_counts_from_evidence(evidence: dict[str, Any]) -> dict[str, int]:
    counts = _safe_dict(evidence.get("scenario_counts"))
    return {name: _safe_int(counts.get(name)) for name in _REQUIRED_SCENARIOS}


def _checks_from_evidence(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks = _safe_dict(evidence.get("checks"))
    return {name: _safe_dict(checks.get(name)) for name in _REQUIRED_CHECKS}


def _safe_invariants(evidence: dict[str, Any] | None = None) -> dict[str, bool]:
    source = _safe_dict(evidence.get("non_expansion_invariants")) if evidence else {}
    result = dict(_DEFAULT_INVARIANTS)
    for key in result:
        if source.get(key) is True:
            result[key] = True
        elif source.get(key) is False:
            result[key] = False
    return result


def _valid_revision_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_REVISION_REF_RE.fullmatch(value.strip()))


def _validate_evidence(evidence: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    if not isinstance(evidence, dict):
        return ["missing_evidence"], {
            "summary": _empty_summary(),
            "scenario_counts": {name: 0 for name in _REQUIRED_SCENARIOS},
            "checks": {name: {} for name in _REQUIRED_CHECKS},
            "non_expansion_invariants": dict(_DEFAULT_INVARIANTS),
        }

    failures: list[str] = []
    if evidence.get("schema_version") != FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA:
        failures.append("invalid_schema_version")
    if evidence.get("artifact_kind") != "foundation_runtime_concurrency":
        failures.append("invalid_artifact_kind")
    if not _valid_revision_ref(evidence.get("commit_sha")):
        failures.append("invalid_commit_sha")
    if not _valid_revision_ref(evidence.get("source_tree_commit_sha")):
        failures.append("invalid_source_tree_commit_sha")
    if not _valid_revision_ref(evidence.get("runtime_subject_commit_sha")):
        failures.append("invalid_runtime_subject_commit_sha")

    summary = _summary_from_evidence(evidence)
    if summary["concurrent_request_count"] < _MINIMUM_CONCURRENT_REQUESTS:
        failures.append("minimum_concurrent_requests_not_met")
    if summary["max_observed_concurrency"] < _MINIMUM_CONCURRENT_REQUESTS:
        failures.append("minimum_observed_concurrency_not_met")
    if summary["concurrency_probe_source"] not in _CONCURRENCY_PROBE_SOURCES:
        failures.append("concurrency_probe_source_missing")
    if summary["concurrency_window_sample_count"] < summary["run_count"]:
        failures.append("concurrency_window_samples_missing")
    if summary["tenant_count"] < _MINIMUM_TENANTS:
        failures.append("minimum_tenants_not_met")
    if summary["user_count"] < 2:
        failures.append("minimum_users_not_met")
    if summary["session_count"] < _MINIMUM_CONCURRENT_REQUESTS:
        failures.append("minimum_sessions_not_met")
    if summary["run_count"] < _MINIMUM_CONCURRENT_REQUESTS:
        failures.append("minimum_runs_not_met")

    scenario_counts = _scenario_counts_from_evidence(evidence)
    for name in _REQUIRED_SCENARIOS:
        if scenario_counts[name] <= 0:
            failures.append(f"scenario_{name}_missing")

    if _safe_int(evidence.get("failed_case_count")) > 0 or _safe_list(evidence.get("failed_cases")):
        failures.append("foundation_runtime_case_failures")

    checks = _checks_from_evidence(evidence)
    for name, check in checks.items():
        if check.get("status") != "passed":
            failures.append(f"check_{name}_not_passed")

    queue_admission = checks["queue_admission"]
    if _safe_int(queue_admission.get("admission_limit_violations")) > 0:
        failures.append("queue_admission_limit_violation")
    if _safe_int(queue_admission.get("cross_tenant_queue_leaks")) > 0:
        failures.append("queue_cross_tenant_leak")
    if _safe_int(queue_admission.get("stale_queue_entries")) > 0:
        failures.append("queue_stale_entries")
    if _safe_int(queue_admission.get("queue_position_sample_count")) < summary["run_count"]:
        failures.append("queue_admission_position_samples_missing")
    if _safe_int(queue_admission.get("queue_probe_sample_count")) < summary["run_count"]:
        failures.append("queue_admission_probe_samples_missing")
    if _safe_int(queue_admission.get("queue_position_duplicate_count")) > 0:
        failures.append("queue_admission_position_duplicate")
    if queue_admission.get("queue_probe_source") not in _QUEUE_PROBE_SOURCES:
        failures.append("queue_admission_probe_source_missing")
    if not _has_success_sample(queue_admission.get("cancel_action_statuses")):
        failures.append("run_control_cancel_samples_missing")
    cancel_effect_run_count = _safe_int(queue_admission.get("cancel_effect_run_count"))
    if cancel_effect_run_count == 0:
        cancel_effect_run_count = _cancel_effect_sample_count(queue_admission.get("cancel_effect_statuses"))
    if cancel_effect_run_count < max(1, scenario_counts["cancel"]):
        failures.append("run_control_cancel_effect_missing")
    if not _has_success_sample(queue_admission.get("retry_action_statuses")):
        failures.append("run_control_retry_samples_missing")
    if _safe_int(queue_admission.get("retry_created_run_count")) < max(1, scenario_counts["retry"]):
        failures.append("run_control_retry_created_run_missing")

    sandbox = checks["sandbox_workspace"]
    if _safe_int(sandbox.get("workspace_scope_sample_count")) < summary["run_count"]:
        failures.append("sandbox_workspace_samples_missing")
    if _safe_int(sandbox.get("sandbox_lease_sample_count")) < summary["run_count"]:
        failures.append("sandbox_lease_samples_missing")
    if sandbox.get("lease_probe_source") not in _SANDBOX_LEASE_PROBE_SOURCES:
        failures.append("sandbox_lease_probe_source_missing")
    if _safe_int(sandbox.get("cross_scope_lease_leaks")) > 0:
        failures.append("sandbox_lease_cross_scope_leak")
    if _safe_int(sandbox.get("workspace_scope_collisions")) > 0:
        failures.append("sandbox_workspace_scope_collision")

    memory_context = checks["memory_context"]
    if _safe_int(memory_context.get("context_snapshot_count")) < summary["run_count"]:
        failures.append("memory_context_snapshot_count_insufficient")
    if _safe_int(memory_context.get("context_snapshot_public_projection_count")) < summary["run_count"]:
        failures.append("memory_context_public_projection_count_insufficient")
    if _safe_int(memory_context.get("context_pack_version_sample_count")) < summary["run_count"]:
        failures.append("memory_context_pack_version_samples_insufficient")
    if _safe_int(memory_context.get("missing_context_pack_version_count")) > 0:
        failures.append("memory_context_pack_version_missing")
    if _safe_int(memory_context.get("unsafe_context_pack_version_count")) > 0:
        failures.append("memory_context_pack_version_unsafe")
    if _safe_list(memory_context.get("missing_public_summary_fields")):
        failures.append("memory_context_public_summary_fields_missing")
    if _safe_int(memory_context.get("context_scope_probe_count")) < summary["run_count"]:
        failures.append("memory_context_scope_probe_missing")
    if _safe_int(memory_context.get("cross_scope_context_leaks")) > 0:
        failures.append("memory_context_cross_scope_leak")
    if memory_context.get("long_term_cross_session_memory_read") is not False:
        failures.append("long_term_cross_session_memory_not_fail_closed")

    artifact_acl = checks["artifact_acl"]
    if not _all_denied(artifact_acl.get("cross_user_statuses")):
        failures.append("artifact_acl_cross_user_not_denied")
    if not _all_denied(artifact_acl.get("cross_tenant_statuses")):
        failures.append("artifact_acl_cross_tenant_not_denied")
    if not _all_denied(artifact_acl.get("preview_cross_user_statuses")):
        failures.append("artifact_preview_cross_user_not_denied")
    if not _all_denied(artifact_acl.get("preview_cross_tenant_statuses")):
        failures.append("artifact_preview_cross_tenant_not_denied")

    tool_permission = checks["tool_permission"]
    if _safe_int(tool_permission.get("decision_sample_count")) <= 0:
        failures.append("tool_permission_decision_samples_missing")
    negative_probe_count = _safe_int(tool_permission.get("negative_reuse_probe_count"))
    negative_denied_count = _safe_int(tool_permission.get("negative_reuse_denied_count"))
    minimum_negative_reuse_probe_count = (
        summary["run_count"] * _MINIMUM_TOOL_PERMISSION_NEGATIVE_REUSE_PROBES_PER_RUN
    )
    if negative_probe_count < minimum_negative_reuse_probe_count:
        failures.append("tool_permission_negative_reuse_probe_missing")
    if negative_denied_count < negative_probe_count:
        failures.append("tool_permission_negative_reuse_not_denied")
    if _safe_int(tool_permission.get("negative_reuse_unexpected_successes")) > 0:
        failures.append("tool_permission_negative_reuse_unexpected_success")
    if _safe_int(tool_permission.get("allow_once_reuse_violations")) > 0:
        failures.append("tool_permission_allow_once_reused")
    if _safe_int(tool_permission.get("wrong_decision_reuse_violations")) > 0:
        failures.append("tool_permission_wrong_decision_reused")
    if _safe_int(tool_permission.get("tool_call_id_mismatch_violations")) > 0:
        failures.append("tool_permission_tool_call_id_mismatch")

    skill_snapshots = checks["skill_snapshots"]
    if _safe_int(skill_snapshots.get("run_skill_snapshot_count")) < summary["run_count"]:
        failures.append("skill_snapshots_missing_for_runs")
    if _safe_int(skill_snapshots.get("snapshot_binding_sample_count")) < summary["run_count"]:
        failures.append("skill_snapshot_binding_samples_missing")
    if _safe_list(skill_snapshots.get("missing_pinned_snapshots")):
        failures.append("skill_snapshots_missing_pinned_snapshot")
    if _safe_list(skill_snapshots.get("mismatched_pinned_snapshots")):
        failures.append("skill_snapshots_mismatched_pinned_snapshot")
    if skill_snapshots.get("global_mutable_skill_lookup_used") is not False:
        failures.append("skill_snapshots_used_global_mutable_lookup")

    playback = checks["run_playback"]
    if _safe_int(playback.get("event_order_violations")) > 0:
        failures.append("run_playback_event_order_violation")
    if _safe_int(playback.get("private_payload_leak_count")) > 0:
        failures.append("run_playback_private_payload_leak")

    raw_invariants = evidence.get("non_expansion_invariants")
    invariant_source = raw_invariants if isinstance(raw_invariants, dict) else {}
    invariants = _safe_invariants(evidence)
    for key, default in _DEFAULT_INVARIANTS.items():
        if key not in invariant_source:
            failures.append(f"missing_non_expansion_invariant_{key}")
            continue
        if invariant_source.get(key) is not default:
            failures.append(f"invariant_{key}_violated")

    role_provenance = evidence.get("role_provenance")
    if not isinstance(role_provenance, dict):
        failures.append("missing_role_provenance")
    else:
        if role_provenance.get("ordinary_user_multi_agent_opened") is not False:
            failures.append("ordinary_user_multi_agent_opened")
        if role_provenance.get("public_probe_role") != "user":
            failures.append("public_probe_role_not_user")
        if role_provenance.get("run_creation_role") not in {"user", "developer"}:
            failures.append("invalid_run_creation_role")
        admin_probe_role = role_provenance.get("admin_probe_role")
        if admin_probe_role not in {None, "developer"}:
            failures.append("invalid_admin_probe_role")

    return failures, {
        "summary": summary,
        "scenario_counts": scenario_counts,
        "checks": checks,
        "non_expansion_invariants": invariants,
    }


def build_foundation_runtime_concurrency_readiness(
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize Foundation Runtime 10+ concurrent-run isolation evidence."""
    failures, normalized = _validate_evidence(evidence)
    missing = failures == ["missing_evidence"]
    verified = not failures
    status = (
        "verified_foundation_runtime_concurrency"
        if verified
        else "missing_foundation_runtime_concurrency_evidence"
        if missing
        else "blocked_foundation_runtime_concurrency_evidence"
    )
    return {
        "schema_version": FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
        "status": status,
        "verified": verified,
        "requirements": _requirements(),
        "summary": normalized["summary"],
        "scenario_counts": normalized["scenario_counts"],
        "checks": normalized["checks"],
        "non_expansion_invariants": normalized["non_expansion_invariants"],
        "failures": failures,
        "evidence_policy": (
            "This is Foundation Runtime POC correctness evidence only; it does "
            "not raise production concurrency defaults or open ordinary-user "
            "multi-agent, Docker sandbox hardening, long-term memory, or "
            "department rollout gates."
        ),
    }


def load_foundation_runtime_concurrency_evidence(path: str | Path | None) -> dict[str, Any] | None:
    """Load a reviewed concurrency evidence JSON file, returning None on invalid input."""
    if path is None:
        return None
    evidence_path = Path(path)
    if not evidence_path.exists():
        return None
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def render_foundation_runtime_concurrency_markdown(readiness: dict[str, Any]) -> str:
    """Render a compact operator-readable summary from a readiness payload."""
    failures = readiness.get("failures") or []
    failure_lines = "\n".join(f"- {item}" for item in failures) or "- none"
    invariants = readiness.get("non_expansion_invariants") or {}
    invariant_lines = "\n".join(
        f"- `{key}`: `{value}`" for key, value in sorted(invariants.items())
    )
    summary = readiness.get("summary") or {}
    memory_context = (readiness.get("checks") or {}).get("memory_context") or {}
    return "\n".join(
        [
            "# Foundation Runtime Concurrency Readiness",
            "",
            f"- Status: `{readiness.get('status')}`",
            f"- Verified: `{readiness.get('verified')}`",
            f"- Tenants: `{summary.get('tenant_count', 0)}`",
            f"- Runs: `{summary.get('run_count', 0)}`",
            f"- Concurrent requests: `{summary.get('concurrent_request_count', 0)}`",
            f"- Context pack version samples: `{memory_context.get('context_pack_version_sample_count', 0)}`",
            "",
            "## Non-Expansion Invariants",
            "",
            invariant_lines,
            "",
            "## Failures",
            "",
            failure_lines,
        ]
    )


def output_contains_forbidden_terms(payload: dict[str, Any]) -> bool:
    """Return whether a readiness payload contains terms blocked from public evidence."""
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    return any(term in serialized for term in CONTEXT_FORBIDDEN_PROJECTION_MARKERS)

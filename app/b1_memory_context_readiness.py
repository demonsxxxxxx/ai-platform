from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.memory_erasure_readiness import build_memory_erasure_readiness
from app.office_context_readiness import build_office_context_readiness


SCHEMA_VERSION = "ai-platform.b1-memory-context-readiness.v1"
BACKEND_STAGE = "B1 memory/context usable"
ISSUE = "#75"
RUNTIME_ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"
RUNTIME_ACCEPTANCE_VERIFIER = "tools/verify_b1_memory_context_workflow.py"
RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA = "ai-platform.b1-memory-context-workflow-smoke.v1"
RUNTIME_ACCEPTANCE_TARGET = "211_api_memory_context_workflow"
_RUNTIME_EVIDENCE_ROOT = "docs/release-evidence/b1-memory-context"

_IMPLEMENTED_CONTROLS = [
    "tenant_workspace_user_session_scoped_memory_policy",
    "memory_policy_disabled_blocks_reads_and_writes",
    "ordinary_user_session_scoped_soft_delete",
    "admin_same_tenant_soft_delete",
    "retention_cleanup_admin_and_worker",
    "memory_export_excludes_deleted_and_expired_records",
    "memory_export_requires_session_scope_and_enabled_policy",
    "memory_redaction_preview_and_audit",
    "context_pack_persistence_version_generated_at_provenance",
    "context_snapshot_public_provenance_projection",
    "executor_context_pack_prompt_injection_source_tests",
    "worker_loads_scoped_db_context_snapshot",
    "document_centric_followup_state_source_tests",
    "long_term_cross_session_memory_fail_closed",
]

_NON_EXPANSION_INVARIANTS = {
    "long_term_cross_session_memory_enabled": False,
    "public_projection_only_for_ordinary_users": True,
    "stores_private_executor_material_as_memory": False,
    "frontend_state_is_canonical_context": False,
    "production_claim_allowed": False,
}

_SMOKE_REQUIRED_CHECKS = [
    "context_snapshot_public_provenance",
    "create_governed_run",
    "cross_user_context_denied",
    "deleted_memory_absent_from_future_context",
    "long_term_memory_fail_closed",
    "memory_policy_disabled_blocks_create",
    "memory_policy_disabled_blocks_list",
    "memory_policy_enabled_for_governed_scope",
    "memory_record_create_and_list",
    "no_private_projection_leakage",
    "playback_public_projection",
]

_SMOKE_REQUIRED_BOUNDARIES = [
    "issue review and closure evidence",
    "runtime evidence review against merged source",
    "memory export boundary",
    "rollback boundary",
]

B1_GATE_BOUNDARY_GAPS = [
    "b1_issue_review_and_closure_evidence",
    "b1_runtime_evidence_review_against_merged_source",
    "b1_memory_export_boundary",
    "b1_rollback_boundary",
]


def _path_for_output(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_subject(payload: dict[str, Any]) -> str:
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return ""
    image = str(source_ref.get("image") or "")
    marker = str(source_ref.get("runtime_source_marker") or "")
    if image.startswith("ai-platform:"):
        return image.removeprefix("ai-platform:")
    return marker


def _entry_has_runtime_subject_binding(payload: dict[str, Any]) -> bool:
    runtime_subject = payload.get("runtime_subject_commit_sha")
    if not isinstance(runtime_subject, str) or not runtime_subject:
        return False
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return False
    if source_ref.get("branch") != "main":
        return False
    if source_ref.get("runtime_source_marker") != runtime_subject:
        return False
    if source_ref.get("source_tree_dirty") is not False:
        return False
    source_snapshot = source_ref.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        return False
    if source_snapshot.get("runtime_subject_commit_sha") != runtime_subject:
        return False
    if source_snapshot.get("source_tree_dirty") is not False:
        return False
    if source_snapshot.get("runtime_affecting_changes_since_runtime_subject") != []:
        return False
    if source_snapshot.get("runtime_affecting_dirty_paths") != []:
        return False
    image = source_ref.get("image")
    if not isinstance(image, str) or not image.startswith("ai-platform:"):
        return False
    return True


def _entry_is_reviewed_b1_smoke(payload: dict[str, Any]) -> bool:
    evidence_ref = payload.get("evidence_ref")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == BACKEND_STAGE
        and payload.get("artifact_kind") == RUNTIME_ACCEPTANCE_GAP
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") == "reviewed"
        and _entry_has_runtime_subject_binding(payload)
        and isinstance(evidence_ref, dict)
        and evidence_ref.get("verifier") == RUNTIME_ACCEPTANCE_VERIFIER
        and evidence_ref.get("result") == "ok:true"
        and _runtime_subject(payload) != ""
    )


def _runtime_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    evidence_ref = payload.get("evidence_ref")
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    runtime_payload = (
        runtime_checks.get(RUNTIME_ACCEPTANCE_GAP)
        if isinstance(runtime_checks, dict)
        else None
    )
    return runtime_payload if isinstance(runtime_payload, dict) else None


def _b1_smoke_evidence_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    if not _entry_is_reviewed_b1_smoke(payload):
        return None
    evidence = _runtime_payload(payload)
    if evidence is None:
        return None
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        return None
    if not all(checks.get(check) is True for check in _SMOKE_REQUIRED_CHECKS):
        return None
    if (
        evidence.get("schema_version") != RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA
        or evidence.get("target") != RUNTIME_ACCEPTANCE_TARGET
        or evidence.get("acceptance_gap") != RUNTIME_ACCEPTANCE_GAP
        or evidence.get("ok") is not True
        or evidence.get("redaction_scan_status") != "passed"
        or evidence.get("does_not_close_b1_gate") is not True
        or evidence.get("memory_record_count") != 1
    ):
        return None
    invariants = evidence.get("non_expansion_invariants")
    if invariants != {
        "frontend_state_is_canonical_context": False,
        "gate_closure_claimed": False,
        "long_term_cross_session_memory_enabled": False,
        "stores_private_executor_material_as_memory": False,
    }:
        return None
    boundaries = evidence.get("remaining_gate_boundaries")
    if not isinstance(boundaries, list):
        return None
    if not set(_SMOKE_REQUIRED_BOUNDARIES).issubset(
        {item for item in boundaries if isinstance(item, str)}
    ):
        return None
    return {
        "status": "verified_211_runtime_acceptance",
        "artifact_kind": RUNTIME_ACCEPTANCE_GAP,
        "evidence_id": payload.get("evidence_id"),
        "path": _path_for_output(path, repo_root),
        "verifier": RUNTIME_ACCEPTANCE_VERIFIER,
        "runtime_subject": _runtime_subject(payload),
        "runtime_subject_commit_sha": payload.get("runtime_subject_commit_sha"),
        "target": evidence.get("target"),
        "memory_record_count": evidence.get("memory_record_count"),
        "checks": {check: True for check in _SMOKE_REQUIRED_CHECKS},
        "redaction_scan_status": evidence.get("redaction_scan_status"),
        "remaining_gate_boundaries": list(boundaries),
        "does_not_close_b1_gate": True,
    }


def _runtime_acceptance_evidence(repo_root: Path) -> dict[str, dict[str, Any]]:
    evidence_root = repo_root / _RUNTIME_EVIDENCE_ROOT
    if not evidence_root.exists():
        return {}
    summaries: dict[str, dict[str, Any]] = {}
    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        summary = _b1_smoke_evidence_summary(payload, path=path, repo_root=repo_root)
        if summary is not None:
            summaries[RUNTIME_ACCEPTANCE_GAP] = summary
    return summaries


def _status_for_local_controls(
    memory_erasure: dict[str, Any],
    office_context: dict[str, Any],
) -> str:
    if memory_erasure.get("missing_evidence_markers"):
        return "blocked_missing_local_evidence"
    if office_context.get("open_gaps"):
        return "blocked_missing_context_pack_evidence"
    return "local_controls_ready_runtime_smoke_required"


def _memory_erasure_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": readiness["schema_version"],
        "status": readiness["status"],
        "implemented_control_count": len(readiness.get("implemented_controls", [])),
        "missing_evidence_marker_count": len(readiness.get("missing_evidence_markers", [])),
        "open_gaps": list(readiness.get("open_gaps", [])),
        "closed_runtime_gaps": list(readiness.get("closed_runtime_gaps", [])),
    }


def _office_context_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": readiness["schema_version"],
        "status": readiness["status"],
        "implemented_control_count": len(readiness.get("implemented_controls", [])),
        "open_gap_count": len(readiness.get("open_gaps", [])),
        "closed_runtime_gap_count": len(readiness.get("closed_runtime_gaps", [])),
        "policy": {
            "ordinary_user_policy": readiness.get("policy", {}).get("ordinary_user_policy"),
            "long_term_memory_policy": readiness.get("policy", {}).get("long_term_memory_policy"),
            "does_not_expand_multi_agent_beta": readiness.get("policy", {}).get("does_not_expand_multi_agent_beta"),
        },
    }


def build_b1_memory_context_readiness(repo_root: Path | None = None) -> dict[str, Any]:
    """Build the B1 memory/context readiness rollup with reviewed 211 smoke evidence."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    memory_erasure = build_memory_erasure_readiness(repo_root=root)
    office_context = build_office_context_readiness(repo_root=root)
    local_status = _status_for_local_controls(memory_erasure, office_context)
    runtime_acceptance_evidence = _runtime_acceptance_evidence(root)
    b1_smoke_recorded = RUNTIME_ACCEPTANCE_GAP in runtime_acceptance_evidence
    status = (
        "runtime_acceptance_recorded"
        if local_status == "local_controls_ready_runtime_smoke_required" and b1_smoke_recorded
        else local_status
    )
    status_label = "local partial"
    runtime_acceptance = {
        "required": True,
        "status": (
            "verified_211_runtime_acceptance"
            if status == "runtime_acceptance_recorded"
            else "missing_211_memory_enabled_document_workflow_smoke"
        ),
        "acceptance_gap": RUNTIME_ACCEPTANCE_GAP,
        "verifier_script": RUNTIME_ACCEPTANCE_VERIFIER,
        "verifier_schema_version": RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA,
        "target": RUNTIME_ACCEPTANCE_TARGET,
        "required_workflow": "selected_memory_enabled_document_workflow",
        "required_evidence": [
            "211 upload or select document workflow input",
            "memory policy enabled only for governed scope",
            "context snapshot records provenance and memory policy source",
            "executor context pack includes bounded public summary",
            "public/admin projections expose provenance and status only",
            "deleted or redacted memory does not reappear in context",
            "no executor-private payload, raw storage key, or sandbox workdir leaks",
        ],
        "status_label_before_smoke": "local partial",
        "does_not_close_b1_gate": True,
        "remaining_gate_boundaries": list(_SMOKE_REQUIRED_BOUNDARIES),
    }
    if status == "runtime_acceptance_recorded":
        runtime_acceptance["status_label_after_smoke"] = "211 verified"
    open_gaps = list(B1_GATE_BOUNDARY_GAPS)
    if status != "runtime_acceptance_recorded":
        open_gaps.insert(0, RUNTIME_ACCEPTANCE_GAP)
    closed_runtime_gaps = list(memory_erasure.get("closed_runtime_gaps", []))
    if status == "runtime_acceptance_recorded":
        closed_runtime_gaps.append(RUNTIME_ACCEPTANCE_GAP)
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_stage": BACKEND_STAGE,
        "issue": ISSUE,
        "status": status,
        "status_label": status_label,
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "ordinary_user_policy": "session_scoped_memory_with_public_provenance",
        "implemented_controls": list(_IMPLEMENTED_CONTROLS),
        "local_evidence": {
            "memory_erasure_readiness": _memory_erasure_summary(memory_erasure),
            "office_context_readiness": _office_context_summary(office_context),
        },
        "runtime_acceptance": runtime_acceptance,
        "runtime_acceptance_evidence": runtime_acceptance_evidence,
        "open_gaps": open_gaps,
        "closed_runtime_gaps": closed_runtime_gaps,
        "non_expansion_invariants": dict(_NON_EXPANSION_INVARIANTS),
        "evidence_policy": (
            "B1 local controls plus reviewed 211 memory-enabled document workflow "
            "smoke evidence close only the `211_memory_enabled_document_workflow_smoke` "
            "runtime gap. B1 gate closure still requires issue review, runtime "
            "evidence review, memory export boundary, and rollback boundary."
        ),
    }


def render_b1_memory_context_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B1 memory/context readiness as gap-first operator Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {control}" for control in readiness["implemented_controls"])
    runtime = readiness["runtime_acceptance"]
    runtime_evidence = "\n".join(f"- {item}" for item in runtime["required_evidence"])
    acceptance_summary = readiness.get("runtime_acceptance_evidence", {}).get(
        RUNTIME_ACCEPTANCE_GAP
    )
    if isinstance(acceptance_summary, dict):
        runtime_evidence += (
            "\n\nRecorded 211 smoke evidence:\n\n"
            f"- Evidence: `{acceptance_summary.get('evidence_id')}`\n"
            f"- Path: `{acceptance_summary.get('path')}`\n"
            f"- Runtime subject: `{acceptance_summary.get('runtime_subject')}`\n"
            f"- Memory record count: `{acceptance_summary.get('memory_record_count')}`"
        )
    invariants = "\n".join(
        f"- `{key}`: `{str(value).lower()}`"
        for key, value in readiness["non_expansion_invariants"].items()
    )
    return (
        "# ai-platform B1 Memory/Context Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Backend stage: `{readiness['backend_stage']}`\n\n"
        f"Issue: `{readiness['issue']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Status label: `{readiness['status_label']}`\n\n"
        f"Admin Runtime projection: `{readiness['admin_runtime_projection']}`\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "## Runtime Acceptance\n\n"
        f"Required: `{str(runtime['required']).lower()}`\n\n"
        f"Status: `{runtime['status']}`\n\n"
        f"Smoke status label: `{runtime.get('status_label_after_smoke', 'not recorded')}`\n\n"
        f"Acceptance gap: `{runtime['acceptance_gap']}`\n\n"
        f"Verifier: `{runtime['verifier_script']}` "
        f"(`{runtime['verifier_schema_version']}` targeting `{runtime['target']}`)\n\n"
        f"Does not close B1 gate: `{str(runtime['does_not_close_b1_gate']).lower()}`\n\n"
        "Required evidence:\n\n"
        f"{runtime_evidence}\n\n"
        "## Implemented Controls\n\n"
        f"{controls}\n\n"
        "## Non-expansion Boundaries\n\n"
        "This readiness rollup does not enable long-term cross-session memory by default, "
        "does not store executor-private payloads as memory, and does not make frontend "
        "state canonical context.\n\n"
        f"{invariants}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

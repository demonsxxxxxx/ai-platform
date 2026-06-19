from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backend_stage_closure_evidence import find_stage_issue_closure_evidence
from app.foundation_alpha_readiness import _resolve_runtime_affecting_changes_between
from app.memory_erasure_readiness import build_memory_erasure_readiness
from app.office_context_readiness import build_office_context_readiness


SCHEMA_VERSION = "ai-platform.b1-memory-context-readiness.v1"
BACKEND_STAGE = "B1 memory/context usable"
ISSUE = "#75"
RUNTIME_ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"
RUNTIME_ACCEPTANCE_VERIFIER = "tools/verify_b1_memory_context_workflow.py"
RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA = "ai-platform.b1-memory-context-workflow-smoke.v1"
RUNTIME_ACCEPTANCE_TARGET = "211_api_memory_context_workflow"
MEMORY_ERASURE_READINESS_SCHEMA = "ai-platform.memory-erasure-readiness.v1"
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

_CURRENT_GATE_BOUNDARY_LABELS = {
    "b1_issue_review_and_closure_evidence": "issue review and closure evidence",
    "b1_runtime_evidence_review_against_merged_source": "runtime evidence review against merged source",
    "b1_memory_export_boundary": "memory export boundary",
    "b1_rollback_boundary": "rollback boundary",
}

B1_GATE_BOUNDARY_GAPS = [
    "b1_issue_review_and_closure_evidence",
    "b1_runtime_evidence_review_against_merged_source",
    "b1_memory_export_boundary",
    "b1_rollback_boundary",
]

_MEMORY_EXPORT_BOUNDARY_REQUIRED_CONTROLS = [
    "ordinary_user_export_excludes_deleted_and_expired_records",
    "ordinary_user_export_requires_session_scope_and_enabled_policy",
    "admin_export_operator_projection_without_content_or_metadata",
]

_ROLLBACK_BOUNDARY_CONTROLS = [
    "disable_memory_policy_for_governed_workflow",
    "disable_context_pack_injection_for_governed_workflow",
    "pause_memory_retention_worker_cleanup",
    "verify_existing_memory_records_remain_scoped_and_exportable",
    "verify_public_projections_hide_private_context_material",
    "restore_previous_runtime_configuration_from_release_evidence",
]

_ROLLBACK_BOUNDARY_OPERATOR_STEPS = [
    "capture current source/runtime subject and Admin Runtime memory/context status",
    "disable selected workflow memory policy before disabling context-pack injection",
    "restart or reload API and worker runtime if configuration changed",
    "run B1 verifier or reduced deny-path smoke to confirm no new memory reads or writes",
    "record issue comment with source/runtime subject, verification result, and residual caveats",
]


def _resolve_source_tree_revision(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


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
        "captured_at": payload.get("captured_at"),
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
    candidates: list[dict[str, Any]] = []
    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        summary = _b1_smoke_evidence_summary(payload, path=path, repo_root=repo_root)
        if summary is not None:
            candidates.append(summary)
    if not candidates:
        return {}
    current_source = _resolve_source_tree_revision(repo_root)
    candidates.sort(
        key=lambda summary: _runtime_acceptance_evidence_rank(summary, current_source)
    )
    return {RUNTIME_ACCEPTANCE_GAP: candidates[0]}


def _runtime_acceptance_evidence_rank(
    summary: dict[str, Any],
    current_source: str,
) -> tuple[int, int, float, str, str]:
    runtime_subject = summary.get("runtime_subject_commit_sha")
    if not isinstance(runtime_subject, str) or not runtime_subject:
        return (3, 0, 0, "", str(summary.get("path") or ""))
    captured_at_rank = _captured_at_descending_rank(summary.get("captured_at"))
    if runtime_subject == current_source:
        return (0, 0, captured_at_rank, runtime_subject, str(summary.get("path") or ""))
    runtime_affecting_changes = _resolve_runtime_affecting_changes_between(
        runtime_subject,
        current_source,
    )
    if runtime_affecting_changes == []:
        return (1, 0, captured_at_rank, runtime_subject, str(summary.get("path") or ""))
    if runtime_affecting_changes is None:
        return (2, 0, captured_at_rank, runtime_subject, str(summary.get("path") or ""))
    return (
        3,
        len(runtime_affecting_changes),
        captured_at_rank,
        runtime_subject,
        str(summary.get("path") or ""),
    )


def _captured_at_descending_rank(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0
    try:
        return -datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def _runtime_subject_commit_from_evidence(
    runtime_acceptance_evidence: dict[str, dict[str, Any]],
) -> str:
    evidence = runtime_acceptance_evidence.get(RUNTIME_ACCEPTANCE_GAP)
    if not isinstance(evidence, dict):
        return ""
    value = evidence.get("runtime_subject_commit_sha")
    return value if isinstance(value, str) else ""


def _merged_source_runtime_review(
    repo_root: Path,
    runtime_acceptance_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current_source = _resolve_source_tree_revision(repo_root)
    runtime_subject = _runtime_subject_commit_from_evidence(runtime_acceptance_evidence)
    if not runtime_subject:
        return {
            "status": "open_missing_runtime_subject_evidence",
            "runtime_subject_commit_sha": "",
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": None,
            "required_next_step": "record reviewed 211 B1 smoke evidence before reviewing merged-source drift",
            "closed_gap": None,
            "does_not_close_b1_gate": True,
        }
    runtime_affecting_changes = _resolve_runtime_affecting_changes_between(
        runtime_subject,
        current_source,
    )
    if runtime_affecting_changes is None:
        return {
            "status": "open_unable_to_classify_runtime_delta",
            "runtime_subject_commit_sha": runtime_subject,
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": None,
            "required_next_step": "classify runtime-affecting source delta before accepting or rerunning B1 211 smoke evidence",
            "closed_gap": None,
            "does_not_close_b1_gate": True,
        }
    if runtime_affecting_changes:
        return {
            "status": "runtime_affecting_delta_requires_fresh_211_smoke",
            "runtime_subject_commit_sha": runtime_subject,
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": runtime_affecting_changes,
            "required_next_step": "deploy current main to 211 and rerun tools/verify_b1_memory_context_workflow.py before closing this gap",
            "closed_gap": None,
            "does_not_close_b1_gate": True,
        }
    return {
        "status": "recorded_local_contract",
        "closed_gap": "b1_runtime_evidence_review_against_merged_source",
        "runtime_subject_commit_sha": runtime_subject,
        "current_source_commit_sha": current_source,
        "runtime_affecting_changes_since_runtime_subject": [],
        "required_next_step": "record issue closure evidence after final issue review",
        "does_not_close_b1_gate": True,
    }


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


def _memory_export_boundary_recorded(memory_erasure: dict[str, Any]) -> bool:
    implemented = set(memory_erasure.get("implemented_controls", []))
    missing_markers = set(memory_erasure.get("missing_evidence_markers", []))
    return (
        set(_MEMORY_EXPORT_BOUNDARY_REQUIRED_CONTROLS).issubset(implemented)
        and "ordinary_user_export_query" not in missing_markers
        and "ordinary_user_export_route_policy" not in missing_markers
        and "admin_export_operator_projection" not in missing_markers
        and "repository_export_erasure_tests" not in missing_markers
        and "route_export_erasure_tests" not in missing_markers
    )


def _gate_boundary_evidence(
    memory_erasure: dict[str, Any],
    *,
    repo_root: Path,
    runtime_acceptance_evidence: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    export_recorded = _memory_export_boundary_recorded(memory_erasure)
    issue_closure_evidence = _issue_closure_boundary_evidence(repo_root)
    return {
        "b1_issue_review_and_closure_evidence": issue_closure_evidence,
        "b1_runtime_evidence_review_against_merged_source": _merged_source_runtime_review(
            repo_root,
            runtime_acceptance_evidence,
        ),
        "b1_memory_export_boundary": {
            "status": "recorded_local_contract" if export_recorded else "open_missing_local_contract",
            "closed_gap": "b1_memory_export_boundary" if export_recorded else None,
            "source_readiness": MEMORY_ERASURE_READINESS_SCHEMA,
            "required_controls": list(_MEMORY_EXPORT_BOUNDARY_REQUIRED_CONTROLS),
            "required_markers": [
                "ordinary_user_export_query",
                "ordinary_user_export_route_policy",
                "admin_export_operator_projection",
                "repository_export_erasure_tests",
                "route_export_erasure_tests",
            ],
            "does_not_close_b1_gate": True,
        },
        "b1_rollback_boundary": {
            "status": "recorded_local_contract",
            "closed_gap": "b1_rollback_boundary",
            "rollback_controls": list(_ROLLBACK_BOUNDARY_CONTROLS),
            "operator_steps": list(_ROLLBACK_BOUNDARY_OPERATOR_STEPS),
            "does_not_run_211_smoke": True,
            "does_not_close_b1_gate": True,
        },
    }


def _issue_closure_boundary_evidence(repo_root: Path) -> dict[str, Any]:
    evidence = find_stage_issue_closure_evidence(
        repo_root,
        issue=ISSUE,
        backend_stage=BACKEND_STAGE,
        closed_gap="b1_issue_review_and_closure_evidence",
    )
    if evidence is None:
        return {
            "status": "open_missing_issue_closure_evidence",
            "closed_gap": None,
            "issue": ISSUE,
            "required_next_step": "record reviewed local issue-closure evidence for #75 before closing this boundary",
            "does_not_close_b1_gate": True,
        }
    return {
        "status": "recorded_issue_closure_evidence",
        "closed_gap": "b1_issue_review_and_closure_evidence",
        "issue": evidence["issue"],
        "issue_state": evidence["issue_state"],
        "closed_at": evidence.get("closed_at"),
        "path": evidence["path"],
        "linked_prs": evidence["linked_prs"],
        "closure_comments": evidence["closure_comments"],
        "evidence_refs": evidence["evidence_refs"],
        "residual_caveats": evidence["residual_caveats"],
        "non_expansion_invariants": evidence["non_expansion_invariants"],
        "does_not_close_b1_gate": True,
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
    gate_boundary_evidence = _gate_boundary_evidence(
        memory_erasure,
        repo_root=root,
        runtime_acceptance_evidence=runtime_acceptance_evidence,
    )
    closed_gate_boundary_gaps = [
        gap
        for gap, evidence in gate_boundary_evidence.items()
        if evidence.get("closed_gap") == gap
    ]
    open_gaps = [
        gap
        for gap in B1_GATE_BOUNDARY_GAPS
        if gap not in closed_gate_boundary_gaps
    ]
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
        "remaining_gate_boundaries": [
            _CURRENT_GATE_BOUNDARY_LABELS[gap] for gap in open_gaps
        ],
    }
    if status == "runtime_acceptance_recorded":
        runtime_acceptance["status_label_after_smoke"] = "211 verified"
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
        "gate_boundary_evidence": gate_boundary_evidence,
        "open_gaps": open_gaps,
        "closed_runtime_gaps": closed_runtime_gaps,
        "closed_gate_boundary_gaps": closed_gate_boundary_gaps,
        "non_expansion_invariants": dict(_NON_EXPANSION_INVARIANTS),
        "evidence_policy": (
            "B1 local controls plus reviewed 211 memory-enabled document workflow "
            "smoke evidence close only the `211_memory_enabled_document_workflow_smoke` "
            "runtime gap. The memory export boundary is recorded as a local "
            "contract when memory-erasure readiness has the required export "
            "controls and tests. The rollback boundary is a local operator "
            "contract for disabling governed memory/context workflow exposure "
            "and reverting runtime/config state. Repo-local #75 closure evidence "
            "can close only the issue-review boundary. Reviewed merged-source "
            "runtime evidence can close only the runtime evidence review boundary."
        ),
    }


def render_b1_memory_context_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B1 memory/context readiness as gap-first operator Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    closed_gate_boundary_gaps = (
        "\n".join(f"- {gap}" for gap in readiness.get("closed_gate_boundary_gaps", []))
        or "- none"
    )
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
    gate_boundary_evidence = readiness.get("gate_boundary_evidence", {})
    export_boundary = (
        gate_boundary_evidence.get("b1_memory_export_boundary")
        if isinstance(gate_boundary_evidence, dict)
        else None
    )
    rollback_boundary = (
        gate_boundary_evidence.get("b1_rollback_boundary")
        if isinstance(gate_boundary_evidence, dict)
        else None
    )
    runtime_review = (
        gate_boundary_evidence.get("b1_runtime_evidence_review_against_merged_source")
        if isinstance(gate_boundary_evidence, dict)
        else None
    )
    issue_closure = (
        gate_boundary_evidence.get("b1_issue_review_and_closure_evidence")
        if isinstance(gate_boundary_evidence, dict)
        else None
    )
    export_boundary_lines = "- none"
    if isinstance(export_boundary, dict):
        required_controls = export_boundary.get("required_controls")
        if isinstance(required_controls, list):
            controls_lines = "\n".join(f"- {item}" for item in required_controls)
        else:
            controls_lines = "- none"
        export_boundary_lines = (
            f"- status: `{export_boundary.get('status')}`\n"
            f"- source readiness: `{export_boundary.get('source_readiness')}`\n"
            "- required controls:\n"
            f"{controls_lines}"
        )
    rollback_boundary_lines = "- none"
    if isinstance(rollback_boundary, dict):
        rollback_controls = rollback_boundary.get("rollback_controls")
        operator_steps = rollback_boundary.get("operator_steps")
        if isinstance(rollback_controls, list):
            rollback_control_lines = "\n".join(f"- {item}" for item in rollback_controls)
        else:
            rollback_control_lines = "- none"
        if isinstance(operator_steps, list):
            operator_step_lines = "\n".join(f"- {item}" for item in operator_steps)
        else:
            operator_step_lines = "- none"
        rollback_boundary_lines = (
            f"- status: `{rollback_boundary.get('status')}`\n"
            f"- does not run 211 smoke: `{str(rollback_boundary.get('does_not_run_211_smoke')).lower()}`\n"
            "- rollback controls:\n"
            f"{rollback_control_lines}\n"
            "- operator steps:\n"
            f"{operator_step_lines}"
        )
    runtime_review_lines = "- none"
    if isinstance(runtime_review, dict):
        runtime_delta = runtime_review.get("runtime_affecting_changes_since_runtime_subject")
        if isinstance(runtime_delta, list):
            runtime_delta_lines = "\n".join(f"- {item}" for item in runtime_delta) or "- none"
        else:
            runtime_delta_lines = "- unknown"
        runtime_review_lines = (
            f"- status: `{runtime_review.get('status')}`\n"
            f"- runtime subject commit: `{runtime_review.get('runtime_subject_commit_sha')}`\n"
            f"- current source commit: `{runtime_review.get('current_source_commit_sha')}`\n"
            "- runtime-affecting changes since runtime subject:\n"
            f"{runtime_delta_lines}\n"
            f"- required next step: `{runtime_review.get('required_next_step')}`\n"
            f"- does not close B1 gate: `{str(runtime_review.get('does_not_close_b1_gate')).lower()}`"
        )
    issue_closure_lines = "- none"
    if isinstance(issue_closure, dict):
        evidence_refs = issue_closure.get("evidence_refs")
        residual_caveats = issue_closure.get("residual_caveats")
        linked_prs = issue_closure.get("linked_prs")
        evidence_ref_lines = (
            "\n".join(f"- `{item}`" for item in evidence_refs)
            if isinstance(evidence_refs, list)
            else "- none"
        )
        residual_caveat_lines = (
            "\n".join(f"- `{item}`" for item in residual_caveats)
            if isinstance(residual_caveats, list)
            else "- none"
        )
        linked_pr_lines = (
            "\n".join(f"- `{item.get('url')}`" for item in linked_prs if isinstance(item, dict))
            if isinstance(linked_prs, list)
            else "- none"
        )
        issue_closure_lines = (
            f"- status: `{issue_closure.get('status')}`\n"
            f"- path: `{issue_closure.get('path')}`\n"
            f"- closed at: `{issue_closure.get('closed_at')}`\n"
            "- linked PRs:\n"
            f"{linked_pr_lines}\n"
            "- evidence refs:\n"
            f"{evidence_ref_lines}\n"
            "- residual caveats:\n"
            f"{residual_caveat_lines}\n"
            f"- does not close B1 gate: `{str(issue_closure.get('does_not_close_b1_gate')).lower()}`"
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
        "## Closed Gate Boundary Gaps\n\n"
        f"{closed_gate_boundary_gaps}\n\n"
        "## Gate Boundary Evidence\n\n"
        "### B1 Memory Export Boundary\n\n"
        f"{export_boundary_lines}\n\n"
        "### B1 Rollback Boundary\n\n"
        f"{rollback_boundary_lines}\n\n"
        "### B1 Runtime Evidence Review Against Merged Source\n\n"
        f"{runtime_review_lines}\n\n"
        "### B1 Issue Closure Evidence\n\n"
        f"{issue_closure_lines}\n\n"
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

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.memory_erasure_readiness import build_memory_erasure_readiness
from app.office_context_readiness import build_office_context_readiness


SCHEMA_VERSION = "ai-platform.b1-memory-context-readiness.v1"
BACKEND_STAGE = "B1 memory/context usable"
ISSUE = "#75"
RUNTIME_ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"

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
    """Build the B1 memory/context readiness rollup without claiming 211 smoke."""
    root = repo_root or Path(__file__).resolve().parents[1]
    memory_erasure = build_memory_erasure_readiness(repo_root=root)
    office_context = build_office_context_readiness(repo_root=root)
    status = _status_for_local_controls(memory_erasure, office_context)
    runtime_acceptance = {
        "required": True,
        "status": "missing_211_memory_enabled_document_workflow_smoke",
        "acceptance_gap": RUNTIME_ACCEPTANCE_GAP,
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
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_stage": BACKEND_STAGE,
        "issue": ISSUE,
        "status": status,
        "status_label": "local partial",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "ordinary_user_policy": "session_scoped_memory_with_public_provenance",
        "implemented_controls": list(_IMPLEMENTED_CONTROLS),
        "local_evidence": {
            "memory_erasure_readiness": _memory_erasure_summary(memory_erasure),
            "office_context_readiness": _office_context_summary(office_context),
        },
        "runtime_acceptance": runtime_acceptance,
        "open_gaps": [RUNTIME_ACCEPTANCE_GAP],
        "closed_runtime_gaps": list(memory_erasure.get("closed_runtime_gaps", [])),
        "non_expansion_invariants": dict(_NON_EXPANSION_INVARIANTS),
        "evidence_policy": (
            "B1 local controls are readiness-rollup evidence only. B1 target "
            "runtime acceptance requires a selected memory-enabled document "
            "workflow smoke on 211 with provenance and no private projection leaks."
        ),
    }


def render_b1_memory_context_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B1 memory/context readiness as gap-first operator Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {control}" for control in readiness["implemented_controls"])
    runtime = readiness["runtime_acceptance"]
    runtime_evidence = "\n".join(f"- {item}" for item in runtime["required_evidence"])
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
        f"Acceptance gap: `{runtime['acceptance_gap']}`\n\n"
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

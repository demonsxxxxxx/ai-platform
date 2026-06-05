import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import repositories
from app.artifact_preview import artifact_preview_allowed, artifact_preview_url
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capabilities import get_capability
from app.context_builder import record_initial_context_snapshot
from app.db import transaction
from app.models import (
    CreateRunRequest,
    CreateRunResponse,
    MultiAgentDispatchClaimRequest,
    MultiAgentDispatchClaimResponse,
    MultiAgentDispatchHandoffResponse,
    MultiAgentDispatchTickResponse,
    QueueRunPayload,
    RunControlResponse,
    RunResponse,
)
from app.product_events import initial_run_event_specs
from app.control_plane_contracts import (
    ARTIFACT_LINEAGE_ID_PREFIXES,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    FORBIDDEN_PUBLIC_KEY_ALIASES,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    HASH_LIKE_VALUE_PATTERN,
    RUN_CONTRACT_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import (
    CAPABILITY_BY_AGENT_ID,
    CAPABILITY_BY_SKILL_ID,
    PUBLIC_AGENT_ID_BY_CAPABILITY,
    capability_id_from_skill,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
)
from app.queue import enqueue_run, get_queue_insight, get_run_queue_position, remove_queued_run
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, stop_sandbox_leases
from app.runtime.sandbox.container_provider import create_container_provider
from app.settings import get_settings
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision
from app.skills.registry import BuiltinSkillRegistry
from app.tool_permission_projection import tool_permission_public_event_payload
from app.validation import assert_safe_id

router = APIRouter()
RUN_PLAYBACK_CONTRACT_VERSION = "ai-platform.run-playback.v1"
RUN_PROVENANCE_CONTRACT_VERSION = "ai-platform.run-provenance.v1"
RUN_CONTROL_READINESS_CONTRACT_VERSION = "ai-platform.run-control-readiness.v1"
RUN_RESUME_MANIFEST_CONTRACT_VERSION = "ai-platform.run-resume-manifest.v1"
RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION = "ai-platform.run-checkpoint-audit.v1"
MULTI_AGENT_DISPATCH_CLAIM_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-claim.v1"
MULTI_AGENT_DISPATCH_HANDOFF_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-handoff.v1"
MULTI_AGENT_DISPATCH_TICK_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-tick.v1"
RUN_CONTROL_ACTIVE_STATUSES = {"queued", "running"}
RUN_CONTROL_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RUN_CONTROL_RETRY_PREVIEW_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}
RUN_CONTROL_PUBLIC_AGENT_IDS = set(PUBLIC_AGENT_ID_BY_CAPABILITY.values())
RUN_CONTROL_RAW_PROJECTION_TERMS = {
    *CAPABILITY_BY_SKILL_ID.keys(),
    *(set(CAPABILITY_BY_AGENT_ID.keys()) - RUN_CONTROL_PUBLIC_AGENT_IDS),
}


def _lease_ids_by_run_id(leases: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for lease in leases:
        run_id = str(lease.get("run_id") or "").strip()
        lease_id = str(lease.get("id") or "").strip()
        if not run_id or not lease_id:
            continue
        grouped.setdefault(run_id, []).append(lease_id)
    return grouped


async def _release_stopped_cancel_leases(
    conn,
    *,
    tenant_id: str,
    leases: list[dict[str, Any]],
    reason: str,
    trace_id: str | None,
) -> None:
    for lease_run_id, lease_ids in _lease_ids_by_run_id(leases).items():
        await repositories.release_stopped_sandbox_leases_for_cancel(
            conn,
            tenant_id=tenant_id,
            run_id=lease_run_id,
            reason=reason,
            lease_ids=lease_ids,
            trace_id=trace_id,
        )


async def _remove_cancelled_queue_payloads(
    *,
    tenant_id: str,
    parent_run_id: str,
    result: dict[str, Any],
) -> list[Exception]:
    failures: list[Exception] = []
    run_ids: list[str] = []
    if result["status"] == "cancelled":
        run_ids.append(parent_run_id)
    run_ids.extend(str(child_run_id) for child_run_id in result.get("queued_child_run_ids") or [])
    for queued_run_id in run_ids:
        try:
            await remove_queued_run(tenant_id=tenant_id, run_id=queued_run_id)
        except Exception as exc:
            failures.append(exc)
    return failures


def _skill_manifest_pins(skill_id: str, input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        return build_skill_manifest_pins(
            skill_id=skill_id,
            input_payload=input_payload,
            builtin_skills=BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills(),
        )
    except ValueError as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc


def _available_builtin_skill_ids_for_policy() -> set[str]:
    settings = get_settings()
    try:
        return {skill.name for skill in BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills()}
    except ValueError as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc


def _validate_queue_payload_for_enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return QueueRunPayload.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="queue_payload_invalid") from exc


async def _governed_skill_manifest_pins(
    conn,
    *,
    skill_id: str,
    input_payload: dict[str, Any],
    release_policy_version: object | None,
) -> list[dict[str, Any]]:
    policy_version = str(release_policy_version or "")
    if policy_version:
        version = await repositories.get_effective_skill_version_for_policy(
            conn,
            skill_id=skill_id,
            version=policy_version,
        )
        if version is None:
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        return build_skill_version_policy_manifest_pins(
            version,
            available_skill_ids=_available_builtin_skill_ids_for_policy(),
        )
    try:
        skill_manifests = _skill_manifest_pins(skill_id, input_payload)
    except SkillVersionMaterializationError:
        raise
    return skill_manifests


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _release_decision_event_payload(release_decision: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    return {
        **release_decision,
        "skill_id": skill_id,
        "skill_version": release_decision.get("selected_version"),
        "visible_to_user": False,
    }


def sse(event: str, data: dict[str, object], event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    payload = json.dumps(data, ensure_ascii=False, default=_json_default)
    return f"{prefix}event: {event}\ndata: {payload}\n\n"


def normalize_run_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


def normalize_step_status(status: object) -> str:
    return normalize_run_status(str(status or ""))


def progress_for_status(status: str) -> int:
    status = normalize_run_status(status)
    if status == "queued":
        return 10
    if status == "running":
        return 55
    if status in {"succeeded", "failed", "cancelled"}:
        return 100
    return 0


def artifact_download_url(artifact_id: str) -> str:
    return f"/api/ai/artifacts/{artifact_id}/download"


def public_text_or_fallback(value: object, fallback: object = "") -> str:
    text = sanitize_public_text(value)
    if text:
        return text
    fallback_text = sanitize_public_text(fallback)
    return fallback_text or ""


def _readiness_raw_projection_terms(run: dict[str, object]) -> set[str]:
    terms = {term.lower() for term in RUN_CONTROL_RAW_PROJECTION_TERMS if term}
    raw_skill_id = str(run.get("skill_id") or "")
    if raw_skill_id:
        terms.add(raw_skill_id.lower())
    raw_agent_id = str(run.get("agent_id") or "")
    public_agent_id = public_agent_id_for_projection(raw_agent_id, raw_skill_id)
    if raw_agent_id and raw_agent_id != public_agent_id:
        terms.add(raw_agent_id.lower())
    return terms


def _contains_raw_projection_term(text: str, raw_terms: set[str]) -> bool:
    normalized = text.lower()
    return any(term and term in normalized for term in raw_terms)


def _readiness_public_text(value: object, *, fallback: object = "", raw_terms: set[str]) -> str:
    text = public_text_or_fallback(value)
    if text and not _contains_raw_projection_term(text, raw_terms):
        return text
    fallback_text = public_text_or_fallback(fallback)
    if fallback_text and not _contains_raw_projection_term(fallback_text, raw_terms):
        return fallback_text
    return ""


def artifact_card(row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    artifact_id = str(row["id"])
    artifact_type = str(row["artifact_type"])
    content_type = str(row.get("content_type") or "application/octet-stream")
    manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
    if principal is not None and not is_ai_admin(principal):
        manifest = redact_raw_skill_references(manifest)
        label = public_text_or_fallback(row.get("label"), artifact_type)
    else:
        label = public_text_or_fallback(row.get("label"), artifact_type)
    return {
        "id": artifact_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "label": label,
        "content_type": content_type,
        "size_bytes": int(row.get("size_bytes") or 0),
        "download_url": artifact_download_url(artifact_id),
        "preview_url": artifact_preview_url(artifact_id) if artifact_preview_allowed(content_type) else None,
        "status": "available",
        "lineage": artifact_lineage_contract(manifest, row=row),
        "manifest": artifact_manifest_contract(
            artifact_type=artifact_type,
            manifest=manifest,
            schema_version=str(row.get("manifest_version") or ARTIFACT_MANIFEST_SCHEMA_VERSION),
        ),
        "created_at": row.get("created_at"),
    }


PUBLIC_EVENT_TYPE_ALIASES = {
    "legacy_runtime211_direct_executor_denied": "status",
    "mcp_tool_call_completed": "tool_call_completed",
    "mcp_tool_call_started": "tool_call_started",
    "mcp_tool_denied": "tool_denied",
    "skill_selected": "capability_selected",
    "tool_permission_decided": "tool_permission_card",
    "tool_permission_requested": "tool_permission_card",
    "worker_started": "run_started",
}

PUBLIC_STAGE_ALIASES = {
    "download": "artifact",
    "executor": "agent",
    "skills": "capability",
    "tool_policy": "policy",
    "upload": "artifact",
    "worker": "status",
}


def public_event_type(value: object, principal: AuthPrincipal | None = None) -> str:
    raw = str(value or "status")
    if principal is None or is_ai_admin(principal):
        return raw
    public_value = PUBLIC_EVENT_TYPE_ALIASES.get(raw, raw)
    sanitized = sanitize_public_text(public_value)
    return sanitized or "status"


def public_event_stage(value: object, principal: AuthPrincipal | None = None) -> str:
    raw = str(value or "")
    if principal is None or is_ai_admin(principal):
        return raw
    public_value = PUBLIC_STAGE_ALIASES.get(raw, raw)
    sanitized = sanitize_public_text(public_value)
    return sanitized or "status"


def required_schema_version(row: dict[str, object], field: str, expected: str, detail: str) -> str:
    value = row.get(field)
    if value != expected:
        raise HTTPException(status_code=500, detail=detail)
    return str(value)


def run_contract_version(run: dict[str, object]) -> str:
    return required_schema_version(run, "schema_version", RUN_CONTRACT_VERSION, "invalid_run_contract")


def executor_result_schema_version(run: dict[str, object]) -> str:
    return required_schema_version(
        run,
        "executor_schema_version",
        EXECUTOR_RESULT_SCHEMA_VERSION,
        "invalid_executor_result_schema_version",
    )


def run_event_response(run_id: str, row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    raw_event_type = str(row["event_type"])
    payload = row.get("payload_json") or {}
    if not isinstance(payload, dict):
        payload = {}
    payload = sanitize_public_payload(payload)
    if not isinstance(payload, dict):
        payload = {}
    if principal is not None and not is_ai_admin(principal):
        payload = redact_raw_skill_references(payload)
        if raw_event_type in {"tool_permission_requested", "tool_permission_decided"}:
            payload = tool_permission_public_event_payload(
                run_id=run_id,
                event_type=raw_event_type,
                payload=payload,
            )
    severity = str(row.get("severity") or payload.get("severity") or ("error" if row.get("event_type") == "error" else "info"))
    if severity not in {"info", "warning", "error"}:
        severity = "info"
    event_type = public_event_type(raw_event_type, principal)
    stage = public_event_stage(row.get("stage"), principal)
    visible_to_user = bool(row.get("visible_to_user", payload.get("visible_to_user", True)))
    message = sanitize_public_text(row.get("message"))
    sanitized_error_code = sanitize_public_text(row.get("error_code"))
    error_code = sanitized_error_code or (None if not row.get("error_code") else "run_failed")
    if principal is not None and not is_ai_admin(principal):
        error_code = sanitized_error_code or ("run_failed" if error_code else None)
    return {
        "id": str(row["id"]),
        "schema_version": required_schema_version(
            row,
            "schema_version",
            EVENT_ENVELOPE_SCHEMA_VERSION,
            "invalid_event_schema_version",
        ),
        "event_id": str(row["id"]),
        "sequence": int(row.get("sequence") or 0),
        "run_id": run_id,
        "trace_id": str(row.get("trace_id") or standard_trace_id(run_id)),
        "event_type": event_type,
        "type": event_type,
        "stage": stage,
        "message": message,
        "severity": severity,
        "visible_to_user": visible_to_user,
        "error_code": error_code,
        "latency_ms": row.get("latency_ms"),
        "token_counts": {
            "input": int(row.get("input_token_count") or 0),
            "output": int(row.get("output_token_count") or 0),
            "total": int(row.get("total_token_count") or 0),
        },
        "cost": {"estimated_cost_minor": int(row.get("estimated_cost_minor") or 0)},
        "payload": payload,
        "created_at": row.get("created_at"),
    }


def run_step_response(row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    raw_payload = row.get("payload_json") or {}
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    payload = sanitize_public_payload(raw_payload)
    if not isinstance(payload, dict):
        payload = {}
    if principal is not None and not is_ai_admin(principal):
        payload = sanitize_user_control_input(raw_payload)
    show_runtime_controls = principal is None or is_ai_admin(principal)
    skill_ids = raw_payload.get("skill_ids")
    mcp_tool_ids = raw_payload.get("mcp_tool_ids")
    resource_limits = raw_payload.get("resource_limits")
    sandbox_mode = raw_payload.get("sandbox_mode")
    browser_enabled = raw_payload.get("browser_enabled")
    title = sanitize_public_text(row.get("title"))
    role = sanitize_public_text(row.get("role")) if row.get("role") is not None else None
    if principal is not None and not is_ai_admin(principal):
        title = public_text_or_fallback(title, row.get("step_key")) or "step"
        if role is not None:
            role = public_text_or_fallback(role) or None

    response = {
        "id": str(row["id"]),
        "step_id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "step_key": str(row["step_key"]),
        "step_kind": str(row["step_kind"]),
        "status": normalize_step_status(row["status"]),
        "title": title,
        "role": role,
        "sequence": int(row.get("sequence") or 0),
        "payload": payload,
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if show_runtime_controls:
        response["skill_ids"] = [str(item) for item in skill_ids] if isinstance(skill_ids, list) else []
        response["mcp_tool_ids"] = [str(item) for item in mcp_tool_ids] if isinstance(mcp_tool_ids, list) else []
        response["resource_limits"] = dict(resource_limits) if isinstance(resource_limits, dict) else {}
        response["sandbox_mode"] = str(sandbox_mode) if sandbox_mode is not None else None
        response["browser_enabled"] = browser_enabled if isinstance(browser_enabled, bool) else None
    return response


def multi_agent_snapshot_from_steps(
    run_id: str,
    rows: list[dict[str, object]],
    principal: AuthPrincipal | None = None,
) -> dict[str, object] | None:
    steps = [run_step_response(row, principal=principal) for row in rows]
    if not steps:
        return None
    counts = {
        "total": len(steps),
        "pending": sum(1 for item in steps if item["status"] == "pending"),
        "succeeded": sum(1 for item in steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in steps if item["status"] == "failed"),
        "running": sum(1 for item in steps if item["status"] == "running"),
        "cancelled": sum(1 for item in steps if item["status"] == "cancelled"),
        "reused": sum(1 for item in steps if bool(item["payload"].get("checkpoint_reused"))),
        "blocked": sum(1 for item in steps if bool(item["payload"].get("missing_dependencies"))),
    }
    return {"run_id": run_id, "steps": steps, "counts": counts}


def _unique_sorted(values: list[object]) -> list[str]:
    return sorted({str(item) for item in values if item})


def _safe_provenance_graph_id(field_name: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    sanitized = sanitize_public_text(raw)
    if not sanitized or sanitized != raw:
        return None
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized):
        return None
    try:
        safe_id = assert_safe_id(sanitized, field_name)
    except ValueError:
        return None
    normalized = safe_id.lower()
    prefixes = ARTIFACT_LINEAGE_ID_PREFIXES.get(field_name, ())
    if not any(normalized == prefix or normalized.startswith(f"{prefix}-") or normalized.startswith(f"{prefix}_") for prefix in prefixes):
        return None
    return safe_id


def _checkpoint_audit_safe_checkpoint_id(
    value: object,
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str | None:
    checkpoint_id = _safe_provenance_graph_id("checkpoint_id", value)
    if checkpoint_id is None:
        return None
    if not is_ai_admin(principal) and _contains_raw_projection_term(checkpoint_id, raw_terms):
        return None
    return checkpoint_id


def _provenance_step_card(row: dict[str, object], principal: AuthPrincipal) -> dict[str, object]:
    card = run_step_response(row, principal=principal)
    raw_payload = card.get("payload")
    if not isinstance(raw_payload, dict):
        return card
    payload = dict(raw_payload)
    checkpoint_id = _safe_provenance_graph_id("checkpoint_id", payload.get("checkpoint_id"))
    subagent_id = _safe_provenance_graph_id("subagent_id", payload.get("subagent_id"))
    if checkpoint_id:
        payload["checkpoint_id"] = checkpoint_id
        payload["checkpoint_reused"] = bool(payload.get("checkpoint_reused"))
    else:
        payload.pop("checkpoint_id", None)
        payload.pop("checkpoint_reused", None)
    if subagent_id:
        payload["subagent_id"] = subagent_id
    else:
        payload.pop("subagent_id", None)
    card["payload"] = payload
    return card


def _add_provenance_edge(
    edges: list[dict[str, str]],
    seen_edges: set[tuple[str, str, str]],
    *,
    source_id: object,
    target_id: object,
    edge_kind: str,
) -> None:
    source = str(source_id) if source_id else ""
    target = str(target_id) if target_id else ""
    if not source or not target:
        return
    key = (source, target, edge_kind)
    if key in seen_edges:
        return
    seen_edges.add(key)
    edges.append({"source_id": source, "target_id": target, "edge_kind": edge_kind})


def _artifact_raw_lineage_value(row: dict[str, object], key: str) -> object:
    if key in row:
        return row.get(key)
    manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
    return manifest.get(key)


def _artifact_graph_id_from_row_or_lineage(
    *,
    field_name: str,
    row: dict[str, object],
    lineage: dict[str, object],
    unsafe_gap: str,
    missing_gap: str | None = None,
) -> tuple[str | None, list[str]]:
    raw_value = _artifact_raw_lineage_value(row, field_name)
    candidate = raw_value if raw_value is not None else lineage.get(field_name)
    if candidate is None:
        return None, [missing_gap] if missing_gap else []
    graph_id = _safe_provenance_graph_id(field_name, candidate)
    if graph_id is None:
        return None, [unsafe_gap]
    return graph_id, []


def _artifact_tree_source_step(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
    step_by_id: dict[str, dict[str, object]],
) -> tuple[str | None, list[str]]:
    source_step_id, gaps = _artifact_graph_id_from_row_or_lineage(
        field_name="source_step_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_source_step_unsafe",
        missing_gap="artifact_source_step_missing",
    )
    if source_step_id and source_step_id not in step_by_id:
        gaps.append("producer_step_missing")
    return source_step_id, sorted(set(gaps))


def _artifact_tree_checkpoint(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
) -> tuple[str | None, list[str]]:
    return _artifact_graph_id_from_row_or_lineage(
        field_name="checkpoint_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_checkpoint_unsafe",
    )


def _artifact_tree_subagent(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
) -> tuple[str | None, list[str]]:
    return _artifact_graph_id_from_row_or_lineage(
        field_name="subagent_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_subagent_unsafe",
    )


def _artifact_tree_lineage(
    lineage: dict[str, object],
    *,
    source_step_id: str | None,
    checkpoint_id: str | None,
    subagent_id: str | None,
) -> dict[str, object]:
    projected = artifact_lineage_contract(lineage)
    projected.pop("source_step_id", None)
    projected.pop("checkpoint_id", None)
    projected.pop("subagent_id", None)
    if source_step_id:
        projected["source_step_id"] = source_step_id
    if checkpoint_id:
        projected["checkpoint_id"] = checkpoint_id
    if subagent_id:
        projected["subagent_id"] = subagent_id
    return projected


def _artifact_tree_parent(
    *,
    produced_by_step_id: str | None,
    checkpoint_id: object,
    subagent_id: object,
) -> tuple[str | None, str | None]:
    if produced_by_step_id:
        return produced_by_step_id, "step"
    if checkpoint_id:
        return str(checkpoint_id), "checkpoint"
    if subagent_id:
        return str(subagent_id), "subagent"
    return None, None


def run_provenance_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    """Build the public run provenance graph from existing sanitized projections."""
    step_cards = [_provenance_step_card(row, principal=principal) for row in steps]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_by_id = {str(item["step_id"]): item for item in step_cards}
    artifacts_by_checkpoint: dict[str, list[str]] = {}
    artifacts_by_subagent: dict[str, list[str]] = {}
    checkpoints: dict[str, dict[str, object]] = {}
    subagents: dict[str, dict[str, object]] = {}
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    artifact_tree: list[dict[str, object]] = []
    graph_gaps: list[dict[str, object]] = []
    for step in step_cards:
        raw_payload = step.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        step_id = str(step["step_id"])
        checkpoint_id = payload.get("checkpoint_id")
        subagent_id = payload.get("subagent_id")
        if checkpoint_id:
            checkpoint_key = str(checkpoint_id)
            checkpoint = checkpoints.setdefault(
                checkpoint_key,
                {"checkpoint_id": checkpoint_key, "step_ids": [], "artifact_ids": [], "reused": False},
            )
            checkpoint["step_ids"].append(step_id)
            checkpoint["reused"] = bool(checkpoint["reused"]) or bool(payload.get("checkpoint_reused"))
        if subagent_id:
            subagent_key = str(subagent_id)
            subagent = subagents.setdefault(
                subagent_key,
                {
                    "subagent_id": subagent_key,
                    "role": step.get("role"),
                    "step_ids": [],
                    "statuses": [],
                    "checkpoint_ids": [],
                    "artifact_ids": [],
                },
            )
            subagent["step_ids"].append(step_id)
            subagent["statuses"].append(step.get("status"))
            if checkpoint_id:
                subagent["checkpoint_ids"].append(str(checkpoint_id))
        if checkpoint_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=step_id,
                target_id=checkpoint_id,
                edge_kind="step_checkpoint",
            )
        if subagent_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=subagent_id,
                target_id=step_id,
                edge_kind="subagent_step",
            )

    for row, artifact in zip(artifacts, artifact_cards):
        raw_lineage = artifact.get("lineage")
        lineage = raw_lineage if isinstance(raw_lineage, dict) else {}
        source_step_id, source_gaps = _artifact_tree_source_step(row=row, lineage=lineage, step_by_id=step_by_id)
        checkpoint_id, checkpoint_gaps = _artifact_tree_checkpoint(row=row, lineage=lineage)
        subagent_id, subagent_gaps = _artifact_tree_subagent(row=row, lineage=lineage)
        gaps = sorted(set(source_gaps + checkpoint_gaps + subagent_gaps))
        public_lineage = _artifact_tree_lineage(
            lineage,
            source_step_id=source_step_id,
            checkpoint_id=checkpoint_id,
            subagent_id=subagent_id,
        )
        artifact_id = str(artifact["artifact_id"])
        if checkpoint_id:
            artifacts_by_checkpoint.setdefault(checkpoint_id, []).append(artifact_id)
        if subagent_id:
            artifacts_by_subagent.setdefault(subagent_id, []).append(artifact_id)
        produced_by_step_id = source_step_id if source_step_id in step_by_id else None
        parent_id, parent_kind = _artifact_tree_parent(
            produced_by_step_id=produced_by_step_id,
            checkpoint_id=checkpoint_id,
            subagent_id=subagent_id,
        )
        if produced_by_step_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=produced_by_step_id,
                target_id=artifact_id,
                edge_kind="produced_artifact",
            )
        if checkpoint_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=checkpoint_id,
                target_id=artifact_id,
                edge_kind="checkpoint_artifact",
            )
        if subagent_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=subagent_id,
                target_id=artifact_id,
                edge_kind="subagent_artifact",
            )
        if gaps:
            graph_gaps.append({"node_id": artifact_id, "node_kind": "artifact", "gaps": gaps})
        artifact_tree.append(
            {
                "node_id": artifact_id,
                "node_kind": "artifact",
                "artifact_id": artifact_id,
                "artifact_type": artifact.get("artifact_type"),
                "label": artifact.get("label"),
                "produced_by_step_id": produced_by_step_id,
                "source_step_id": source_step_id,
                "parent_id": parent_id,
                "parent_kind": parent_kind,
                "children_ids": [],
                "producer_kind": public_lineage.get("producer_kind"),
                "producer_role": public_lineage.get("producer_role"),
                "checkpoint_id": checkpoint_id,
                "subagent_id": subagent_id,
                "lineage": public_lineage,
                "gaps": gaps,
            }
        )

    for checkpoint_id, artifact_ids in artifacts_by_checkpoint.items():
        checkpoint = checkpoints.setdefault(
            checkpoint_id,
            {"checkpoint_id": checkpoint_id, "step_ids": [], "artifact_ids": [], "reused": False},
        )
        checkpoint["artifact_ids"].extend(artifact_ids)
    for subagent_id, artifact_ids in artifacts_by_subagent.items():
        subagent = subagents.setdefault(
            subagent_id,
            {
                "subagent_id": subagent_id,
                "role": None,
                "step_ids": [],
                "statuses": [],
                "checkpoint_ids": [],
                "artifact_ids": [],
            },
        )
        subagent["artifact_ids"].extend(artifact_ids)

    checkpoint_items = [
        {
            "checkpoint_id": str(item["checkpoint_id"]),
            "step_ids": _unique_sorted(item["step_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
            "reused": bool(item["reused"]),
        }
        for item in checkpoints.values()
    ]
    subagent_items = [
        {
            "subagent_id": str(item["subagent_id"]),
            "role": item.get("role"),
            "step_ids": _unique_sorted(item["step_ids"]),
            "statuses": _unique_sorted(item["statuses"]),
            "checkpoint_ids": _unique_sorted(item["checkpoint_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
        }
        for item in subagents.values()
    ]
    return {
        "contract_version": RUN_PROVENANCE_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
        "steps": step_cards,
        "artifact_tree": artifact_tree,
        "checkpoints": sorted(checkpoint_items, key=lambda item: item["checkpoint_id"]),
        "subagents": sorted(subagent_items, key=lambda item: item["subagent_id"]),
        "graph": {
            "counts": {
                "steps": len(step_cards),
                "artifacts": len(artifact_cards),
                "checkpoints": len(checkpoint_items),
                "subagents": len(subagent_items),
            },
            "edges": edges,
            "gaps": graph_gaps,
        },
    }


def _control_action(*, enabled: bool, reason: str, method: str | None, href: str | None) -> dict[str, object]:
    return {"enabled": enabled, "reason": reason, "method": method, "href": href}


def _checkpoint_candidate_from_step(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> dict[str, object] | None:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    status = normalize_step_status(row.get("status"))
    if status != "succeeded" or payload.get("output") is None:
        return None
    public_step = run_step_response(row, principal=principal)
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    title = public_step.get("title")
    role = public_step.get("role")
    if not is_ai_admin(principal):
        step_key = _readiness_public_text(step_key, fallback=step_id, raw_terms=raw_terms) or step_id
        title = _readiness_public_text(title, fallback=step_key, raw_terms=raw_terms) or step_key
        if role is not None:
            role = _readiness_public_text(role, raw_terms=raw_terms) or None
    return {
        "step_id": step_id,
        "step_key": step_key,
        "status": str(public_step["status"]),
        "title": title,
        "role": role,
        "sequence": int(public_step.get("sequence") or 0),
        "reusable": True,
        "reason": "output_available",
    }


def _run_execution_input(run: dict[str, object]) -> dict[str, object]:
    source_input = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    return execution_input if isinstance(execution_input, dict) else {}


def _configured_multi_agent_steps(run: dict[str, object]) -> list[dict[str, object]]:
    execution_input = _run_execution_input(run)
    configured = execution_input.get("multi_agent_steps")
    if not isinstance(configured, list):
        return []
    return [dict(item) for item in configured if isinstance(item, dict) and (item.get("step_key") or item.get("stepKey"))]


def _raw_depends_on(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    depends_on: list[str] = []
    for value in values:
        dependency = str(value).strip() if value is not None else ""
        if dependency and dependency not in depends_on:
            depends_on.append(dependency)
    return depends_on


def _projected_dependency(value: str, *, raw_terms: set[str], principal: AuthPrincipal) -> dict[str, str | None]:
    if is_ai_admin(principal):
        return {"step_key": public_text_or_fallback(value, value), "reason": None}
    projected = _readiness_public_text(value, raw_terms=raw_terms)
    if projected:
        return {"step_key": projected, "reason": None}
    return {"step_key": None, "reason": "unsafe_dependency"}


def _multi_agent_step_public_text(
    value: object,
    *,
    fallback: object,
    raw_terms: set[str],
    principal: AuthPrincipal,
) -> str:
    if is_ai_admin(principal):
        return public_text_or_fallback(value, fallback)
    return _readiness_public_text(value, fallback=fallback, raw_terms=raw_terms)


def _dependency_statuses(
    depends_on: list[str],
    status_by_key: dict[str, str],
    *,
    raw_terms: set[str],
    principal: AuthPrincipal,
) -> list[dict[str, str | None]]:
    statuses: list[dict[str, str | None]] = []
    for dependency in depends_on:
        projected = _projected_dependency(dependency, raw_terms=raw_terms, principal=principal)
        if projected["reason"] == "unsafe_dependency":
            statuses.append({"step_key": None, "status": "hidden", "reason": "unsafe_dependency"})
            continue
        statuses.append({"step_key": projected["step_key"], "status": status_by_key.get(dependency, "missing")})
    return statuses


def _multi_agent_blocked_reason(status: str, dependency_statuses: list[dict[str, str | None]]) -> str | None:
    if status in RUN_CONTROL_TERMINAL_STATUSES:
        return "terminal_step"
    if status == "running":
        return "already_running"
    if any(item["status"] == "hidden" for item in dependency_statuses):
        return "hidden_dependencies"
    if any(item["status"] == "missing" for item in dependency_statuses):
        return "missing_dependencies"
    if any(item["status"] != "succeeded" for item in dependency_statuses):
        return "waiting_on_dependencies"
    return None


def multi_agent_readiness_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object] | None:
    """Return public dependency readiness without opening autonomous dispatch."""
    configured_steps = _configured_multi_agent_steps(run)
    execution_input = _run_execution_input(run)
    execution_mode = str(execution_input.get("execution_mode") or "")
    if execution_mode != "multi_agent":
        return None
    public_execution_mode = "multi_agent"
    run_status = normalize_run_status(str(run.get("status") or ""))
    raw_terms = _readiness_raw_projection_terms(run)
    configured_by_key = {
        str(item.get("step_key") or item.get("stepKey")): item
        for item in configured_steps
    }
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    ordered_keys = list(configured_by_key)
    for key, row in sorted(recorded_by_key.items(), key=lambda item: int(item[1].get("sequence") or 0)):
        if key not in configured_by_key:
            ordered_keys.append(key)

    status_by_key = {key: normalize_step_status(row.get("status")) for key, row in recorded_by_key.items()}
    readiness_steps: list[dict[str, object]] = []
    safe_ready_count = 0
    for index, step_key in enumerate(ordered_keys, start=1):
        configured = configured_by_key.get(step_key, {})
        row = recorded_by_key.get(step_key)
        if row is not None:
            public_step = run_step_response(row, principal=principal)
            payload = public_step.get("payload") if isinstance(public_step.get("payload"), dict) else {}
            status = str(public_step["status"])
            step_id = str(public_step["step_id"])
            sequence = int(public_step.get("sequence") or index)
            public_step_key = _multi_agent_step_public_text(
                step_key,
                fallback=step_id,
                raw_terms=raw_terms,
                principal=principal,
            ) or step_id
            title_fallback = public_step_key
            role_fallback = ""
            title = _multi_agent_step_public_text(
                public_step.get("title"),
                fallback=title_fallback,
                raw_terms=raw_terms,
                principal=principal,
            ) or public_step_key
            role = _multi_agent_step_public_text(
                public_step.get("role"),
                fallback=role_fallback,
                raw_terms=raw_terms,
                principal=principal,
            ) or None
            raw_depends_on = _raw_depends_on(
                payload.get("depends_on") or configured.get("depends_on") or configured.get("dependsOn"),
            )
            source = "recorded"
        else:
            status = "pending"
            step_id = None
            sequence = index
            public_step_key = _multi_agent_step_public_text(
                step_key,
                fallback=f"step-{index}",
                raw_terms=raw_terms,
                principal=principal,
            ) or f"step-{index}"
            title = _multi_agent_step_public_text(
                configured.get("title"),
                fallback=public_step_key,
                raw_terms=raw_terms,
                principal=principal,
            ) or public_step_key
            role = _multi_agent_step_public_text(
                configured.get("role"),
                fallback="",
                raw_terms=raw_terms,
                principal=principal,
            ) or None
            raw_depends_on = _raw_depends_on(
                configured.get("depends_on") or configured.get("dependsOn"),
            )
            source = "configured"

        dependency_statuses = _dependency_statuses(
            raw_depends_on,
            status_by_key,
            raw_terms=raw_terms,
            principal=principal,
        )
        projected_depends_on = [str(item["step_key"]) for item in dependency_statuses if item.get("step_key")]
        blocked_reason = _multi_agent_blocked_reason(status, dependency_statuses)
        ready = status == "pending" and blocked_reason is None
        if (
            ready
            and not _unsafe_dispatch_reference(step_key, raw_terms=raw_terms)
            and not any(_unsafe_dispatch_reference(dependency, raw_terms=raw_terms) for dependency in raw_depends_on)
        ):
            safe_ready_count += 1
        readiness_steps.append(
            {
                "step_key": public_step_key,
                "step_id": step_id,
                "title": title,
                "role": role,
                "sequence": sequence,
                "status": status,
                "depends_on": projected_depends_on,
                "dependency_statuses": dependency_statuses,
                "ready": ready,
                "blocked_reason": blocked_reason,
                "source": source,
            }
        )

    missing_dependencies = sum(
        1
        for item in readiness_steps
        for dependency in item["dependency_statuses"]
        if isinstance(dependency, dict) and dependency.get("status") == "missing"
    )
    hidden_dependencies = sum(
        1
        for item in readiness_steps
        for dependency in item["dependency_statuses"]
        if isinstance(dependency, dict) and dependency.get("status") == "hidden"
    )
    blocked = sum(
        1
        for item in readiness_steps
        if item["status"] == "pending"
        and not item["ready"]
        and item["blocked_reason"] in {"waiting_on_dependencies", "missing_dependencies", "hidden_dependencies"}
    )
    ready_count = sum(1 for item in readiness_steps if item["ready"])
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        dispatch_gate = _control_action(enabled=False, reason="run_not_dispatchable", method=None, href=None)
    elif ready_count <= 0:
        dispatch_gate = _control_action(enabled=False, reason="no_ready_steps", method=None, href=None)
    elif safe_ready_count <= 0:
        dispatch_gate = _control_action(enabled=False, reason="no_safe_ready_steps", method=None, href=None)
    elif is_ai_admin(principal):
        dispatch_gate = _control_action(
            enabled=True,
            reason="ready_steps_available",
            method="POST",
            href=f"/api/ai/runs/{run['id']}/multi-agent/dispatch/claims",
        )
    else:
        dispatch_gate = _control_action(enabled=False, reason="admin_only_dispatch", method=None, href=None)
    return {
        "enabled": True,
        "execution_mode": public_execution_mode,
        "steps": readiness_steps,
        "counts": {
            "configured": len(configured_steps),
            "recorded": len(steps),
            "completed": sum(1 for item in readiness_steps if item["status"] == "succeeded"),
            "ready": ready_count,
            "blocked": blocked,
            "missing_dependencies": missing_dependencies,
            "hidden_dependencies": hidden_dependencies,
        },
        "gates": {"dispatch": dispatch_gate},
    }


def _unsafe_dispatch_reference(value: str, *, raw_terms: set[str]) -> bool:
    raw = str(value or "").strip()
    sanitized = sanitize_public_text(raw)
    if not sanitized or sanitized != raw:
        return True
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized):
        return True
    normalized_key = "".join(ch for ch in sanitized if ch.isalnum()).lower()
    if normalized_key in FORBIDDEN_PUBLIC_KEY_ALIASES:
        return True
    try:
        assert_safe_id(sanitized, "step_key")
    except ValueError:
        return True
    return _contains_raw_projection_term(sanitized, raw_terms)


def _dispatch_claim_sequence(
    *,
    step_key: str,
    row: dict[str, object] | None,
    configured_by_key: dict[str, dict[str, object]],
) -> int:
    if row is not None:
        sequence = int(row.get("sequence") or 0)
        if sequence > 0:
            return sequence
    for index, configured_key in enumerate(configured_by_key, start=1):
        if configured_key == step_key:
            return index
    return len(configured_by_key) + 1


def _dispatch_claim_candidate(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    step_key: str,
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_status = normalize_run_status(str(run.get("status") or ""))
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run_not_dispatchable")
    configured_steps = _configured_multi_agent_steps(run)
    execution_input = _run_execution_input(run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        raise HTTPException(status_code=409, detail="multi_agent_not_enabled")
    raw_terms = _readiness_raw_projection_terms(run)
    if _unsafe_dispatch_reference(step_key, raw_terms=raw_terms):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    configured = configured_by_key.get(step_key)
    row = recorded_by_key.get(step_key)
    if configured is None and row is None:
        raise HTTPException(status_code=409, detail="step_not_found")

    payload = row.get("payload_json") if row is not None and isinstance(row.get("payload_json"), dict) else {}
    depends_on = _raw_depends_on(
        payload.get("depends_on")
        or (configured or {}).get("depends_on")
        or (configured or {}).get("dependsOn")
    )
    if any(_unsafe_dispatch_reference(dependency, raw_terms=raw_terms) for dependency in depends_on):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    status_by_key = {key: normalize_step_status(item.get("status")) for key, item in recorded_by_key.items()}
    dependency_statuses = _dependency_statuses(
        depends_on,
        status_by_key,
        raw_terms=raw_terms,
        principal=principal,
    )
    status = normalize_step_status(row.get("status") if row is not None else "pending")
    blocked_reason = _multi_agent_blocked_reason(status, dependency_statuses)
    if status != "pending" or blocked_reason is not None:
        raise HTTPException(status_code=409, detail=blocked_reason or "step_not_pending")

    sequence = _dispatch_claim_sequence(step_key=step_key, row=row, configured_by_key=configured_by_key)
    title = public_text_or_fallback(
        (row or {}).get("title") or (configured or {}).get("title"),
        step_key,
    ) or step_key
    role_value = (row or {}).get("role") or (configured or {}).get("role")
    role = public_text_or_fallback(role_value) if role_value is not None else None
    return {
        "step_key": step_key,
        "step_kind": str((row or {}).get("step_kind") or "agent"),
        "title": title,
        "role": role,
        "sequence": sequence,
        "depends_on": depends_on,
    }


def _dispatch_tick_candidate(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_status = normalize_run_status(str(run.get("status") or ""))
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run_not_dispatchable")
    if str(_run_execution_input(run).get("execution_mode") or "") != "multi_agent":
        raise HTTPException(status_code=409, detail="multi_agent_not_enabled")

    readiness = multi_agent_readiness_snapshot(run=run, steps=steps, principal=principal)
    counts = readiness.get("counts") if isinstance(readiness, dict) else {}
    if not isinstance(counts, dict) or int(counts.get("ready") or 0) <= 0:
        raise HTTPException(status_code=409, detail="no_ready_steps")

    configured_steps = _configured_multi_agent_steps(run)
    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    ordered_keys = list(configured_by_key)
    for key, row in sorted(recorded_by_key.items(), key=lambda item: int(item[1].get("sequence") or 0)):
        if key not in configured_by_key:
            ordered_keys.append(key)

    for step_key in ordered_keys:
        try:
            return {
                **_dispatch_claim_candidate(
                    run=run,
                    steps=steps,
                    step_key=step_key,
                    principal=principal,
                ),
                "step_key": step_key,
            }
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail in {
                "unsafe_step_reference",
                "step_not_pending",
                "terminal_step",
                "already_running",
                "waiting_on_dependencies",
                "missing_dependencies",
                "hidden_dependencies",
            }:
                continue
            raise
    raise HTTPException(status_code=409, detail="no_safe_ready_steps")


def run_control_readiness_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    queue_insight: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return read-only readiness for platform-controlled run actions."""
    run_id = str(run["id"])
    status = normalize_run_status(str(run["status"]))
    raw_terms = _readiness_raw_projection_terms(run)
    checkpoint_candidates = [
        item
        for item in (_checkpoint_candidate_from_step(row, principal, raw_terms=raw_terms) for row in steps)
        if item is not None
    ]
    cancel_requested = bool(run.get("cancel_requested_at"))
    if cancel_requested:
        cancel_reason = "cancel_already_requested"
    elif status in RUN_CONTROL_ACTIVE_STATUSES:
        cancel_reason = "cancel_available"
    elif status in RUN_CONTROL_TERMINAL_STATUSES:
        cancel_reason = "terminal_run"
    else:
        cancel_reason = "status_not_cancellable"
    cancel_enabled = cancel_reason == "cancel_available"

    if status in RUN_CONTROL_ACTIVE_STATUSES:
        resume_reason = "active_run"
    elif checkpoint_candidates:
        resume_reason = "checkpoint_outputs_available"
    else:
        resume_reason = "no_checkpoint_outputs"
    resume_enabled = resume_reason == "checkpoint_outputs_available"

    retry_enabled = status in RUN_CONTROL_RETRY_PREVIEW_STATUSES
    retry_reason = "retry_available" if retry_enabled else "status_not_retryable"
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = "run_failed" if raw_error_message and status == "failed" else ""
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    return {
        "contract_version": RUN_CONTROL_READINESS_CONTRACT_VERSION,
        "run": run_summary,
        "actions": {
            "cancel": _control_action(
                enabled=cancel_enabled,
                reason=cancel_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/cancel",
            ),
            "resume": _control_action(
                enabled=resume_enabled,
                reason=resume_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/resume",
            ),
            "retry": _control_action(
                enabled=retry_enabled,
                reason=retry_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/retry",
            ),
        },
        "checkpoint_candidates": checkpoint_candidates,
        "queue_insight": queue_insight,
        "multi_agent": multi_agent_readiness_snapshot(run=run, steps=steps, principal=principal),
    }


def _resume_manifest_public_depends_on(values: object, *, raw_terms: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = _resume_manifest_public_text(value, raw_terms=raw_terms)
        if text:
            result.append(text)
    return result


def _resume_manifest_has_fingerprint(text: str) -> bool:
    if HASH_LIKE_VALUE_PATTERN.fullmatch(text.strip()):
        return True
    return any(HASH_LIKE_VALUE_PATTERN.fullmatch(token) for token in re.split(r"[^A-Fa-f0-9]+", text))


def _resume_manifest_public_text(value: object, *, fallback: object = "", raw_terms: set[str]) -> str:
    text = _readiness_public_text(value, raw_terms=raw_terms)
    if text and not _resume_manifest_has_fingerprint(text):
        return text
    fallback_text = _readiness_public_text(fallback, raw_terms=raw_terms)
    if fallback_text and not _resume_manifest_has_fingerprint(fallback_text):
        return fallback_text
    return ""


def _resume_manifest_step(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
    authorized_source_run_ids: set[str],
) -> dict[str, object]:
    public_step = run_step_response(row, principal=principal)
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    title = public_step.get("title")
    role = public_step.get("role")
    source_run_id = (
        _safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
        if payload.get("checkpoint_reuse_pending")
        else None
    )
    if source_run_id and source_run_id not in authorized_source_run_ids:
        source_run_id = None
    depends_on = payload.get("depends_on")
    public_raw_terms = raw_terms if not is_ai_admin(principal) else set()
    step_key = _resume_manifest_public_text(step_key, fallback=step_id, raw_terms=public_raw_terms) or step_id
    title = _resume_manifest_public_text(title, fallback=step_key, raw_terms=public_raw_terms) or step_key
    role = _resume_manifest_public_text(role, raw_terms=public_raw_terms) if role is not None else None
    role = role or None
    depends_on = _resume_manifest_public_depends_on(depends_on, raw_terms=public_raw_terms)
    return {
        "step_id": step_id,
        "step_key": step_key,
        "status": str(public_step["status"]),
        "title": title,
        "role": role,
        "sequence": int(public_step.get("sequence") or 0),
        "depends_on": depends_on,
        "reuse_intent": "reuse_pending" if payload.get("checkpoint_reuse_pending") else "rerun",
        "source_run_id": str(source_run_id) if source_run_id else None,
    }


def run_resume_manifest_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    authorized_source_run_ids: set[str] | None = None,
) -> dict[str, object]:
    """Return read-only checkpoint reuse intent for a copied run."""
    raw_terms = _readiness_raw_projection_terms(run)
    manifest_steps = [
        _resume_manifest_step(
            row,
            principal,
            raw_terms=raw_terms,
            authorized_source_run_ids=authorized_source_run_ids or set(),
        )
        for row in steps
    ]
    source_run_ids = sorted({str(item["source_run_id"]) for item in manifest_steps if item.get("source_run_id")})
    source_run_id = source_run_ids[0] if len(source_run_ids) == 1 else None
    counts = {
        "total": len(manifest_steps),
        "reuse_pending": sum(1 for item in manifest_steps if item["reuse_intent"] == "reuse_pending"),
        "rerun": sum(1 for item in manifest_steps if item["reuse_intent"] == "rerun"),
        "pending": sum(1 for item in manifest_steps if item["status"] == "pending"),
        "running": sum(1 for item in manifest_steps if item["status"] == "running"),
        "succeeded": sum(1 for item in manifest_steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in manifest_steps if item["status"] == "failed"),
        "cancelled": sum(1 for item in manifest_steps if item["status"] == "cancelled"),
    }
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = (
            "run_failed"
            if raw_error_message and normalize_run_status(str(run["status"])) == "failed"
            else ""
        )
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    resume_enabled = counts["reuse_pending"] > 0
    return {
        "contract_version": RUN_RESUME_MANIFEST_CONTRACT_VERSION,
        "run": run_summary,
        "source_run_id": source_run_id,
        "resume_enabled": resume_enabled,
        "reason": "reuse_pending" if resume_enabled else "no_reuse_pending",
        "counts": counts,
        "steps": manifest_steps,
    }


def _checkpoint_audit_step_label(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str:
    public_step = run_step_response(row, principal=principal)
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    if is_ai_admin(principal):
        return step_key
    return _resume_manifest_public_text(step_key, fallback=step_id, raw_terms=raw_terms) or step_id


def _checkpoint_audit_state(
    *,
    has_steps: bool,
    resume_reusable: bool,
    artifact_materialized: bool,
) -> str:
    if resume_reusable and artifact_materialized:
        return "materialized"
    if has_steps and not artifact_materialized:
        return "step_only" if resume_reusable else "incomplete"
    if artifact_materialized and not has_steps:
        return "artifact_only"
    return "incomplete"


def run_checkpoint_audit_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    """Return read-only checkpoint reusable-output and artifact materialization state."""
    raw_terms = _readiness_raw_projection_terms(run)
    checkpoints: dict[str, dict[str, object]] = {}
    step_ids = {str(row["id"]) for row in steps}
    step_checkpoint_ids: dict[str, str] = {}
    uncheckpointed: list[dict[str, object]] = []

    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        status = normalize_step_status(row.get("status"))
        output_available = status == "succeeded" and payload.get("output") is not None
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(payload.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if checkpoint_id:
            step_checkpoint_ids[str(row["id"])] = checkpoint_id
            item = checkpoints.setdefault(
                checkpoint_id,
                {
                    "checkpoint_id": checkpoint_id,
                    "step_ids": [],
                    "artifact_ids": [],
                    "resume_reusable": False,
                    "artifact_materialized": False,
                    "reuse_pending": 0,
                    "reused": 0,
                    "gaps": set(),
                },
            )
            item["step_ids"].append(str(row["id"]))
            item["resume_reusable"] = bool(item["resume_reusable"]) or output_available
            item["reuse_pending"] = int(item["reuse_pending"]) + (1 if payload.get("checkpoint_reuse_pending") else 0)
            item["reused"] = int(item["reused"]) + (1 if payload.get("checkpoint_reused") else 0)
        elif output_available:
            uncheckpointed.append(
                {
                    "step_id": str(row["id"]),
                    "step_key": _checkpoint_audit_step_label(row, principal, raw_terms=raw_terms),
                    "status": status,
                    "reason": "missing_checkpoint_id",
                }
            )

    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    for row, artifact in zip(artifacts, artifact_cards):
        lineage = artifact.get("lineage") if isinstance(artifact.get("lineage"), dict) else {}
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(lineage.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if not checkpoint_id:
            continue
        item = checkpoints.setdefault(
            checkpoint_id,
            {
                "checkpoint_id": checkpoint_id,
                "step_ids": [],
                "artifact_ids": [],
                "resume_reusable": False,
                "artifact_materialized": False,
                "reuse_pending": 0,
                "reused": 0,
                "gaps": set(),
            },
        )
        item["artifact_ids"].append(str(artifact["artifact_id"]))
        manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
        raw_source_step_id = manifest.get("source_step_id") if isinstance(manifest, dict) else None
        source_step_id = _safe_provenance_graph_id("source_step_id", raw_source_step_id)
        source_step_checkpoint_id = step_checkpoint_ids.get(str(source_step_id)) if source_step_id else None
        if raw_source_step_id is None:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("artifact_source_step_missing")
            item["gaps"] = gaps
        elif source_step_id is None:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("artifact_source_step_unsafe")
            item["gaps"] = gaps
        elif str(source_step_id) not in step_ids:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("producer_step_missing")
            item["gaps"] = gaps
            if not item["step_ids"]:
                item["artifact_materialized"] = True
        elif source_step_checkpoint_id != checkpoint_id:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("producer_checkpoint_mismatch")
            item["gaps"] = gaps
        else:
            item["artifact_materialized"] = True

    checkpoint_items = []
    for item in checkpoints.values():
        step_ids_for_checkpoint = _unique_sorted(item["step_ids"] if isinstance(item["step_ids"], list) else [])
        artifact_ids = _unique_sorted(item["artifact_ids"] if isinstance(item["artifact_ids"], list) else [])
        resume_reusable = bool(item["resume_reusable"])
        artifact_materialized = bool(item["artifact_materialized"])
        state = _checkpoint_audit_state(
            has_steps=bool(step_ids_for_checkpoint),
            resume_reusable=resume_reusable,
            artifact_materialized=artifact_materialized,
        )
        gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
        gaps = set(gaps)
        if bool(step_ids_for_checkpoint) and not resume_reusable:
            gaps.add("no_reusable_output")
        if state == "step_only" and not artifact_ids:
            gaps.add("no_artifact_lineage")
        if state == "artifact_only" and not gaps:
            gaps.add("producer_step_missing")
        checkpoint_items.append(
            {
                "checkpoint_id": str(item["checkpoint_id"]),
                "audit_state": state,
                "resume_reusable": resume_reusable,
                "artifact_materialized": artifact_materialized,
                "step_ids": step_ids_for_checkpoint,
                "artifact_ids": artifact_ids,
                "reuse": {
                    "pending": int(item["reuse_pending"]),
                    "reused": int(item["reused"]),
                },
                "gaps": sorted(gaps),
            }
        )

    checkpoint_items = sorted(checkpoint_items, key=lambda entry: str(entry["checkpoint_id"]))
    counts = {
        "checkpoints": len(checkpoint_items),
        "resume_reusable": sum(1 for item in checkpoint_items if item["resume_reusable"]),
        "artifact_materialized": sum(1 for item in checkpoint_items if item["artifact_materialized"]),
        "step_only": sum(1 for item in checkpoint_items if item["audit_state"] == "step_only"),
        "artifact_only": sum(1 for item in checkpoint_items if item["audit_state"] == "artifact_only"),
        "incomplete": sum(1 for item in checkpoint_items if item["audit_state"] == "incomplete"),
        "gaps": sum(len(item["gaps"]) for item in checkpoint_items) + len(uncheckpointed),
        "uncheckpointed_reusable_steps": len(uncheckpointed),
    }
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = (
            "run_failed"
            if raw_error_message and normalize_run_status(str(run["status"])) == "failed"
            else ""
        )
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    return {
        "contract_version": RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION,
        "run": run_summary,
        "counts": counts,
        "checkpoints": checkpoint_items,
        "uncheckpointed_reusable_steps": uncheckpointed,
    }


def _resume_manifest_source_run_candidates(steps: list[dict[str, object]]) -> list[str]:
    source_run_ids: set[str] = set()
    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if not payload.get("checkpoint_reuse_pending"):
            continue
        source_run_id = _safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
        if source_run_id:
            source_run_ids.add(source_run_id)
    return sorted(source_run_ids)


def run_playback_summary(run: dict[str, object], principal: AuthPrincipal) -> dict[str, object]:
    raw_skill_id = str(run["skill_id"])
    raw_agent_id = str(run["agent_id"])
    show_raw_skill = is_ai_admin(principal)
    return {
        "run_id": str(run["id"]),
        "session_id": str(run["session_id"]),
        "agent_id": raw_agent_id if show_raw_skill else public_agent_id_for_projection(raw_agent_id, raw_skill_id),
        "skill_id": raw_skill_id if show_raw_skill else None,
        "capability_id": capability_id_from_skill(raw_skill_id, raw_agent_id),
        "trace_id": str(run.get("trace_id") or standard_trace_id(str(run["id"]))),
        "contract_version": run_contract_version(run),
        "executor_schema_version": executor_result_schema_version(run) if show_raw_skill else None,
        "status": normalize_run_status(str(run["status"])),
        "progress": progress_for_status(str(run["status"])),
        "cancel_requested_at": run.get("cancel_requested_at"),
        "cancel_requested_by": run.get("cancel_requested_by"),
        "error_code": (
            sanitize_public_text(run.get("error_code"))
            if show_raw_skill
            else ("run_failed" if run.get("error_code") else None)
        ),
        "error_message": sanitize_public_text(run.get("error_message")),
    }


def run_playback_timeline(
    *,
    events: list[dict[str, object]],
    artifacts: list[dict[str, object]],
) -> list[dict[str, object]]:
    timeline: list[dict[str, object]] = [
        {
            "entry_type": "event",
            "sequence": int(event.get("sequence") or 0),
            "created_at": event.get("created_at"),
            "event": event,
        }
        for event in events
    ]
    timeline.extend(
        {
            "entry_type": "artifact",
            "sequence": None,
            "created_at": artifact.get("created_at"),
            "artifact": artifact,
        }
        for artifact in artifacts
    )
    return timeline


def next_sequence_from_rows(rows: list[dict[str, object]], fallback: int | None = None) -> int:
    return max([int(row.get("sequence") or 0) for row in rows], default=fallback or 0)


def copy_recovery_plan(run: dict[str, Any], rows: list[dict[str, object]], *, include_raw_skill: bool = False) -> dict[str, object]:
    source_input = run.get("input_json") or {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    configured_steps = execution_input.get("multi_agent_steps") if isinstance(execution_input, dict) else []
    configured_by_key = {
        str(item.get("step_key") or item.get("stepKey")): item
        for item in configured_steps or []
        if isinstance(item, dict) and (item.get("step_key") or item.get("stepKey"))
    }
    source_rows = rows or [
        {
            "step_key": key,
            "role": item.get("role") or "",
            "title": item.get("title") or "",
            "status": "pending",
            "payload_json": {"depends_on": item.get("depends_on") or item.get("dependsOn") or []},
        }
        for key, item in configured_by_key.items()
    ]
    status_labels = {
        "blocked": "阻塞",
        "cancelled": "已取消",
        "canceled": "已取消",
        "failed": "失败",
        "pending": "等待中",
        "running": "执行中",
        "succeeded": "已完成",
    }
    planned_steps: list[dict[str, object]] = []
    reused = 0
    rerun = 0
    for index, row in enumerate(source_rows, start=1):
        step_key = str(row.get("step_key") or row.get("stepKey") or f"step-{index}")
        configured = configured_by_key.get(step_key, {})
        payload = row.get("payload_json") or row.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        status = str(row.get("status") or "pending")
        can_reuse = status == "succeeded" and payload.get("output") is not None
        if can_reuse:
            reused += 1
            recovery_label = "已完成 · 将复用"
        else:
            rerun += 1
            recovery_label = f"{status_labels.get(status, status)} · 将重跑"
        planned_steps.append(
            {
                "step_key": step_key,
                "role": recovery_label,
                "title": (
                    str(row.get("title") or configured.get("title") or step_key)
                    if include_raw_skill
                    else (public_text_or_fallback(row.get("title") or configured.get("title"), step_key) or "step")
                ),
                "depends_on": payload.get("depends_on")
                or configured.get("depends_on")
                or configured.get("dependsOn")
                or [],
            }
        )
    raw_skill_id = str(run.get("skill_id") or "")
    capability_id = capability_id_from_skill(raw_skill_id, run.get("agent_id"))
    capability = get_capability(str(capability_id)) if capability_id else None
    if include_raw_skill:
        skills = [{"skill_id": raw_skill_id, "label": raw_skill_id}]
    elif capability is not None:
        skills = [{"capability_id": capability.capability_id, "label": capability.label}]
    else:
        skills = []
    confirmation_card: dict[str, object] = {
        "title": "确认恢复执行",
        "summary": f"将复制为新任务，复用 {reused} 个已完成步骤，重跑 {rerun} 个未完成步骤。",
        "skills": skills,
        "mcp_tools": [],
        "steps": planned_steps,
        "risk_level": "medium",
    }
    if include_raw_skill:
        confirmation_card["resource_limits"] = {}
    return {
        "contract_version": "ai-platform.copy-recovery-plan.v1",
        "source_run_id": run["id"],
        "requires_confirmation": True,
        "confirmation_card": confirmation_card,
    }


def event_visible_to_principal(row: dict[str, object], principal: AuthPrincipal) -> bool:
    if is_ai_admin(principal):
        return True
    if row.get("visible_to_user") is not None:
        return bool(row.get("visible_to_user"))
    payload = row.get("payload_json") or {}
    if not isinstance(payload, dict):
        payload = {}
    return bool(payload.get("visible_to_user", True))


async def enforce_user_active_run_limit(conn, *, tenant_id: str, user_id: str) -> None:
    limit = int(get_settings().max_active_runs_per_user)
    if limit <= 0:
        return
    active_count = await repositories.count_active_runs_for_user(conn, tenant_id=tenant_id, user_id=user_id)
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")


async def queue_insight_for_status(status: str, tenant_id: str) -> dict[str, Any] | None:
    if normalize_run_status(status) != "queued":
        return None
    return await get_queue_insight(tenant_id)


def _resume_checkpoint_lineage(
    completed_checkpoints: dict[object, object],
    *,
    step_key: str,
    copied_from_run_id: object,
) -> dict[str, str]:
    checkpoint = completed_checkpoints.get(step_key)
    if not isinstance(checkpoint, dict):
        return {}
    lineage = artifact_lineage_contract(
        {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "source_step_id": checkpoint.get("source_step_id"),
        },
        source_run_id=checkpoint.get("copied_from_run_id") or copied_from_run_id,
    )
    checkpoint_id = lineage.get("checkpoint_id")
    source_step_id = lineage.get("source_step_id")
    source_run_id = lineage.get("source_run_id")
    if not checkpoint_id or not source_step_id:
        return {}
    result = {
        "checkpoint_id": str(checkpoint_id),
        "source_step_id": str(source_step_id),
    }
    if source_run_id:
        result["copied_from_run_id"] = str(source_run_id)
    return result


def _strip_server_owned_resume(input_payload: object) -> object:
    if not isinstance(input_payload, dict):
        return input_payload
    cleaned = dict(input_payload)
    cleaned.pop("resume", None)
    return cleaned


async def seed_copied_run_steps(conn, *, tenant_id: str, run_id: str, copied_input: dict[str, Any], source: str) -> None:
    steps = copied_input.get("multi_agent_steps")
    if not isinstance(steps, list):
        return
    resume = copied_input.get("resume")
    completed_outputs = resume.get("completed_step_outputs") if isinstance(resume, dict) else {}
    if not isinstance(completed_outputs, dict):
        completed_outputs = {}
    completed_checkpoints = resume.get("completed_step_checkpoints") if isinstance(resume, dict) else {}
    if not isinstance(completed_checkpoints, dict):
        completed_checkpoints = {}
    copied_from_run_id = resume.get("copied_from_run_id") if isinstance(resume, dict) else copied_input.get("copied_from_run_id")
    for index, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            continue
        step_key = str(raw_step.get("step_key") or raw_step.get("stepKey") or f"step-{index}")
        step_output = completed_outputs.get(step_key)
        reused = step_output is not None
        payload_json = {
            "role": raw_step.get("role") or "",
            "step_key": step_key,
            "step_index": index,
            "depends_on": raw_step.get("depends_on") or raw_step.get("dependsOn") or [],
            "skill_ids": raw_step.get("skill_ids") or raw_step.get("skillIds") or [],
            "mcp_tool_ids": raw_step.get("mcp_tool_ids") or raw_step.get("mcpToolIds") or [],
            "seeded_from": source,
        }
        if raw_step.get("sandbox_mode") is not None:
            payload_json["sandbox_mode"] = raw_step.get("sandbox_mode")
        if raw_step.get("browser_enabled") is not None:
            payload_json["browser_enabled"] = raw_step.get("browser_enabled")
        resource_limits = raw_step.get("resource_limits") or raw_step.get("resourceLimits")
        if isinstance(resource_limits, dict):
            payload_json["resource_limits"] = resource_limits
        if reused:
            checkpoint_lineage = _resume_checkpoint_lineage(
                completed_checkpoints,
                step_key=step_key,
                copied_from_run_id=copied_from_run_id,
            )
            payload_json.update(
                {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": copied_from_run_id,
                    **checkpoint_lineage,
                }
            )
        await repositories.upsert_run_step(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            step_kind=str(raw_step.get("step_kind") or raw_step.get("stepKind") or "agent"),
            status="pending",
            title=str(raw_step.get("title") or step_key),
            role=str(raw_step.get("role") or ""),
            sequence=int(raw_step.get("sequence") or index),
            payload_json=payload_json,
        )


async def prepare_copied_run_for_queue(
    conn,
    *,
    copied: dict[str, Any],
    principal: AuthPrincipal,
    source: str,
    queue_principal: AuthPrincipal | None = None,
) -> dict[str, Any]:
    effective_principal = queue_principal or principal
    copied_skill_version = str(copied.get("skill_version") or "")
    skill_manifests = await _governed_skill_manifest_pins(
        conn,
        skill_id=str(copied["skill_id"]),
        input_payload=copied["input"] if isinstance(copied.get("input"), dict) else {},
        release_policy_version=copied.get("release_policy_version"),
    )
    copied_skill_version = governed_locked_skill_version(
        skill_id=str(copied["skill_id"]),
        skill_manifests=skill_manifests,
        fallback_version=copied_skill_version,
        release_policy_version=copied.get("release_policy_version"),
    )
    copied["skill_version"] = copied_skill_version
    copied["release_decision"] = release_decision_payload_for_locked_version(
        copied.get("release_decision") if isinstance(copied.get("release_decision"), dict) else {},
        locked_version=copied_skill_version,
    )
    await repositories.update_run_input_skill_version(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        skill_version=copied_skill_version,
    )
    await repositories.append_event(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        event_type="skill_release_decision",
        stage="control",
        message="已锁定 Skill 发布决策",
        payload=_release_decision_event_payload(
            copied.get("release_decision") if isinstance(copied.get("release_decision"), dict) else {},
            skill_id=str(copied["skill_id"]),
        ),
    )
    context_ref = await record_initial_context_snapshot(
        conn,
        tenant_id=effective_principal.tenant_id,
        workspace_id=str(copied["workspace_id"]),
        user_id=effective_principal.user_id,
        session_id=str(copied["session_id"]),
        run_id=str(copied["run_id"]),
        trace_id=standard_trace_id(str(copied["run_id"])),
        agent_id=str(copied["agent_id"]),
        skill_id=str(copied["skill_id"]),
        input_payload=copied["input"] if isinstance(copied.get("input"), dict) else {},
        message_ids=[],
        file_ids=list(copied["file_ids"]),
        source=source,
    )
    for event in initial_run_event_specs(
        agent_id=str(copied["agent_id"]),
        skill_id=str(copied["skill_id"]),
        skill_version=copied_skill_version,
        executor_type=str(copied["executor_type"]),
        file_ids=list(copied["file_ids"]),
        source=source,
    ):
        await repositories.append_event(
            conn,
            tenant_id=effective_principal.tenant_id,
            run_id=copied["run_id"],
            event_type=event["event_type"],
            stage=event["stage"],
            message=event["message"],
            payload=event["payload"],
        )
    queue_payload = _validate_queue_payload_for_enqueue(
        {
            "tenant_id": effective_principal.tenant_id,
            "workspace_id": copied["workspace_id"],
            "user_id": effective_principal.user_id,
            "session_id": copied["session_id"],
            "run_id": copied["run_id"],
            "agent_id": copied["agent_id"],
            "skill_id": copied["skill_id"],
            "file_ids": copied["file_ids"],
            "input": copied["input"],
            "executor_type": copied["executor_type"],
            "skill_version": copied_skill_version,
            "release_decision": copied.get("release_decision") if isinstance(copied.get("release_decision"), dict) else {},
            "skill_manifests": skill_manifests,
            "context_snapshot_id": context_ref["context_snapshot_id"],
            "context_snapshot": context_ref,
        }
    )
    await seed_copied_run_steps(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        copied_input=copied["input"],
        source=source,
    )
    return queue_payload


def resolve_run_selector(request: CreateRunRequest, principal: AuthPrincipal) -> tuple[str, str]:
    requested_agent_id = internal_agent_id_for_request(request.agent_id) or request.agent_id
    if request.skill_id and not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="raw_skill_selector_forbidden")
    if request.skill_id:
        return requested_agent_id, request.skill_id

    capability_id = request.capability_id or capability_id_from_skill(None, requested_agent_id)
    capability = get_capability(str(capability_id)) if capability_id else None
    if capability is None:
        raise HTTPException(status_code=400, detail="capability_required")
    if requested_agent_id and requested_agent_id != capability.agent_id:
        raise HTTPException(status_code=409, detail="agent_capability_mismatch")
    return capability.agent_id, capability.skill_id


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(
    request: CreateRunRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> CreateRunResponse:
    tenant_id = principal.tenant_id
    user_id = principal.user_id
    resolved_agent_id, resolved_skill_id = resolve_run_selector(request, principal)
    run_input = request.input if is_ai_admin(principal) else sanitize_user_control_input(request.input)
    run_input = _strip_server_owned_resume(run_input)
    try:
        async with transaction() as conn:
            skill = await repositories.resolve_agent_skill(
                conn,
                tenant_id=tenant_id,
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
            )
            input_modes = skill.get("input_modes") or []
            if "docx" in input_modes and not request.file_ids:
                raise RepositoryConflictError("file_required_for_skill")
            await enforce_user_active_run_limit(conn, tenant_id=tenant_id, user_id=user_id)
            release_decision = resolve_rollout_skill_decision(
                skill,
                tenant_id=tenant_id,
                skill_id=resolved_skill_id,
                rollout_key=user_id,
            )
            selected_policy_version = release_decision.selected_version
            release_decision_payload = release_decision.to_payload()
            release_policy_version = selected_policy_version if release_decision.policy_active else None
            skill_manifests = await _governed_skill_manifest_pins(
                conn,
                skill_id=resolved_skill_id,
                input_payload=run_input,
                release_policy_version=release_policy_version,
            )
            skill_version = governed_locked_skill_version(
                skill_id=resolved_skill_id,
                skill_manifests=skill_manifests,
                fallback_version=selected_policy_version,
                release_policy_version=release_policy_version,
            )
            release_decision_payload = release_decision_payload_for_locked_version(
                release_decision,
                locked_version=skill_version,
            )
            session_id = request.session_id or repositories.new_id("ses")
            run_id = repositories.new_id("run")
            base_queue_payload = {
                "tenant_id": tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": user_id,
                "session_id": session_id,
                "run_id": run_id,
                "agent_id": resolved_agent_id,
                "skill_id": resolved_skill_id,
                "file_ids": request.file_ids,
                "input": run_input,
                "executor_type": skill["executor_type"],
                "skill_version": skill_version,
                "release_decision": release_decision_payload,
                "skill_manifests": skill_manifests,
            }
            queue_payload = _validate_queue_payload_for_enqueue(base_queue_payload)
            await repositories.ensure_user(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                display_name=principal.display_name,
            )
            session_id = await repositories.create_session(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                user_id=user_id,
                agent_id=resolved_agent_id,
                title=request.title or resolved_agent_id,
                session_id=session_id,
            )
            run_id = await repositories.create_run(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                session_id=session_id,
                user_id=user_id,
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                input_json={
                    "input": run_input,
                    "file_ids": request.file_ids,
                    "executor_type": skill["executor_type"],
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                },
                run_id=run_id,
            )
            await repositories.bind_files_to_run(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=request.file_ids,
            )
            context_ref = await record_initial_context_snapshot(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                trace_id=standard_trace_id(run_id),
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                input_payload=run_input,
                message_ids=[],
                file_ids=request.file_ids,
                source="runs_api",
            )
            queue_payload = _validate_queue_payload_for_enqueue(
                {
                    **base_queue_payload,
                    "session_id": session_id,
                    "run_id": run_id,
                    "context_snapshot_id": context_ref["context_snapshot_id"],
                    "context_snapshot": context_ref,
                }
            )
            for event in initial_run_event_specs(
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                skill_version=skill_version,
                executor_type=str(skill["executor_type"]),
                file_ids=request.file_ids,
                source="runs_api",
            ):
                await repositories.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type=event["event_type"],
                    stage=event["stage"],
                    message=event["message"],
                    payload=event["payload"],
                )
            await repositories.append_event(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                event_type="skill_release_decision",
                stage="control",
                message="已锁定 Skill 发布决策",
                payload=_release_decision_event_payload(release_decision_payload, skill_id=resolved_skill_id),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await enqueue_run(queue_payload)
    return CreateRunResponse(run_id=run_id, session_id=session_id, status="queued")


@router.post("/runs/{run_id}/copy", response_model=RunControlResponse)
async def copy_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    try:
        async with transaction() as conn:
            copied = await repositories.copy_run_as_new_task(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if copied is not None:
                queue_payload = await prepare_copied_run_for_queue(
                    conn,
                    copied=copied,
                    principal=principal,
                    source="copy_run",
                )
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id),
    )


@router.post("/runs/{run_id}/retry", response_model=RunControlResponse)
async def retry_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    try:
        async with transaction() as conn:
            await enforce_user_active_run_limit(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
            copied = await repositories.retry_run_as_new_task(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if copied is not None:
                queue_payload = await prepare_copied_run_for_queue(
                    conn,
                    copied=copied,
                    principal=principal,
                    source="retry_run",
                )
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id),
    )


@router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    """Queue a platform-controlled resume run for an authorized checkpointed source."""
    try:
        async with transaction() as conn:
            await enforce_user_active_run_limit(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
            copied = await repositories.resume_run_as_new_task(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if copied is not None:
                queue_payload = await prepare_copied_run_for_queue(
                    conn,
                    copied=copied,
                    principal=principal,
                    source="resume_run",
                )
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id),
    )


@router.get("/runs/{run_id}/copy/plan")
async def get_copy_run_plan(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
    plan = copy_recovery_plan(run, steps, include_raw_skill=is_ai_admin(principal))
    plan["queue_insight"] = await get_queue_insight(principal.tenant_id)
    return plan


@router.get("/runs/{run_id}/control/readiness")
async def get_run_control_readiness(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only readiness for platform-controlled run actions."""
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
    run_status = normalize_run_status(str(run["status"]))
    queue_insight = (
        await queue_insight_for_status(run_status, principal.tenant_id)
        if run_status == "queued"
        else None
    )
    return run_control_readiness_snapshot(
        run=run,
        steps=steps,
        principal=principal,
        queue_insight=queue_insight,
    )


@router.post(
    "/runs/{run_id}/multi-agent/dispatch/claims",
    response_model=MultiAgentDispatchClaimResponse,
)
async def claim_multi_agent_dispatch(
    run_id: str,
    request: MultiAgentDispatchClaimRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> MultiAgentDispatchClaimResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="admin_required")
    try:
        async with transaction() as conn:
            run = await repositories.get_run(conn, tenant_id=principal.tenant_id, run_id=run_id, for_update=True)
            if run is None:
                raise HTTPException(status_code=404, detail="run_not_found")
            steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
            candidate = _dispatch_claim_candidate(
                run=run,
                steps=steps,
                step_key=request.step_key,
                principal=principal,
            )
            result = await repositories.claim_multi_agent_dispatch_step(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                claimed_by=principal.user_id,
                trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
                lease_ttl_seconds=int(get_settings().multi_agent_dispatch_lease_ttl_seconds),
                **candidate,
            )
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MultiAgentDispatchClaimResponse(
        contract_version=MULTI_AGENT_DISPATCH_CLAIM_CONTRACT_VERSION,
        run_id=run_id,
        step_key=request.step_key,
        step_id=str(result["step"]["id"]),
        status="claimed",
        dispatch_id=str(result["dispatch_id"]),
        event_id=str(result["event_id"]),
        audit_id=str(result["audit_id"]),
        step=run_step_response(result["step"], principal=principal),
    )


@router.post(
    "/runs/{run_id}/multi-agent/dispatch/claims/{dispatch_id}/handoff",
    response_model=MultiAgentDispatchHandoffResponse,
)
async def handoff_multi_agent_dispatch(
    run_id: str,
    dispatch_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MultiAgentDispatchHandoffResponse:
    """Create one owner-scoped child run from an admin-claimed dispatch step."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="admin_required")
    try:
        async with transaction() as conn:
            copied = await repositories.create_multi_agent_dispatch_child_run(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
                dispatch_id=dispatch_id,
                handed_off_by=principal.user_id,
            )
            owner_principal = AuthPrincipal(
                user_id=str(copied["user_id"]),
                display_name=str(copied.get("user_id") or ""),
                tenant_id=principal.tenant_id,
                roles=["user"],
                source="multi_agent_dispatch_handoff",
            )
            queue_payload = await prepare_copied_run_for_queue(
                conn,
                copied={**copied, "run_id": copied["child_run_id"]},
                principal=principal,
                queue_principal=owner_principal,
                source="multi_agent_dispatch_handoff",
            )
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    queue_position = await enqueue_run(queue_payload)
    return MultiAgentDispatchHandoffResponse(
        contract_version=MULTI_AGENT_DISPATCH_HANDOFF_CONTRACT_VERSION,
        parent_run_id=run_id,
        dispatch_id=dispatch_id,
        step_key=str(copied["step_key"]),
        step_id=str(copied["parent_step_id"]),
        status="queued",
        child_run_id=str(copied["child_run_id"]),
        session_id=str(copied["session_id"]),
        queue_position=queue_position,
        queue_insight=await get_queue_insight(principal.tenant_id),
        event_id=str(copied["event_id"]),
        child_event_id=str(copied["child_event_id"]),
        audit_id=str(copied["audit_id"]),
    )


@router.post(
    "/runs/{run_id}/multi-agent/dispatch/tick",
    response_model=MultiAgentDispatchTickResponse,
)
async def tick_multi_agent_dispatch(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MultiAgentDispatchTickResponse:
    """Claim, hand off, and enqueue one safe ready multi-agent step."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="admin_required")
    try:
        async with transaction() as conn:
            run = await repositories.get_run(conn, tenant_id=principal.tenant_id, run_id=run_id, for_update=True)
            if run is None:
                raise HTTPException(status_code=404, detail="run_not_found")
            steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
            candidate = _dispatch_tick_candidate(run=run, steps=steps, principal=principal)
            claimed_step_key = str(candidate["step_key"])
            claim = await repositories.claim_multi_agent_dispatch_step(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                claimed_by=principal.user_id,
                trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
                lease_ttl_seconds=int(get_settings().multi_agent_dispatch_lease_ttl_seconds),
                **candidate,
            )
            copied = await repositories.create_multi_agent_dispatch_child_run(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
                dispatch_id=str(claim["dispatch_id"]),
                handed_off_by=principal.user_id,
            )
            owner_principal = AuthPrincipal(
                user_id=str(copied["user_id"]),
                display_name=str(copied.get("user_id") or ""),
                tenant_id=principal.tenant_id,
                roles=["user"],
                source="multi_agent_dispatch_tick",
            )
            queue_payload = await prepare_copied_run_for_queue(
                conn,
                copied={**copied, "run_id": copied["child_run_id"]},
                principal=principal,
                queue_principal=owner_principal,
                source="multi_agent_dispatch_tick",
            )
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    queue_position = await enqueue_run(queue_payload)
    return MultiAgentDispatchTickResponse(
        contract_version=MULTI_AGENT_DISPATCH_TICK_CONTRACT_VERSION,
        parent_run_id=run_id,
        dispatch_id=str(claim["dispatch_id"]),
        step_key=claimed_step_key,
        step_id=str(claim["step"]["id"]),
        status="queued",
        child_run_id=str(copied["child_run_id"]),
        session_id=str(copied["session_id"]),
        queue_position=queue_position,
        queue_insight=await get_queue_insight(principal.tenant_id),
        claim_event_id=str(claim["event_id"]),
        claim_audit_id=str(claim["audit_id"]),
        handoff_event_id=str(copied["event_id"]),
        child_event_id=str(copied["child_event_id"]),
        handoff_audit_id=str(copied["audit_id"]),
    )


@router.get("/runs/{run_id}/resume/manifest")
async def get_run_resume_manifest(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only checkpoint reuse intent for an authorized copied run."""
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
        authorized_source_run_ids: set[str] = set()
        for source_run_id in _resume_manifest_source_run_candidates(steps):
            source_run = await repositories.get_authorized_run(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                run_id=source_run_id,
            )
            if source_run is not None:
                authorized_source_run_ids.add(source_run_id)
    return run_resume_manifest_snapshot(
        run=run,
        steps=steps,
        principal=principal,
        authorized_source_run_ids=authorized_source_run_ids,
    )


@router.get("/runs/{run_id}/checkpoints/audit")
async def get_run_checkpoint_audit(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only checkpoint materialization audit for an authorized run."""
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
    return run_checkpoint_audit_snapshot(run=run, steps=steps, artifacts=artifacts, principal=principal)


@router.post("/runs/{run_id}/cancel", response_model=RunControlResponse, response_model_exclude={"queue_position", "queue_insight"})
async def cancel_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    async with transaction() as conn:
        result = await repositories.request_run_cancel(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if result is not None:
            propagation = await repositories.propagate_multi_agent_parent_cancel(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
                requested_by=principal.user_id,
            )
            if propagation.get("queued_child_run_ids"):
                result["queued_child_run_ids"] = list(propagation["queued_child_run_ids"])
            if propagation.get("active_sandbox_leases"):
                result["active_sandbox_leases"] = [
                    *list(result.get("active_sandbox_leases") or []),
                    *list(propagation["active_sandbox_leases"]),
                ]
            finalized_parent = await repositories.finalize_multi_agent_parent_run_if_ready(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
            )
            if finalized_parent and finalized_parent.get("status"):
                result["status"] = str(finalized_parent["status"])
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    queue_cleanup_failures = await _remove_cancelled_queue_payloads(
        tenant_id=principal.tenant_id,
        parent_run_id=run_id,
        result=result,
    )
    try:
        stopped_sandbox_leases = await stop_sandbox_leases(
            result.get("active_sandbox_leases"),
            reason="cancel_requested",
            provider_factory=create_container_provider,
        )
    except SandboxRuntimeCleanupError as exc:
        if exc.stopped_leases:
            async with transaction() as conn:
                await _release_stopped_cancel_leases(
                    conn,
                    tenant_id=principal.tenant_id,
                    reason="cancel_requested",
                    leases=exc.stopped_leases,
                    trace_id=result.get("trace_id"),
                )
        raise HTTPException(status_code=502, detail="sandbox_runtime_cleanup_failed") from exc
    if stopped_sandbox_leases:
        async with transaction() as conn:
            await _release_stopped_cancel_leases(
                conn,
                tenant_id=principal.tenant_id,
                reason="cancel_requested",
                leases=stopped_sandbox_leases,
                trace_id=result.get("trace_id"),
            )
    if queue_cleanup_failures:
        raise HTTPException(status_code=502, detail="queue_cleanup_failed") from queue_cleanup_failures[0]
    return RunControlResponse(run_id=result["run_id"], status=result["status"])


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunResponse:
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id) if run else []
        events = await repositories.list_run_events(conn, tenant_id=tenant_id, run_id=run_id) if run else []
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id) if run else []
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    run_status = str(run["status"])
    queue_position = (
        await get_run_queue_position(tenant_id=tenant_id, run_id=run_id)
        if run_status == "queued"
        else None
    )
    queue_insight = await queue_insight_for_status(run_status, tenant_id)
    contract_version = run_contract_version(run)
    executor_schema_version = executor_result_schema_version(run)
    result = run["result_json"] if isinstance(run["result_json"], dict) else {}
    result_payload = dict(result)
    raw_skill_id = str(run["skill_id"])
    raw_agent_id = str(run["agent_id"])
    show_raw_skill = is_ai_admin(principal)
    multi_agent_snapshot = multi_agent_snapshot_from_steps(run_id, steps, principal=principal)
    if multi_agent_snapshot is not None:
        result_payload["multi_agent"] = multi_agent_snapshot
    input_payload = run["input_json"] if isinstance(run["input_json"], dict) else {}
    if show_raw_skill:
        input_payload = sanitize_public_payload(input_payload)
        result_payload = sanitize_public_payload(result_payload)
    else:
        input_payload = sanitize_public_payload(redact_raw_skill_references(input_payload))
        result_payload = sanitize_public_payload(redact_raw_skill_references(result_payload))
    if not isinstance(input_payload, dict):
        input_payload = {}
    if not isinstance(result_payload, dict):
        result_payload = {}
    error_code = (
        sanitize_public_text(run.get("error_code"))
        if show_raw_skill
        else ("run_failed" if run.get("error_code") else None)
    )
    error_message = sanitize_public_text(run.get("error_message"))
    return RunResponse(
        run_id=run["id"],
        session_id=run["session_id"],
        agent_id=raw_agent_id if show_raw_skill else public_agent_id_for_projection(raw_agent_id, raw_skill_id),
        skill_id=raw_skill_id if show_raw_skill else None,
        capability_id=capability_id_from_skill(raw_skill_id, raw_agent_id),
        trace_id=str(run.get("trace_id") or standard_trace_id(str(run["id"]))),
        contract_version=contract_version,
        executor_schema_version=executor_schema_version if show_raw_skill else None,
        status=normalize_run_status(str(run["status"])),
        progress=progress_for_status(run["status"]),
        input=input_payload,
        result=result_payload,
        artifacts=[artifact_card(row, principal=principal) for row in artifacts],
        events=[run_event_response(run_id, row, principal=principal) for row in events if event_visible_to_principal(row, principal)],
        steps=[run_step_response(row, principal=principal) for row in steps],
        queue_position=queue_position,
        queue_insight=queue_insight,
        cancel_requested_at=run.get("cancel_requested_at"),
        cancel_requested_by=run.get("cancel_requested_by"),
        error_code=error_code,
        error_message=error_message,
    )


@router.get("/runs/{run_id}/playback")
async def get_run_playback(
    run_id: str,
    after_sequence: int | None = None,
    limit: int = 200,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    tenant_id = principal.tenant_id
    event_limit = max(min(limit, 500), 1)
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        events = await repositories.list_run_events(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=event_limit,
        )
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)

    projected_events = [
        run_event_response(run_id, row, principal=principal)
        for row in events
        if event_visible_to_principal(row, principal)
    ]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_cards = [run_step_response(row, principal=principal) for row in steps]
    next_after_sequence = next_sequence_from_rows(events, fallback=after_sequence)
    return {
        "contract_version": RUN_PLAYBACK_CONTRACT_VERSION,
        "run_id": run_id,
        "after_sequence": after_sequence,
        "next_after_sequence": next_after_sequence,
        "run": run_playback_summary(run, principal),
        "timeline": run_playback_timeline(events=projected_events, artifacts=artifact_cards),
        "events": projected_events,
        "artifacts": artifact_cards,
        "steps": step_cards,
        "multi_agent": multi_agent_snapshot_from_steps(run_id, steps, principal=principal),
    }


@router.get("/runs/{run_id}/provenance")
async def get_run_provenance(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return the read-only public provenance graph for an authorized run."""
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    return run_provenance_snapshot(run=run, steps=steps, artifacts=artifacts, principal=principal)


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    after_sequence: int | None = None,
    limit: int = 200,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        run_contract_version(run)
        executor_result_schema_version(run)
        events = await repositories.list_run_events(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=max(min(limit, 500), 1),
        )
    projected = [run_event_response(run_id, row, principal=principal) for row in events if event_visible_to_principal(row, principal)]
    next_after_sequence = next_sequence_from_rows(events, fallback=after_sequence)
    return {
        "run_id": run_id,
        "after_sequence": after_sequence,
        "next_after_sequence": next_after_sequence,
        "events": projected,
    }


@router.get("/runs/{run_id}/steps")
async def get_run_steps(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    return {"run_id": run_id, "steps": [run_step_response(row, principal=principal) for row in steps]}


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    after_sequence: int | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> StreamingResponse:
    async with transaction() as conn:
        initial_run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if initial_run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        run_contract_version(initial_run)
        executor_result_schema_version(initial_run)

    async def stream():
        seen: set[str] = set()
        last_sequence = after_sequence
        last_snapshot_json: str | None = None
        heartbeat_index = 0
        max_heartbeats = max(int(get_settings().run_event_stream_max_heartbeats), 1)
        next_run: dict[str, Any] | None = initial_run

        async def list_events(conn):
            if last_sequence is None:
                return await repositories.list_run_events(conn, tenant_id=principal.tenant_id, run_id=run_id)
            return await repositories.list_run_events(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                after_sequence=last_sequence,
            )

        for _ in range(max_heartbeats):
            if next_run is None:
                async with transaction() as conn:
                    run = await repositories.get_authorized_run(
                        conn,
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        run_id=run_id,
                    )
                    if run is None:
                        yield sse("error", {"error": "run_not_found"})
                        yield sse("done", {"status": "not_found"})
                        return
                    run_contract_version(run)
                    executor_result_schema_version(run)
                    rows = await list_events(conn)
                    step_rows = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
            else:
                run = next_run
                next_run = None
                async with transaction() as conn:
                    rows = await list_events(conn)
                    step_rows = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
            for row in rows:
                event_id = str(row["id"])
                if event_id in seen:
                    continue
                seen.add(event_id)
                if not event_visible_to_principal(row, principal):
                    continue
                try:
                    payload = run_event_response(run_id, row, principal=principal)
                except HTTPException as exc:
                    yield sse("error", {"error": str(exc.detail)})
                    yield sse("done", {"status": "error"})
                    return
                last_sequence = max(int(payload.get("sequence") or 0), int(last_sequence or 0))
                yield sse("run_event", payload, event_id=event_id)
            snapshot = multi_agent_snapshot_from_steps(run_id, step_rows, principal=principal)
            if snapshot is not None:
                snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=_json_default, sort_keys=True)
                if snapshot_json != last_snapshot_json:
                    last_snapshot_json = snapshot_json
                    yield sse("multi_agent_snapshot", snapshot, event_id=f"{run_id}:steps:{heartbeat_index + 1}")
            status = str(run["status"])
            if status in {"succeeded", "failed", "cancelled", "canceled"}:
                yield sse("done", {"status": normalize_run_status(status)}, event_id=f"{run_id}:done")
                return
            heartbeat_index += 1
            heartbeat_payload: dict[str, object] = {"run_id": run_id, "status": status}
            if run.get("cancel_requested_at"):
                heartbeat_payload["cancel_requested_at"] = run.get("cancel_requested_at")
                heartbeat_payload["cancel_requested_by"] = run.get("cancel_requested_by")
            if status == "queued":
                queue_position = await get_run_queue_position(tenant_id=principal.tenant_id, run_id=run_id)
                if queue_position is not None:
                    heartbeat_payload["queue_position"] = queue_position
            queue_insight = await queue_insight_for_status(status, principal.tenant_id)
            if queue_insight is not None:
                heartbeat_payload["queue_insight"] = queue_insight
            yield sse(
                "heartbeat",
                heartbeat_payload,
                event_id=f"{run_id}:heartbeat:{heartbeat_index}",
            )
            await asyncio.sleep(1)
        yield sse("error", {"error": "stream_timeout"})
        yield sse("done", {"status": "timeout"}, event_id=f"{run_id}:done")

    return StreamingResponse(stream(), media_type="text/event-stream")

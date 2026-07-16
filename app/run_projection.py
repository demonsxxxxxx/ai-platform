from fastapi import HTTPException

from app.artifact_preview import artifact_preview_allowed, artifact_preview_url
from app.auth import AuthPrincipal, is_ai_admin
from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    RUN_CONTRACT_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import redact_raw_skill_references, sanitize_user_control_input
from app.tool_permission_projection import tool_permission_public_event_payload


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
    lineage = artifact_lineage_contract(manifest, row=row)
    lineage.pop("source_run_id", None)
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
        "lineage": lineage,
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
    "run_multi_agent_child_created": "run_child_created",
    "skill_selected": "capability_selected",
    "tool_permission_decided": "tool_permission_card",
    "tool_permission_requested": "tool_permission_card",
    "tool_permission_terminalized": "tool_permission_card",
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
        payload = sanitize_user_control_input(payload)
        if raw_event_type in {
            "tool_permission_requested",
            "tool_permission_decided",
            "tool_permission_terminalized",
        }:
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

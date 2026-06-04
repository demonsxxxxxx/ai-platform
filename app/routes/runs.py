import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capabilities import get_capability
from app.context_builder import record_initial_context_snapshot
from app.db import transaction
from app.models import CreateRunRequest, CreateRunResponse, QueueRunPayload, RunControlResponse, RunResponse
from app.product_events import initial_run_event_specs
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
from app.projection_redaction import capability_id_from_skill, redact_raw_skill_references, sanitize_user_control_input
from app.queue import enqueue_run, get_queue_insight, get_run_queue_position, remove_queued_run
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
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

router = APIRouter()
RUN_PLAYBACK_CONTRACT_VERSION = "ai-platform.run-playback.v1"


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


def artifact_card(row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    artifact_id = str(row["id"])
    artifact_type = str(row["artifact_type"])
    manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
    if principal is not None and not is_ai_admin(principal):
        manifest = redact_raw_skill_references(manifest)
        label = public_text_or_fallback(row.get("label"), artifact_type)
    else:
        label = str(row.get("label") or artifact_type)
    return {
        "id": artifact_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "label": label,
        "content_type": str(row.get("content_type") or "application/octet-stream"),
        "size_bytes": int(row.get("size_bytes") or 0),
        "download_url": artifact_download_url(artifact_id),
        "preview_url": None,
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
    message = str(row.get("message") or "")
    if principal is not None and not is_ai_admin(principal):
        message = sanitize_public_text(message)
    error_code = row.get("error_code")
    if principal is not None and not is_ai_admin(principal):
        sanitized_error_code = sanitize_public_text(error_code)
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
    payload = row.get("payload_json") or {}
    if not isinstance(payload, dict):
        payload = {}
    if principal is not None and not is_ai_admin(principal):
        payload = sanitize_user_control_input(payload)
    show_runtime_controls = principal is None or is_ai_admin(principal)
    skill_ids = payload.get("skill_ids")
    mcp_tool_ids = payload.get("mcp_tool_ids")
    resource_limits = payload.get("resource_limits")
    sandbox_mode = payload.get("sandbox_mode")
    browser_enabled = payload.get("browser_enabled")
    title = str(row.get("title") or "")
    role = row.get("role")
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


def run_playback_summary(run: dict[str, object], principal: AuthPrincipal) -> dict[str, object]:
    raw_skill_id = str(run["skill_id"])
    show_raw_skill = is_ai_admin(principal)
    return {
        "run_id": str(run["id"]),
        "session_id": str(run["session_id"]),
        "agent_id": str(run["agent_id"]),
        "skill_id": raw_skill_id if show_raw_skill else None,
        "capability_id": capability_id_from_skill(raw_skill_id, run["agent_id"]),
        "trace_id": str(run.get("trace_id") or standard_trace_id(str(run["id"]))),
        "contract_version": run_contract_version(run),
        "executor_schema_version": executor_result_schema_version(run) if show_raw_skill else None,
        "status": normalize_run_status(str(run["status"])),
        "progress": progress_for_status(str(run["status"])),
        "cancel_requested_at": run.get("cancel_requested_at"),
        "cancel_requested_by": run.get("cancel_requested_by"),
        "error_code": run.get("error_code") if show_raw_skill else ("run_failed" if run.get("error_code") else None),
        "error_message": run.get("error_message") if show_raw_skill else sanitize_public_text(run.get("error_message")),
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


async def seed_copied_run_steps(conn, *, tenant_id: str, run_id: str, copied_input: dict[str, Any]) -> None:
    steps = copied_input.get("multi_agent_steps")
    if not isinstance(steps, list):
        return
    resume = copied_input.get("resume")
    completed_outputs = resume.get("completed_step_outputs") if isinstance(resume, dict) else {}
    if not isinstance(completed_outputs, dict):
        completed_outputs = {}
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
            "seeded_from": "copy_run",
        }
        if raw_step.get("sandbox_mode") is not None:
            payload_json["sandbox_mode"] = raw_step.get("sandbox_mode")
        if raw_step.get("browser_enabled") is not None:
            payload_json["browser_enabled"] = raw_step.get("browser_enabled")
        resource_limits = raw_step.get("resource_limits") or raw_step.get("resourceLimits")
        if isinstance(resource_limits, dict):
            payload_json["resource_limits"] = resource_limits
        if reused:
            payload_json.update(
                {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": copied_from_run_id,
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


def resolve_run_selector(request: CreateRunRequest, principal: AuthPrincipal) -> tuple[str, str]:
    if request.skill_id and not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="raw_skill_selector_forbidden")
    if request.skill_id:
        return request.agent_id, request.skill_id

    capability_id = request.capability_id or capability_id_from_skill(None, request.agent_id)
    capability = get_capability(str(capability_id)) if capability_id else None
    if capability is None:
        raise HTTPException(status_code=400, detail="capability_required")
    if request.agent_id and request.agent_id != capability.agent_id:
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
                    tenant_id=principal.tenant_id,
                    run_id=copied["run_id"],
                    skill_version=copied_skill_version,
                )
                await repositories.append_event(
                    conn,
                    tenant_id=principal.tenant_id,
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
                    tenant_id=principal.tenant_id,
                    workspace_id=str(copied["workspace_id"]),
                    user_id=principal.user_id,
                    session_id=str(copied["session_id"]),
                    run_id=str(copied["run_id"]),
                    trace_id=standard_trace_id(str(copied["run_id"])),
                    agent_id=str(copied["agent_id"]),
                    skill_id=str(copied["skill_id"]),
                    input_payload=copied["input"] if isinstance(copied.get("input"), dict) else {},
                    message_ids=[],
                    file_ids=list(copied["file_ids"]),
                    source="copy_run",
                )
                for event in initial_run_event_specs(
                    agent_id=str(copied["agent_id"]),
                    skill_id=str(copied["skill_id"]),
                    skill_version=copied_skill_version,
                    executor_type=str(copied["executor_type"]),
                    file_ids=list(copied["file_ids"]),
                    source="copy_run",
                ):
                    await repositories.append_event(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=copied["run_id"],
                        event_type=event["event_type"],
                        stage=event["stage"],
                        message=event["message"],
                        payload=event["payload"],
                    )
                queue_payload = _validate_queue_payload_for_enqueue(
                    {
                        "tenant_id": principal.tenant_id,
                        "workspace_id": copied["workspace_id"],
                        "user_id": principal.user_id,
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
                    tenant_id=principal.tenant_id,
                    run_id=copied["run_id"],
                    copied_input=copied["input"],
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
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    if result["status"] == "cancelled":
        await remove_queued_run(tenant_id=principal.tenant_id, run_id=run_id)
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
    show_raw_skill = is_ai_admin(principal)
    multi_agent_snapshot = multi_agent_snapshot_from_steps(run_id, steps, principal=principal)
    if multi_agent_snapshot is not None:
        result_payload["multi_agent"] = multi_agent_snapshot
    input_payload = run["input_json"] if isinstance(run["input_json"], dict) else {}
    if not show_raw_skill:
        input_payload = sanitize_public_payload(redact_raw_skill_references(input_payload))
        result_payload = sanitize_public_payload(redact_raw_skill_references(result_payload))
        if not isinstance(input_payload, dict):
            input_payload = {}
        if not isinstance(result_payload, dict):
            result_payload = {}
    error_code = run["error_code"] if show_raw_skill else ("run_failed" if run.get("error_code") else None)
    error_message = run["error_message"] if show_raw_skill else sanitize_public_text(run.get("error_message"))
    return RunResponse(
        run_id=run["id"],
        session_id=run["session_id"],
        agent_id=run["agent_id"],
        skill_id=raw_skill_id if show_raw_skill else None,
        capability_id=capability_id_from_skill(raw_skill_id, run["agent_id"]),
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

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capabilities import get_capability
from app.context_builder import ensure_public_context_provenance, record_initial_context_snapshot
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
from app.queue_payload_validation import queue_payload_invalid_detail
from app.control_plane_contracts import (
    HASH_LIKE_VALUE_PATTERN,
    artifact_lineage_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import (
    capability_id_from_skill,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
)
from app.queue import enqueue_run, get_queue_insight, get_run_queue_position, remove_queued_run
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.run_projection import (
    artifact_card,
    executor_result_schema_version,
    multi_agent_snapshot_from_steps,
    normalize_run_status,
    progress_for_status,
    public_text_or_fallback,
    run_contract_version,
    run_event_response,
    run_step_response,
)
from app.run_provenance import (
    readiness_public_text,
    readiness_raw_projection_terms,
    run_checkpoint_audit_snapshot,
    run_playback_summary,
    run_provenance_snapshot,
    safe_provenance_graph_id,
)
from app.run_control_readiness import (
    dispatch_claim_candidate as _dispatch_claim_candidate,
    dispatch_tick_candidate as _dispatch_tick_candidate,
    run_control_readiness_snapshot,
)
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, stop_sandbox_leases
from app.runtime.sandbox.container_provider import create_container_provider
from app.settings import get_settings
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision
from app.skills.registry import BuiltinSkillRegistry
from app.validation import assert_safe_principal_user_id

router = APIRouter()
RUN_PLAYBACK_CONTRACT_VERSION = "ai-platform.run-playback.v1"
RUN_RESUME_MANIFEST_CONTRACT_VERSION = "ai-platform.run-resume-manifest.v1"
MULTI_AGENT_DISPATCH_CLAIM_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-claim.v1"
MULTI_AGENT_DISPATCH_HANDOFF_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-handoff.v1"
MULTI_AGENT_DISPATCH_TICK_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-tick.v1"
_CAPABILITY_REVOCATION_LIFECYCLE_ERRORS = {"agent_or_skill_not_found", "skill_inactive", "mcp_tool_disabled"}


def _raise_if_capability_revoked(exc: Exception) -> None:
    if str(exc) in _CAPABILITY_REVOCATION_LIFECYCLE_ERRORS:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc


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
        raise HTTPException(status_code=500, detail=queue_payload_invalid_detail(exc)) from exc


def _validate_principal_user_id_for_route(principal: AuthPrincipal) -> None:
    try:
        assert_safe_principal_user_id(principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_principal_user_id") from exc


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
        if not is_user_runnable_status(version.get("status")):
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
    text = readiness_public_text(value, raw_terms=raw_terms)
    if text and not _resume_manifest_has_fingerprint(text):
        return text
    fallback_text = readiness_public_text(fallback, raw_terms=raw_terms)
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
        safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
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
    raw_terms = readiness_raw_projection_terms(run)
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
        run_summary["error_message"] = readiness_public_text(
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


def _resume_manifest_source_run_candidates(steps: list[dict[str, object]]) -> list[str]:
    source_run_ids: set[str] = set()
    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if not payload.get("checkpoint_reuse_pending"):
            continue
        source_run_id = safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
        if source_run_id:
            source_run_ids.add(source_run_id)
    return sorted(source_run_ids)


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


def _run_context_ref_from_payload(
    context_snapshot: dict[str, object],
    *,
    message_count: int,
    file_count: int,
    artifact_count: int,
    memory_record_count: int,
) -> dict[str, object] | None:
    if not isinstance(context_snapshot, dict):
        return None
    context_ref = ensure_public_context_provenance(
        context_snapshot,
        source="stored_context_snapshot",
        message_count=message_count,
        file_count=file_count,
        artifact_count=artifact_count,
        memory_record_count=memory_record_count,
        preserve_stored_input_keys=True,
    )
    used_context_summary = context_ref.get("used_context_summary")
    referenced_materials = context_ref.get("referenced_materials")
    if not isinstance(used_context_summary, dict) or not isinstance(referenced_materials, dict):
        return None
    return {
        "source": used_context_summary.get("source"),
        "referenced_materials": {
            "message_count": _safe_public_count(referenced_materials.get("message_count")),
            "file_count": _safe_public_count(referenced_materials.get("file_count")),
            "artifact_count": _safe_public_count(referenced_materials.get("artifact_count")),
            "memory_record_count": _safe_public_count(referenced_materials.get("memory_record_count")),
        },
        "used_context_summary": {
            "source": used_context_summary.get("source"),
            "input_keys": used_context_summary.get("input_keys") if isinstance(used_context_summary.get("input_keys"), list) else [],
            "memory_policy_source": used_context_summary.get("memory_policy_source") or "not_recorded",
            "long_term_memory_read": bool(used_context_summary.get("long_term_memory_read")),
        },
        "latest_artifact_version": context_ref.get("latest_artifact_version"),
        "execution_tier": context_ref.get("execution_tier"),
        "context_pack_version": context_ref.get("context_pack_version"),
        "context_pack_generated_at": context_ref.get("context_pack_generated_at"),
    }


def run_context_ref(run: dict[str, object]) -> dict[str, object] | None:
    source_input = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    context_snapshot = source_input.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        return None
    return _run_context_ref_from_payload(
        context_snapshot,
        message_count=_context_material_count(context_snapshot, "message_count"),
        file_count=_context_material_count(context_snapshot, "file_count"),
        artifact_count=_context_material_count(context_snapshot, "artifact_count"),
        memory_record_count=_context_material_count(context_snapshot, "memory_record_count"),
    )


def run_context_ref_from_snapshot_row(row: dict[str, object]) -> dict[str, object] | None:
    context_snapshot = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    return _run_context_ref_from_payload(
        context_snapshot,
        message_count=len(row.get("included_message_ids") or []),
        file_count=len(row.get("included_file_ids") or []),
        artifact_count=len(row.get("included_artifact_ids") or []),
        memory_record_count=len(row.get("included_memory_record_ids") or []),
    )


def _context_material_count(context_snapshot: dict[str, object], key: str) -> int:
    materials = context_snapshot.get("referenced_materials")
    value = materials.get(key) if isinstance(materials, dict) else None
    return _safe_public_count(value)


def _safe_public_count(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


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
    await repositories.enforce_user_active_run_admission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )


async def queue_insight_for_status(status: str, tenant_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
    if normalize_run_status(status) != "queued":
        return None
    return await get_queue_insight(tenant_id, user_id=user_id)


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


def _strip_server_owned_control_metadata(input_payload: object, *, redact_public: bool = False) -> dict[str, Any]:
    return repositories.normalize_run_input_for_enqueue(input_payload, redact_public=redact_public)


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


def _copied_run_source_run_id(authorized_source_run_id: str | None) -> str | None:
    return safe_provenance_graph_id("source_run_id", authorized_source_run_id)


def _run_execution_input(run: dict[str, Any]) -> dict[str, Any]:
    input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = input_json.get("input") if isinstance(input_json.get("input"), dict) else input_json
    return execution_input if isinstance(execution_input, dict) else {}


def _persisted_owner_principal(run: dict[str, Any], *, tenant_id: str) -> AuthPrincipal:
    return AuthPrincipal(
        user_id=str(run.get("user_id") or ""),
        display_name=str(run.get("user_id") or ""),
        tenant_id=tenant_id,
        department_id=str(run.get("principal_department_id") or ""),
        roles=[str(role) for role in run.get("principal_roles") or []],
        source=str(run.get("auth_source") or ""),
    )


async def _authorize_persisted_run_for_queue(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    run: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], AuthPrincipal]:
    persisted_run = run or await repositories.get_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        for_update=True,
    )
    if persisted_run is None:
        raise RepositoryNotFoundError("run_not_found")
    owner_principal = _persisted_owner_principal(persisted_run, tenant_id=tenant_id)
    await repositories.authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=str(persisted_run.get("agent_id") or ""),
        skill_id=str(persisted_run.get("skill_id") or ""),
        normalized_input=_run_execution_input(persisted_run),
        principal_department_id=owner_principal.department_id,
        principal_roles=owner_principal.roles,
        is_admin=is_ai_admin(owner_principal),
        permissions=owner_principal.permissions,
    )
    return persisted_run, owner_principal


async def prepare_copied_run_for_queue(
    conn,
    *,
    copied: dict[str, Any],
    principal: AuthPrincipal,
    source: str,
    queue_principal: AuthPrincipal | None = None,
    authorized_source_run_id: str | None = None,
) -> dict[str, Any]:
    effective_principal = queue_principal or principal
    snapshot_auth_source = copied.get("auth_source") if queue_principal is not None else principal.source
    copied_input = copied["input"] if isinstance(copied.get("input"), dict) else {}
    source_run_id = _copied_run_source_run_id(authorized_source_run_id)
    copied_skill_version = str(copied.get("skill_version") or "")
    await repositories.authorize_run_capabilities(
        conn,
        tenant_id=effective_principal.tenant_id,
        agent_id=str(copied["agent_id"]),
        skill_id=str(copied["skill_id"]),
        normalized_input=copied_input,
        principal_department_id=effective_principal.department_id,
        principal_roles=effective_principal.roles,
        is_admin=is_ai_admin(effective_principal),
        permissions=effective_principal.permissions,
    )
    skill_manifests = await _governed_skill_manifest_pins(
        conn,
        skill_id=str(copied["skill_id"]),
        input_payload=copied_input,
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
    skill_manifests = attach_skill_snapshot_governance(
        skill_manifests,
        release_decision=copied.get("release_decision") if isinstance(copied.get("release_decision"), dict) else {},
    )
    await repositories.update_run_auth_snapshot(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        principal_roles=effective_principal.roles,
        principal_department_id=effective_principal.department_id,
        auth_source=snapshot_auth_source,
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
        input_payload=copied_input,
        message_ids=[],
        file_ids=list(copied["file_ids"]),
        source=source,
        source_run_id=source_run_id,
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
    _validate_principal_user_id_for_route(principal)
    tenant_id = principal.tenant_id
    user_id = principal.user_id
    resolved_agent_id, resolved_skill_id = resolve_run_selector(request, principal)
    try:
        run_input = _strip_server_owned_control_metadata(
            request.input,
            redact_public=not is_ai_admin(principal),
        )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    try:
        async with transaction() as conn:
            skill = await repositories.authorize_run_capabilities(
                conn,
                tenant_id=tenant_id,
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                normalized_input=run_input,
                principal_department_id=principal.department_id,
                principal_roles=principal.roles,
                is_admin=is_ai_admin(principal),
                permissions=principal.permissions,
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
            skill_manifests = attach_skill_snapshot_governance(
                skill_manifests,
                release_decision=release_decision_payload,
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
                    "skill_manifests": queue_payload["skill_manifests"],
                },
                principal_roles=principal.roles,
                principal_department_id=principal.department_id,
                auth_source=principal.source,
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
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
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
    _validate_principal_user_id_for_route(principal)
    try:
        async with transaction() as conn:
            await enforce_user_active_run_limit(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
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
                    authorized_source_run_id=run_id,
                )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id, user_id=principal.user_id),
    )


@router.post("/runs/{run_id}/retry", response_model=RunControlResponse)
async def retry_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    _validate_principal_user_id_for_route(principal)
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
                    authorized_source_run_id=run_id,
                )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id, user_id=principal.user_id),
    )


@router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    """Queue a platform-controlled resume run for an authorized checkpointed source."""
    _validate_principal_user_id_for_route(principal)
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
                    authorized_source_run_id=run_id,
                )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    queue_position = await enqueue_run(queue_payload)
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id, user_id=principal.user_id),
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
    plan["queue_insight"] = await get_queue_insight(principal.tenant_id, user_id=principal.user_id)
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
        await queue_insight_for_status(run_status, principal.tenant_id, user_id=principal.user_id)
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
    conflict_detail: str | None = None
    try:
        async with transaction() as conn:
            await _authorize_persisted_run_for_queue(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
            )
            try:
                copied = await repositories.create_multi_agent_dispatch_child_run(
                    conn,
                    tenant_id=principal.tenant_id,
                    parent_run_id=run_id,
                    dispatch_id=dispatch_id,
                    handed_off_by=principal.user_id,
                    active_run_admission_limit=int(get_settings().max_active_runs_per_user),
                )
            except RepositoryConflictError as exc:
                conflict_detail = str(exc)
            else:
                owner_principal = AuthPrincipal(
                    user_id=str(copied["user_id"]),
                    display_name=str(copied.get("user_id") or ""),
                    tenant_id=principal.tenant_id,
                    department_id=str(copied.get("principal_department_id") or ""),
                    roles=[str(role) for role in copied.get("principal_roles") or []],
                    source=str(copied.get("auth_source") or ""),
                )
                queue_payload = await prepare_copied_run_for_queue(
                    conn,
                    copied={**copied, "run_id": copied["child_run_id"]},
                    principal=principal,
                    queue_principal=owner_principal,
                    source="multi_agent_dispatch_handoff",
                    authorized_source_run_id=run_id,
                )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if conflict_detail is not None:
        raise HTTPException(status_code=409, detail=conflict_detail)
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
        queue_insight=await get_queue_insight(principal.tenant_id, include_user_breakdown=True),
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
    conflict_detail: str | None = None
    try:
        async with transaction() as conn:
            run = await repositories.get_run(conn, tenant_id=principal.tenant_id, run_id=run_id, for_update=True)
            if run is None:
                raise HTTPException(status_code=404, detail="run_not_found")
            await _authorize_persisted_run_for_queue(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                run=run,
            )
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
            try:
                copied = await repositories.create_multi_agent_dispatch_child_run(
                    conn,
                    tenant_id=principal.tenant_id,
                    parent_run_id=run_id,
                    dispatch_id=str(claim["dispatch_id"]),
                    handed_off_by=principal.user_id,
                    active_run_admission_limit=int(get_settings().max_active_runs_per_user),
                )
            except RepositoryConflictError as exc:
                conflict_detail = str(exc)
            else:
                owner_principal = AuthPrincipal(
                    user_id=str(copied["user_id"]),
                    display_name=str(copied.get("user_id") or ""),
                    tenant_id=principal.tenant_id,
                    department_id=str(copied.get("principal_department_id") or ""),
                    roles=[str(role) for role in copied.get("principal_roles") or []],
                    source=str(copied.get("auth_source") or ""),
                )
                queue_payload = await prepare_copied_run_for_queue(
                    conn,
                    copied={**copied, "run_id": copied["child_run_id"]},
                    principal=principal,
                    queue_principal=owner_principal,
                    source="multi_agent_dispatch_tick",
                    authorized_source_run_id=run_id,
                )
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if conflict_detail is not None:
        raise HTTPException(status_code=409, detail=conflict_detail)
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
        queue_insight=await get_queue_insight(principal.tenant_id, include_user_breakdown=True),
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
    queue_insight = await queue_insight_for_status(run_status, tenant_id, user_id=principal.user_id)
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
        input_payload = sanitize_user_control_input(input_payload)
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
        context_snapshots = await repositories.list_context_snapshots(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )

    projected_events = [
        run_event_response(run_id, row, principal=principal)
        for row in events
        if event_visible_to_principal(row, principal)
    ]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_cards = [run_step_response(row, principal=principal) for row in steps]
    next_after_sequence = next_sequence_from_rows(events, fallback=after_sequence)
    latest_context_ref = (
        run_context_ref_from_snapshot_row(context_snapshots[0])
        if context_snapshots
        else run_context_ref(run)
    )
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
        "context_ref": latest_context_ref,
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
            queue_insight = await queue_insight_for_status(status, principal.tenant_id, user_id=principal.user_id)
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

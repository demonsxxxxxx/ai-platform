import re
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import UUID4

from app import repositories
from app.agent_profiles import reauthorize_pinned_run_for_replay
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capabilities import get_capability
from app.context_builder import record_initial_context_snapshot
from app.context.file_continuity import has_file_input_mode, primary_file_ids_for_run
from app.context_manifest import public_context_manifest_projection
from app.db import transaction
from app.models import (
    CreateRunRequest,
    CreateRunResponse,
    QueueRunPayload,
    RunControlMutationResponse,
    RunControlOperationResponse,
    RunControlResponse,
    RunResponse,
)
from app.product_events import initial_run_event_specs
from app.queue_payload_validation import queue_payload_invalid_detail
from app.control_plane_contracts import (
    HARNESS_CHAT_EXECUTOR_TYPE,
    HASH_LIKE_VALUE_PATTERN,
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
    RUN_PAYLOAD_SCHEMA_VERSION_V2,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import (
    capability_id_from_skill,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    public_execution_kind_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
    strip_server_owned_control_metadata,
)
from app.queue import (
    QueueAdmissionMetadata,
    QueueAdmissionRejected,
    enqueue_run,
    get_queue_insight,
    get_run_queue_position,
    read_queue_admission,
    remove_queued_run,
)
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.run_projection import (
    artifact_card,
    executor_result_schema_version,
    normalize_run_status,
    progress_for_status,
    public_text_or_fallback,
    public_terminal_projection,
    run_contract_version,
    run_event_response,
    run_step_response,
    run_step_responses,
)
from app.run_provenance import (
    readiness_public_text,
    readiness_raw_projection_terms,
    run_checkpoint_audit_snapshot,
    run_playback_summary,
    run_provenance_snapshot,
    safe_provenance_graph_id,
)
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    contains_persisted_platform_multi_agent_control,
    contains_platform_multi_agent_control,
)
from app.run_admission_terminalization import terminalize_retired_platform_multi_agent_run
from app.run_control_readiness import run_control_readiness_snapshot
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, stop_sandbox_leases
from app.runtime.sandbox.container_provider import create_container_provider
from app.settings import get_settings
from app.skills.lifecycle import is_user_runnable_status
from app.tool_permission_lifecycle import drain_run_tool_permission_terminalization, reconcile_terminalized_permission_run
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
    validate_skill_manifest_refs,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision
from app.skills.registry import BuiltinSkillRegistry
from app.validation import assert_safe_principal_user_id

router = APIRouter()
RUN_PLAYBACK_CONTRACT_VERSION = "ai-platform.run-playback.v1"
RUN_RESUME_MANIFEST_CONTRACT_VERSION = "ai-platform.run-resume-manifest.v1"
_CAPABILITY_REVOCATION_LIFECYCLE_ERRORS = {"agent_or_skill_not_found", "skill_inactive", "mcp_tool_disabled"}


def _raise_if_capability_revoked(exc: Exception) -> None:
    if str(exc) in _CAPABILITY_REVOCATION_LIFECYCLE_ERRORS:
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc


async def _audit_capability_denial(
    principal: AuthPrincipal,
    error: repositories.RepositoryAuthorizationError,
    *,
    source: str,
) -> None:
    if error.denial is None:
        return
    async with transaction() as conn:
        await repositories.append_capability_authorization_denial_audit(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            error=error,
            source=source,
        )


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
    run_id: str,
    result: dict[str, Any],
) -> list[Exception]:
    failures: list[Exception] = []
    if result["status"] == "cancelled":
        try:
            await remove_queued_run(tenant_id=tenant_id, run_id=run_id)
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
        validated = QueueRunPayload.model_validate(payload)
        validate_skill_manifest_refs(validated.skill_manifests)
        return validated.model_dump(mode="json")
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


def _release_decision_event_payload(release_decision: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    return {
        **release_decision,
        "skill_id": skill_id,
        "skill_version": release_decision.get("selected_version"),
        "visible_to_user": False,
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


def _ordinary_resume_manifest_step(public_step: dict[str, object]) -> dict[str, object]:
    payload = public_step.get("payload") if isinstance(public_step.get("payload"), dict) else {}
    dependencies = payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else []
    return {
        "step_id": str(public_step["step_id"]),
        "step_key": str(public_step["step_id"]),
        "status": str(public_step["status"]),
        "title": public_step.get("title"),
        "role": None,
        "sequence": int(public_step.get("sequence") or 0),
        "depends_on": [str(item) for item in dependencies if isinstance(item, str)],
        "reuse_intent": "reuse_pending" if payload.get("checkpoint_reuse_pending") else "rerun",
        "source_run_id": None,
    }


def run_resume_manifest_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    authorized_source_run_ids: set[str] | None = None,
) -> dict[str, object]:
    """Return read-only checkpoint reuse intent for a copied run."""
    show_raw_skill = is_ai_admin(principal)
    if not show_raw_skill:
        manifest_steps = [_ordinary_resume_manifest_step(step) for step in run_step_responses(steps, principal=principal)]
        source_run_id = None
    else:
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
    resume_enabled = counts["reuse_pending"] > 0
    return {
        "contract_version": RUN_RESUME_MANIFEST_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
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
    manifest = context_snapshot.get("context_manifest")
    if not isinstance(manifest, dict):
        return {"context_window": _degraded_context_window()}
    projection = public_context_manifest_projection(manifest)
    context_window = projection.get("context_window")
    return {
        "context_window": context_window
        if isinstance(context_window, dict)
        else _degraded_context_window(),
    }


def _degraded_context_window() -> dict[str, object]:
    """Return the safe public state when an exact context binding is unavailable."""

    return {
        "status": "degraded",
        "selection_version": "session-context-v1",
        "history_candidate_count": 0,
        "history_inline_count": 0,
        "history_trimmed_count": 0,
        "legacy_history_excluded": False,
        "selected_file_names": [],
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
    for index, row in enumerate(rows, start=1):
        step_key = str(row.get("step_key") or row.get("stepKey") or f"step-{index}")
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
                    str(row.get("title") or step_key)
                    if include_raw_skill
                    else (public_text_or_fallback(row.get("title"), step_key) or "step")
                ),
                "depends_on": payload.get("depends_on") or [],
            }
        )
    raw_skill_id = str(run.get("skill_id") or "")
    capability_id = capability_id_from_skill(raw_skill_id, run.get("agent_id"))
    capability = get_capability(str(capability_id)) if capability_id else None
    if include_raw_skill and raw_skill_id:
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


async def _compensate_enqueue_failure(
    *,
    principal: AuthPrincipal,
    run_id: str,
    trace_id: str | None = None,
) -> None:
    """Leave a committed run in a truthful terminal state when queue admission fails."""

    async with transaction() as conn:
        await repositories.mark_run_enqueue_failed(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            trace_id=trace_id or standard_trace_id(run_id),
        )


def _strip_server_owned_control_metadata(input_payload: object, *, redact_public: bool = False) -> dict[str, Any]:
    return repositories.normalize_run_input_for_enqueue(input_payload, redact_public=redact_public)


def _copied_run_source_run_id(authorized_source_run_id: str | None) -> str | None:
    return safe_provenance_graph_id("source_run_id", authorized_source_run_id)


def _run_execution_input(run: dict[str, Any]) -> dict[str, Any]:
    input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = input_json.get("input") if isinstance(input_json.get("input"), dict) else input_json
    return execution_input if isinstance(execution_input, dict) else {}


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
    copied_snapshot = repositories.copied_run_execution_snapshot(copied)
    copied_input = copied_snapshot["input"]
    execution_kind = str(
        copied_snapshot.get("execution_kind") or RUN_EXECUTION_KIND_SKILL
    )
    if contains_persisted_platform_multi_agent_control(copied):
        raise RepositoryConflictError(PLATFORM_MULTI_AGENT_NOT_SUPPORTED)
    source_run_id = _copied_run_source_run_id(authorized_source_run_id)
    copied_skill_version = str(copied_snapshot["skill_version"] or "")
    skill_manifest_refs = copied_snapshot["skill_manifests"]
    skill_manifests = await repositories.materialize_run_skill_manifests(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=str(copied["run_id"]),
        skill_manifest_refs=skill_manifest_refs,
    )
    copied_skill_id = copied.get("skill_id")
    copied_profile_snapshot = copied_snapshot.get("agent_profile")
    copied_skill_set = (
        copied_profile_snapshot.get("skill_set")
        if isinstance(copied_profile_snapshot, dict)
        and isinstance(copied_profile_snapshot.get("skill_set"), list)
        else None
    )
    if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        if copied_skill_id is not None:
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        await repositories.authorize_selected_chat_mcp_tools(
            conn,
            tenant_id=effective_principal.tenant_id,
            tool_ids=repositories.extract_run_mcp_tool_ids(copied_input),
            principal_department_id=effective_principal.department_id,
            principal_roles=effective_principal.roles,
            is_admin=is_ai_admin(effective_principal),
            permissions=effective_principal.permissions,
        )
    elif execution_kind == RUN_EXECUTION_KIND_SKILL:
        if not isinstance(copied_skill_id, str) or not copied_skill_id.strip():
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        await repositories.authorize_replay_run_capabilities(
            conn,
            tenant_id=effective_principal.tenant_id,
            agent_id=str(copied["agent_id"]),
            skill_id=copied_skill_id,
            pinned_version=copied_skill_version,
            pinned_executor_type=str(copied_snapshot.get("executor_type") or ""),
            skill_manifests=skill_manifests,
            skill_set=copied_skill_set,
            normalized_input=copied_input,
            principal_department_id=effective_principal.department_id,
            principal_roles=effective_principal.roles,
            is_admin=is_ai_admin(effective_principal),
            permissions=effective_principal.permissions,
        )
    else:
        raise RepositoryConflictError("run_execution_skill_identity_mismatch")
    copied["skill_version"] = copied_skill_version
    copied["release_decision"] = copied_snapshot["release_decision"]
    await repositories.update_run_auth_snapshot(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        principal_roles=effective_principal.roles,
        principal_department_id=effective_principal.department_id,
        auth_source=snapshot_auth_source,
        authz_policy_version=effective_principal.authz_policy_version,
        authority_source=(
            effective_principal.authority_source or effective_principal.source
        ),
        authority_checked_at=effective_principal.authority_checked_at or None,
    )
    if execution_kind == RUN_EXECUTION_KIND_SKILL:
        await repositories.append_event(
            conn,
            tenant_id=effective_principal.tenant_id,
            run_id=copied["run_id"],
            event_type="skill_release_decision",
            stage="control",
            message="已锁定 Skill 发布决策",
            payload=_release_decision_event_payload(
                copied.get("release_decision")
                if isinstance(copied.get("release_decision"), dict)
                else {},
                skill_id=copied_skill_id,
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
        skill_id=(
            str(copied["skill_id"]) if copied.get("skill_id") is not None else None
        ),
        input_payload=copied_input,
        message_ids=[],
        file_ids=list(copied_snapshot["file_ids"]),
        source=source,
        source_run_id=source_run_id,
    )
    for event in initial_run_event_specs(
        agent_id=str(copied["agent_id"]),
        skill_id=(
            str(copied["skill_id"]) if copied.get("skill_id") is not None else None
        ),
        skill_version=copied_skill_version,
        executor_type=str(copied_snapshot["executor_type"]),
        file_ids=list(copied_snapshot["file_ids"]),
        source=source,
        execution_kind=execution_kind,
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
    queue_snapshot = repositories.copied_run_execution_snapshot(
        {
            **copied_snapshot,
            "skill_version": copied_skill_version,
            "release_decision": copied["release_decision"],
            "skill_manifests": skill_manifest_refs,
            "context_snapshot_id": context_ref["context_snapshot_id"],
            "context_snapshot": context_ref,
        }
    )
    queue_payload = _validate_queue_payload_for_enqueue(
        {
            "tenant_id": effective_principal.tenant_id,
            "workspace_id": copied["workspace_id"],
            "user_id": effective_principal.user_id,
            "session_id": copied["session_id"],
            "run_id": copied["run_id"],
            "agent_id": copied["agent_id"],
            "execution_kind": execution_kind,
            "skill_id": copied["skill_id"],
            **queue_snapshot,
        }
    )
    validated_snapshot = repositories.copied_run_execution_snapshot(queue_payload)
    await repositories.update_run_input_execution_snapshot(
        conn,
        tenant_id=effective_principal.tenant_id,
        run_id=copied["run_id"],
        execution_snapshot=validated_snapshot,
    )
    return queue_payload


def resolve_run_selector(request: CreateRunRequest, principal: AuthPrincipal) -> tuple[str, str | None]:
    requested_agent_id = internal_agent_id_for_request(request.agent_id) or request.agent_id
    if request.selected_skill is not None:
        if request.skill_id:
            raise HTTPException(status_code=400, detail="skill_selector_conflict")
        if request.selected_skill.skill_id == LEGACY_SYNTHETIC_CHAT_SKILL_ID:
            raise HTTPException(status_code=400, detail="general_chat_is_not_a_skill")
        return requested_agent_id, request.selected_skill.skill_id
    if request.skill_id and not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="raw_skill_selector_forbidden")
    if request.skill_id:
        if request.skill_id == LEGACY_SYNTHETIC_CHAT_SKILL_ID:
            raise HTTPException(status_code=400, detail="general_chat_is_not_a_skill")
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
    if contains_platform_multi_agent_control(request.input):
        raise HTTPException(status_code=400, detail=PLATFORM_MULTI_AGENT_NOT_SUPPORTED)
    tenant_id = principal.tenant_id
    user_id = principal.user_id
    resolved_agent_id, resolved_skill_id = resolve_run_selector(request, principal)
    execution_kind = (
        RUN_EXECUTION_KIND_HARNESS_CHAT
        if resolved_skill_id is None
        else RUN_EXECUTION_KIND_SKILL
    )
    try:
        run_input = _strip_server_owned_control_metadata(
            request.input,
            redact_public=not is_ai_admin(principal),
        )
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="create_run")
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    try:
        async with transaction() as conn:
            if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
                harness_agent = await repositories.get_agent(
                    conn,
                    tenant_id=tenant_id,
                    agent_id=resolved_agent_id,
                )
                if (
                    harness_agent is None
                    or str(harness_agent.get("agent_type") or "") != "chat"
                ):
                    raise RepositoryConflictError("harness_chat_agent_unavailable")
                await repositories.authorize_selected_chat_mcp_tools(
                    conn,
                    tenant_id=tenant_id,
                    tool_ids=repositories.extract_run_mcp_tool_ids(run_input),
                    principal_department_id=principal.department_id,
                    principal_roles=principal.roles,
                    is_admin=is_ai_admin(principal),
                    permissions=principal.permissions,
                )
                skill = None
                executor_type = HARNESS_CHAT_EXECUTOR_TYPE
                input_modes = ["chat"]
            else:
                assert resolved_skill_id is not None
                authorization_kwargs = {
                    "tenant_id": tenant_id,
                    "agent_id": resolved_agent_id,
                    "skill_id": resolved_skill_id,
                    "normalized_input": run_input,
                    "principal_department_id": principal.department_id,
                    "principal_roles": principal.roles,
                    "is_admin": is_ai_admin(principal),
                    "permissions": principal.permissions,
                }
                if request.selected_skill is not None:
                    skill = await repositories.authorize_selected_run_capabilities(
                        conn,
                        expected_version=request.selected_skill.expected_version,
                        rollout_key=user_id,
                        **authorization_kwargs,
                    )
                else:
                    skill = await repositories.authorize_run_capabilities(
                        conn,
                        **authorization_kwargs,
                    )
                executor_type = str(skill["executor_type"])
                input_modes = list(skill.get("input_modes") or [])
            reusable_file_rows = []
            if request.session_id and not request.file_ids and has_file_input_mode(input_modes):
                reusable_file_rows = await repositories.list_authorized_session_input_files(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=user_id,
                    session_id=request.session_id,
                )
            primary_file_ids = primary_file_ids_for_run(
                requested_file_ids=request.file_ids,
                reusable_rows=[dict(row) for row in reusable_file_rows],
                input_modes=input_modes,
            )
            if has_file_input_mode(input_modes) and not primary_file_ids:
                raise RepositoryConflictError("file_required_for_skill")
            await enforce_user_active_run_limit(conn, tenant_id=tenant_id, user_id=user_id)
            if execution_kind == RUN_EXECUTION_KIND_SKILL:
                assert skill is not None and resolved_skill_id is not None
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
                skill_manifests = repositories.pin_primary_skill_mcp_tool_ids(
                    skill_manifests,
                    skill_id=resolved_skill_id,
                    mcp_tool_ids=repositories.run_mcp_tool_ids_for_skill(skill, run_input),
                )
            else:
                skill_version = None
                release_decision_payload = {}
                skill_manifests = []
            skill_manifest_transport = repositories.skill_manifest_refs(skill_manifests)
            session_id = request.session_id or repositories.new_id("ses")
            run_id = repositories.new_id("run")
            base_queue_payload = {
                "tenant_id": tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": user_id,
                "session_id": session_id,
                "run_id": run_id,
                "agent_id": resolved_agent_id,
                "execution_kind": execution_kind,
                "skill_id": resolved_skill_id,
                "file_ids": primary_file_ids,
                "input": run_input,
                "executor_type": executor_type,
                "skill_version": skill_version,
                "release_decision": release_decision_payload,
                "skill_manifests": skill_manifest_transport,
                **(
                    {"schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2}
                    if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
                    else {}
                ),
            }
            queue_payload = _validate_queue_payload_for_enqueue(base_queue_payload)
            await repositories.ensure_workspace_belongs_to_tenant(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
            )
            await repositories.authorize_files_for_run(
                conn,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=request.file_ids,
                input_modes=input_modes,
            )
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
                execution_kind=execution_kind,
                skill_id=resolved_skill_id,
                input_json={
                    "input": run_input,
                    "file_ids": primary_file_ids,
                    "execution_kind": execution_kind,
                    "executor_type": executor_type,
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                    "skill_manifests": queue_payload["skill_manifests"],
                    "schema_version": queue_payload["schema_version"],
                },
                principal_roles=principal.roles,
                principal_department_id=principal.department_id,
                auth_source=principal.source,
                authz_policy_version=principal.authz_policy_version,
                authority_source=principal.authority_source or principal.source,
                authority_checked_at=principal.authority_checked_at or None,
                run_id=run_id,
            )
            if execution_kind == RUN_EXECUTION_KIND_SKILL:
                await repositories.insert_run_skill_snapshots_at_creation(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    skill_manifests=skill_manifests,
                    release_decision=release_decision_payload,
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
                file_ids=primary_file_ids,
                source="runs_api",
                include_session_history=bool(request.session_id),
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
                executor_type=executor_type,
                file_ids=primary_file_ids,
                source="runs_api",
                execution_kind=execution_kind,
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
            if execution_kind == RUN_EXECUTION_KIND_SKILL:
                assert resolved_skill_id is not None
                await repositories.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type="skill_release_decision",
                    stage="control",
                    message="已锁定 Skill 发布决策",
                    payload=_release_decision_event_payload(
                        release_decision_payload,
                        skill_id=resolved_skill_id,
                    ),
                )
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="create_run")
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        await enqueue_run(queue_payload)
    except Exception as exc:
        await _compensate_enqueue_failure(principal=principal, run_id=run_id)
        raise HTTPException(status_code=503, detail="queue_enqueue_failed") from exc
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
            await reauthorize_pinned_run_for_replay(
                conn,
                principal=principal,
                run_id=run_id,
            )
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
        await _audit_capability_denial(principal, exc, source="copy_run")
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
    try:
        async with transaction() as conn:
            await reauthorize_pinned_run_for_replay(
                conn,
                principal=principal,
                run_id=str(copied["run_id"]),
            )
            queue_position = await enqueue_run(queue_payload)
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="copy_run")
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await _compensate_enqueue_failure(principal=principal, run_id=str(copied["run_id"]))
        raise HTTPException(status_code=503, detail="queue_enqueue_failed") from exc
    return RunControlResponse(
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status="queued",
        queue_position=queue_position,
        queue_insight=await queue_insight_for_status("queued", principal.tenant_id, user_id=principal.user_id),
    )


def _run_control_queue_payload(
    operation: dict[str, Any],
    *,
    principal: AuthPrincipal,
) -> dict[str, Any]:
    """Rebuild the exact immutable queue payload stored on a committed child."""

    snapshot = repositories.copied_run_execution_snapshot(operation.get("input_json"))
    return _validate_queue_payload_for_enqueue(
        {
            "tenant_id": principal.tenant_id,
            "workspace_id": operation["workspace_id"],
            "user_id": operation["user_id"],
            "session_id": operation["session_id"],
            "run_id": operation["run_id"],
            "agent_id": operation["agent_id"],
            "skill_id": operation["skill_id"],
            **snapshot,
        }
    )


async def _ensure_run_control_queue_admission(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
    principal: AuthPrincipal,
) -> QueueAdmissionMetadata:
    """Recover or idempotently admit one immutable committed control child."""

    async with transaction() as conn:
        await reauthorize_pinned_run_for_replay(
            conn,
            principal=principal,
            run_id=str(queue_payload["run_id"]),
        )
        if check_existing:
            try:
                existing = await read_queue_admission(queue_payload)
            except QueueAdmissionRejected as exc:
                raise HTTPException(status_code=503, detail="queue_admission_recovery_failed") from exc
            except Exception:
                existing = None
            if existing is not None:
                return existing

        try:
            queue_position = await enqueue_run(queue_payload)
            return QueueAdmissionMetadata(
                queue_position=queue_position,
                queue_admission_ordinal=0,
                message_id="",
                source="idempotent_enqueue",
            )
        except Exception as enqueue_error:
            try:
                recovered = await read_queue_admission(queue_payload)
            except Exception:
                recovered = None
            if recovered is not None:
                return recovered
            # The DB mapping and immutable queued child remain authoritative. A
            # same-operation replay can safely retry the deterministic Redis admit.
            raise HTTPException(status_code=503, detail="queue_admission_unconfirmed") from enqueue_error


async def _run_control_queue_admission_state(
    operation: dict[str, Any],
    *,
    principal: AuthPrincipal,
) -> Literal["admitted", "pending", "settled", "unknown"]:
    status = str(operation.get("status") or "").strip().lower()
    if status not in {"pending", "queued"}:
        return "settled"
    try:
        payload = _run_control_queue_payload(operation, principal=principal)
        admission = await read_queue_admission(payload)
    except Exception:
        return "unknown"
    return "admitted" if admission is not None else "pending"


async def _mutate_run_control_child(
    *,
    run_id: str,
    action: Literal["retry", "resume"],
    operation_id: UUID4,
    principal: AuthPrincipal,
) -> RunControlMutationResponse:
    normalized_operation_id = str(operation_id)
    created = False
    retired_control_rejected = False
    queue_payload: dict[str, Any] | None = None
    copied: dict[str, Any] | None = None
    try:
        async with transaction() as conn:
            # Global order: operation advisory -> user admission advisory ->
            # source-run row. The resolver takes the same first lock.
            await repositories.acquire_run_control_operation_lock(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                source_run_id=run_id,
                action=action,
                operation_id=normalized_operation_id,
            )
            copied = await repositories.get_run_control_operation(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                source_run_id=run_id,
                action=action,
                operation_id=normalized_operation_id,
            )
            if copied is not None:
                retired_control_rejected = contains_persisted_platform_multi_agent_control(
                    copied.get("input_json")
                )
                if retired_control_rejected:
                    child_run_id = str(copied["run_id"])
                    await terminalize_retired_platform_multi_agent_run(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=child_run_id,
                    )
            if copied is None:
                await enforce_user_active_run_limit(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                )
                await reauthorize_pinned_run_for_replay(
                    conn,
                    principal=principal,
                    run_id=run_id,
                )
                mutation = (
                    repositories.retry_run_as_new_task
                    if action == "retry"
                    else repositories.resume_run_as_new_task
                )
                copied = await mutation(
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
                        source=f"{action}_run",
                        authorized_source_run_id=run_id,
                    )
                    await repositories.record_run_control_operation(
                        conn,
                        tenant_id=principal.tenant_id,
                        source_run_id=run_id,
                        child_run_id=str(copied["run_id"]),
                        action=action,
                        operation_id=normalized_operation_id,
                    )
                    created = True
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source=f"{action}_run")
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        _raise_if_capability_revoked(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if retired_control_rejected:
        raise HTTPException(status_code=409, detail=PLATFORM_MULTI_AGENT_NOT_SUPPORTED)
    if copied is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    status = str(copied.get("status") or "queued")
    queue_position: int | None = None
    if status == "queued":
        if queue_payload is None:
            queue_payload = _run_control_queue_payload(copied, principal=principal)
        try:
            admission = await _ensure_run_control_queue_admission(
                queue_payload,
                check_existing=not created,
                principal=principal,
            )
        except repositories.RepositoryAuthorizationError as exc:
            await _audit_capability_denial(principal, exc, source=f"{action}_run")
            raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
        except SkillVersionMaterializationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RepositoryNotFoundError as exc:
            _raise_if_capability_revoked(exc)
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RepositoryConflictError as exc:
            _raise_if_capability_revoked(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        queue_position = admission.queue_position
    return RunControlMutationResponse(
        source_run_id=run_id,
        run_id=copied["run_id"],
        session_id=copied["session_id"],
        status=status,
        action=action,
        operation_id=operation_id,
        queue_admission="admitted",
        queue_position=queue_position,
        queue_insight=(
            await queue_insight_for_status(status, principal.tenant_id, user_id=principal.user_id)
            if status == "queued"
            else None
        ),
    )


@router.post("/runs/{run_id}/retry", response_model=RunControlMutationResponse)
async def retry_run(
    run_id: str,
    operation_id: UUID4 | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlMutationResponse:
    """Queue or resolve one idempotent retry operation."""

    _validate_principal_user_id_for_route(principal)
    return await _mutate_run_control_child(
        run_id=run_id,
        action="retry",
        operation_id=operation_id or uuid4(),
        principal=principal,
    )


@router.post("/runs/{run_id}/resume", response_model=RunControlMutationResponse)
async def resume_run(
    run_id: str,
    operation_id: UUID4 | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlMutationResponse:
    """Queue or resolve one idempotent checkpoint-resume operation."""

    _validate_principal_user_id_for_route(principal)
    return await _mutate_run_control_child(
        run_id=run_id,
        action="resume",
        operation_id=operation_id or uuid4(),
        principal=principal,
    )


@router.get(
    "/runs/{run_id}/control-operations/{action}/{operation_id}",
    response_model=RunControlOperationResponse,
)
async def get_run_control_operation(
    run_id: str,
    action: Literal["retry", "resume"],
    operation_id: UUID4,
    response: Response,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlOperationResponse:
    """Resolve one exact operation after linearizing with its concurrent mutation."""

    _validate_principal_user_id_for_route(principal)
    response.headers["Cache-Control"] = "private, no-store"
    normalized_operation_id = str(operation_id)
    async with transaction() as conn:
        await repositories.acquire_run_control_operation_lock(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            source_run_id=run_id,
            action=action,
            operation_id=normalized_operation_id,
        )
        source = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if source is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        operation = await repositories.get_run_control_operation(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            source_run_id=run_id,
            action=action,
            operation_id=normalized_operation_id,
        )
    if operation is None:
        return RunControlOperationResponse(
            source_run_id=run_id,
            action=action,
            operation_id=operation_id,
            status="absent",
        )
    queue_admission = await _run_control_queue_admission_state(
        operation,
        principal=principal,
    )
    return RunControlOperationResponse(
        source_run_id=run_id,
        action=action,
        operation_id=operation_id,
        run_id=operation["run_id"],
        session_id=operation["session_id"],
        status=str(operation.get("status") or "queued"),
        queue_admission=queue_admission,
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
        if is_ai_admin(principal):
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
        initial_progress = result.pop("_permission_terminalization_progress", None)
        if initial_progress is not None:
            await reconcile_terminalized_permission_run(
                tenant_id=principal.tenant_id,
                run_id=run_id,
                progress=initial_progress,
                transaction_factory=transaction,
            )
        progress = await drain_run_tool_permission_terminalization(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
        )
        if progress is not None and progress.is_terminal():
            progressed_status = str(progress.status or result["status"])
            if result["status"] not in {"succeeded", "failed", "cancelled"} or progressed_status in {
                "failed",
                "cancelled",
            }:
                result["status"] = progressed_status
        await reconcile_terminalized_permission_run(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            progress=progress,
            transaction_factory=transaction,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    queue_cleanup_failures = await _remove_cancelled_queue_payloads(
        tenant_id=principal.tenant_id,
        run_id=run_id,
        result=result,
    )
    try:
        stopped_sandbox_leases = await stop_sandbox_leases(
            result.get("active_sandbox_leases"),
            reason="cancel_requested",
            provider_factory=create_container_provider,
        )
    except SandboxRuntimeCleanupError as exc:
        failed_lease_ids = [str(lease["id"]) for lease in exc.failed_leases if lease.get("id")]
        try:
            async with transaction() as conn:
                if exc.stopped_leases:
                    await _release_stopped_cancel_leases(
                        conn,
                        tenant_id=principal.tenant_id,
                        reason="cancel_requested",
                        leases=exc.stopped_leases,
                        trace_id=result.get("trace_id"),
                    )
                await repositories.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=str(result["run_id"]),
                    trace_id=result.get("trace_id"),
                    user_id=principal.user_id,
                    requested_by_role="owner",
                    reason="cancel_requested",
                    status="failed",
                    lease_ids=failed_lease_ids,
                    failures=exc.failures,
                )
        except Exception as persistence_exc:
            raise HTTPException(status_code=503, detail="sandbox_cleanup_persistence_unavailable") from persistence_exc
        raise HTTPException(status_code=502, detail="sandbox_runtime_cleanup_failed") from exc
    if stopped_sandbox_leases:
        stopped_lease_ids = [str(lease["id"]) for lease in stopped_sandbox_leases if lease.get("id")]
        try:
            async with transaction() as conn:
                await _release_stopped_cancel_leases(
                    conn,
                    tenant_id=principal.tenant_id,
                    reason="cancel_requested",
                    leases=stopped_sandbox_leases,
                    trace_id=result.get("trace_id"),
                )
                await repositories.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=str(result["run_id"]),
                    trace_id=result.get("trace_id"),
                    user_id=principal.user_id,
                    requested_by_role="owner",
                    reason="cancel_requested",
                    status="succeeded",
                    lease_ids=stopped_lease_ids,
                    failures=[],
                )
        except Exception as persistence_exc:
            raise HTTPException(status_code=503, detail="sandbox_cleanup_persistence_unavailable") from persistence_exc
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
        bound_context_snapshot = None
        if run is not None and hasattr(conn, "execute"):
            bound_context_snapshot = await repositories.get_bound_executor_context_snapshot(
                conn,
                tenant_id=tenant_id,
                workspace_id=str(run.get("workspace_id") or ""),
                user_id=principal.user_id,
                session_id=str(run.get("session_id") or ""),
                run_id=run_id,
            )
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
    raw_skill_id = str(run.get("skill_id") or "")
    raw_agent_id = str(run["agent_id"])
    execution_kind = str(run.get("execution_kind") or RUN_EXECUTION_KIND_SKILL)
    show_raw_skill = is_ai_admin(principal)
    projected_execution_kind = (
        execution_kind
        if show_raw_skill
        else public_execution_kind_for_projection(
            execution_kind,
            agent_id=raw_agent_id,
            skill_id=raw_skill_id,
        )
    )
    terminal_projection = (
        public_terminal_projection(
            run_status,
            run.get("error_code"),
        )
        if not show_raw_skill
        else None
    )
    input_payload = run["input_json"] if isinstance(run["input_json"], dict) else {}
    if show_raw_skill:
        input_payload = sanitize_public_payload(strip_server_owned_control_metadata(input_payload))
        result_payload = sanitize_public_payload(result)
    else:
        input_payload = sanitize_user_control_input(input_payload)
        result_payload = (
            dict(terminal_projection["result"])
            if terminal_projection is not None
            else (
                {}
                if normalize_run_status(run_status) in {"queued", "running"}
                else sanitize_public_payload(redact_raw_skill_references(result))
            )
        )
    if not isinstance(input_payload, dict):
        input_payload = {}
    input_payload.pop("context_snapshot_id", None)
    input_payload.pop("context_snapshot", None)
    if not isinstance(result_payload, dict):
        result_payload = {}
    result_payload.pop("multi_agent", None)
    if terminal_projection is not None:
        error_code = terminal_projection["error_code"]
        error_message = str(terminal_projection["message"])
    else:
        if show_raw_skill:
            error_code = sanitize_public_text(run.get("error_code"))
            error_message = sanitize_public_text(run.get("error_message"))
        else:
            public_summary = run_playback_summary(run, principal)
            error_code = public_summary["error_code"]
            error_message = str(public_summary["error_message"])
    context_ref = (
        run_context_ref_from_snapshot_row(bound_context_snapshot)
        if isinstance(bound_context_snapshot, dict)
        else {"context_window": _degraded_context_window()}
    )
    return RunResponse(
        run_id=run["id"],
        session_id=run["session_id"],
        agent_id=raw_agent_id
        if show_raw_skill
        else public_agent_id_for_projection(raw_agent_id, raw_skill_id),
        execution_kind=projected_execution_kind,
        skill_id=(raw_skill_id or None) if show_raw_skill else None,
        capability_id=capability_id_from_skill(raw_skill_id, raw_agent_id),
        trace_id=(
            str(run.get("trace_id") or standard_trace_id(str(run["id"])))
            if show_raw_skill
            else standard_trace_id(str(run["id"]))
        ),
        contract_version=contract_version,
        executor_schema_version=executor_schema_version if show_raw_skill else None,
        status=normalize_run_status(str(run["status"])),
        progress=progress_for_status(run["status"]),
        input=input_payload,
        result=result_payload,
        artifacts=[artifact_card(row, principal=principal) for row in artifacts],
        events=[run_event_response(run_id, row, principal=principal) for row in events if event_visible_to_principal(row, principal)],
        steps=run_step_responses(steps, principal=principal),
        queue_position=queue_position,
        queue_insight=queue_insight,
        cancel_requested_at=run.get("cancel_requested_at"),
        cancel_requested_by=run.get("cancel_requested_by"),
        error_code=error_code,
        error_message=error_message,
        context_window=context_ref["context_window"],
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
        bound_context_snapshot = None
        if hasattr(conn, "execute"):
            bound_context_snapshot = await repositories.get_bound_executor_context_snapshot(
                conn,
                tenant_id=tenant_id,
                workspace_id=str(run.get("workspace_id") or ""),
                user_id=principal.user_id,
                session_id=str(run.get("session_id") or ""),
                run_id=run_id,
            )

    projected_events = [
        run_event_response(run_id, row, principal=principal)
        for row in events
        if event_visible_to_principal(row, principal)
    ]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_cards = run_step_responses(steps, principal=principal)
    next_after_sequence = next_sequence_from_rows(events, fallback=after_sequence)
    latest_context_ref = (
        run_context_ref_from_snapshot_row(bound_context_snapshot)
        if isinstance(bound_context_snapshot, dict)
        else {"context_window": _degraded_context_window()}
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
        "context_ref": latest_context_ref,
        "context_window": latest_context_ref["context_window"],
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
    return {"run_id": run_id, "steps": run_step_responses(steps, principal=principal)}

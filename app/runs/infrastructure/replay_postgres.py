"""Replay persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.auth import ADMIN_ROLE_ALIASES
from app.auth import normalize_roles
from app.control_plane_contracts import EXECUTOR_RESULT_SCHEMA_VERSION
from app.control_plane_contracts import HARNESS_CHAT_EXECUTOR_TYPE
from app.control_plane_contracts import RUN_CONTRACT_VERSION
from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT
from app.control_plane_contracts import RUN_EXECUTION_KIND_SKILL
from app.control_plane_contracts import RUN_PAYLOAD_SCHEMA_VERSION
from app.control_plane_contracts import RUN_PAYLOAD_SCHEMA_VERSION_V2
from app.control_plane_contracts import artifact_lineage_contract
from app.control_plane_contracts import is_legacy_synthetic_chat_identity
from app.control_plane_contracts import standard_trace_id
from app.conversations.infrastructure.postgres import append_message
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.mcp.repository import authorize_selected_chat_mcp_tools
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import RUN_INPUT_MAX_BYTES
from app.platform.postgres.limits import compact_json_dumps
from app.platform.postgres.values import _require_json_size
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from app.projection_redaction import sanitize_user_control_input
from app.runs.infrastructure.capability_admission_postgres import authorize_replay_run_capabilities
from app.runs.infrastructure.capability_admission_postgres import extract_run_mcp_tool_ids
from app.runs.infrastructure.capability_admission_postgres import normalize_run_input_for_enqueue
from app.runs.infrastructure.capability_admission_postgres import require_replay_source_identity
from app.runs.infrastructure.capability_admission_postgres import strip_caller_run_auth_snapshot_fields
from app.runs.infrastructure.creation_postgres import ACTIVE_RUN_STATUSES
from app.runs.infrastructure.creation_postgres import RETRYABLE_RUN_STATUSES
from app.runs.infrastructure.creation_postgres import allocate_session_run_generation
from app.runs.infrastructure.creation_postgres import get_authorized_run
from app.runs.infrastructure.postgres import get_active_resume_for_source_run
from app.runs.infrastructure.postgres import get_active_retry_for_source_run
from app.skills.infrastructure.run_snapshots_postgres import insert_run_skill_snapshots_at_creation
from app.skills.infrastructure.run_snapshots_postgres import materialize_run_skill_manifests
from app.skills.infrastructure.run_snapshots_postgres import skill_manifest_refs
from app.skills.infrastructure.run_snapshots_postgres import validate_run_skill_snapshots_for_dispatch
from app.streaming.infrastructure.run_events_postgres import append_event
from psycopg import AsyncConnection
from typing import Any


async def copy_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    if source is None:
        return None
    source_input = source["input_json"] if isinstance(source.get("input_json"), dict) else {}
    sanitized_source_input = strip_caller_run_auth_snapshot_fields(sanitize_user_control_input(source_input))
    inherited_roles = normalize_roles(source.get("principal_roles") or [])
    inherited_department_id = str(source.get("principal_department_id") or "")
    inherited_auth_source = source.get("auth_source")
    inherited_authz_policy_version = int(source.get("authz_policy_version") or 1)
    inherited_authority_source = str(
        source.get("authority_source") or inherited_auth_source or ""
    )
    inherited_authority_checked_at = source.get("authority_checked_at")
    source_execution_input = (
        source_input.get("input")
        if isinstance(source_input.get("input"), dict)
        else source_input
    )
    if isinstance(source_execution_input, dict):
        source_execution_input = normalize_run_input_for_enqueue(source_execution_input, redact_public=True)
        source_execution_input.pop("resume", None)
    else:
        source_execution_input = {}
    source_execution_snapshot = copied_run_execution_snapshot(source_input)
    source_execution_kind = str(
        source.get("execution_kind")
        or source_execution_snapshot.get("execution_kind")
        or RUN_EXECUTION_KIND_SKILL
    )
    admitted_profile_revision, admitted_profile_hash = (
        admitted_agent_profile_pins_for_copy(
            source,
            source_execution_snapshot,
        )
    )
    upgrade_legacy_chat_to_harness = (
        is_legacy_synthetic_chat_identity(
            agent_id=source.get("agent_id"),
            skill_id=source.get("skill_id"),
            execution_kind=source_execution_kind,
        )
        and admitted_profile_revision is None
        and admitted_profile_hash is None
    )
    execution_kind = (
        RUN_EXECUTION_KIND_HARNESS_CHAT
        if upgrade_legacy_chat_to_harness
        else source_execution_kind
    )
    copied_skill_id = None if upgrade_legacy_chat_to_harness else source.get("skill_id")
    skill_version = str(source_execution_snapshot.get("skill_version") or "")
    skill_refs = (
        source_execution_snapshot["skill_manifests"]
        if "skill_manifests" in source_execution_snapshot
        else []
    )
    skill_manifests = await materialize_run_skill_manifests(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        skill_manifest_refs=skill_refs,
    )
    skill_transport = skill_manifest_refs(skill_manifests)
    release_decision_payload = source_execution_snapshot.get("release_decision") or {}
    executor_type = str(source_execution_snapshot.get("executor_type") or "")
    if upgrade_legacy_chat_to_harness:
        skill_version = None
        skill_transport = []
        skill_manifests = []
        release_decision_payload = {}
        executor_type = HARNESS_CHAT_EXECUTOR_TYPE
    if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        if (
            copied_skill_id is not None
            or skill_version
            or release_decision_payload
            or skill_manifests
            or executor_type != HARNESS_CHAT_EXECUTOR_TYPE
        ):
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        await authorize_selected_chat_mcp_tools(
            conn,
            tenant_id=tenant_id,
            tool_ids=extract_run_mcp_tool_ids(source_execution_input),
            principal_department_id=inherited_department_id,
            principal_roles=inherited_roles,
            is_admin=bool(set(inherited_roles).intersection(ADMIN_ROLE_ALIASES)),
            permissions=[],
        )
    elif execution_kind == RUN_EXECUTION_KIND_SKILL:
        if (
            not isinstance(source.get("skill_id"), str)
            or not str(source.get("skill_id")).strip()
        ):
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        require_replay_source_identity(
            pinned_version=skill_version,
            pinned_executor_type=executor_type,
            release_decision=release_decision_payload,
            skill_manifests=skill_manifests,
        )
        await validate_run_skill_snapshots_for_dispatch(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            skill_manifests=skill_manifests,
            release_decision=release_decision_payload,
        )
        await authorize_replay_run_capabilities(
            conn,
            tenant_id=tenant_id,
            agent_id=source["agent_id"],
            skill_id=source["skill_id"],
            pinned_version=skill_version,
            pinned_executor_type=executor_type,
            skill_manifests=skill_manifests,
            normalized_input=source_execution_input,
            principal_department_id=inherited_department_id,
            principal_roles=inherited_roles,
            is_admin=bool(set(inherited_roles).intersection(ADMIN_ROLE_ALIASES)),
            permissions=[],
        )
    else:
        raise RepositoryConflictError("run_execution_skill_identity_mismatch")
    new_run_id = new_id("run")
    copied_execution_input = {**source_execution_input, "copied_from_run_id": run_id}
    completed_step_outputs, completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if completed_step_outputs:
        resume_payload: dict[str, Any] = {
            "copied_from_run_id": run_id,
            "completed_step_outputs": completed_step_outputs,
        }
        if completed_step_checkpoints:
            resume_payload["completed_step_checkpoints"] = completed_step_checkpoints
        copied_execution_input["resume"] = resume_payload
    copied_input_json = {
        **sanitized_source_input,
        "input": copied_execution_input,
        "copied_from_run_id": run_id,
    }
    copied_input_json.update(
        execution_kind=execution_kind,
        executor_type=executor_type,
        skill_version=skill_version,
        release_decision=release_decision_payload,
        skill_manifests=skill_transport,
        context_snapshot_id=None,
        context_snapshot={},
        schema_version=(
            RUN_PAYLOAD_SCHEMA_VERSION_V2
            if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
            else str(
                source_execution_snapshot.get("schema_version")
                or RUN_PAYLOAD_SCHEMA_VERSION
            )
        ),
    )
    copied_input_json.update(preserved_server_owned_execution_snapshot(source_execution_snapshot))
    copied_execution_snapshot = copied_run_execution_snapshot(copied_input_json)
    copied_input_json.update(copied_execution_snapshot)
    _require_json_size(copied_input_json, max_bytes=RUN_INPUT_MAX_BYTES, code="run_input_too_large")
    session_generation = await allocate_session_run_generation(
        conn,
        tenant_id=tenant_id,
        workspace_id=str(source["workspace_id"]),
        user_id=user_id,
        session_id=str(source["session_id"]),
        agent_id=str(source["agent_id"]),
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, execution_kind, skill_id,
          trace_id, schema_version, executor_schema_version,
          principal_roles, principal_department_id, auth_source,
          authz_policy_version, authority_source, authority_checked_at,
          admitted_agent_profile_revision, admitted_agent_profile_hash,
          status, input_json, queued_at, copied_from_run_id, session_generation
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, now(), %s, %s)
        """,
        (
            new_run_id,
            tenant_id,
            source["workspace_id"],
            source["session_id"],
            user_id,
            source["agent_id"],
            execution_kind,
            copied_skill_id,
            standard_trace_id(new_run_id),
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(inherited_roles),
            inherited_department_id,
            inherited_auth_source,
            inherited_authz_policy_version,
            inherited_authority_source,
            inherited_authority_checked_at,
            admitted_profile_revision,
            admitted_profile_hash,
            dumps_json(copied_input_json),
            run_id,
            session_generation,
        ),
    )
    if execution_kind == RUN_EXECUTION_KIND_SKILL:
        await insert_run_skill_snapshots_at_creation(
            conn,
            tenant_id=tenant_id,
            run_id=new_run_id,
            skill_manifests=skill_manifests,
            release_decision=release_decision_payload,
        )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=new_run_id,
        event_type="run_created",
        stage="control",
        message="已复制为新任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_message(
        conn,
        tenant_id=tenant_id,
        session_id=source["session_id"],
        run_id=new_run_id,
        role="assistant",
        content="已复制为新任务，将继续执行未完成步骤。",
        metadata_json={
            "type": "copy_run_anchor",
            "copied_from_run_id": run_id,
            "agent_id": source["agent_id"],
            "skill_id": copied_skill_id,
        },
    )
    return {
        "session_id": source["session_id"],
        "run_id": new_run_id,
        "agent_id": source["agent_id"],
        "execution_kind": execution_kind,
        "skill_id": copied_skill_id,
        "workspace_id": source["workspace_id"],
        "principal_roles": inherited_roles,
        "principal_department_id": inherited_department_id,
        "auth_source": inherited_auth_source,
        "release_policy_version": "",
        **copied_execution_snapshot,
    }


async def retry_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status not in RETRYABLE_RUN_STATUSES:
        raise RepositoryConflictError("status_not_retryable")
    active_retry = await get_active_retry_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_retry is not None:
        raise RepositoryConflictError("retry_already_active")
    copied = await copy_run_as_new_task(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="retry_requested",
        stage="control",
        message="已请求重试",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_retry_created",
        stage="control",
        message="已创建重试任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.retry",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


async def resume_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Create a queued resume child run from a non-active source with reusable output."""
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status in ACTIVE_RUN_STATUSES:
        raise RepositoryConflictError("active_run")
    completed_step_outputs, _completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if not completed_step_outputs:
        raise RepositoryConflictError("no_checkpoint_outputs")
    active_resume = await get_active_resume_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_resume is not None:
        raise RepositoryConflictError("resume_already_active")
    copied = await copy_run_as_new_task(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="resume_requested",
        stage="control",
        message="已请求从 checkpoint 恢复",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_resume_created",
        stage="control",
        message="已创建恢复任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.resume",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


def copied_run_execution_snapshot(input_json: object) -> dict[str, Any]:
    """Project every QueueRunPayload non-identity field from copied run input JSON."""
    source = input_json if isinstance(input_json, dict) else {}
    file_ids = source.get("file_ids")
    execution_input = source.get("input")
    release_decision = source.get("release_decision")
    skill_manifests = source.get("skill_manifests")
    context_snapshot = source.get("context_snapshot")
    skill_version = source.get("skill_version")
    context_snapshot_id = source.get("context_snapshot_id")
    model_id = source.get("model_id")
    model_value = source.get("model_value")
    agent_profile = source.get("agent_profile")
    schema_version = source.get("schema_version")
    execution_kind = source.get("execution_kind")
    snapshot = {
        "file_ids": list(file_ids) if isinstance(file_ids, list) else [],
        "input": dict(execution_input) if isinstance(execution_input, dict) else {},
        "executor_type": str(source.get("executor_type") or ""),
        "execution_kind": (
            str(execution_kind)
            if execution_kind
            in {RUN_EXECUTION_KIND_HARNESS_CHAT, RUN_EXECUTION_KIND_SKILL}
            else RUN_EXECUTION_KIND_SKILL
        ),
        "skill_version": skill_version if isinstance(skill_version, str) else None,
        "release_decision": dict(release_decision) if isinstance(release_decision, dict) else {},
        "skill_manifests": (
            [dict(item) for item in skill_manifests]
            if isinstance(skill_manifests, list)
            and all(isinstance(item, dict) for item in skill_manifests)
            else skill_manifests
            if "skill_manifests" in source
            else []
        ),
        "context_snapshot_id": context_snapshot_id if isinstance(context_snapshot_id, str) else None,
        "context_snapshot": dict(context_snapshot) if isinstance(context_snapshot, dict) else {},
        "model_id": model_id if isinstance(model_id, str) else None,
        "model_value": model_value if isinstance(model_value, str) else None,
        "schema_version": schema_version
        if isinstance(schema_version, str) and schema_version
        else RUN_PAYLOAD_SCHEMA_VERSION,
    }
    if isinstance(agent_profile, dict):
        snapshot["agent_profile"] = dict(agent_profile)
    return snapshot


def preserved_server_owned_execution_snapshot(source_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return admitted execution facts that sanitization must never reconstruct from a caller."""

    preserved = {
        "model_id": source_snapshot.get("model_id"),
        "model_value": source_snapshot.get("model_value"),
    }
    agent_profile = source_snapshot.get("agent_profile")
    if isinstance(agent_profile, dict):
        preserved["agent_profile"] = dict(agent_profile)
    return preserved


def admitted_agent_profile_pins_for_copy(
    source_run: dict[str, Any],
    source_snapshot: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Require a profile's private snapshot and durable pins to remain identical on descendants."""

    revision = source_run.get("admitted_agent_profile_revision")
    content_hash = source_run.get("admitted_agent_profile_hash")
    profile = source_snapshot.get("agent_profile")
    if profile is None:
        if revision is not None or content_hash is not None:
            raise RepositoryConflictError("agent_profile_snapshot_missing")
        return None, None
    if not isinstance(profile, dict):
        raise RepositoryConflictError("agent_profile_snapshot_invalid")
    profile_revision = profile.get("revision")
    profile_hash = profile.get("content_hash")
    profile_agent_id = profile.get("agent_id")
    if (
        not isinstance(profile_revision, int)
        or isinstance(profile_revision, bool)
        or profile_revision < 1
        or not isinstance(profile_hash, str)
        or not profile_hash
        or profile_agent_id != source_run.get("agent_id")
        or revision != profile_revision
        or content_hash != profile_hash
        or not isinstance(source_snapshot.get("model_id"), str)
        or not source_snapshot.get("model_id")
        or not isinstance(source_snapshot.get("model_value"), str)
        or not source_snapshot.get("model_value")
    ):
        raise RepositoryConflictError("agent_profile_snapshot_invalid")
    return profile_revision, profile_hash


async def update_run_input_execution_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    execution_snapshot: dict[str, Any],
) -> None:
    """Merge one canonical copied-run execution snapshot in a tenant-scoped update."""
    canonical_snapshot = copied_run_execution_snapshot(execution_snapshot)
    serialized_snapshot = compact_json_dumps(canonical_snapshot)
    cursor = await conn.execute(
        """
        select id, input_json
        from runs
        where tenant_id = %s
          and id = %s
          and (
            context_snapshot_id is null
            and coalesce(%s::jsonb->>'context_snapshot_id', '') = ''
            and coalesce(%s::jsonb->'context_snapshot'->>'context_snapshot_id', '') = ''
            or (
              context_snapshot_id is not null
              and %s::jsonb->>'context_snapshot_id' = context_snapshot_id
              and %s::jsonb->'context_snapshot'->>'context_snapshot_id' = context_snapshot_id
            )
          )
        for update
        """,
        (
            tenant_id,
            run_id,
            serialized_snapshot,
            serialized_snapshot,
            serialized_snapshot,
            serialized_snapshot,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("context_snapshot_binding_invalid")
    existing_input = row.get("input_json")
    if not isinstance(existing_input, dict):
        existing_input = {}
    merged_input = {**existing_input, **canonical_snapshot}
    _require_json_size(
        merged_input,
        max_bytes=RUN_INPUT_MAX_BYTES,
        code="run_input_too_large",
    )
    updated = await conn.execute(
        """
        update runs
        set input_json = %s::jsonb
        where tenant_id = %s and id = %s
        returning id
        """,
        (compact_json_dumps(merged_input), tenant_id, run_id),
    )
    if await updated.fetchone() is None:
        raise RepositoryConflictError("context_snapshot_binding_invalid")


async def _completed_steps_for_resume(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    cursor = await conn.execute(
        """
        select id, step_key, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and status = 'succeeded'
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    rows = await cursor.fetchall()
    outputs: dict[str, str] = {}
    checkpoints: dict[str, dict[str, str]] = {}
    for row in rows:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict) or payload.get("output") is None:
            continue
        step_key = str(row["step_key"])
        outputs[step_key] = str(payload["output"])
        lineage = artifact_lineage_contract(
            {
                "checkpoint_id": payload.get("checkpoint_id"),
                "source_step_id": payload.get("source_step_id") or row.get("id"),
            },
            source_run_id=payload.get("copied_from_run_id") or run_id,
        )
        checkpoint_id = lineage.get("checkpoint_id")
        source_step_id = lineage.get("source_step_id")
        source_run_id = lineage.get("source_run_id")
        if checkpoint_id and source_step_id and source_run_id:
            checkpoints[step_key] = {
                "checkpoint_id": str(checkpoint_id),
                "source_step_id": str(source_step_id),
                "copied_from_run_id": str(source_run_id),
            }
    return outputs, checkpoints

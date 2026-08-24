"""PostgreSQL persistence for Run identity, admission, and terminal intent."""

from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError
from app.runs.domain.attempt_lifecycle import (
    RUN_ATTEMPT_OWNER_KINDS,
    TERMINAL_RUN_ATTEMPT_STATUSES,
    decide_run_attempt_transition,
)
from app.runs.domain.execution_spec import ExecutionSpec


def _dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _validated_attempt_owner(*, owner_kind: str, owner_id: str) -> tuple[str, str]:
    if owner_kind not in RUN_ATTEMPT_OWNER_KINDS:
        raise ValueError("run_attempt_owner_kind_invalid")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("run_attempt_owner_id_invalid")
    return owner_kind, owner_id.strip()


async def create_run_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    ordinal: int,
    owner_kind: str,
    owner_id: str,
    queue_attempt_id: str,
    execution_spec: ExecutionSpec,
) -> dict[str, Any]:
    """Create the only legal initial attempt from one validated canonical spec."""

    owner = _validated_attempt_owner(owner_kind=owner_kind, owner_id=owner_id)
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("run_attempt_id_invalid")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise ValueError("run_attempt_ordinal_invalid")
    if not isinstance(queue_attempt_id, str) or not queue_attempt_id.strip():
        raise ValueError("run_attempt_queue_attempt_id_invalid")
    if not isinstance(execution_spec, ExecutionSpec):
        raise ValueError("run_attempt_execution_spec_invalid")
    spec_mapping = execution_spec.to_mapping()
    if spec_mapping["tenant_id"] != tenant_id or spec_mapping["run_id"] != run_id:
        raise ValueError("run_attempt_execution_spec_identity_mismatch")
    canonical_json = execution_spec.canonical_json.decode("utf-8")
    cursor = await conn.execute(
        """
        insert into run_attempts(
          id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
          owner_generation, queue_attempt_id, execution_spec_schema_version,
          execution_spec_json, execution_spec_canonical_json, execution_spec_sha256
        ) values (
          %s, %s, %s, %s, 'created', %s, %s,
          1, %s, %s, %s::jsonb, %s, %s
        )
        returning *
        """,
        (
            attempt_id.strip(),
            tenant_id,
            run_id,
            ordinal,
            owner[0],
            owner[1],
            queue_attempt_id.strip(),
            spec_mapping["schema_version"],
            canonical_json,
            canonical_json,
            execution_spec.spec_sha256,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("run_attempt_create_conflict")
    return dict(row)


async def transition_run_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    expected_status: str,
    requested_status: str,
    expected_owner_kind: str,
    expected_owner_id: str,
    expected_owner_generation: int,
    next_owner_kind: str | None = None,
    next_owner_id: str | None = None,
    queue_message_id: str | None = None,
    lease_expires_at: Any | None = None,
    last_heartbeat_at: Any | None = None,
    terminal_reason: str = "",
    error_code: str | None = None,
) -> dict[str, Any]:
    """Persist one owner-fenced attempt transition and its Run projection."""

    current_owner = _validated_attempt_owner(
        owner_kind=expected_owner_kind,
        owner_id=expected_owner_id,
    )
    if (next_owner_kind is None) is not (next_owner_id is None):
        raise ValueError("run_attempt_next_owner_incomplete")
    next_owner = (
        current_owner
        if next_owner_kind is None or next_owner_id is None
        else _validated_attempt_owner(
            owner_kind=next_owner_kind,
            owner_id=next_owner_id,
        )
    )
    decision = decide_run_attempt_transition(
        current_status=expected_status,
        requested_status=requested_status,
        owner_generation=expected_owner_generation,
        expected_owner_generation=expected_owner_generation,
    )
    if requested_status == "expired" and next_owner[0] != "reconciler":
        raise ValueError("run_attempt_expiry_reconciler_required")
    if queue_message_id is not None and not queue_message_id.strip():
        raise ValueError("run_attempt_queue_message_id_invalid")
    if requested_status in TERMINAL_RUN_ATTEMPT_STATUSES and not terminal_reason.strip():
        raise ValueError("run_attempt_terminal_reason_required")

    if not decision.did_transition:
        cursor = await conn.execute(
            """
            select *
            from run_attempts
            where tenant_id = %s
              and run_id = %s
              and id = %s
              and status = %s
              and owner_kind = %s
              and owner_id = %s
              and owner_generation = %s
            """,
            (
                tenant_id,
                run_id,
                attempt_id,
                expected_status,
                current_owner[0],
                current_owner[1],
                expected_owner_generation,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryConflictError("run_attempt_transition_conflict")
        return dict(row)

    cursor = await conn.execute(
        """
        with locked as materialized (
          select run_attempts.id
          from run_attempts
          join runs
            on runs.tenant_id = run_attempts.tenant_id
           and runs.id = run_attempts.run_id
          where run_attempts.tenant_id = %s
            and run_attempts.run_id = %s
            and run_attempts.id = %s
            and run_attempts.status = %s
            and run_attempts.owner_kind = %s
            and run_attempts.owner_id = %s
            and run_attempts.owner_generation = %s
            and (
              (%s = 'queued' and runs.status = 'queued')
              or (%s = 'running' and runs.status in ('queued', 'running'))
              or (
                %s in ('succeeded', 'failed', 'cancelled')
                and runs.status in ('queued', 'running', %s)
              )
            )
          for update of run_attempts, runs
        ), transitioned as (
          update run_attempts
          set status = %s,
              owner_kind = %s,
              owner_id = %s,
              owner_generation = %s,
              queue_message_id = coalesce(%s, queue_message_id),
              lease_expires_at = coalesce(%s, lease_expires_at),
              last_heartbeat_at = coalesce(%s, last_heartbeat_at),
              started_at = case
                when %s = 'running' then coalesce(started_at, now())
                else started_at
              end,
              finished_at = case
                when %s in ('succeeded', 'failed', 'cancelled') then now()
                else null
              end,
              terminal_reason = case
                when %s in ('succeeded', 'failed', 'cancelled') then %s
                else terminal_reason
              end,
              error_code = case
                when %s in ('succeeded', 'failed', 'cancelled') then %s
                else error_code
              end,
              updated_at = now()
          where tenant_id = %s
            and run_id = %s
            and id = %s
            and status = %s
            and owner_kind = %s
            and owner_id = %s
            and owner_generation = %s
            and exists (
              select 1 from locked where locked.id = run_attempts.id
            )
          returning *
        )
        select *
        from transitioned
        """,
        (
            tenant_id,
            run_id,
            attempt_id,
            expected_status,
            current_owner[0],
            current_owner[1],
            expected_owner_generation,
            decision.projected_run_status,
            decision.projected_run_status,
            decision.projected_run_status,
            decision.projected_run_status,
            requested_status,
            next_owner[0],
            next_owner[1],
            decision.owner_generation,
            queue_message_id,
            lease_expires_at,
            last_heartbeat_at,
            requested_status,
            requested_status,
            requested_status,
            terminal_reason.strip(),
            requested_status,
            error_code,
            tenant_id,
            run_id,
            attempt_id,
            expected_status,
            current_owner[0],
            current_owner[1],
            expected_owner_generation,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("run_attempt_transition_conflict")
    return dict(row)


async def count_active_runs_for_user(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> int:
    cursor = await conn.execute(
        """
        select count(*) as count
        from runs
        where tenant_id = %s
          and user_id = %s
          and status in ('queued', 'running')
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return int(row["count"] if row else 0)


async def enforce_user_active_run_admission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    limit = int(limit)
    if limit <= 0:
        return 0
    await acquire_user_active_run_admission_lock(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return await enforce_user_active_run_admission_under_lock(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )


async def acquire_user_active_run_admission_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Acquire the transaction-scoped per-user run-admission serialization lock."""

    lock_scope = _dumps_json({"tenant_id": tenant_id, "user_id": user_id})
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


async def enforce_user_active_run_admission_under_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    """Check the active-run limit after the caller acquired its user lock."""

    limit = int(limit)
    if limit <= 0:
        return 0
    active_count = await count_active_runs_for_user(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")
    return active_count


async def get_active_retry_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_active_resume_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return an active same-owner child run that would duplicate a resume request."""

    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update of runs" if for_update else ""
    cursor = await conn.execute(
        f"""
        select runs.*,
               sessions.admitted_agent_profile_revision
                 as session_admitted_agent_profile_revision,
               sessions.admitted_agent_profile_hash
                 as session_admitted_agent_profile_hash
        from runs
        left join sessions
          on sessions.tenant_id = runs.tenant_id
         and sessions.id = runs.session_id
         and sessions.workspace_id = runs.workspace_id
         and sessions.user_id is not distinct from runs.user_id
         and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s and runs.id = %s
        {lock_clause}
        """,
        (tenant_id, run_id),
    )
    return await cursor.fetchone()


async def get_run_identity(
    conn: AsyncConnection,
    *,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    sql = (
        "select id, tenant_id, workspace_id, user_id, session_id, agent_id, status, "
        "context_snapshot_id from runs where id = %s"
    )
    if for_update:
        sql = f"{sql} for update"
    cursor = await conn.execute(
        sql,
        (run_id,),
    )
    return await cursor.fetchone()


async def _stage_run_tool_permission_terminalization(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    target_status: str,
    terminal_reason: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Persist the first terminal intent while holding the owning run row first."""

    if target_status not in {"failed", "cancel_requested", "cancelled"}:
        raise ValueError("invalid_run_tool_permission_terminal_target")
    cursor = await conn.execute(
        """
        update runs
        set permission_terminalization_target = case
              when permission_terminalization_target = 'cancel_requested'
                   and %s = 'cancelled' then 'cancelled'
              else coalesce(permission_terminalization_target, %s)
            end,
            permission_terminalization_reason = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_reason
            end,
            permission_terminalization_result_json = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s::jsonb
              else permission_terminalization_result_json
            end,
            permission_terminalization_error_code = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_error_code
            end,
            permission_terminalization_error_message = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_error_message
            end
        where tenant_id = %s
          and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, trace_id, permission_terminalization_target,
                  permission_terminalization_reason, permission_terminalization_result_json,
                  permission_terminalization_error_code, permission_terminalization_error_message
        """,
        (
            target_status,
            target_status,
            target_status,
            terminal_reason,
            target_status,
            _dumps_json(result_json or {}),
            target_status,
            error_code,
            target_status,
            error_message,
            tenant_id,
            run_id,
        ),
    )
    return await cursor.fetchone()

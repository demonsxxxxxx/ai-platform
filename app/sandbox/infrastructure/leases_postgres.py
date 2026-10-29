"""Sandbox lease PostgreSQL adapters. Callers own the connection and transaction."""

from __future__ import annotations

from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError as SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError as SandboxLeaseReleaseScopeMismatchError,
)
from app.sandbox.infrastructure.executor_reconciliation_postgres import (
    claim_failed_sandbox_executor_reconciliation_cleanups as claim_failed_sandbox_executor_reconciliation_cleanups,
    claim_sandbox_executor_reconciliations as claim_sandbox_executor_reconciliations,
    claim_sandbox_executor_suspects as claim_sandbox_executor_suspects,
    finalize_failed_sandbox_executor_reconciliation_cleanup as finalize_failed_sandbox_executor_reconciliation_cleanup,
    get_sandbox_executor_reconciliation_summary as get_sandbox_executor_reconciliation_summary,
    get_sandbox_resource_cleanup_summary as get_sandbox_resource_cleanup_summary,
    read_unknown_receipt_probe_recovery_facts as read_unknown_receipt_probe_recovery_facts,
    has_failed_sandbox_executor_reconciliation_cleanup_claim as has_failed_sandbox_executor_reconciliation_cleanup_claim,
    has_sandbox_executor_reconciliation_claim as has_sandbox_executor_reconciliation_claim,
    is_sandbox_executor_reconciliation_claim_current as is_sandbox_executor_reconciliation_claim_current,
    list_sandbox_executor_reconciliation_candidate_scopes as list_sandbox_executor_reconciliation_candidate_scopes,
    list_sandbox_executor_suspect_candidate_scopes as list_sandbox_executor_suspect_candidate_scopes,
    mark_sandbox_executor_reconciliation_cleanup_pending as mark_sandbox_executor_reconciliation_cleanup_pending,
    mark_sandbox_executor_cleanup_pending as mark_sandbox_executor_cleanup_pending,
    next_sandbox_executor_reconciliation_retry_delay as next_sandbox_executor_reconciliation_retry_delay,
    quarantine_failed_sandbox_executor_reconciliation_cleanup as quarantine_failed_sandbox_executor_reconciliation_cleanup,
    quarantine_sandbox_executor_reconciliation as quarantine_sandbox_executor_reconciliation,
    record_sandbox_executor_terminal as record_sandbox_executor_terminal,
    record_sandbox_executor_terminal_observation as record_sandbox_executor_terminal_observation,
    record_sandbox_executor_terminal_diagnostics as record_sandbox_executor_terminal_diagnostics,
    release_failed_sandbox_executor_reconciliation_cleanup_claim as release_failed_sandbox_executor_reconciliation_cleanup_claim,
    release_sandbox_executor_probe_claim as release_sandbox_executor_probe_claim,
    retry_sandbox_executor_reconciliation as retry_sandbox_executor_reconciliation,
)
from app.sandbox.infrastructure.lease_runtime_postgres import (
    cleanup_expired_sandbox_leases as cleanup_expired_sandbox_leases,
    create_sandbox_lease as create_sandbox_lease,
    fence_sandbox_lease_release as fence_sandbox_lease_release,
    get_sandbox_lease_scope as get_sandbox_lease_scope,
    list_expired_active_sandbox_leases as list_expired_active_sandbox_leases,
    new_lease_id as new_lease_id,
    record_opensandbox_renewal_receipt as record_opensandbox_renewal_receipt,
    record_sandbox_executor_accepted as record_sandbox_executor_accepted,
    record_sandbox_executor_accepted_observation as record_sandbox_executor_accepted_observation,
    record_sandbox_executor_heartbeat as record_sandbox_executor_heartbeat,
    release_active_sandbox_leases_for_run as release_active_sandbox_leases_for_run,
    release_sandbox_lease as release_sandbox_lease,
    release_stopped_sandbox_leases as release_stopped_sandbox_leases,
)
from app.sandbox.infrastructure.checkpoints_postgres import (
    claim_quarantined_checkpoint_for_repair_legacy as claim_quarantined_checkpoint_for_repair_legacy,
    claim_quarantined_checkpoint_for_repair_resource as claim_quarantined_checkpoint_for_repair_resource,
    finalize_stopped_executor_checkpoint_legacy as finalize_stopped_executor_checkpoint_legacy,
    finalize_stopped_executor_checkpoint_resource as finalize_stopped_executor_checkpoint_resource,
    get_quarantined_checkpoint_for_repair_legacy as get_quarantined_checkpoint_for_repair_legacy,
    get_quarantined_checkpoint_for_repair_resource as get_quarantined_checkpoint_for_repair_resource,
    get_scoped_checkpoint_lease as get_scoped_checkpoint_lease,
    record_sandbox_reconciliation_stage_legacy as record_sandbox_reconciliation_stage_legacy,
    record_sandbox_reconciliation_stage_resource as record_sandbox_reconciliation_stage_resource,
)


from app.platform.public_payload import sanitize_public_text
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.streaming.infrastructure.run_events_postgres import append_event
from psycopg import AsyncConnection
from typing import Any


async def read_run_runtime_debug(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        """select id as lease_id, attempt_id, provider, sandbox_mode, status,
                  heartbeat_at, expires_at, released_at, release_reason,
                  executor_status, executor_heartbeat_at, executor_terminal_received_at,
                  executor_reconciliation_attempt_count, executor_reconciliation_error,
                  executor_reconciled_at, executor_probe_claimed_at,
                  executor_cleanup_status, executor_cleanup_claimed_at,
                  executor_cleanup_retry_not_before
           from sandbox_leases where tenant_id=%s and run_id=%s
           order by created_at desc limit 101""", (tenant_id, run_id),
    )
    rows = list(await cursor.fetchall())
    projected = []
    for row in rows[:100]:
        item = dict(row)
        for name in ("provider", "sandbox_mode", "status", "executor_status", "executor_cleanup_status", "release_reason"):
            item[name] = sanitize_public_text(item.get(name))[:256] or None
        item["dispatch_timings"] = {}
        projected.append(item)
    return {"leases": projected, "leases_truncated": len(rows) > 100, "lease_limit": 100}


async def get_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s and user_id = %s and run_id = %s and id = %s for update
        """,
        (tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def renew_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set heartbeat_at = now(),
            expires_at = now() + (%s * interval '1 second'),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
          and (expires_at is null or expires_at > now())
        returning *
        """,
        (int(ttl_seconds), tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def list_active_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return only active sandbox lease rows for runtime cleanup."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_current_sandbox_runtime_leases_for_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> list[dict[str, Any]]:
    """Lock exact-attempt unexpired active runtime leases for one authoritative run."""

    cursor = await conn.execute(
        """
        select sandbox_leases.*
        from sandbox_leases
        join run_attempts
          on run_attempts.tenant_id = sandbox_leases.tenant_id
         and run_attempts.run_id = sandbox_leases.run_id
         and run_attempts.id = sandbox_leases.attempt_id
        where sandbox_leases.tenant_id = %s
          and sandbox_leases.run_id = %s
          and sandbox_leases.lease_payload_json ->> 'attempt_id' = %s
          and sandbox_leases.attempt_id = sandbox_leases.lease_payload_json ->> 'attempt_id'
          and sandbox_leases.lease_payload_json ->> 'owner_generation' = run_attempts.owner_generation::text
          and run_attempts.status in ('running', 'cancel_requested')
          and sandbox_leases.status = 'active'
          and sandbox_leases.expires_at is not null
          and sandbox_leases.expires_at > clock_timestamp()
        order by sandbox_leases.id asc
        for update of sandbox_leases
        """,
        (tenant_id, run_id, attempt_id),
    )
    rows = list(await cursor.fetchall())
    if rows:
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    return rows


async def list_cancelling_provider_tail_leases_for_attempt(
    conn: AsyncConnection, *, tenant_id: str, run_id: str, attempt_id: str,
) -> list[dict[str, Any]]:
    """Only the original live lease may persist a cancelling writer's tail.

    This is not runtime/tool authority. Cancellation's single generation step
    may be drained; a reconciler takeover, later generation or terminal Run
    cannot reuse the old lease.
    """
    cursor = await conn.execute(
        """
        select lease.* from sandbox_leases lease
        join run_attempts attempt on attempt.tenant_id = lease.tenant_id
          and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
        join runs on runs.tenant_id = attempt.tenant_id and runs.id = attempt.run_id
        where lease.tenant_id = %s and lease.run_id = %s
          and lease.attempt_id = %s
          and lease.lease_payload_json->>'attempt_id' = lease.attempt_id
          and lease.lease_payload_json->>'owner_generation' = (attempt.owner_generation - 1)::text
          and attempt.status = 'cancel_requested' and attempt.owner_kind = 'queue_worker'
          and runs.status = 'running' and runs.cancel_requested_at is not null
          and lease.status = 'active' and lease.released_at is null
          and lease.expires_at is not null and lease.expires_at > clock_timestamp()
        order by lease.id asc for update of lease
        """, (tenant_id, run_id, attempt_id),
    )
    rows = list(await cursor.fetchall())
    if rows:
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    return rows


async def list_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return same-run leases with a read-only current Attempt-owner projection."""
    cursor = await conn.execute(
        """
        select lease.*,
          lease.lease_payload_json ->> 'owner_generation' as lease_owner_generation,
          (
            lease.lease_payload_json ->> 'attempt_id' = lease.attempt_id
            and lease.lease_payload_json ->> 'owner_generation' = attempt.owner_generation::text
          ) as current_attempt_owner
        from sandbox_leases lease
        left join run_attempts attempt
          on attempt.tenant_id = lease.tenant_id
         and attempt.run_id = lease.run_id
         and attempt.id = lease.attempt_id
        where lease.tenant_id = %s
          and lease.run_id = %s
        order by lease.created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def record_sandbox_runtime_cleanup_outcome(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    trace_id: str | None = None,
    user_id: str | None = None,
    requested_by_role: str | None = None,
    reason: str,
    status: str,
    lease_ids: list[str] | None = None,
    failures: list[dict[str, str]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "visible_to_user": False,
        "reason": reason,
        "status": status,
        "lease_ids": lease_ids or [],
        "failure_count": len(failures or []),
    }
    if requested_by_role:
        payload["requested_by_role"] = requested_by_role
    if failures:
        payload["failures"] = failures
    event_type = "sandbox_runtime_cleanup_failed" if status == "failed" else "sandbox_runtime_cleanup_succeeded"
    message = "Sandbox runtime cleanup failed" if status == "failed" else "Sandbox runtime cleanup succeeded"
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type=event_type,
        stage="sandbox",
        message=message,
        payload=payload,
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action=f"sandbox.runtime.cleanup.{status}",
        target_type="run",
        target_id=run_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "reason": reason,
            "status": status,
            "lease_ids": lease_ids or [],
            "failures": failures or [],
            "requested_by_role": requested_by_role,
        },
    )


async def list_sandbox_leases(conn: AsyncConnection, *, tenant_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def lock_scoped_reconciliation_receipt(
    conn, *, tenant_id: str, workspace_id: str, user_id: str, session_id: str,
    run_id: str, attempt_id: str, lease_id: str, claim_token: str,
    expected_owner_generation: int,
) -> bool:
    """Fence receipt settlement to its original exact scope and generation."""
    cursor = await conn.execute(
        """
        select id from sandbox_leases
        where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and run_id = %s and attempt_id = %s
          and status in ('active', 'released') and executor_terminal_json is not null
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
          and lease_payload_json->>'owner_generation' = %s
        for update
        """,
        (lease_id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
         claim_token, str(expected_owner_generation)),
    )
    return await cursor.fetchone() is not None


async def lock_scoped_reconciliation_resource(
    conn, *, tenant_id: str, workspace_id: str, user_id: str, session_id: str,
    run_id: str, attempt_id: str, lease_id: str, expected_owner_generation: int,
) -> bool:
    """Lock the exact Sandbox resource while Runs supplies settlement authority."""
    cursor = await conn.execute(
        """
        select id from sandbox_leases
        where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and run_id = %s and attempt_id = %s
          and status in ('active', 'released')
          and lease_payload_json->>'owner_generation' = %s
        for update
        """,
        (lease_id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
         str(expected_owner_generation)),
    )
    return await cursor.fetchone() is not None

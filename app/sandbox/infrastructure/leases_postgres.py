"""Leases persistence. Callers own the connection and transaction."""

from __future__ import annotations

from psycopg import AsyncConnection
from typing import Any


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
        order by sandbox_leases.created_at asc
        for update of sandbox_leases, run_attempts
        """,
        (tenant_id, run_id, attempt_id),
    )
    return list(await cursor.fetchall())


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
        order by lease.created_at asc for update of lease, attempt
        """, (tenant_id, run_id, attempt_id),
    )
    return list(await cursor.fetchall())


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

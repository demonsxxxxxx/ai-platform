"""Leases persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app import run_event_repository as _run_event_repository
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.streaming.infrastructure.run_events_postgres import append_event
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
    return await _run_event_repository.list_current_sandbox_runtime_leases_for_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )


async def list_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return same-run sandbox lease rows for admin runtime provenance."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
        order by created_at asc
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

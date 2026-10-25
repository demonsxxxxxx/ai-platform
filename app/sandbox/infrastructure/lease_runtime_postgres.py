"""Sandbox lease persistence for runtime ownership and lifecycle transitions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from app.sandbox.domain.lease_errors import (
    SandboxLeaseReleaseScopeMismatchError,
)


def new_lease_id() -> str:
    return f"lease_{uuid.uuid4().hex}"

async def create_sandbox_lease(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    attempt_id: str | None = None,
    trace_id: str,
    sandbox_mode: str,
    provider: str,
    browser_enabled: bool,
    ttl_seconds: int,
    resource_limits_json: dict[str, Any],
    user_visible_payload_json: dict[str, Any],
    lease_payload_json: dict[str, Any],
    runtime_container_id: str | None = None,
    runtime_container_name: str | None = None,
    runtime_executor_url: str | None = None,
    runtime_workspace_container_path: str | None = None,
    lease_id: str | None = None,
) -> dict[str, Any]:
    lease_id = lease_id or new_lease_id()
    if provider == "fake" and not runtime_container_id:
        runtime_container_id = f"exec-{run_id}"
        runtime_container_name = f"executor-{runtime_container_id}"
        runtime_executor_url = "http://sandbox-runtime.invalid"
        runtime_workspace_container_path = "/workspace"
    runtime_handle_verified = all(
        (
            runtime_container_id,
            runtime_container_name,
            runtime_executor_url,
            runtime_workspace_container_path,
        )
    )
    if attempt_id and lease_payload_json.get("attempt_id") != attempt_id:
        raise ValueError("sandbox_lease_attempt_binding_mismatch")
    if provider in {"docker", "opensandbox"} and (
        not attempt_id or not runtime_handle_verified
    ):
        raise ValueError("sandbox_runtime_handle_required")
    cursor = await connection.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id, trace_id,
          sandbox_mode, provider, browser_enabled, resource_limits_json,
          user_visible_payload_json, lease_payload_json,
          runtime_container_id, runtime_container_name, runtime_executor_url,
          runtime_workspace_container_path, runtime_handle_verified_at,
          heartbeat_at, expires_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s,
          case when %s then now() else null end,
          now(), now() + (%s * interval '1 second')
        )
        returning *
        """,
        (
            lease_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            attempt_id,
            trace_id,
            sandbox_mode,
            provider,
            browser_enabled,
            json.dumps(resource_limits_json, ensure_ascii=False),
            json.dumps(user_visible_payload_json, ensure_ascii=False),
            json.dumps(lease_payload_json, ensure_ascii=False),
            runtime_container_id,
            runtime_container_name,
            runtime_executor_url,
            runtime_workspace_container_path,
            runtime_handle_verified,
            int(ttl_seconds),
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("sandbox_lease_insert_returning_missing")
    return row

async def record_sandbox_executor_accepted(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    reconciliation_context: dict[str, Any],
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_status = case
                when executor_status in ('running', 'completed', 'failed', 'cancelled')
                    then executor_status
                else 'accepted'
            end,
            executor_heartbeat_at = now(),
            executor_reconciliation_context_json = %s::jsonb,
            executor_reconciliation_status = case
                when executor_terminal_json is not null
                  and executor_reconciliation_status = 'waiting_terminal'
                    then 'pending'
                else executor_reconciliation_status
            end,
            expires_at = now() + make_interval(secs => %s),
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
        returning *
        """,
        (
            json.dumps(reconciliation_context, ensure_ascii=False),
            int(ttl_seconds),
            lease_id,
            tenant_id,
            run_id,
            attempt_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_attempt_inactive"
        )
    return dict(row)

async def record_sandbox_executor_heartbeat(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    executor_status: str,
    ttl_seconds: int = 1800,
) -> dict[str, Any] | None:
    if executor_status not in {"accepted", "running"}:
        raise ValueError("sandbox_executor_heartbeat_status_invalid")
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_status = case
                when executor_status = 'running' and %s = 'accepted'
                    then executor_status
                else %s
            end,
            executor_heartbeat_at = now(),
            heartbeat_at = now(),
            expires_at = now() + make_interval(secs => %s),
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
          and (expires_at is null or expires_at > now())
          and executor_terminal_json is null
        returning *
        """,
        (executor_status, executor_status, int(ttl_seconds), lease_id, tenant_id, run_id, attempt_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None

async def record_opensandbox_renewal_receipt(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    provider_expires_at: datetime,
) -> dict[str, Any] | None:
    if provider_expires_at.tzinfo is None or provider_expires_at.utcoffset() is None:
        raise ValueError("opensandbox_renewal_receipt_timezone_invalid")
    cursor = await connection.execute(
        """
        update sandbox_leases
        set provider_renewed_at = now(),
            provider_expires_at = %s,
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and provider = 'opensandbox'
          and status = 'active'
          and (expires_at is null or expires_at > now())
          and executor_terminal_json is null
        returning *
        """,
        (provider_expires_at, lease_id, tenant_id, run_id, attempt_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None

async def release_sandbox_lease(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    reason: str,
) -> dict[str, Any] | None:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
          and (
            executor_terminal_json is null
            or executor_reconciliation_status = 'finalized'
          )
        returning *
        """,
        (reason, tenant_id, user_id, run_id, lease_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None

async def release_active_sandbox_leases_for_run(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
          and (
            executor_terminal_json is null
            or executor_reconciliation_status = 'finalized'
          )
        returning *
        """,
        (reason, tenant_id, run_id),
    )
    return [dict(row) for row in await cursor.fetchall()]

async def release_stopped_sandbox_leases(
    connection: Any,
    *,
    tenant_id: str,
    reason: str,
    lease_ids: list[str],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if not lease_ids:
        return []
    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and (%s::text is null or run_id = %s)
          and id = any(%s)
          and status = 'active'
          and (
            executor_terminal_json is null
            or executor_reconciliation_status = 'finalized'
          )
        returning *
        """,
        (reason, tenant_id, run_id, run_id, lease_ids),
    )
    return [dict(row) for row in await cursor.fetchall()]

async def list_expired_active_sandbox_leases(
    connection: Any,
    *,
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Lock expired cleanup candidates that do not carry terminal recovery work."""

    cursor = await connection.execute(
        """
        select *
        from sandbox_leases
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
          and (
            executor_terminal_json is null
            or executor_reconciliation_status = 'finalized'
          )
        order by expires_at asc, created_at asc
        for update skip locked
        limit %s
        """,
        (tenant_id, tenant_id, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]

async def cleanup_expired_sandbox_leases(
    connection: Any,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
          and provider not in ('fake', 'docker', 'opensandbox')
          and (
            executor_terminal_json is null
            or executor_reconciliation_status = 'finalized'
          )
        returning id, tenant_id, run_id, trace_id, release_reason
        """,
        (reason, tenant_id, tenant_id),
    )
    return [dict(row) for row in await cursor.fetchall()]

async def fence_sandbox_lease_release(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    attempt_id: str | None,
    lease_id: str,
    sandbox_mode: str,
    provider: str,
    browser_enabled: bool,
    reason: str,
) -> dict[str, Any]:
    """Persist a monotonic release fence after an uncertain lease insertion."""

    cursor = await connection.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
          sandbox_mode, provider, status, browser_enabled, lease_payload_json,
          heartbeat_at, expires_at, released_at, release_reason
        )
        values (
          %s, %s, %s, %s, %s, %s, %s,
          %s, %s, 'released', %s,
          '{"source":"sandbox_runtime_release_fence"}'::jsonb,
          now(), now(), now(), %s
        )
        on conflict (id) do update
        set status = 'released',
            released_at = coalesce(sandbox_leases.released_at, now()),
            release_reason = excluded.release_reason,
            updated_at = now()
        where sandbox_leases.tenant_id = excluded.tenant_id
          and sandbox_leases.workspace_id = excluded.workspace_id
          and sandbox_leases.user_id = excluded.user_id
          and sandbox_leases.session_id = excluded.session_id
          and sandbox_leases.run_id = excluded.run_id
          and sandbox_leases.sandbox_mode = excluded.sandbox_mode
          and sandbox_leases.provider = excluded.provider
          and coalesce(sandbox_leases.attempt_id, '') = coalesce(excluded.attempt_id, '')
          and (
            sandbox_leases.executor_terminal_json is null
            or sandbox_leases.executor_reconciliation_status = 'finalized'
          )
        returning *
        """,
        (
            lease_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            attempt_id,
            sandbox_mode,
            provider,
            browser_enabled,
            reason,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_lease_release_scope_mismatch"
        )
    return row

"""Persistence operations for real-sandbox lease creation and release fencing."""

from __future__ import annotations

import json
import uuid
from typing import Any


class SandboxLeaseReleaseScopeMismatchError(RuntimeError):
    """Raised when a release fence collides with another lease scope."""


class SandboxExecutorTerminalConflictError(RuntimeError):
    """Raised when one execution attempt reports two different terminal results."""


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
                when executor_status in ('running', 'succeeded', 'failed', 'cancelled')
                    then executor_status
                else 'accepted'
            end,
            executor_heartbeat_at = now(),
            executor_reconciliation_context_json = %s::jsonb,
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


async def record_sandbox_executor_terminal(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str | None = None,
) -> dict[str, Any]:
    if executor_status not in {"completed", "failed", "cancelled"}:
        raise ValueError("sandbox_executor_terminal_status_invalid")
    normalized_result_status = str(terminal_result.get("status") or "").strip().lower()
    if str(terminal_result.get("run_id") or "") != run_id:
        raise ValueError("sandbox_executor_terminal_run_id_invalid")
    allowed_result_statuses = {
        "completed": {"completed", "succeeded"},
        "failed": {"failed"},
        "cancelled": {"cancelled", "canceled"},
    }
    if normalized_result_status not in allowed_result_statuses[executor_status]:
        raise ValueError("sandbox_executor_terminal_result_status_invalid")
    cursor = await connection.execute(
        """
        select *
        from sandbox_leases
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
        for update
        """,
        (lease_id, tenant_id, run_id, attempt_id),
    )
    current = await cursor.fetchone()
    if current is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )
    existing = current.get("executor_terminal_json")
    if existing is not None:
        if existing == terminal_result and str(current.get("executor_status") or "") == executor_status:
            return dict(current)
        raise SandboxExecutorTerminalConflictError(
            "sandbox_executor_terminal_conflict"
        )
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_status = %s,
            executor_heartbeat_at = now(),
            executor_terminal_json = %s::jsonb,
            executor_terminal_received_at = now(),
            executor_reconciliation_status = case
              when executor_reconciliation_context_json is null then 'waiting_terminal'
              else 'pending'
            end,
            heartbeat_at = now(),
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
          and executor_terminal_json is null
          and (
            %s::text is null
            or (
              executor_reconciliation_status = 'claimed'
              and executor_reconciliation_claim_token = %s
            )
          )
        returning *
        """,
        (
            executor_status,
            json.dumps(terminal_result, ensure_ascii=False),
            lease_id,
            tenant_id,
            run_id,
            attempt_id,
            claim_token,
            claim_token,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxExecutorTerminalConflictError(
            "sandbox_executor_terminal_conflict"
        )
    return dict(row)


async def record_sandbox_executor_terminal_diagnostics(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    diagnostics: list[str],
) -> bool:
    """Durably retain safe reconciliation diagnostics for the active claim."""

    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_terminal_json = jsonb_set(
              executor_terminal_json,
              '{diagnostics}',
              %s::jsonb,
              true
            ),
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_terminal_json is not null
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (json.dumps(diagnostics, ensure_ascii=False), lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def record_sandbox_executor_reconciliation_context(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_context_json = %s::jsonb,
            executor_reconciliation_status = case
              when executor_terminal_json is null then 'waiting_terminal'
              else 'pending'
            end,
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
          and executor_reconciliation_status <> 'finalized'
          and (
            executor_reconciliation_context_json is null
            or executor_reconciliation_context_json = %s::jsonb
          )
        returning *
        """,
        (
            json.dumps(context, ensure_ascii=False),
            lease_id,
            tenant_id,
            run_id,
            attempt_id,
            json.dumps(context, ensure_ascii=False),
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_reconciliation_context_scope_mismatch"
        )
    return dict(row)


async def claim_sandbox_executor_suspects(
    connection: Any,
    *,
    claim_token: str,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, Any]]:
    """Claim accepted tasks whose executor heartbeat has gone stale."""

    cursor = await connection.execute(
        """
        with candidates as (
          select id
          from sandbox_leases
          where status = 'active'
            and executor_terminal_json is null
            and executor_reconciliation_context_json is not null
            and (
              (
                executor_reconciliation_status = 'waiting_terminal'
                and coalesce(executor_heartbeat_at, updated_at, created_at)
                    < now() - make_interval(secs => %s)
              )
              or (
                executor_reconciliation_status = 'claimed'
                and coalesce(
                  executor_reconciliation_claimed_at,
                  updated_at,
                  created_at
                ) < now() - make_interval(secs => %s)
              )
            )
          order by coalesce(executor_heartbeat_at, updated_at, created_at), id
          for update skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_reconciliation_status = 'claimed',
            executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
            executor_reconciliation_attempt_count = executor_reconciliation_attempt_count + 1,
            executor_reconciliation_error = '',
            updated_at = now()
        from candidates
        where lease.id = candidates.id
        returning lease.*
        """,
        (stale_after_seconds, stale_after_seconds, limit, claim_token),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def release_sandbox_executor_probe_claim(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str = "",
) -> bool:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_status = 'waiting_terminal',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status = 'active'
          and executor_terminal_json is null
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def claim_sandbox_executor_reconciliations(
    connection: Any,
    *,
    claim_token: str,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        """
        with candidates as (
          select id
          from sandbox_leases
          where status in ('active', 'released')
            and executor_terminal_json is not null
            and executor_reconciliation_context_json is not null
            and (
              executor_reconciliation_status in ('pending', 'retry')
              or (
                executor_reconciliation_status = 'claimed'
                and executor_reconciliation_claimed_at < now() - make_interval(secs => %s)
              )
            )
          order by
            case executor_reconciliation_status
              when 'pending' then 0
              when 'claimed' then 1
              else 2
            end,
            executor_terminal_received_at asc,
            id asc
          for update skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_reconciliation_status = 'claimed',
            executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
            executor_terminal_reconciliation_attempt_count =
              executor_terminal_reconciliation_attempt_count + 1,
            executor_reconciliation_error = '',
            updated_at = now()
        from candidates
        where lease.id = candidates.id
        returning lease.*
        """,
        (stale_after_seconds, limit, claim_token),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def has_sandbox_executor_reconciliation_claim(
    conn,
    *,
    lease_id: str,
    claim_token: str,
) -> bool:
    row = await (
        await conn.execute(
            """
            select id
            from sandbox_leases
            where id = %s
              and status in ('active', 'released')
              and executor_reconciliation_status = 'claimed'
              and executor_reconciliation_claim_token = %s
            for update
            """,
            (lease_id, claim_token),
        )
    ).fetchone()
    return row is not None


async def is_sandbox_executor_reconciliation_claim_current(
    conn,
    *,
    lease_id: str,
    claim_token: str,
) -> bool:
    """Read a claim already protected by the reconciler's owner transaction."""

    row = await (
        await conn.execute(
            """
            select id
            from sandbox_leases
            where id = %s
              and status in ('active', 'released')
              and executor_reconciliation_status = 'claimed'
              and executor_reconciliation_claim_token = %s
            """,
            (lease_id, claim_token),
        )
    ).fetchone()
    return row is not None


async def retry_sandbox_executor_reconciliation(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
) -> bool:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_status = 'retry',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def mark_sandbox_executor_reconciliation_cleanup_pending(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
) -> bool:
    """Leave a verified runtime eligible for claim-fenced cleanup retry."""

    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_status = 'failed',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = %s,
            expires_at = least(coalesce(expires_at, now()), now()),
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def claim_failed_sandbox_executor_reconciliation_cleanups(
    connection: Any,
    *,
    claim_token: str,
    tenant_id: str | None,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, Any]]:
    cursor = await connection.execute(
        """
        with candidates as (
          select id
          from sandbox_leases
          where status in ('active', 'released')
            and executor_terminal_json is not null
            and executor_reconciliation_status = 'failed'
            and (
              executor_reconciliation_claim_token is null
              or executor_reconciliation_claimed_at is null
              or executor_reconciliation_claimed_at < now() - make_interval(secs => %s)
            )
            and (%s::text is null or tenant_id = %s)
          order by coalesce(executor_reconciliation_claimed_at, executor_terminal_received_at, created_at) asc, id asc
          for update skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
            updated_at = now()
        from candidates
        where lease.id = candidates.id
        returning lease.*
        """,
        (stale_after_seconds, tenant_id, tenant_id, limit, claim_token),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def has_failed_sandbox_executor_reconciliation_cleanup_claim(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
) -> bool:
    row = await (
        await connection.execute(
            """
            select id
            from sandbox_leases
            where id = %s
              and status in ('active', 'released')
              and executor_reconciliation_status = 'failed'
              and executor_reconciliation_claim_token = %s
            for update
            """,
            (lease_id, claim_token),
        )
    ).fetchone()
    return row is not None


async def release_failed_sandbox_executor_reconciliation_cleanup_claim(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
) -> bool:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = now(),
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'failed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def finalize_failed_sandbox_executor_reconciliation_cleanup(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    claim_token: str,
    reason: str,
) -> dict[str, Any] | None:
    row = await (
        await connection.execute(
            """
            update sandbox_leases
            set status = 'released',
                released_at = coalesce(released_at, now()),
                release_reason = %s,
                executor_reconciliation_status = 'finalized',
                executor_reconciliation_claim_token = null,
                executor_reconciliation_claimed_at = null,
                executor_reconciliation_error = '',
                executor_reconciled_at = now(),
                updated_at = now()
            where tenant_id = %s
              and user_id = %s
              and run_id = %s
              and id = %s
              and status in ('active', 'released')
              and executor_reconciliation_status = 'failed'
              and executor_reconciliation_claim_token = %s
            returning *
            """,
            (reason, tenant_id, user_id, run_id, lease_id, claim_token),
        )
    ).fetchone()
    return dict(row) if row else None


async def quarantine_failed_sandbox_executor_reconciliation_cleanup(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
) -> bool:
    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'quarantined',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'failed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def quarantine_sandbox_executor_reconciliation(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
) -> bool:
    """Stop automatic claims without falsely recording an unverifiable runtime as released."""

    cursor = await connection.execute(
        """
        update sandbox_leases
        set status = 'quarantined',
            executor_reconciliation_status = 'failed',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def get_sandbox_executor_reconciliation_summary(
    connection: Any,
    *,
    tenant_id: str,
    slo_seconds: int,
) -> dict[str, int | None]:
    """Return tenant-scoped aggregate receipt health without subject identifiers."""

    cursor = await connection.execute(
        """
        select
          count(*) filter (
            where status in ('active', 'released')
              and executor_terminal_json is not null
              and executor_reconciliation_status in ('pending', 'claimed', 'retry')
          ) as pending_receipt_count,
          count(*) filter (
            where status = 'released'
              and executor_terminal_json is not null
              and executor_reconciliation_status in ('pending', 'claimed', 'retry')
          ) as released_pending_receipt_count,
          count(*) filter (
            where status in ('active', 'released')
              and executor_terminal_json is not null
              and executor_reconciliation_status = 'retry'
          ) as retry_receipt_count,
          coalesce(sum(greatest(executor_terminal_reconciliation_attempt_count - 1, 0)) filter (
            where status in ('active', 'released', 'quarantined')
              and executor_terminal_json is not null
          ), 0)::bigint as retry_attempt_count,
          count(*) filter (
            where status in ('active', 'released')
              and executor_reconciliation_status = 'failed'
          ) as cleanup_pending_receipt_count,
          count(*) filter (
            where status = 'quarantined'
              and executor_reconciliation_status = 'failed'
          ) as quarantined_receipt_count,
          max(executor_terminal_reconciliation_attempt_count) filter (
            where executor_terminal_json is not null
          ) as max_attempt_count,
          floor(max(extract(epoch from (now() - executor_terminal_received_at))) filter (
            where status in ('active', 'released')
              and executor_terminal_json is not null
              and executor_reconciliation_status in ('pending', 'claimed', 'retry')
          ))::bigint as oldest_pending_receipt_age_seconds,
          count(*) filter (
            where status in ('active', 'released')
              and executor_terminal_json is not null
              and executor_reconciliation_status in ('pending', 'claimed', 'retry')
              and executor_terminal_received_at < now() - make_interval(secs => %s)
          ) as terminalization_slo_breach_count
        from sandbox_leases
        where tenant_id = %s
        """,
        (slo_seconds, tenant_id),
    )
    row = await cursor.fetchone() or {}
    oldest_age = row.get("oldest_pending_receipt_age_seconds")
    return {
        "pending_receipt_count": int(row.get("pending_receipt_count") or 0),
        "released_pending_receipt_count": int(
            row.get("released_pending_receipt_count") or 0
        ),
        "retry_receipt_count": int(row.get("retry_receipt_count") or 0),
        "retry_attempt_count": int(row.get("retry_attempt_count") or 0),
        "cleanup_pending_receipt_count": int(
            row.get("cleanup_pending_receipt_count") or 0
        ),
        "quarantined_receipt_count": int(row.get("quarantined_receipt_count") or 0),
        "max_attempt_count": int(row.get("max_attempt_count") or 0),
        "oldest_pending_receipt_age_seconds": (
            int(oldest_age) if oldest_age is not None else None
        ),
        "terminalization_slo_seconds": int(slo_seconds),
        "terminalization_slo_breach_count": int(
            row.get("terminalization_slo_breach_count") or 0
        ),
    }


async def release_and_finalize_sandbox_executor_reconciliation(
    conn: Any,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    claim_token: str,
    reason: str,
) -> bool:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            executor_reconciliation_status = 'finalized',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_reconciliation_error = '',
            executor_reconciled_at = now(),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (reason, tenant_id, user_id, run_id, lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


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

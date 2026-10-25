"""Sandbox executor terminal and reconciliation persistence operations."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size
from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError,
)


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
    ensure_json_size(
        terminal_result,
        max_bytes=RUN_RESULT_MAX_BYTES,
        code="executor_terminal_receipt_too_large",
    )
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
        if (
            existing == terminal_result
            and str(current.get("executor_status") or "") == executor_status
            and (
                claim_token is None
                or str(current.get("executor_reconciliation_claim_token") or "")
                == claim_token
            )
        ):
            return dict(current)
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
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
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
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
              executor_reconciliation_status = 'pending'
              or (
                executor_reconciliation_status = 'retry'
                and coalesce(executor_reconciliation_retry_not_before, '-infinity'::timestamptz) <= now()
              )
              or (
                executor_reconciliation_status = 'claimed'
                and executor_reconciliation_claimed_at < now() - make_interval(secs => %s)
              )
            )
          order by
            coalesce(executor_terminal_received_at, created_at) asc,
            id asc
          for update skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_reconciliation_status = 'claimed',
            executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
            executor_reconciliation_retry_not_before = null,
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

async def next_sandbox_executor_reconciliation_retry_delay(connection: Any) -> float | None:
    """Read the durable terminal retry clock independently of general lease updates."""

    row = await (
        await connection.execute(
            """
            select extract(epoch from (
              min(executor_reconciliation_retry_not_before) - now()
            )) as delay_seconds
            from sandbox_leases
            where status in ('active', 'released')
              and executor_terminal_json is not null
              and executor_reconciliation_context_json is not null
              and executor_reconciliation_status = 'retry'
            """
        )
    ).fetchone()
    delay = row.get("delay_seconds") if row else None
    return max(float(delay), 0.0) if delay is not None else None

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
            executor_reconciliation_retry_not_before = now() + make_interval(
              secs => least(30, greatest(1, executor_terminal_reconciliation_attempt_count))
            ),
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
    """Hand off a settled Run to the independent claim-fenced cleanup owner."""

    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_reconciliation_status = 'cleanup_pending',
            executor_reconciliation_retry_not_before = null,
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
            and executor_reconciliation_status in ('failed', 'cleanup_pending')
            and coalesce(executor_reconciliation_retry_not_before, '-infinity'::timestamptz) <= now()
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
              and executor_reconciliation_status in ('failed', 'cleanup_pending')
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
        set executor_reconciliation_retry_not_before = now() + interval '30 seconds',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = now(),
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_reconciliation_status in ('failed', 'cleanup_pending')
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
                executor_reconciliation_retry_not_before = null,
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
              and executor_reconciliation_status in ('failed', 'cleanup_pending')
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
          and executor_reconciliation_status in ('failed', 'cleanup_pending')
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
              and executor_reconciliation_status in ('failed', 'cleanup_pending')
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

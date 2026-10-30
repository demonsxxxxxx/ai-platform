"""Sandbox executor terminal reconciliation PostgreSQL operations."""

from __future__ import annotations

from typing import Any

async def claim_sandbox_executor_reconciliations(
    connection: Any,
    *,
    claim_token: str,
    limit: int,
    stale_after_seconds: int,
    candidate_lease_ids: list[str] | None = None,
    legacy_settlement_compat: bool = True,
) -> list[dict[str, Any]]:
    if not legacy_settlement_compat:
        raise RuntimeError("attempt_settlement_claim_requires_runs_owner")
    cursor = await connection.execute(
        """
        with candidates as (
          select id
          from sandbox_leases
          where status in ('active', 'released')
            and (%s::text[] is null or id = any(%s::text[]))
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
        (
            candidate_lease_ids,
            candidate_lease_ids,
            stale_after_seconds,
            limit,
            claim_token,
        ),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]

async def list_sandbox_executor_reconciliation_candidate_scopes(
    connection: Any,
    *,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, str]]:
    """Read a bounded terminal-claim set before callers acquire source locks."""

    cursor = await connection.execute(
        """
        select id, tenant_id, run_id, attempt_id
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
        order by coalesce(executor_terminal_received_at, created_at) asc, id asc
        limit %s
        """,
        (stale_after_seconds, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]

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

async def quarantine_sandbox_executor_reconciliation(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
    legacy_settlement_compat: bool = True,
    probe_claim_token: str | None = None,
) -> bool:
    """Stop automatic claims without falsely recording an unverifiable runtime as released."""

    compatibility_assignments = ""
    compatibility_predicate = ""
    parameters: tuple[Any, ...]
    if probe_claim_token is not None:
        compatibility_assignments = (
            """
            executor_reconciliation_status = 'failed',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            """
            if legacy_settlement_compat else
            """
            executor_cleanup_status = 'failed',
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            """
        )
        compatibility_predicate = """
            and executor_probe_claim_token = %s
        """
        parameters = (error[:1000], lease_id, probe_claim_token)
    elif legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = 'failed',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
        compatibility_predicate = """
            and executor_reconciliation_status = 'claimed'
            and executor_reconciliation_claim_token = %s
        """
        parameters = (error[:1000], lease_id, claim_token)
    else:
        compatibility_assignments = """
            executor_cleanup_status = 'failed',
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
        """
        compatibility_predicate = """
            and executor_cleanup_status = 'waiting_settlement'
            and exists (
              select 1 from run_attempt_settlements detail
              where detail.tenant_id = sandbox_leases.tenant_id
                and detail.run_id = sandbox_leases.run_id
                and detail.attempt_id = sandbox_leases.attempt_id
                and detail.source_lease_id = sandbox_leases.id
                and detail.mapping_state = 'mapped'
                and detail.settlement_phase = 'claimed'
                and detail.settlement_claim_token = %s
            )
        """
        parameters = (error[:1000], lease_id, claim_token)

    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set status = 'quarantined',
            {compatibility_assignments}
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          {compatibility_predicate}
        returning id
        """,
        parameters,
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

"""Sandbox executor resource cleanup PostgreSQL operations."""

from __future__ import annotations

from typing import Any

_RESOURCE_CLEANUP_FIELDS = """
  id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id, trace_id,
  sandbox_mode, provider, status, browser_enabled, lease_payload_json,
  runtime_container_id, runtime_container_name, runtime_executor_url,
  runtime_workspace_container_path, runtime_handle_verified_at,
  executor_status, executor_heartbeat_at,
  executor_terminal_received_at, executor_cleanup_status,
  executor_cleanup_claim_token, executor_cleanup_claimed_at,
  executor_cleanup_retry_not_before, expires_at, released_at, release_reason,
  created_at, updated_at
"""

_RESOURCE_CLEANUP_FIELDS_FOR_LEASE = """
  lease.id, lease.tenant_id, lease.workspace_id, lease.user_id, lease.session_id,
  lease.run_id, lease.attempt_id, lease.trace_id, lease.sandbox_mode, lease.provider,
  lease.status, lease.browser_enabled, lease.lease_payload_json, lease.runtime_container_id,
  lease.runtime_container_name, lease.runtime_executor_url,
  lease.runtime_workspace_container_path, lease.runtime_handle_verified_at,
  lease.executor_status, lease.executor_heartbeat_at,
  lease.executor_terminal_received_at, lease.executor_cleanup_status,
  lease.executor_cleanup_claim_token, lease.executor_cleanup_claimed_at,
  lease.executor_cleanup_retry_not_before, lease.expires_at, lease.released_at,
  lease.release_reason, lease.created_at, lease.updated_at
"""

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
        set executor_cleanup_status = 'cleanup_pending',
            executor_cleanup_retry_not_before = null,
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            executor_reconciliation_status = 'cleanup_pending',
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

async def mark_sandbox_executor_cleanup_pending(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
    legacy_settlement_compat: bool = False,
) -> bool:
    """Assign resource cleanup after Runs has committed a terminal outcome."""
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = 'cleanup_pending',
            executor_reconciliation_retry_not_before = null,
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set executor_cleanup_status = 'cleanup_pending',
            executor_cleanup_retry_not_before = null,
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            {compatibility_assignments}
            executor_reconciliation_error = %s,
            expires_at = least(coalesce(expires_at, now()), now()),
            updated_at = now()
        where id = %s and status in ('active', 'released')
          and executor_cleanup_status in ('waiting_settlement', 'cleanup_pending')
        returning id
        """,
        (error[:1000], lease_id),
    )
    return await cursor.fetchone() is not None

async def claim_failed_sandbox_executor_reconciliation_cleanups(
    connection: Any,
    *,
    claim_token: str,
    tenant_id: str | None,
    limit: int,
    stale_after_seconds: int,
    legacy_settlement_compat: bool = True,
) -> list[dict[str, Any]]:
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
        """
    cursor = await connection.execute(
        f"""
        with candidates as (
          select lease.id
          from sandbox_leases lease
          left join run_attempt_settlements detail
            on detail.tenant_id = lease.tenant_id
           and detail.run_id = lease.run_id
           and detail.attempt_id = lease.attempt_id
           and detail.source_lease_id = lease.id
          where lease.status in ('active', 'released')
            and (
              (lease.executor_cleanup_status in ('failed', 'cleanup_pending', 'retry', 'claimed')
               and coalesce(lease.executor_cleanup_retry_not_before, '-infinity'::timestamptz) <= now())
              or (
                lease.executor_cleanup_status = 'waiting_settlement'
                and detail.mapping_state = 'mapped'
                and detail.settlement_phase = 'settled'
                and detail.result_status in ('succeeded', 'failed', 'cancelled', 'canceled')
                and detail.result_json is not null
                and detail.settled_at is not null
              )
            )
            and (
              lease.executor_cleanup_claim_token is null
              or lease.executor_cleanup_claimed_at is null
              or lease.executor_cleanup_claimed_at < now() - make_interval(secs => %s)
            )
            and (%s::text is null or lease.tenant_id = %s)
          order by coalesce(lease.executor_cleanup_claimed_at, lease.executor_terminal_received_at, lease.created_at) asc, lease.id asc
          for update of lease skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_cleanup_status = 'claimed',
            executor_cleanup_claim_token = %s,
            executor_cleanup_claimed_at = now(),
            executor_cleanup_retry_not_before = null,
            {compatibility_assignments}
            updated_at = now()
        from candidates
        where lease.id = candidates.id
        returning {_RESOURCE_CLEANUP_FIELDS_FOR_LEASE}
        """,
        (stale_after_seconds, tenant_id, tenant_id, limit, claim_token,
         *([claim_token] if legacy_settlement_compat else [])),
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
              and executor_cleanup_status = 'claimed'
              and executor_cleanup_claim_token = %s
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
    legacy_settlement_compat: bool = True,
) -> bool:
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_retry_not_before = now() + interval '30 seconds',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set executor_cleanup_status = 'retry',
            executor_cleanup_retry_not_before = now() + interval '30 seconds',
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            {compatibility_assignments}
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_cleanup_status = 'claimed'
          and executor_cleanup_claim_token = %s
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
    legacy_settlement_compat: bool = True,
) -> dict[str, Any] | None:
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
                executor_reconciliation_status = 'finalized',
                executor_reconciliation_retry_not_before = null,
                executor_reconciliation_claim_token = null,
                executor_reconciliation_claimed_at = null,
        """
    row = await (
        await connection.execute(
            f"""
            update sandbox_leases
            set status = 'released',
                released_at = coalesce(released_at, now()),
                release_reason = %s,
                executor_cleanup_status = 'finalized',
                executor_cleanup_retry_not_before = null,
                executor_cleanup_claim_token = null,
                executor_cleanup_claimed_at = null,
                {compatibility_assignments}
                executor_reconciliation_error = '',
                executor_reconciled_at = now(),
                updated_at = now()
            where tenant_id = %s
              and user_id = %s
              and run_id = %s
              and id = %s
              and status in ('active', 'released')
              and executor_cleanup_status = 'claimed'
              and executor_cleanup_claim_token = %s
            returning {_RESOURCE_CLEANUP_FIELDS}
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
    legacy_settlement_compat: bool = True,
) -> bool:
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set status = 'quarantined',
            executor_cleanup_status = 'failed',
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            {compatibility_assignments}
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_cleanup_status = 'claimed'
          and executor_cleanup_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None

async def get_sandbox_resource_cleanup_summary(
    connection: Any, *, tenant_id: str,
) -> dict[str, int]:
    """Summarize Sandbox-owned cleanup responsibility independently of Runs."""
    row = await (
        await connection.execute(
            """
            select
              count(*) filter (
                where status in ('active', 'released')
                  and executor_cleanup_status in ('cleanup_pending', 'retry', 'claimed', 'failed')
              ) as cleanup_pending_count,
              count(*) filter (
                where status = 'quarantined'
                  and executor_cleanup_status = 'failed'
              ) as quarantined_count
            from sandbox_leases
            where tenant_id = %s
            """,
            (tenant_id,),
        )
    ).fetchone() or {}
    return {
        "cleanup_pending_receipt_count": int(row.get("cleanup_pending_count") or 0),
        "quarantined_receipt_count": int(row.get("quarantined_count") or 0),
    }

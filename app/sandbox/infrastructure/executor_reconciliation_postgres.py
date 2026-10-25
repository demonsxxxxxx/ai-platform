"""Sandbox executor terminal and reconciliation persistence operations."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size
from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError,
)


_RESOURCE_CLEANUP_FIELDS = """
  id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id, trace_id,
  sandbox_mode, provider, status, browser_enabled, lease_payload_json,
  runtime_container_id, runtime_container_name, runtime_executor_url,
  runtime_workspace_container_path, executor_status, executor_heartbeat_at,
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
  lease.runtime_workspace_container_path, lease.executor_status, lease.executor_heartbeat_at,
  lease.executor_terminal_received_at, lease.executor_cleanup_status,
  lease.executor_cleanup_claim_token, lease.executor_cleanup_claimed_at,
  lease.executor_cleanup_retry_not_before, lease.expires_at, lease.released_at,
  lease.release_reason, lease.created_at, lease.updated_at
"""


def _validate_terminal_observation(
    *, run_id: str, executor_status: str, terminal_result: dict[str, Any]
) -> None:
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
    _validate_terminal_observation(
        run_id=run_id, executor_status=executor_status, terminal_result=terminal_result
    )
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
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            executor_cleanup_status = 'waiting_settlement',
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


async def record_sandbox_executor_terminal_observation(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str | None = None,
    legacy_settlement_compat: bool = False,
) -> dict[str, Any]:
    """Record Sandbox execution status while Runs owns the terminal receipt."""
    _validate_terminal_observation(
        run_id=run_id, executor_status=executor_status, terminal_result=terminal_result
    )
    cursor = await connection.execute(
        """
        select id, tenant_id, run_id, attempt_id, status, lease_payload_json,
               executor_status, executor_terminal_received_at,
               executor_probe_claim_token, executor_probe_claimed_at,
               executor_cleanup_status, executor_cleanup_claim_token,
               executor_cleanup_claimed_at, executor_cleanup_retry_not_before,
               executor_heartbeat_at, heartbeat_at, expires_at, updated_at
        from sandbox_leases
        where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
          and status = 'active'
        for update
        """,
        (lease_id, tenant_id, run_id, attempt_id),
    )
    current = await cursor.fetchone()
    if current is None:
        raise SandboxLeaseReleaseScopeMismatchError("sandbox_executor_terminal_scope_mismatch")
    current_status = str(current.get("executor_status") or "")
    if claim_token is not None and str(current.get("executor_probe_claim_token") or "") != claim_token:
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
    if current_status in {"completed", "failed", "cancelled"}:
        if current_status != executor_status:
            raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
        return dict(current)
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = case
              when executor_reconciliation_context_json is null then 'waiting_terminal'
              else 'pending'
            end,
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set executor_status = %s,
            executor_heartbeat_at = now(),
            executor_terminal_received_at = coalesce(executor_terminal_received_at, now()),
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            executor_cleanup_status = 'waiting_settlement',
            {compatibility_assignments}
            heartbeat_at = now(), updated_at = now()
        where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
          and status = 'active'
          and executor_status not in ('completed', 'failed', 'cancelled')
          and (%s::text is null or executor_probe_claim_token = %s)
        returning id, tenant_id, run_id, attempt_id, status, lease_payload_json,
                  executor_status, executor_terminal_received_at,
                  executor_probe_claim_token, executor_probe_claimed_at,
                  executor_cleanup_status, executor_cleanup_claim_token,
                  executor_cleanup_claimed_at, executor_cleanup_retry_not_before,
                  executor_heartbeat_at, heartbeat_at, expires_at, updated_at
        """,
        (executor_status, lease_id, tenant_id, run_id, attempt_id, claim_token, claim_token),
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
    candidate_lease_ids: list[str] | None = None,
    legacy_settlement_compat: bool = True,
) -> list[dict[str, Any]]:
    """Claim stale accepted tasks and exact unknown dispatch intents for probing."""

    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = 'claimed',
            executor_reconciliation_claim_token = %s,
            executor_reconciliation_claimed_at = now(),
        """

    cursor = await connection.execute(
        f"""
        with candidates as (
          select id
          from sandbox_leases lease
          left join run_attempt_settlements detail
            on detail.tenant_id = lease.tenant_id
           and detail.run_id = lease.run_id
           and detail.attempt_id = lease.attempt_id
           and detail.source_lease_id = lease.id
          where lease.status = 'active'
            and (%s::text[] is null or id = any(%s::text[]))
            and (
              lease.executor_status in ('accepted', 'running')
              or (
                lease.executor_status = 'pending'
                and (
                  (detail.mapping_state = 'mapped'
                  and detail.dispatch_state in ('unknown', 'accepted')
                  and detail.intent_recovery_context_json is not null
                  and detail.accepted_context_json is null
                  and detail.receipt_json is null)
                  or (
                    detail.attempt_id is null
                    and to_jsonb(lease)->>'executor_reconciliation_intent_context_json' is not null
                    and exists (
                      select 1 from sandbox_execution_dispatches dispatch
                      where dispatch.tenant_id = lease.tenant_id and dispatch.run_id = lease.run_id
                        and dispatch.attempt_id = lease.attempt_id and dispatch.lease_id = lease.id
                        and dispatch.owner_generation::text = lease.lease_payload_json->>'owner_generation'
                        and dispatch.status in ('intent', 'unknown', 'accepted')
                    )
                  )
                )
              )
            )
            and detail.receipt_json is null
            and (detail.attempt_id is not null or to_jsonb(lease)->>'executor_terminal_json' is null)
            and (
              (lease.executor_probe_claim_token is null
               and coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                    < now() - make_interval(secs => %s))
              or
              (lease.executor_probe_claim_token is not null
               and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                    < now() - make_interval(secs => %s))
            )
          order by coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at), lease.id
          for update of lease skip locked
          limit %s
        )
        update sandbox_leases as lease
        set executor_probe_claim_token = %s,
            executor_probe_claimed_at = now(),
            {compatibility_assignments}
            executor_reconciliation_attempt_count = executor_reconciliation_attempt_count + 1,
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
            stale_after_seconds,
            limit,
            claim_token,
            *([claim_token] if legacy_settlement_compat else []),
        ),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]

async def release_sandbox_executor_probe_claim(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str = "",
    legacy_settlement_compat: bool = True,
) -> bool:
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = 'waiting_terminal',
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            {compatibility_assignments}
            executor_reconciliation_error = %s,
            updated_at = now()
        where id = %s
          and status = 'active'
          and (
            executor_status in ('accepted', 'running')
            or exists (
              select 1 from run_attempt_settlements detail
              where detail.tenant_id = sandbox_leases.tenant_id
                and detail.run_id = sandbox_leases.run_id
                and detail.attempt_id = sandbox_leases.attempt_id
                and detail.source_lease_id = sandbox_leases.id
                and detail.mapping_state = 'mapped'
                and detail.dispatch_state in ('unknown', 'accepted')
                and detail.intent_recovery_context_json is not null
                and detail.accepted_context_json is null
                and detail.receipt_json is null
            )
          )
          and executor_probe_claim_token = %s
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


async def list_sandbox_executor_suspect_candidate_scopes(
    connection: Any,
    *,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, str]]:
    """Read a bounded probe-claim set before callers acquire ordered source locks."""

    cursor = await connection.execute(
        """
        select lease.id, lease.tenant_id, lease.run_id, lease.attempt_id
        from sandbox_leases lease
        left join run_attempt_settlements detail
          on detail.tenant_id = lease.tenant_id
         and detail.run_id = lease.run_id
         and detail.attempt_id = lease.attempt_id
         and detail.source_lease_id = lease.id
        where lease.status = 'active'
          and (
            lease.executor_status in ('accepted', 'running')
            or (
              lease.executor_status = 'pending'
              and (
                (detail.mapping_state = 'mapped'
                and detail.dispatch_state in ('unknown', 'accepted')
                and detail.intent_recovery_context_json is not null
                and detail.accepted_context_json is null
                and detail.receipt_json is null)
                or (
                  detail.attempt_id is null
                  and to_jsonb(lease)->>'executor_reconciliation_intent_context_json' is not null
                  and exists (
                    select 1 from sandbox_execution_dispatches dispatch
                    where dispatch.tenant_id = lease.tenant_id and dispatch.run_id = lease.run_id
                      and dispatch.attempt_id = lease.attempt_id and dispatch.lease_id = lease.id
                      and dispatch.owner_generation::text = lease.lease_payload_json->>'owner_generation'
                      and dispatch.status in ('intent', 'unknown', 'accepted')
                  )
                )
              )
            )
          )
          and detail.receipt_json is null
          and (detail.attempt_id is not null or to_jsonb(lease)->>'executor_terminal_json' is null)
          and (
            (lease.executor_probe_claim_token is null
             and coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                  < now() - make_interval(secs => %s))
            or
            (lease.executor_probe_claim_token is not null
             and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                  < now() - make_interval(secs => %s))
          )
        order by coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at), lease.id
        limit %s
        """,
        (stale_after_seconds, stale_after_seconds, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]


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

async def quarantine_sandbox_executor_reconciliation(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    error: str,
    legacy_settlement_compat: bool = True,
) -> bool:
    """Stop automatic claims without falsely recording an unverifiable runtime as released."""

    compatibility_assignments = ""
    compatibility_predicate = ""
    parameters: tuple[Any, ...]
    if legacy_settlement_compat:
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
        compatibility_predicate = "and executor_cleanup_status = 'waiting_settlement'"
        parameters = (error[:1000], lease_id)

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

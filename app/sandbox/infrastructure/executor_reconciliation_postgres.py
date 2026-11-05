"""Sandbox executor terminal and reconciliation persistence operations."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size


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


# Stop-snapshot continuation checkpoints and prompt cancel delivery (#1899).


async def get_quarantined_checkpoint_for_repair(
    connection: Any, *, scope: dict[str, str], lease_id: str,
) -> dict[str, Any] | None:
    cursor = await connection.execute(
        """select * from sandbox_leases where id=%s and tenant_id=%s and workspace_id=%s
             and user_id=%s and session_id=%s and run_id=%s and attempt_id=%s
             and status='quarantined' and executor_reconciliation_status='failed'
             and executor_reconciliation_error='executor_reconciliation_runtime_handle_invalid'
             and executor_reconciliation_claim_token is null
             and not (lease_payload_json ? 'checkpoint_repair') for update""",
        (lease_id, *(scope[key] for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        ))),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def claim_quarantined_checkpoint_for_repair(
    connection: Any, *, scope: dict[str, str], lease_id: str, invocation_id: str,
    claim_token: str, expected_updated_at: Any, expected_stage: dict[str, Any],
    writer_generation: int, cancellation_generation: int,
) -> dict[str, Any] | None:
    """Explicit operator-only CAS; never included in automatic claim scanning."""
    cursor = await connection.execute(
        """update sandbox_leases set status='released',released_at=coalesce(released_at,now()),
             release_reason='verified_checkpoint_operator_repair',executor_reconciliation_status='claimed',
             executor_reconciliation_claim_token=%s,executor_reconciliation_claimed_at=now(),
             executor_terminal_reconciliation_attempt_count=executor_terminal_reconciliation_attempt_count+1,
             lease_payload_json=jsonb_set(lease_payload_json,'{checkpoint_repair}',jsonb_build_object(
               'schema_version','ai-platform.checkpoint-repair.v1','invocation_id',%s::text,
               'source_status',status,'source_reconciliation_status',executor_reconciliation_status,
               'source_error',executor_reconciliation_error,'source_quarantined_at',updated_at,
               'writer_generation',%s::bigint,'cancellation_generation',%s::bigint,
               'claim_token',%s::text,'authorized_at',now())),updated_at=now()
           where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
             and run_id=%s and attempt_id=%s and status='quarantined'
             and executor_reconciliation_status='failed'
             and executor_reconciliation_error='executor_reconciliation_runtime_handle_invalid'
             and executor_reconciliation_claim_token is null and updated_at=%s
             and not (lease_payload_json ? 'checkpoint_repair')
             and lease_payload_json->'terminal_reconciliation'=%s::jsonb returning *""",
        (claim_token, invocation_id, writer_generation, cancellation_generation, claim_token,
         lease_id, *(scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")),
         expected_updated_at, json.dumps(expected_stage)),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def record_sandbox_reconciliation_stage(
    connection: Any, *, lease_id: str, claim_token: str, stage: dict[str, Any],
) -> bool:
    """Host-only bounded checkpoint; executor callbacks cannot write this field."""
    ensure_json_size(stage, max_bytes=RUN_RESULT_MAX_BYTES, code="sandbox_reconciliation_stage_too_large")
    cursor = await connection.execute(
        """update sandbox_leases set lease_payload_json = jsonb_set(
             lease_payload_json, '{terminal_reconciliation}', %s::jsonb), updated_at=now()
           where id=%s and executor_reconciliation_claim_token=%s
             and executor_reconciliation_status='claimed' and status in ('active','released')
             and tenant_id=%s and run_id=%s and attempt_id=%s
             and lease_payload_json->>'owner_generation'=%s
             and runtime_container_id=%s returning id""",
        (json.dumps(stage), lease_id, claim_token, stage["scope"]["tenant_id"],
         stage["scope"]["run_id"], stage["scope"]["attempt_id"],
         str(stage["owner_generation"]), stage["container_id"]),
    )
    return await cursor.fetchone() is not None


async def finalize_stopped_executor_checkpoint(
    connection: Any, *, scope: dict[str, str], lease_id: str, claim_token: str,
    expected_stage: dict[str, Any],
) -> bool:
    """Finalize only the exact host stop/save checkpoint under its current claim."""
    cursor = await connection.execute(
        """update sandbox_leases set status='released', released_at=coalesce(released_at,now()),
             release_reason='executor_reconciled', executor_reconciliation_status='finalized',
             executor_reconciliation_retry_not_before=null, executor_reconciliation_claim_token=null,
             executor_reconciliation_claimed_at=null, executor_reconciliation_error='',
             executor_reconciled_at=now(), updated_at=now()
           where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
             and run_id=%s and attempt_id=%s and status in ('active','released')
             and executor_reconciliation_status='claimed' and executor_reconciliation_claim_token=%s
             and lease_payload_json->'terminal_reconciliation'=%s::jsonb
             and lease_payload_json->>'owner_generation'=%s and runtime_container_id=%s
             and executor_terminal_json is not null returning id""",
        (lease_id, *(scope[key] for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )), claim_token, json.dumps(expected_stage), str(expected_stage["owner_generation"]), expected_stage["container_id"]),
    )
    return await cursor.fetchone() is not None

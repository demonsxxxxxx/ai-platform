"""Sandbox executor stale probe PostgreSQL operations."""

from __future__ import annotations

import json
from typing import Any

_UNKNOWN_RECEIPT_PROBE_STATE = """
  detail.mapping_state in ('unknown', 'conflict')
  and detail.dispatch_state = 'unknown'
  and detail.intent_recovery_context_json is not null
  and detail.accepted_context_json is null
  and detail.receipt_json is not null
"""

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
                detail.mapping_state in ('unknown', 'conflict')
                and detail.dispatch_state = 'unknown'
                and detail.intent_recovery_context_json is not null
                and detail.accepted_context_json is null
                and detail.receipt_json is not null
                and exists (
                  select 1 from sandbox_execution_dispatches dispatch
                  where dispatch.tenant_id = lease.tenant_id and dispatch.run_id = lease.run_id
                    and dispatch.attempt_id = lease.attempt_id and dispatch.lease_id = lease.id
                    and dispatch.owner_generation::text = lease.lease_payload_json->>'owner_generation'
                    and dispatch.status in ('intent', 'unknown')
                )
                and exists (
                  select 1 from run_attempts attempt
                  where attempt.tenant_id = lease.tenant_id
                    and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
                    and attempt.owner_generation::text = lease.lease_payload_json->>'owner_generation'
                )
              )
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
            and (
              detail.receipt_json is null
              or (
                detail.mapping_state in ('unknown', 'conflict')
                and detail.dispatch_state = 'unknown'
                and detail.intent_recovery_context_json is not null
                and detail.accepted_context_json is null
                and detail.receipt_json is not null
              )
            )
            and (detail.attempt_id is not null or to_jsonb(lease)->>'executor_terminal_json' is null)
            and (
              (
                {_UNKNOWN_RECEIPT_PROBE_STATE}
                and (
                  (lease.executor_probe_claim_token is null
                   and coalesce(lease.updated_at, lease.created_at)
                        < now() - make_interval(secs => %s))
                  or
                  (lease.executor_probe_claim_token is not null
                   and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                        < now() - make_interval(secs => %s))
                )
              )
              or (
                not ({_UNKNOWN_RECEIPT_PROBE_STATE})
                and (
                  (lease.executor_probe_claim_token is null
                   and coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                        < now() - make_interval(secs => %s))
                  or
                  (lease.executor_probe_claim_token is not null
                   and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                        < now() - make_interval(secs => %s))
                )
              )
            )
          order by case when ({_UNKNOWN_RECEIPT_PROBE_STATE})
                        then coalesce(lease.updated_at, lease.created_at)
                        else coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                   end,
                   lease.id
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
            stale_after_seconds,
            stale_after_seconds,
            limit,
            claim_token,
            *([claim_token] if legacy_settlement_compat else []),
        ),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]

async def read_unknown_receipt_probe_recovery_facts(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    owner_generation: int,
    provider: str,
    provider_task_id: str,
    claim_token: str,
    intent_context: dict[str, Any],
) -> dict[str, Any] | None:
    """Lock and verify one unknown-receipt probe recovery identity."""
    row = await (
        await connection.execute(
            """
            select attempt.owner_generation, lease.status, lease.attempt_id,
                   lease.lease_payload_json,
                   dispatch.owner_generation as dispatch_generation,
                   dispatch.lease_id as dispatch_lease_id,
                   dispatch.provider as dispatch_provider,
                   dispatch.provider_task_id as dispatch_task_id,
                   detail.mapping_state, detail.dispatch_state,
                   detail.intent_recovery_context_json,
                   detail.accepted_context_json, detail.receipt_json
            from sandbox_leases lease
            join run_attempts attempt
              on attempt.tenant_id = lease.tenant_id
             and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
            join sandbox_execution_dispatches dispatch
              on dispatch.tenant_id = lease.tenant_id
             and dispatch.run_id = lease.run_id
             and dispatch.attempt_id = lease.attempt_id
             and dispatch.lease_id = lease.id
            left join run_attempt_settlements detail
              on detail.tenant_id = lease.tenant_id
             and detail.run_id = lease.run_id
             and detail.attempt_id = lease.attempt_id
             and detail.source_lease_id = lease.id
            where lease.id = %s and lease.tenant_id = %s
              and lease.run_id = %s and lease.attempt_id = %s
              and lease.status = 'active'
              and lease.executor_probe_claim_token = %s
              and lease.lease_payload_json->>'attempt_id' = %s
              and lease.lease_payload_json->>'owner_generation' = %s
              and attempt.owner_generation = %s
              and dispatch.owner_generation = %s
              and dispatch.lease_id = %s
              and dispatch.provider = %s
              and dispatch.provider_task_id = %s
              and detail.mapping_state in ('unknown', 'conflict')
              and detail.dispatch_state = 'unknown'
              and detail.intent_recovery_context_json = %s::jsonb
              and detail.accepted_context_json is null
              and detail.receipt_json is not null
            for update of lease, attempt
            """,
            (
                lease_id, tenant_id, run_id, attempt_id, claim_token,
                attempt_id, str(owner_generation), owner_generation,
                owner_generation, lease_id, provider, provider_task_id,
                json.dumps(intent_context, ensure_ascii=False, separators=(",", ":")),
            ),
        )
    ).fetchone()
    return dict(row) if row is not None else None

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
            executor_reconciliation_status = case
              when executor_terminal_json is not null
               and (
                 executor_reconciliation_context_json is not null
                 or executor_reconciliation_intent_context_json is not null
               ) then 'pending'
              else 'waiting_terminal'
            end,
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
            or exists (
              select 1 from run_attempt_settlements detail
              where detail.tenant_id = sandbox_leases.tenant_id
                and detail.run_id = sandbox_leases.run_id
                and detail.attempt_id = sandbox_leases.attempt_id
                and detail.source_lease_id = sandbox_leases.id
                and detail.mapping_state in ('unknown', 'conflict')
                and detail.dispatch_state = 'unknown'
                and detail.intent_recovery_context_json is not null
                and detail.accepted_context_json is null
                and detail.receipt_json is not null
            )
            or exists (
              select 1 from run_attempt_settlements detail
              where detail.tenant_id = sandbox_leases.tenant_id
                and detail.run_id = sandbox_leases.run_id
                and detail.attempt_id = sandbox_leases.attempt_id
                and detail.source_lease_id = sandbox_leases.id
                and detail.mapping_state = 'mapped'
                and detail.dispatch_state = 'accepted'
                and detail.accepted_context_json is not null
                and detail.receipt_json is not null
            )
          )
          and executor_probe_claim_token = %s
        returning id
        """,
        (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None

async def list_sandbox_executor_suspect_candidate_scopes(
    connection: Any,
    *,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, str]]:
    """Read a bounded probe-claim set before callers acquire ordered source locks."""

    cursor = await connection.execute(
        f"""
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
              detail.mapping_state in ('unknown', 'conflict')
              and detail.dispatch_state = 'unknown'
              and detail.intent_recovery_context_json is not null
              and detail.accepted_context_json is null
              and detail.receipt_json is not null
              and exists (
                select 1 from sandbox_execution_dispatches dispatch
                where dispatch.tenant_id = lease.tenant_id and dispatch.run_id = lease.run_id
                  and dispatch.attempt_id = lease.attempt_id and dispatch.lease_id = lease.id
                  and dispatch.owner_generation::text = lease.lease_payload_json->>'owner_generation'
                  and dispatch.status in ('intent', 'unknown')
              )
              and exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = lease.tenant_id
                  and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
                  and attempt.owner_generation::text = lease.lease_payload_json->>'owner_generation'
              )
            )
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
          and (
            detail.receipt_json is null
            or (
              detail.mapping_state in ('unknown', 'conflict')
              and detail.dispatch_state = 'unknown'
              and detail.intent_recovery_context_json is not null
              and detail.accepted_context_json is null
              and detail.receipt_json is not null
            )
          )
          and (detail.attempt_id is not null or to_jsonb(lease)->>'executor_terminal_json' is null)
          and (
            (
              {_UNKNOWN_RECEIPT_PROBE_STATE}
              and (
                (lease.executor_probe_claim_token is null
                 and coalesce(lease.updated_at, lease.created_at)
                      < now() - make_interval(secs => %s))
                or
                (lease.executor_probe_claim_token is not null
                 and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                      < now() - make_interval(secs => %s))
              )
            )
            or (
              not ({_UNKNOWN_RECEIPT_PROBE_STATE})
              and (
                (lease.executor_probe_claim_token is null
                 and coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                      < now() - make_interval(secs => %s))
                or
                (lease.executor_probe_claim_token is not null
                 and coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at)
                      < now() - make_interval(secs => %s))
              )
            )
          )
        order by case when ({_UNKNOWN_RECEIPT_PROBE_STATE})
                      then coalesce(lease.updated_at, lease.created_at)
                      else coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at)
                 end,
                 lease.id
        limit %s
        """,
        (stale_after_seconds, stale_after_seconds, stale_after_seconds, stale_after_seconds, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]

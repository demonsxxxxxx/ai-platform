"""PostgreSQL facts and durable ownership handoff for worker supervision."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from app.execution.domain.supervision import OwnerMode, SupervisionCandidate, SupervisionDomain


def _candidate(row: dict[str, Any], domain: SupervisionDomain) -> SupervisionCandidate:
    facts = row.get("facts") or {}
    return SupervisionCandidate(
        domain=domain,
        candidate_id=str(row["candidate_id"]),
        action=str(row["action"]),
        deadline_at=row["deadline_at"],
        priority=int(100 if row.get("priority") is None else row["priority"]),
        facts=tuple(sorted((str(key), str(value)) for key, value in facts.items())),
    )


async def _scan_domain(
    connection: Any,
    *,
    domain: SupervisionDomain,
    limit: int,
    query: str,
    parameters: tuple[Any, ...],
    cursor_column: str = "scan_cursor",
) -> list[SupervisionCandidate]:
    if cursor_column not in {
        "scan_cursor",
        "probe_cancel_scan_cursor",
        "probe_normal_scan_cursor",
    }:
        raise ValueError("execution_supervision_cursor_invalid")
    cursor = await connection.execute(
        f"select {cursor_column} from execution_supervision_domains where domain = %s for update",
        (domain.value,),
    )
    control = await cursor.fetchone()
    if control is None:
        raise RuntimeError("execution_supervision_domain_missing")
    scan_cursor = control.get(cursor_column)
    cursor = await connection.execute(
        query,
        (*parameters, scan_cursor, max(int(limit), 1)),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    if rows:
        await connection.execute(
            f"""update execution_supervision_domains
               set {cursor_column} = %s, updated_at = clock_timestamp()
               where domain = %s""",
            (rows[-1]["scan_key"], domain.value),
        )
    return [_candidate(row, domain) for row in rows]


async def observe_persisted_candidates(
    connection: Any,
    *,
    now: datetime,
    batch_limits: dict[SupervisionDomain, int],
    probe_stale_seconds: int,
    settlement_claim_stale_seconds: int,
) -> tuple[SupervisionCandidate, ...]:
    """Read due candidates only; never claim business rows or call an action owner."""
    candidates: list[SupervisionCandidate] = []
    candidates.extend(await _scan_domain(
        connection,
        domain=SupervisionDomain.DELIVERY,
        limit=batch_limits[SupervisionDomain.DELIVERY],
        parameters=(now, now, now),
        query="""
          with due as (
            select encode(convert_to(intent.tenant_id, 'UTF8'), 'hex') || '.' ||
                   encode(convert_to(intent.run_id, 'UTF8'), 'hex') as scan_key,
                   intent.tenant_id || '/' || intent.run_id as candidate_id,
                   case
                     when attempt.id is not null then 'readback_delivery_receipt'
                     when runs.status <> 'queued' or runs.cancel_requested_at is not null
                       or runs.terminalization_target is not null then 'suppress_delivery'
                     else 'recover_delivery'
                   end::text as action,
                   intent.next_retry_at as deadline_at,
                   100::integer as priority,
                   jsonb_build_object('intent_state', intent.state,
                     'message_id', intent.queue_message_id,
                     'run_status', runs.status,
                     'attempt_id', attempt.id,
                     'attempt_status', attempt.status,
                     'enqueue_permitted', attempt.id is null
                       and runs.status = 'queued'
                       and runs.cancel_requested_at is null
                       and runs.terminalization_target is null) as facts
            from run_delivery_intents intent
            join runs on runs.tenant_id = intent.tenant_id and runs.id = intent.run_id
            left join lateral (
              select candidate_attempt.id, candidate_attempt.status
              from run_attempts candidate_attempt
              where candidate_attempt.tenant_id = intent.tenant_id
                and candidate_attempt.run_id = intent.run_id
              order by candidate_attempt.ordinal desc, candidate_attempt.created_at desc
              limit 1
            ) attempt on true
            where intent.state in ('pending', 'claimed')
              and (intent.state = 'pending' or intent.claim_expires_at <= %s)
              and (
                (attempt.id is not null and intent.next_retry_at <= %s)
                or (attempt.id is null and (
                  (runs.status = 'queued' and runs.cancel_requested_at is null
                    and runs.terminalization_target is null
                    and intent.next_retry_at <= %s)
                  or runs.status <> 'queued'
                  or runs.cancel_requested_at is not null
                  or runs.terminalization_target is not null
                ))
              )
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """,
    ))
    probe_query = """
          with due as (
            select 'l.' || encode(convert_to(lease.id, 'UTF8'), 'hex') as scan_key,
                   lease.id as candidate_id, 'probe_executor'::text as action,
                   (case when lease.executor_probe_claim_token is not null
                     then coalesce(lease.executor_probe_claimed_at,
                                   lease.updated_at, lease.created_at)
                     else coalesce(lease.executor_heartbeat_at,
                                   lease.updated_at, lease.created_at) end) +
                     (%s * interval '1 second') as deadline_at,
                   case when runs.cancel_requested_at is not null then 0 else 100 end as priority,
                   jsonb_build_object('cancel_requested', runs.cancel_requested_at is not null,
                     'executor_status', lease.executor_status) as facts
            from sandbox_leases lease
            join runs on runs.tenant_id = lease.tenant_id and runs.id = lease.run_id
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
                 and coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at) +
                     (%s * interval '1 second') <= %s)
                or
                (lease.executor_probe_claim_token is not null
                 and coalesce(lease.executor_probe_claimed_at,
                              lease.updated_at, lease.created_at) +
                     (%s * interval '1 second') <= %s)
              )
              and {lane_filter}
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """
    # Give cancellations and ordinary probes separate fair cursors and the same
    # per-lane page budget. The total observation page can be twice the setting;
    # execution capacity remains a separate owner budget.
    probe_parameters = (
        probe_stale_seconds,
        probe_stale_seconds,
        now,
        probe_stale_seconds,
        now,
    )
    for cursor_column, lane_filter in (
        ("probe_cancel_scan_cursor", "runs.cancel_requested_at is not null"),
        ("probe_normal_scan_cursor", "runs.cancel_requested_at is null"),
    ):
        candidates.extend(await _scan_domain(
            connection,
            domain=SupervisionDomain.PROBE,
            limit=batch_limits[SupervisionDomain.PROBE],
            parameters=probe_parameters,
            query=probe_query.format(lane_filter=lane_filter),
            cursor_column=cursor_column,
        ))

    candidates.extend(await _scan_domain(
        connection,
        domain=SupervisionDomain.SETTLEMENT,
        limit=batch_limits[SupervisionDomain.SETTLEMENT],
        parameters=(settlement_claim_stale_seconds, settlement_claim_stale_seconds, now, now),
        query="""
          with due as (
            select encode(convert_to(lease.id, 'UTF8'), 'hex') as scan_key,
                   lease.id as candidate_id, 'settle_terminal_receipt'::text as action,
                   case when detail.settlement_phase = 'claimed'
                     then detail.settlement_claimed_at +
                       (%s * interval '1 second')
                     else coalesce(detail.next_retry_at,
                       lease.executor_terminal_received_at, lease.updated_at)
                   end as deadline_at,
                   0::integer as priority,
                   jsonb_build_object('terminal_receipt_present', true,
                     'reconciliation_status', detail.settlement_phase) as facts
            from sandbox_leases lease
            join run_attempt_settlements detail
              on detail.tenant_id = lease.tenant_id
             and detail.run_id = lease.run_id
             and detail.attempt_id = lease.attempt_id
             and detail.source_lease_id = lease.id
            where detail.mapping_state = 'mapped'
              and detail.receipt_json is not null
              and detail.accepted_context_json is not null
              and detail.settlement_phase in ('pending', 'retry', 'claimed')
              and ((detail.settlement_phase = 'claimed'
                    and detail.settlement_claimed_at +
                      (%s * interval '1 second') <= %s)
                or (detail.settlement_phase in ('pending', 'retry')
                    and coalesce(detail.next_retry_at,
                      lease.executor_terminal_received_at, lease.updated_at) <= %s))
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """,
    ))
    candidates.extend(await _scan_domain(
        connection,
        domain=SupervisionDomain.CLEANUP,
        limit=batch_limits[SupervisionDomain.CLEANUP],
        parameters=(now, now, now),
        query="""
          with due as (
            select 'l.' || encode(convert_to(lease.id, 'UTF8'), 'hex') as scan_key,
                   lease.id as candidate_id, 'cleanup_sandbox_lease'::text as action,
                   case when lease.executor_cleanup_status in ('cleanup_pending', 'retry', 'failed', 'claimed')
                     then lease.updated_at else lease.expires_at end as deadline_at,
                   100::integer as priority,
                   jsonb_build_object('lease_status', lease.status,
                     'cleanup_status', lease.executor_cleanup_status) as facts
            from sandbox_leases lease
            where ((lease.executor_cleanup_status in ('cleanup_pending', 'retry', 'failed')
                    and coalesce(lease.executor_cleanup_retry_not_before, '-infinity'::timestamptz) <= %s)
                or (lease.executor_cleanup_status = 'waiting_settlement'
                    and exists (
                      select 1 from run_attempt_settlements detail
                      where detail.tenant_id = lease.tenant_id
                        and detail.run_id = lease.run_id
                        and detail.attempt_id = lease.attempt_id
                        and detail.source_lease_id = lease.id
                        and detail.mapping_state = 'mapped'
                        and detail.settlement_phase = 'settled'
                        and detail.result_status in ('succeeded', 'failed', 'cancelled', 'canceled')
                        and detail.result_json is not null
                        and detail.settled_at is not null
                    ))
                or (lease.status = 'active' and lease.expires_at is not null
                    and lease.expires_at <= %s
                    and lease.executor_cleanup_status in ('not_required', 'finalized')))
            union all
            select 'o.' || encode(convert_to(outbox.id, 'UTF8'), 'hex') as scan_key,
                   'object:' || outbox.id as candidate_id, 'delete_object'::text as action,
                   outbox.available_at as deadline_at, 200::integer as priority,
                   jsonb_build_object('target_type', outbox.target_type,
                     'attempts', outbox.attempts) as facts
            from object_deletion_outbox outbox
            where outbox.state in ('pending', 'failed', 'file_pending', 'file_failed',
              'provider_image_pending', 'provider_image_failed')
              and outbox.available_at <= %s
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """,
    ))
    return tuple(candidates)


async def read_domain_control(connection: Any, *, domain: SupervisionDomain) -> dict[str, Any] | None:
    cursor = await connection.execute(
        "select * from execution_supervision_domains where domain = %s",
        (domain.value,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def claim_domain_work(
    connection: Any,
    *,
    domain: SupervisionDomain,
    owner_mode: OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacity: int = 1,
) -> dict[str, Any] | None:
    """Claim one in-flight legacy or unified scan under the persistent domain version."""
    if not worker_id or ttl_seconds < 1 or capacity < 1:
        raise ValueError("execution_supervision_claim_invalid")
    token = uuid.uuid4()
    cursor = await connection.execute(
        "select mode, owner_version from execution_supervision_domains where domain = %s for update",
        (domain.value,),
    )
    control = await cursor.fetchone()
    if control is None or control["mode"] != owner_mode:
        return None
    if owner_mode == "unified":
        cursor = await connection.execute(
            "select count(*) as inflight_count from execution_supervision_claims where domain = %s and recovered_at is null",
            (domain.value,),
        )
        claim_count = await cursor.fetchone()
        if int(claim_count["inflight_count"]) >= capacity:
            return None
    cursor = await connection.execute(
        """insert into execution_supervision_claims(
             domain, claim_token, owner_mode, worker_id, owner_version, expires_at)
           select domain, %s, mode, %s, owner_version,
                  clock_timestamp() + (%s * interval '1 second')
           from execution_supervision_domains
           where domain = %s and mode = %s
           returning domain, claim_token, owner_mode, worker_id, owner_version, expires_at""",
        (token, worker_id, ttl_seconds, domain.value, owner_mode),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def claim_is_current(connection: Any, claim: dict[str, Any]) -> bool:
    cursor = await connection.execute(
        """select exists (
             select 1 from execution_supervision_claims claim
             join execution_supervision_domains domain on domain.domain = claim.domain
             where claim.domain = %s and claim.claim_token = %s
               and claim.worker_id = %s and claim.owner_version = %s
               and claim.owner_mode = domain.mode
               and claim.owner_version = domain.owner_version
               and claim.recovered_at is null
               and claim.expires_at > clock_timestamp()
           ) as current""",
        (claim["domain"], claim["claim_token"], claim["worker_id"], claim["owner_version"]),
    )
    row = await cursor.fetchone()
    return bool(row and row["current"])


async def release_domain_work(connection: Any, claim: dict[str, Any]) -> bool:
    cursor = await connection.execute(
        "delete from execution_supervision_claims where domain = %s and claim_token = %s and worker_id = %s and recovered_at is null returning claim_token",
        (claim["domain"], claim["claim_token"], claim["worker_id"]),
    )
    return await cursor.fetchone() is not None


async def recover_expired_domain_claim(
    connection: Any,
    *,
    domain: SupervisionDomain,
    expected_domain_version: int,
    expected_claim_owner_version: int,
    claim_token: uuid.UUID | str,
    worker_id: str,
    operator_id: str,
    stop_evidence: str,
) -> dict[str, Any] | None:
    """Fence and record operator recovery after the exact owner is confirmed stopped."""
    if (
        expected_domain_version < 1
        or expected_claim_owner_version < 1
        or not worker_id.strip()
        or not operator_id.strip()
        or not stop_evidence.strip()
        or len(stop_evidence) > 2048
    ):
        raise ValueError("execution_supervision_recovery_evidence_required")
    try:
        token = uuid.UUID(str(claim_token))
    except (TypeError, ValueError) as exc:
        raise ValueError("execution_supervision_claim_token_invalid") from exc
    cursor = await connection.execute(
        """update execution_supervision_claims as claim
           set recovered_at = clock_timestamp(), recovered_by = %s,
               recovery_evidence = %s
           from execution_supervision_domains as control
           where claim.domain = %s and claim.claim_token = %s
             and claim.worker_id = %s
             and claim.owner_version = %s
             and claim.recovered_at is null
             and claim.expires_at <= clock_timestamp()
             and control.domain = claim.domain
             and control.owner_version = %s
           returning claim.domain, claim.claim_token, claim.owner_mode,
                     claim.worker_id, claim.owner_version, claim.recovered_at,
                     claim.recovered_by, claim.recovery_evidence""",
        (
            operator_id.strip(), stop_evidence.strip(), domain.value, token,
            worker_id, expected_claim_owner_version, expected_domain_version,
        ),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def request_domain_drain(
    connection: Any,
    *,
    domain: SupervisionDomain,
    expected_version: int,
    expected_mode: OwnerMode = "legacy",
) -> int | None:
    cursor = await connection.execute(
        """update execution_supervision_domains
           set mode = 'draining', owner_version = owner_version + 1,
               drain_started_at = clock_timestamp(), updated_at = clock_timestamp()
           where domain = %s and mode = %s and owner_version = %s
           returning owner_version""",
        (domain.value, expected_mode, expected_version),
    )
    row = await cursor.fetchone()
    return int(row["owner_version"]) if row else None


async def activate_unified_domain(
    connection: Any, *, domain: SupervisionDomain, expected_version: int
) -> int | None:
    cursor = await connection.execute(
        """update execution_supervision_domains as control
           set mode = 'unified', owner_version = owner_version + 1,
               drain_started_at = null, updated_at = clock_timestamp()
           where control.domain = %s and control.mode = 'draining' and control.owner_version = %s
             and not exists (select 1 from execution_supervision_claims claim
               where claim.domain = control.domain and claim.recovered_at is null)
           returning owner_version""",
        (domain.value, expected_version),
    )
    row = await cursor.fetchone()
    return int(row["owner_version"]) if row else None


async def return_domain_to_legacy(
    connection: Any, *, domain: SupervisionDomain, expected_version: int
) -> int | None:
    cursor = await connection.execute(
        """update execution_supervision_domains as control
           set mode = 'legacy', owner_version = owner_version + 1,
               drain_started_at = null, updated_at = clock_timestamp()
           where control.domain = %s and control.mode = 'draining' and control.owner_version = %s
             and not exists (select 1 from execution_supervision_claims claim
               where claim.domain = control.domain and claim.recovered_at is null)
           returning owner_version""",
        (domain.value, expected_version),
    )
    row = await cursor.fetchone()
    return int(row["owner_version"]) if row else None

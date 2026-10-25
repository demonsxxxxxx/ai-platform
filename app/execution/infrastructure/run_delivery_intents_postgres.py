"""PostgreSQL storage for immutable Run queue delivery intents."""

from __future__ import annotations

from typing import Any

from app.execution.application.run_delivery_intents import (
    QueueDeliveryReceipt,
    RunDeliveryIntent,
)
from app.platform.postgres.errors import RepositoryConflictError


async def insert(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    session_id: str,
    source: str,
    submission_id: str | None,
    user_message_id: str | None,
    payload_json: str,
    message_id: str,
    payload_sha256: str,
) -> dict[str, Any]:
    cursor = await connection.execute(
        """
        insert into run_delivery_intents(
          tenant_id, run_id, session_id, source, submission_id, user_message_id,
          queue_payload_json, queue_message_id, payload_sha256
        )
        values (%s, %s, %s, %s, %s::uuid, %s, %s, %s, %s)
        on conflict (tenant_id, run_id) do nothing
        returning *
        """,
        (
            tenant_id,
            run_id,
            session_id,
            source,
            submission_id,
            user_message_id,
            payload_json,
            message_id,
            payload_sha256,
        ),
    )
    created = await cursor.fetchone()
    if created is not None:
        return dict(created)
    existing = await get(
        connection, tenant_id=tenant_id, run_id=run_id, for_update=True
    )
    if existing is None:
        raise RepositoryConflictError("run_delivery_intent_insert_conflict")
    identity = (
        ("session_id", session_id),
        ("source", source),
        ("submission_id", submission_id),
        ("user_message_id", user_message_id),
        ("queue_payload_json", payload_json),
        ("queue_message_id", message_id),
        ("payload_sha256", payload_sha256),
    )
    if any(
        _identity_value(
            existing.get(column), value, uuid_value=column == "submission_id"
        )
        is False
        for column, value in identity
    ):
        raise RepositoryConflictError("run_delivery_intent_identity_conflict")
    return existing


async def get(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    cursor = await connection.execute(
        """
        select *
        from run_delivery_intents
        where tenant_id = %s and run_id = %s
        """
        + ("for update" if for_update else ""),
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def lock_historical_backfill(connection: Any) -> None:
    """Serialize the one-time historical mapping across API and worker owners."""

    await connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        ("run_delivery_intents.historical_backfill.v1",),
    )


async def list_historical_candidates(
    connection: Any, *, limit: int
) -> list[dict[str, Any]]:
    """Return one bounded page of accepted queued Runs without an intent."""

    cursor = await connection.execute(
        """
        select runs.*,
               runs.id::text as run_id,
               coalesce((
                 select jsonb_agg(jsonb_build_object(
                   'submission_id', submission.submission_id::text,
                   'tenant_id', submission.tenant_id,
                   'user_id', submission.user_id,
                   'session_id', submission.session_id,
                   'run_id', submission.run_id,
                   'state', submission.state,
                   'queue_message_id', submission.queue_message_id
                 ))
                 from chat_submissions submission
                 where submission.tenant_id = runs.tenant_id
                   and submission.run_id = runs.id
               ), '[]'::jsonb) as chat_submission_records,
               coalesce((
                 select jsonb_agg(submission.submission_id::text)
                 from chat_submissions submission
                 where submission.tenant_id = runs.tenant_id
                   and submission.run_id = runs.id
               ), '[]'::jsonb) as chat_submission_ids,
               coalesce((
                 select jsonb_agg(message.id order by message.created_at, message.id)
                 from messages message
                 where message.tenant_id = runs.tenant_id
                   and message.session_id = runs.session_id
                   and message.run_id = runs.id
                   and message.role = 'user'
               ), '[]'::jsonb) as user_message_ids,
               coalesce((
                 select jsonb_agg(operation.payload_json order by operation.sequence, operation.created_at)
                 from run_events operation
                 where operation.tenant_id = runs.tenant_id
                   and operation.event_type = 'run_control_operation_committed'
                   and operation.payload_json->>'child_run_id' = runs.id
               ), '[]'::jsonb) as control_operations
        from runs
        where runs.status = 'queued'
          and runs.cancel_requested_at is null
          and runs.terminalization_target is null
          and not exists (
            select 1 from run_attempts attempt
            where attempt.tenant_id = runs.tenant_id and attempt.run_id = runs.id
          )
          and not exists (
            select 1 from run_delivery_intents delivery
            where delivery.tenant_id = runs.tenant_id and delivery.run_id = runs.id
          )
        order by runs.created_at, runs.tenant_id, runs.id
        limit %s
        for update of runs
        """,
        (max(1, min(int(limit), 100)),),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def claim_due(
    connection: Any,
    *,
    limit: int,
    claim_ttl_seconds: int,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Claim delivery or receipt recovery; an existing Attempt only forbids enqueue."""

    filters = "(%s::text is null or intent.tenant_id = %s) and (%s::text is null or intent.run_id = %s)"
    await connection.execute(
        f"""
        with candidates as (
          select intent.tenant_id, intent.run_id,
                 'run_not_queued' as reason
          from run_delivery_intents as intent
          join runs on runs.tenant_id = intent.tenant_id and runs.id = intent.run_id
          where intent.state in ('pending', 'claimed', 'delivered')
            and {filters}
            and (intent.state in ('pending', 'delivered') or intent.claim_expires_at <= now())
            and not exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
            )
            and (
              runs.status <> 'queued'
              or runs.cancel_requested_at is not null
              or runs.terminalization_target is not null
            )
          order by intent.updated_at, intent.created_at, intent.tenant_id, intent.run_id
          limit %s
          for update of intent skip locked
        )
        update run_delivery_intents as intent
        set state = 'suppressed',
            claim_token = null,
            claimed_at = null,
            claim_expires_at = null,
            last_error_code = candidates.reason,
            suppressed_at = now(),
            updated_at = now()
        from candidates
        where intent.tenant_id = candidates.tenant_id
          and intent.run_id = candidates.run_id
        """,
        (tenant_id, tenant_id, run_id, run_id, max(int(limit), 1)),
    )
    cursor = await connection.execute(
        f"""
        with candidates as (
          select intent.tenant_id, intent.run_id
          from run_delivery_intents as intent
          join runs on runs.tenant_id = intent.tenant_id and runs.id = intent.run_id
          where intent.state in ('pending', 'claimed', 'delivered')
            and intent.next_retry_at <= now()
            and (intent.state in ('pending', 'delivered') or intent.claim_expires_at <= now())
            and ((runs.status = 'queued'
            and runs.cancel_requested_at is null
            and runs.terminalization_target is null
            and not exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
            )) or exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
            ))
            and {filters}
          order by intent.next_retry_at, intent.created_at, intent.tenant_id, intent.run_id
          limit %s
          for update of intent skip locked
        )
        update run_delivery_intents as intent
        set state = 'claimed',
            claim_token = gen_random_uuid(),
            claimed_at = now(),
            claim_expires_at = now() + (%s * interval '1 second'),
            attempt_count = intent.attempt_count + 1,
            updated_at = now()
        from candidates
        where intent.tenant_id = candidates.tenant_id and intent.run_id = candidates.run_id
        returning intent.*
        """,
        (
            tenant_id,
            tenant_id,
            run_id,
            run_id,
            max(int(limit), 1),
            max(int(claim_ttl_seconds), 1),
        ),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def claim_is_current(
    connection: Any, intent: RunDeliveryIntent, *, for_enqueue: bool = True
) -> bool:
    cursor = await connection.execute(
        """
        select exists (
          select 1
          from run_delivery_intents delivery
          join runs on runs.tenant_id = delivery.tenant_id and runs.id = delivery.run_id
          where delivery.tenant_id = %s and delivery.run_id = %s
            and delivery.state = 'claimed' and delivery.claim_token = %s::uuid
            and delivery.claim_expires_at > now()
            and (not %s or (runs.status = 'queued'
            and runs.cancel_requested_at is null
            and runs.terminalization_target is null
            and not exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
            )))
        ) as current
        """,
        (intent.tenant_id, intent.run_id, intent.claim_token, for_enqueue),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("current"))


async def record_receipt(
    connection: Any,
    *,
    intent: RunDeliveryIntent,
    receipt: QueueDeliveryReceipt,
    retry_after_seconds: int = 900,
) -> bool:
    cursor = await connection.execute(
        """
        update run_delivery_intents as delivery
        set state = 'delivered',
            claim_token = null,
            claimed_at = null,
            claim_expires_at = null,
            queue_position = %s,
            queue_admission_ordinal = %s,
            queue_message_id = %s,
            queue_admission_source = %s,
            delivered_at = now(),
            next_retry_at = now() + (%s * interval '1 second'),
            last_error_code = null,
            updated_at = now()
        where delivery.tenant_id = %s and delivery.run_id = %s
          and delivery.state = 'claimed' and delivery.claim_token = %s::uuid
          and delivery.claim_expires_at > now()
          and delivery.queue_payload_json = %s
          and delivery.queue_message_id = %s
          and delivery.payload_sha256 = %s
          and %s = %s
        returning delivery.run_id
        """,
        (
            receipt.queue_position,
            receipt.queue_admission_ordinal,
            receipt.message_id,
            receipt.source,
            max(int(retry_after_seconds), 1),
            intent.tenant_id,
            intent.run_id,
            intent.claim_token,
            intent.payload_json,
            intent.message_id,
            intent.payload_sha256,
            receipt.message_id,
            intent.message_id,
        ),
    )
    return await cursor.fetchone() is not None


def _identity_value(existing: Any, expected: Any, *, uuid_value: bool) -> bool:
    if uuid_value and existing is not None and expected is not None:
        return str(existing).lower() == str(expected).lower()
    return existing == expected


async def reschedule(
    connection: Any,
    *,
    intent: RunDeliveryIntent,
    retry_after_seconds: int,
    error_code: str,
) -> bool:
    cursor = await connection.execute(
        """
        update run_delivery_intents as delivery
        set state = case
              when exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
              ) then 'pending'
              when runs.status = 'queued'
                and runs.cancel_requested_at is null
                and runs.terminalization_target is null
                and not exists (
                  select 1 from run_attempts attempt
                  where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
                ) then 'pending'
              else 'suppressed'
            end,
            claim_token = null,
            claimed_at = null,
            claim_expires_at = null,
            next_retry_at = now() + (%s * interval '1 second'),
            last_error_code = %s,
            suppressed_at = case
              when exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
              ) then null
              when runs.status = 'queued'
                and runs.cancel_requested_at is null
                and runs.terminalization_target is null
                and not exists (
                  select 1 from run_attempts attempt
                  where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
                ) then null
              else now()
            end,
            updated_at = now()
        from runs
        where runs.tenant_id = delivery.tenant_id and runs.id = delivery.run_id
          and delivery.tenant_id = %s and delivery.run_id = %s
          and delivery.state = 'claimed' and delivery.claim_token = %s::uuid
          and delivery.claim_expires_at > now()
        returning delivery.state
        """,
        (
            max(int(retry_after_seconds), 1),
            error_code,
            intent.tenant_id,
            intent.run_id,
            intent.claim_token,
        ),
    )
    return await cursor.fetchone() is not None


async def suppress(
    connection: Any,
    *,
    intent: RunDeliveryIntent,
    reason: str,
) -> bool:
    cursor = await connection.execute(
        """
        update run_delivery_intents
        set state = 'suppressed',
            claim_token = null,
            claimed_at = null,
            claim_expires_at = null,
            last_error_code = %s,
            suppressed_at = now(),
            updated_at = now()
        where tenant_id = %s and run_id = %s
          and state = 'claimed' and claim_token = %s::uuid
          and claim_expires_at > now()
        returning run_id
        """,
        (reason, intent.tenant_id, intent.run_id, intent.claim_token),
    )
    return await cursor.fetchone() is not None

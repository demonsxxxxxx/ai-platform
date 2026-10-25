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


async def claim_due(
    connection: Any,
    *,
    limit: int,
    claim_ttl_seconds: int,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Suppress ineligible rows, then claim a fair bounded page with SKIP LOCKED."""

    filters = "(%s::text is null or intent.tenant_id = %s) and (%s::text is null or intent.run_id = %s)"
    await connection.execute(
        f"""
        with candidates as (
          select intent.tenant_id, intent.run_id,
                 case
                   when exists (
                     select 1 from run_attempts attempt
                     where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
                   ) then 'attempt_already_exists'
                   else 'run_not_queued'
                 end as reason
          from run_delivery_intents as intent
          join runs on runs.tenant_id = intent.tenant_id and runs.id = intent.run_id
          where intent.state in ('pending', 'claimed')
            and {filters}
            and (
              runs.status <> 'queued'
              or runs.cancel_requested_at is not null
              or runs.terminalization_target is not null
              or exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
              )
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
          where intent.state in ('pending', 'claimed')
            and intent.next_retry_at <= now()
            and (intent.state = 'pending' or intent.claim_expires_at <= now())
            and runs.status = 'queued'
            and runs.cancel_requested_at is null
            and runs.terminalization_target is null
            and not exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = intent.tenant_id and attempt.run_id = intent.run_id
            )
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


async def claim_is_current(connection: Any, intent: RunDeliveryIntent) -> bool:
    cursor = await connection.execute(
        """
        select exists (
          select 1
          from run_delivery_intents delivery
          join runs on runs.tenant_id = delivery.tenant_id and runs.id = delivery.run_id
          where delivery.tenant_id = %s and delivery.run_id = %s
            and delivery.state = 'claimed' and delivery.claim_token = %s::uuid
            and delivery.claim_expires_at > now()
            and runs.status = 'queued'
            and runs.cancel_requested_at is null
            and runs.terminalization_target is null
            and not exists (
              select 1 from run_attempts attempt
              where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
            )
        ) as current
        """,
        (intent.tenant_id, intent.run_id, intent.claim_token),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("current"))


async def record_receipt(
    connection: Any,
    *,
    intent: RunDeliveryIntent,
    receipt: QueueDeliveryReceipt,
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
            last_error_code = null,
            updated_at = now()
        where delivery.tenant_id = %s and delivery.run_id = %s
          and delivery.state = 'claimed' and delivery.claim_token = %s::uuid
          and delivery.claim_expires_at > now()
          and exists (
            select 1 from runs
            where runs.tenant_id = delivery.tenant_id and runs.id = delivery.run_id
              and runs.status = 'queued'
              and runs.cancel_requested_at is null
              and runs.terminalization_target is null
          )
          and not exists (
            select 1 from run_attempts attempt
            where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
          )
        returning delivery.run_id
        """,
        (
            receipt.queue_position,
            receipt.queue_admission_ordinal,
            receipt.message_id,
            receipt.source,
            intent.tenant_id,
            intent.run_id,
            intent.claim_token,
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
            last_error_code = case
              when runs.status = 'queued'
                and runs.cancel_requested_at is null
                and runs.terminalization_target is null
                and not exists (
                  select 1 from run_attempts attempt
                  where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
                ) then %s
              when exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = delivery.tenant_id and attempt.run_id = delivery.run_id
              ) then 'attempt_already_exists'
              else 'run_not_queued'
            end,
            suppressed_at = case
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

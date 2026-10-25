"""Persist Conversations projections for accepted queue deliveries."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatQueueAdmission:
    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str


def queued_outcome_projection(
    outcome: Mapping[str, Any],
    *,
    queue_position: int,
    submission_id: str | None,
) -> dict[str, Any]:
    """Project a saved accepted response after the queue receipt is committed."""

    projected = dict(outcome)
    projected.update(
        {
            "status": "queued",
            "queue_position": int(queue_position) or None,
        }
    )
    if submission_id is not None:
        projected["submission_id"] = submission_id
    return projected


async def persist_chat_queue_success(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    queue_admission: Any,
    outcome: Mapping[str, Any] | None,
    submission_id: str | None,
    user_id: str | None,
    append_event: Callable[..., Awaitable[Any]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> None:
    """Persist one queue receipt and, when keyed, its Chat submission state."""

    queue_position = int(queue_admission.queue_position)
    queue_ordinal = int(queue_admission.queue_admission_ordinal)
    await append_event(
        connection,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="queued",
        stage="queue",
        message="任务队列接纳完成",
        payload={
            "visible_to_user": False,
            "source": "admin_runtime_queue",
            "queue_position": queue_position or None,
            "queue_admission_ordinal": queue_ordinal or None,
            "queue_probe_source": str(queue_admission.source),
        },
    )
    if submission_id is not None:
        if not user_id or outcome is None:
            raise ValueError("chat_delivery_submission_projection_invalid")
        await finalize_submission(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            state="queued",
            outcome_json=dict(outcome),
            queue_position=queue_position or None,
            queue_admission_ordinal=queue_ordinal or None,
            queue_message_id=str(queue_admission.message_id),
        )


async def record_accepted_chat_delivery_receipt(
    connection: Any,
    *,
    intent: Any,
    receipt: Any,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
    append_event: Callable[..., Awaitable[Any]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> None:
    """Apply an exact durable Chat receipt in Execution's receipt transaction."""

    if str(intent.source) != "chat":
        return
    payload = _intent_payload(intent)
    user_id = payload.get("user_id")
    if (
        payload.get("tenant_id") != str(intent.tenant_id)
        or payload.get("run_id") != str(intent.run_id)
        or not isinstance(user_id, str)
        or not user_id
    ):
        raise ValueError("chat_delivery_payload_identity_invalid")
    if str(receipt.message_id) != str(intent.message_id):
        raise ValueError("chat_delivery_receipt_identity_invalid")

    tenant_id = str(intent.tenant_id)
    run_id = str(intent.run_id)
    submission_id = str(intent.submission_id) if intent.submission_id is not None else None
    queue_admission = _queue_admission_from_receipt(receipt)
    outcome: dict[str, Any] | None = None
    if submission_id is not None:
        submission = await get_submission(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            for_update=True,
        )
        if submission is None or str(submission.get("run_id") or "") != run_id:
            raise ValueError("chat_delivery_submission_identity_invalid")
        row = dict(submission)
        state = str(row.get("state") or "")
        if state == "queued":
            saved_message_id = str(row.get("queue_message_id") or "")
            if saved_message_id and saved_message_id != str(receipt.message_id):
                raise ValueError("chat_delivery_submission_receipt_conflict")
            if not saved_message_id:
                raw_outcome = row.get("outcome_json")
                await finalize_submission(
                    connection,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    submission_id=submission_id,
                    state="queued",
                    outcome_json=dict(raw_outcome) if isinstance(raw_outcome, Mapping) else {},
                    queue_position=int(receipt.queue_position) or None,
                    queue_admission_ordinal=int(receipt.queue_admission_ordinal) or None,
                    queue_message_id=str(receipt.message_id),
                )
            return
        if state != "accepted_pending_enqueue":
            return
        raw_outcome = row.get("outcome_json")
        if not isinstance(raw_outcome, Mapping) or not raw_outcome:
            raise ValueError("chat_delivery_submission_outcome_missing")
        outcome = queued_outcome_projection(
            raw_outcome,
            queue_position=int(receipt.queue_position),
            submission_id=submission_id,
        )

    await persist_chat_queue_success(
        connection,
        tenant_id=tenant_id,
        run_id=run_id,
        queue_admission=queue_admission,
        outcome=outcome,
        submission_id=submission_id,
        user_id=user_id if submission_id is not None else None,
        append_event=append_event,
        finalize_submission=finalize_submission,
    )


async def record_accepted_chat_delivery_rejection(
    connection: Any,
    *,
    intent: Any,
    reason: str,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> None:
    """Resolve a permanently rejected keyed Chat delivery after Runs terminalizes it."""

    if str(intent.source) != "chat" or intent.submission_id is None:
        return
    payload = _intent_payload(intent)
    user_id = payload.get("user_id")
    tenant_id = str(intent.tenant_id)
    run_id = str(intent.run_id)
    if (
        payload.get("tenant_id") != tenant_id
        or payload.get("run_id") != run_id
        or not isinstance(user_id, str)
        or not user_id
    ):
        raise ValueError("chat_delivery_payload_identity_invalid")
    submission_id = str(intent.submission_id)
    submission = await get_submission(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        for_update=True,
    )
    if submission is None or str(submission.get("run_id") or "") != run_id:
        raise ValueError("chat_delivery_submission_identity_invalid")
    if str(submission.get("state") or "") != "accepted_pending_enqueue":
        return
    await finalize_submission(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        state="enqueue_failed",
        rejection_code="queue_enqueue_failed",
    )


def _intent_payload(intent: Any) -> dict[str, Any]:
    try:
        payload = json.loads(intent.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("chat_delivery_payload_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("chat_delivery_payload_invalid")
    return payload


def _queue_admission_from_receipt(receipt: Any) -> ChatQueueAdmission:
    return ChatQueueAdmission(
        queue_position=int(receipt.queue_position),
        queue_admission_ordinal=int(receipt.queue_admission_ordinal),
        message_id=str(receipt.message_id),
        source=str(receipt.source),
    )

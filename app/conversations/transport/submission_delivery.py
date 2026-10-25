"""Persist Conversations projections after an accepted Run is delivered."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models import ChatStreamResponse


@dataclass(frozen=True)
class ChatQueueAdmission:
    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str


async def persist_chat_queue_success(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    queue_admission: Any,
    outcome: ChatStreamResponse | None,
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
            outcome_json=outcome.model_dump(mode="json"),
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
    """Apply a durable Chat receipt in the same transaction as Execution's receipt."""

    if str(intent.source) != "chat":
        return
    try:
        payload = json.loads(intent.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("chat_delivery_payload_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("tenant_id") != intent.tenant_id
        or payload.get("run_id") != intent.run_id
        or not isinstance(payload.get("user_id"), str)
        or not payload["user_id"]
    ):
        raise ValueError("chat_delivery_payload_identity_invalid")
    tenant_id = str(intent.tenant_id)
    user_id = str(payload["user_id"])
    submission_id = (
        str(intent.submission_id) if intent.submission_id is not None else None
    )
    queue_admission = _queue_admission_from_receipt(receipt)
    outcome: ChatStreamResponse | None = None
    if submission_id is not None:
        submission = await get_submission(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            for_update=True,
        )
        if submission is None or str(submission.get("run_id") or "") != str(intent.run_id):
            raise ValueError("chat_delivery_submission_identity_invalid")
        state = str(submission.get("state") or "")
        if state == "queued":
            if str(submission.get("queue_message_id") or "") != str(receipt.message_id):
                raise ValueError("chat_delivery_submission_receipt_conflict")
            return
        if state != "accepted_pending_enqueue":
            return
        raw_outcome = submission.get("outcome_json")
        if not isinstance(raw_outcome, dict) or not raw_outcome:
            raise ValueError("chat_delivery_submission_outcome_missing")
        outcome = ChatStreamResponse.model_validate(raw_outcome).model_copy(
            update={
                "status": "queued",
                "queue_position": int(receipt.queue_position) or None,
                "submission_id": submission_id,
            }
        )

    await persist_chat_queue_success(
        connection,
        tenant_id=tenant_id,
        run_id=str(intent.run_id),
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
    try:
        payload = json.loads(intent.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("chat_delivery_payload_invalid") from exc
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("chat_delivery_payload_identity_invalid")
    submission_id = str(intent.submission_id)
    submission = await get_submission(
        connection,
        tenant_id=str(intent.tenant_id),
        user_id=user_id,
        submission_id=submission_id,
        for_update=True,
    )
    if submission is None or str(submission.get("run_id") or "") != str(intent.run_id):
        raise ValueError("chat_delivery_submission_identity_invalid")
    if str(submission.get("state") or "") != "accepted_pending_enqueue":
        return
    await finalize_submission(
        connection,
        tenant_id=str(intent.tenant_id),
        user_id=user_id,
        submission_id=submission_id,
        state="enqueue_failed",
        rejection_code="queue_enqueue_failed",
    )


def _queue_admission_from_receipt(receipt: Any) -> Any:
    return ChatQueueAdmission(
        queue_position=int(receipt.queue_position),
        queue_admission_ordinal=int(receipt.queue_admission_ordinal),
        message_id=str(receipt.message_id),
        source=str(receipt.source),
    )

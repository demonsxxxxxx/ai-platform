"""Application operations for already accepted Chat submissions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.conversations.application.queue_delivery_receipts import (
    persist_chat_queue_success,
    queued_outcome_projection,
)
from app.conversations.application.submission_resolution import (
    submission_resolution_projection,
)


async def reload_chat_submission_resolution(
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    transaction: Callable[..., Any],
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
) -> dict[str, Any]:
    """Reload one principal-scoped accepted submission without changing it."""

    async with transaction() as connection:
        submission = await get_submission(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            for_update=True,
        )
    if submission is None:
        raise LookupError("chat_submission_not_found")
    return submission_resolution_projection(dict(submission))


async def record_chat_submission_delivery_receipt(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    receipt: Any,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
    append_event: Callable[..., Awaitable[Any]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> dict[str, Any]:
    """Apply a queue receipt to the accepted submission in its transaction."""

    submission = await get_submission(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        for_update=True,
    )
    if submission is None:
        raise LookupError("chat_submission_not_found")
    row = dict(submission)
    if str(row.get("run_id") or "") != run_id:
        raise ValueError("chat_submission_run_mismatch")
    queue_message_id = str(receipt.message_id)
    state = str(row.get("state") or "")
    if state == "queued":
        saved_message_id = str(row.get("queue_message_id") or "")
        if saved_message_id and saved_message_id != queue_message_id:
            raise ValueError("chat_submission_receipt_mismatch")
        if not saved_message_id:
            await finalize_submission(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                submission_id=submission_id,
                state="queued",
                outcome_json=(
                    dict(row["outcome_json"])
                    if isinstance(row.get("outcome_json"), Mapping)
                    else {}
                ),
                queue_position=int(receipt.queue_position) or None,
                queue_admission_ordinal=int(receipt.queue_admission_ordinal) or None,
                queue_message_id=queue_message_id,
            )
            row["queue_message_id"] = queue_message_id
            row["queue_position"] = int(receipt.queue_position) or None
            row["queue_admission_ordinal"] = (
                int(receipt.queue_admission_ordinal) or None
            )
        return submission_resolution_projection(row)
    if state != "accepted_pending_enqueue":
        return submission_resolution_projection(row)

    raw_outcome = row.get("outcome_json")
    if not isinstance(raw_outcome, Mapping) or not raw_outcome:
        raise ValueError("chat_delivery_submission_outcome_missing")
    queued_outcome = queued_outcome_projection(
        raw_outcome,
        queue_position=int(receipt.queue_position),
        submission_id=submission_id,
    )
    await persist_chat_queue_success(
        connection,
        tenant_id=tenant_id,
        run_id=run_id,
        queue_admission=receipt,
        outcome=queued_outcome,
        submission_id=submission_id,
        user_id=user_id,
        append_event=append_event,
        finalize_submission=finalize_submission,
    )
    row["state"] = "queued"
    row["outcome_json"] = queued_outcome
    row["queue_message_id"] = queue_message_id
    row["queue_position"] = int(receipt.queue_position) or None
    row["queue_admission_ordinal"] = int(receipt.queue_admission_ordinal) or None
    return submission_resolution_projection(row)


async def settle_chat_submission_enqueue_rejection(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    error: BaseException | None,
    definitive_rejection: bool,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
    get_run: Callable[..., Awaitable[Mapping[str, Any] | None]],
    terminalize_run: Callable[..., Awaitable[Any]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> dict[str, Any]:
    """Settle a keyed queue rejection only while its accepted Run is still queued."""

    submission = await get_submission(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        for_update=True,
    )
    if submission is None:
        raise LookupError("chat_submission_not_found")
    row = dict(submission)
    if str(row.get("run_id") or "") != run_id:
        raise ValueError("chat_submission_run_mismatch")
    if row.get("state") != "accepted_pending_enqueue" or not definitive_rejection:
        return submission_resolution_projection(row)
    if error is None:
        raise ValueError("chat_submission_rejection_error_missing")

    run = await get_run(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        for_update=True,
    )
    if run is None:
        raise LookupError("run_not_found")
    if str(run.get("status") or "") != "queued":
        return submission_resolution_projection(row)

    await terminalize_run(connection, run=run, error=error)
    await finalize_submission(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        state="enqueue_failed",
        rejection_code="queue_enqueue_failed",
    )
    row["state"] = "enqueue_failed"
    row["rejection_code"] = "queue_enqueue_failed"
    return submission_resolution_projection(row)

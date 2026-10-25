"""Resolve accepted Chat submissions after queue transport returns a receipt."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.auth import AuthPrincipal
from app.models import ChatStreamResponse, ChatSubmissionResponse
from app.conversations.application.submission_resolution import (
    submission_resolution_projection,
)
from app.conversations.transport.submission_delivery import (
    persist_chat_queue_success,
)


async def reload_chat_submission_resolution(
    *,
    transaction: Callable[..., Any],
    principal: AuthPrincipal,
    submission_id: str,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
) -> ChatSubmissionResponse:
    async with transaction() as connection:
        submission = await get_submission(
            connection,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            for_update=True,
        )
    if submission is None:
        raise LookupError("chat_submission_not_found")
    return ChatSubmissionResponse(**submission_resolution_projection(dict(submission)))


async def persist_chat_submission_delivery_receipt(
    *,
    transaction: Callable[..., Any],
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    receipt: Any,
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]],
    append_event: Callable[..., Awaitable[Any]],
    finalize_submission: Callable[..., Awaitable[Any]],
) -> ChatSubmissionResponse:
    async with transaction() as connection:
        submission = await get_submission(
            connection,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            for_update=True,
        )
        if submission is None:
            raise LookupError("chat_submission_not_found")
        row = dict(submission)
        if str(row.get("run_id") or "") != run_id:
            raise ValueError("chat_submission_run_mismatch")
        state = str(row.get("state") or "")
        if state == "queued":
            if str(row.get("queue_message_id") or "") != str(receipt.message_id):
                raise ValueError("chat_submission_receipt_mismatch")
        elif state == "accepted_pending_enqueue":
            raw_outcome = row.get("outcome_json")
            if not isinstance(raw_outcome, dict) or not raw_outcome:
                raise ValueError("chat_delivery_submission_outcome_missing")
            queued_outcome = ChatStreamResponse.model_validate(raw_outcome).model_copy(
                update={
                    "status": "queued",
                    "queue_position": int(receipt.queue_position) or None,
                    "submission_id": submission_id,
                }
            )
            await persist_chat_queue_success(
                connection,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                queue_admission=receipt,
                outcome=queued_outcome,
                submission_id=submission_id,
                user_id=principal.user_id,
                append_event=append_event,
                finalize_submission=finalize_submission,
            )
            row["state"] = "queued"
            row["outcome_json"] = queued_outcome.model_dump(mode="json")
            row["queue_message_id"] = str(receipt.message_id)
        return ChatSubmissionResponse(**submission_resolution_projection(row))

"""HTTP adapters for accepted Chat submission delivery operations."""

from typing import Any

from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.conversations.api import (
    persist_chat_queue_success as persist_accepted_chat_queue_success,
    record_chat_submission_delivery_receipt as record_accepted_submission_delivery_receipt,
    reload_chat_submission_resolution as reload_accepted_chat_submission_resolution,
    settle_chat_submission_enqueue_rejection as settle_accepted_chat_submission_enqueue_rejection,
)
from app.models import ChatStreamResponse, ChatSubmissionResponse
from app.persistence import chat_submissions as persistence_chat_submissions
from app.queue import QueueAdmissionMetadata
from app.run_admission_terminalization import terminalize_enqueue_failure
from app.runs.infrastructure import creation_postgres as runs_creation
from app.streaming.api import WorkerV4Capabilities
from app.streaming.infrastructure import run_events_postgres as streaming_run_events
from app.control_plane_contracts import standard_trace_id


async def persist_chat_queue_success(
    conn,
    *,
    principal: AuthPrincipal,
    run_id: str,
    queue_admission: QueueAdmissionMetadata,
    outcome: ChatStreamResponse,
    submission_id: str | None,
) -> None:
    """Map the HTTP response before delegating receipt persistence to Conversations."""

    await persist_accepted_chat_queue_success(
        conn,
        tenant_id=principal.tenant_id,
        run_id=run_id,
        queue_admission=queue_admission,
        outcome=outcome.model_dump(mode="json") if outcome is not None else None,
        submission_id=submission_id,
        user_id=principal.user_id if submission_id is not None else None,
        append_event=streaming_run_events.append_event,
        finalize_submission=persistence_chat_submissions.finalize_chat_submission,
    )


async def record_chat_submission_delivery_receipt(
    conn,
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    receipt: Any,
) -> ChatSubmissionResponse:
    try:
        projection = await record_accepted_submission_delivery_receipt(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            run_id=run_id,
            receipt=receipt,
            get_submission=persistence_chat_submissions.get_chat_submission,
            append_event=streaming_run_events.append_event,
            finalize_submission=persistence_chat_submissions.finalize_chat_submission,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChatSubmissionResponse(**projection)


async def reload_chat_submission_resolution(
    *,
    principal: AuthPrincipal,
    submission_id: str,
    transaction_factory,
) -> ChatSubmissionResponse:
    try:
        projection = await reload_accepted_chat_submission_resolution(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            transaction=transaction_factory,
            get_submission=persistence_chat_submissions.get_chat_submission,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatSubmissionResponse(**projection)


async def persist_chat_submission_delivery_receipt(
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    receipt: Any,
    transaction_factory,
) -> ChatSubmissionResponse:
    async with transaction_factory() as conn:
        return await record_chat_submission_delivery_receipt(
            conn,
            principal=principal,
            submission_id=submission_id,
            run_id=run_id,
            receipt=receipt,
        )


async def settle_chat_submission_enqueue_rejection(
    conn,
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    error: BaseException,
    definitive_rejection: bool,
    v4_capabilities: WorkerV4Capabilities,
    run_lifecycle: Any,
) -> ChatSubmissionResponse:
    async def terminalize(connection, *, run, error):
        await terminalize_enqueue_failure(
            v4_capabilities,
            connection,
            tenant_id=principal.tenant_id,
            lifecycle=run_lifecycle,
            user_id=principal.user_id,
            run_id=run_id,
            trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
            diagnostic_error=error,
        )

    projection = await settle_accepted_chat_submission_enqueue_rejection(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        submission_id=submission_id,
        run_id=run_id,
        error=error,
        definitive_rejection=definitive_rejection,
        get_submission=persistence_chat_submissions.get_chat_submission,
        get_run=runs_creation.get_authorized_run,
        terminalize_run=terminalize,
        finalize_submission=persistence_chat_submissions.finalize_chat_submission,
    )
    return ChatSubmissionResponse(**projection)

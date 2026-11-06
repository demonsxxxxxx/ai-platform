"""Compose Chat delivery transport ports with the existing persistence owners."""

from typing import Any

from app.auth import AuthPrincipal
from app.control_plane_contracts import standard_trace_id
from app.conversations.transport.submission_delivery import (
    ChatSubmissionDeliveryPorts,
    persist_chat_queue_success as persist_chat_queue_success_transport,
    persist_chat_submission_delivery_receipt as persist_chat_submission_delivery_receipt_transport,
    record_chat_submission_delivery_receipt as record_chat_submission_delivery_receipt_transport,
    reload_chat_submission_resolution as reload_chat_submission_resolution_transport,
    settle_chat_submission_enqueue_rejection as settle_chat_submission_enqueue_rejection_transport,
)
from app.models import ChatStreamResponse, ChatSubmissionResponse
from app.persistence import chat_submissions as persistence_chat_submissions
from app.runs.infrastructure import creation_postgres as runs_creation
from app.run_admission_terminalization import terminalize_enqueue_failure
from app.streaming.api import WorkerV4Capabilities
from app.streaming.infrastructure import run_events_postgres as streaming_run_events


def _delivery_ports(
    principal: AuthPrincipal,
    *,
    run_id: str | None = None,
    v4_capabilities: WorkerV4Capabilities | None = None,
    run_lifecycle: Any | None = None,
) -> ChatSubmissionDeliveryPorts:
    async def terminalize(connection, *, run, error):
        if run_id is None or v4_capabilities is None or run_lifecycle is None:
            raise RuntimeError("chat_submission_terminalization_unavailable")
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

    return ChatSubmissionDeliveryPorts(
        get_submission=persistence_chat_submissions.get_chat_submission,
        finalize_submission=persistence_chat_submissions.finalize_chat_submission,
        append_event=streaming_run_events.append_event,
        get_run=runs_creation.get_authorized_run,
        terminalize_run=terminalize,
    )


async def persist_chat_queue_success(
    connection,
    *,
    principal: AuthPrincipal,
    run_id: str,
    queue_admission: Any,
    outcome: ChatStreamResponse | None,
    submission_id: str | None,
) -> None:
    await persist_chat_queue_success_transport(
        connection,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id if submission_id is not None else None,
        run_id=run_id,
        queue_admission=queue_admission,
        outcome=outcome.model_dump(mode="json") if outcome is not None else None,
        submission_id=submission_id,
        ports=_delivery_ports(principal),
    )


async def record_chat_submission_delivery_receipt(
    connection,
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    receipt: Any,
) -> ChatSubmissionResponse:
    projection = await record_chat_submission_delivery_receipt_transport(
        connection,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        submission_id=submission_id,
        run_id=run_id,
        receipt=receipt,
        ports=_delivery_ports(principal),
    )
    return ChatSubmissionResponse(**projection)


async def reload_chat_submission_resolution(
    *,
    principal: AuthPrincipal,
    submission_id: str,
    transaction_factory,
) -> ChatSubmissionResponse:
    projection = await reload_chat_submission_resolution_transport(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        submission_id=submission_id,
        transaction=transaction_factory,
        ports=_delivery_ports(principal),
    )
    return ChatSubmissionResponse(**projection)


async def persist_chat_submission_delivery_receipt(
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    receipt: Any,
    transaction_factory,
) -> ChatSubmissionResponse:
    projection = await persist_chat_submission_delivery_receipt_transport(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        submission_id=submission_id,
        run_id=run_id,
        receipt=receipt,
        transaction=transaction_factory,
        ports=_delivery_ports(principal),
    )
    return ChatSubmissionResponse(**projection)


async def settle_chat_submission_enqueue_rejection(
    connection,
    *,
    principal: AuthPrincipal,
    submission_id: str,
    run_id: str,
    error: BaseException,
    definitive_rejection: bool,
    v4_capabilities: WorkerV4Capabilities,
    run_lifecycle: Any,
) -> ChatSubmissionResponse:
    projection = await settle_chat_submission_enqueue_rejection_transport(
        connection,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        submission_id=submission_id,
        run_id=run_id,
        error=error,
        definitive_rejection=definitive_rejection,
        ports=_delivery_ports(
            principal,
            run_id=run_id,
            v4_capabilities=v4_capabilities,
            run_lifecycle=run_lifecycle,
        ),
    )
    return ChatSubmissionResponse(**projection)

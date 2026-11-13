"""Translate accepted Chat submission delivery operations at the HTTP boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.conversations.application.accepted_submission_delivery import (
    record_chat_submission_delivery_receipt as apply_chat_submission_delivery_receipt,
    reload_chat_submission_resolution as load_chat_submission_resolution,
    settle_chat_submission_enqueue_rejection as apply_chat_submission_enqueue_rejection,
)
from app.conversations.application.queue_delivery_receipts import (
    persist_chat_queue_success as persist_accepted_chat_queue_success,
)


@dataclass(frozen=True)
class ChatSubmissionDeliveryPorts:
    get_submission: Callable[..., Awaitable[Mapping[str, Any] | None]]
    finalize_submission: Callable[..., Awaitable[Any]]
    append_event: Callable[..., Awaitable[Any]]
    get_run: Callable[..., Awaitable[Mapping[str, Any] | None]]
    terminalize_run: Callable[..., Awaitable[Any]]


async def persist_chat_queue_success(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str | None,
    run_id: str,
    queue_admission: Any,
    outcome: Mapping[str, Any] | None,
    submission_id: str | None,
    ports: ChatSubmissionDeliveryPorts,
) -> None:
    await persist_accepted_chat_queue_success(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        queue_admission=queue_admission,
        outcome=outcome,
        submission_id=submission_id,
        append_event=ports.append_event,
        finalize_submission=ports.finalize_submission,
    )


async def record_chat_submission_delivery_receipt(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    receipt: Any,
    ports: ChatSubmissionDeliveryPorts,
) -> dict[str, Any]:
    try:
        return await apply_chat_submission_delivery_receipt(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            run_id=run_id,
            receipt=receipt,
            get_submission=ports.get_submission,
            append_event=ports.append_event,
            finalize_submission=ports.finalize_submission,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def reload_chat_submission_resolution(
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    transaction: Callable[..., Any],
    ports: ChatSubmissionDeliveryPorts,
) -> dict[str, Any]:
    try:
        return await load_chat_submission_resolution(
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            transaction=transaction,
            get_submission=ports.get_submission,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def persist_chat_submission_delivery_receipt(
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    receipt: Any,
    transaction: Callable[..., Any],
    ports: ChatSubmissionDeliveryPorts,
) -> dict[str, Any]:
    async with transaction() as connection:
        return await record_chat_submission_delivery_receipt(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            submission_id=submission_id,
            run_id=run_id,
            receipt=receipt,
            ports=ports,
        )


async def settle_chat_submission_enqueue_rejection(
    connection: Any,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    error: BaseException,
    definitive_rejection: bool,
    ports: ChatSubmissionDeliveryPorts,
) -> dict[str, Any]:
    return await apply_chat_submission_enqueue_rejection(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        run_id=run_id,
        error=error,
        definitive_rejection=definitive_rejection,
        get_submission=ports.get_submission,
        get_run=ports.get_run,
        terminalize_run=ports.terminalize_run,
        finalize_submission=ports.finalize_submission,
    )

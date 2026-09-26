"""Run events persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app import run_event_repository as _run_event_repository
from app.platform.postgres.errors import RepositoryConflictError
from psycopg import AsyncConnection
from typing import Any


def _repository_ledger_conflict(error: _run_event_repository.RunEventLedgerConflictError) -> RepositoryConflictError:
    return RepositoryConflictError(str(error))


async def append_event(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    severity: str | None = None,
    visible_to_user: bool | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
    input_token_count: int = 0,
    output_token_count: int = 0,
    total_token_count: int = 0,
    estimated_cost_minor: int = 0, return_record: bool = False,
) -> str | dict[str, Any]:
    try:
        return await _run_event_repository.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload,
            trace_id=trace_id,
            severity=severity,
            visible_to_user=visible_to_user,
            error_code=error_code,
            latency_ms=latency_ms,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
            total_token_count=total_token_count,
            estimated_cost_minor=estimated_cost_minor, return_record=return_record,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def append_event_batch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return await _run_event_repository.append_event_batch(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
            events=events,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def acquire_run_event_terminal_drain_fence(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
) -> dict[str, bool]:
    try:
        return await _run_event_repository.acquire_terminal_drain_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def list_run_events(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return await _run_event_repository.list_run_events(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )

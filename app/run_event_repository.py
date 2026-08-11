"""Repository adapter for durable run-event ledger compatibility operations."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.persistence_limits import (
    RUN_EVENT_MESSAGE_MAX_BYTES,
    RUN_EVENT_PAYLOAD_MAX_BYTES,
    ensure_json_size,
    ensure_text_size,
)
from app.streaming import postgres as _ledger
from app.streaming.authority import RunCursor


RunEventLedgerConflictError = _ledger.RunEventLedgerConflictError


def _ledger_event_from_values(
    *,
    event_type: object,
    stage: object,
    message: object = "",
    payload: object = None,
    trace_id: object = None,
    severity: object = None,
    visible_to_user: object = None,
    error_code: object = None,
    latency_ms: object = None,
    input_token_count: object = 0,
    output_token_count: object = 0,
    total_token_count: object = 0,
    estimated_cost_minor: object = 0,
) -> _ledger.LedgerEvent:
    required = {"type": event_type, "stage": stage}
    for field, value in required.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"run_event_{field}_invalid")
    if not isinstance(message, str):
        raise ValueError("run_event_message_invalid")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("run_event_payload_invalid")
    ensure_text_size(message, max_bytes=RUN_EVENT_MESSAGE_MAX_BYTES, code="run_event_message_too_large")
    ensure_json_size(payload, max_bytes=RUN_EVENT_PAYLOAD_MAX_BYTES, code="run_event_payload_too_large")
    optional_strings = {
        "trace_id": trace_id,
        "severity": severity,
        "error_code": error_code,
    }
    for field, value in optional_strings.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"run_event_{field}_invalid")
    if visible_to_user is not None and not isinstance(visible_to_user, bool):
        raise ValueError("run_event_visible_to_user_invalid")
    integer_values = {
        "latency_ms": latency_ms,
        "input_token_count": input_token_count,
        "output_token_count": output_token_count,
        "total_token_count": total_token_count,
        "estimated_cost_minor": estimated_cost_minor,
    }
    for field, value in integer_values.items():
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError(f"run_event_{field}_invalid")
    return _ledger.LedgerEvent(
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
        estimated_cost_minor=estimated_cost_minor,
    )


async def _append_event_receipt(
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
    estimated_cost_minor: int = 0,
) -> tuple[_ledger.LedgerEvent, _ledger.EventReceipt]:
    event = _ledger_event_from_values(
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
        estimated_cost_minor=estimated_cost_minor,
    )
    receipt = await _ledger.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event=event,
    )
    return event, receipt


async def append_event(
    conn: AsyncConnection,
    **kwargs: Any,
) -> str | dict[str, Any]:
    if kwargs.pop("return_record", False):
        return await append_event_record(conn, **kwargs)
    _, receipt = await _append_event_receipt(conn, **kwargs)
    return receipt.event_id


async def append_event_record(
    conn: AsyncConnection,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return the exact durable row facts needed by a post-commit projector."""

    event, receipt = await _append_event_receipt(conn, **kwargs)
    return {
        "id": receipt.event_id,
        "run_id": kwargs["run_id"],
        "sequence": receipt.cursor.sequence,
        "event_type": event.event_type,
        "stage": event.stage,
        "message": event.message,
        "severity": event.severity or event.payload.get("severity") or "info",
        "visible_to_user": (
            event.visible_to_user
            if event.visible_to_user is not None
            else bool(event.payload.get("visible_to_user", True))
        ),
        "payload_json": dict(event.payload),
        "created_at": receipt.created_at,
    }


def _ledger_event_from_dict(event: object) -> _ledger.LedgerEvent:
    if not isinstance(event, dict):
        raise ValueError("run_event_batch_event_invalid")
    return _ledger_event_from_values(
        event_type=event.get("event_type"),
        stage=event.get("stage"),
        message=event.get("message", ""),
        payload=event.get("payload"),
        trace_id=event.get("trace_id"),
        severity=event.get("severity"),
        visible_to_user=event.get("visible_to_user"),
        error_code=event.get("error_code"),
        latency_ms=event.get("latency_ms"),
        input_token_count=event.get("input_token_count", 0),
        output_token_count=event.get("output_token_count", 0),
        total_token_count=event.get("total_token_count", 0),
        estimated_cost_minor=event.get("estimated_cost_minor", 0),
    )


async def append_event_batch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    receipt = await _ledger.append_batch(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
        events=tuple(_ledger_event_from_dict(event) for event in events),
    )
    return {
        "accepted": True,
        "duplicate": receipt.duplicate,
        "id": receipt.receipt_id,
        "event_ids_json": list(receipt.event_ids),
        "first_sequence": receipt.first_cursor.sequence if receipt.first_cursor else None,
        "through_sequence": receipt.through_cursor.sequence if receipt.through_cursor else None,
        "callback_received_at": receipt.callback_received_at,
    }


async def acquire_terminal_drain_fence(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
) -> dict[str, bool]:
    receipt = await _ledger.acquire_terminal_drain_fence(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
    )
    return {"accepted": True, "duplicate": receipt.duplicate}


async def list_run_events(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cursor = RunCursor(run_id=run_id, sequence=0 if after_sequence is None else int(after_sequence))
    rows = await _ledger.read_event_rows(conn, tenant_id=tenant_id, cursor=cursor, limit=limit)
    return [dict(row) for row in rows]


async def list_run_capability_evidence(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Lock private capability evidence for one exact runtime attempt."""

    cursor = await conn.execute(
        """
        select sequence, payload_json
        from run_events
        where tenant_id = %s
          and run_id = %s
          and event_type = 'capability_invocation_evidence'
          and stage = 'capability_evidence'
          and visible_to_user = false
          and payload_json ->> 'attempt_id' = %s
        order by sequence asc
        limit %s
        for update
        """,
        (tenant_id, run_id, attempt_id, limit),
    )
    return list(await cursor.fetchall())


async def list_current_sandbox_runtime_leases_for_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> list[dict[str, Any]]:
    """Lock exact-attempt unexpired active runtime leases for one authoritative run."""

    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
          and lease_payload_json ->> 'attempt_id' = %s
          and (attempt_id is null or attempt_id = lease_payload_json ->> 'attempt_id')
          and status = 'active'
          and expires_at is not null
          and expires_at > clock_timestamp()
        order by created_at asc
        for update
        """,
        (tenant_id, run_id, attempt_id),
    )
    return list(await cursor.fetchall())


async def list_terminal_sandbox_runtime_leases_for_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    release_reason: str,
) -> list[dict[str, Any]]:
    """Lock released runtime leases for one exact terminal attempt and reason."""

    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
          and lease_payload_json ->> 'attempt_id' = %s
          and (attempt_id is null or attempt_id = lease_payload_json ->> 'attempt_id')
          and status = 'released'
          and release_reason = %s
        order by released_at desc, id asc
        for update
        """,
        (tenant_id, run_id, attempt_id, release_reason),
    )
    return list(await cursor.fetchall())

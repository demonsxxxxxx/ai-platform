"""PostgreSQL adapter for durable run-event ledger operations.

The caller owns the transaction. This adapter never commits or rolls back: a
batch receipt, its events, and its cursor allocation therefore share the
caller's PostgreSQL transaction and either persist together or are rolled back
together.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.control_plane_contracts import EVENT_ENVELOPE_SCHEMA_VERSION, standard_error_code, standard_trace_id
from app.streaming.authority import RunCursor


class RunEventLedgerConflictError(ValueError):
    """Signal an unavailable durable receipt, cursor, or terminal fence."""


class RunEventRowCursor(Protocol):
    async def fetchone(self) -> Mapping[str, object] | None: ...

    async def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class RunEventSqlConnection(Protocol):
    """The transaction-owned SQL protocol required by the PostgreSQL adapter."""

    async def execute(self, statement: str, params: tuple[object, ...]) -> RunEventRowCursor: ...


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_type: str
    stage: str
    message: str = ""
    payload: Mapping[str, object] = field(default_factory=dict)
    trace_id: str | None = None
    severity: str | None = None
    visible_to_user: bool | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    input_token_count: int = 0
    output_token_count: int = 0
    total_token_count: int = 0
    estimated_cost_minor: int = 0


@dataclass(frozen=True, slots=True)
class EventReceipt:
    event_id: str
    cursor: RunCursor


@dataclass(frozen=True, slots=True)
class BatchReceipt:
    receipt_id: str
    event_ids: tuple[str, ...]
    first_cursor: RunCursor | None
    through_cursor: RunCursor | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class TerminalDrainReceipt:
    duplicate: bool


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _require_nonempty(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"run_event_{field_name}_invalid")
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _severity(event: LedgerEvent) -> str:
    candidate = event.severity or event.payload.get("severity") or "info"
    return str(candidate) if str(candidate) in {"info", "warning", "error"} else "info"


def _visible(event: LedgerEvent) -> bool:
    if event.visible_to_user is not None:
        return bool(event.visible_to_user)
    return bool(event.payload.get("visible_to_user", True))


def _error_code(event: LedgerEvent) -> str | None:
    candidate = event.error_code or event.payload.get("error_code")
    return standard_error_code(str(candidate)) if candidate else None


def _event_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _batch_receipt(row: Mapping[str, object], *, run_id: str, duplicate: bool) -> BatchReceipt:
    first_sequence = row.get("first_sequence")
    through_sequence = row.get("through_sequence")
    first_cursor = RunCursor(run_id, int(first_sequence)) if first_sequence is not None else None
    through_cursor = RunCursor(run_id, int(through_sequence)) if through_sequence is not None else None
    receipt_id = row.get("id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise RunEventLedgerConflictError("run_event_batch_receipt_unavailable")
    return BatchReceipt(
        receipt_id=receipt_id,
        event_ids=_event_ids(row.get("event_ids_json")),
        first_cursor=first_cursor,
        through_cursor=through_cursor,
        duplicate=duplicate,
    )


async def _allocate_cursor(
    conn: RunEventSqlConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> RunCursor:
    """Atomically allocate a unique, monotonic cursor for one run."""

    await conn.execute(
        """
        insert into run_event_cursors(tenant_id, run_id, next_sequence)
        select %s, %s, coalesce(max(sequence), 0) + 1
        from run_events
        where tenant_id = %s and run_id = %s
        on conflict (tenant_id, run_id) do nothing
        """,
        (tenant_id, run_id, tenant_id, run_id),
    )
    result = await conn.execute(
        """
        update run_event_cursors
        set next_sequence = next_sequence + 1, updated_at = now()
        where tenant_id = %s and run_id = %s
        returning next_sequence - 1 as sequence
        """,
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    if row is None:
        raise RunEventLedgerConflictError("run_event_cursor_unavailable")
    try:
        return RunCursor(run_id=run_id, sequence=int(row["sequence"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RunEventLedgerConflictError("run_event_cursor_unavailable") from exc


async def append_event(
    conn: RunEventSqlConnection,
    *,
    tenant_id: str,
    run_id: str,
    event: LedgerEvent,
) -> EventReceipt:
    """Append one event using a transaction-owned connection."""

    _require_nonempty(tenant_id, field_name="tenant_id")
    _require_nonempty(run_id, field_name="run_id")
    _require_nonempty(event.event_type, field_name="type")
    _require_nonempty(event.stage, field_name="stage")
    event_id = _new_id("evt")
    cursor = await _allocate_cursor(conn, tenant_id=tenant_id, run_id=run_id)
    await conn.execute(
        """
        insert into run_events(
          id, tenant_id, run_id, trace_id, schema_version, sequence, event_type, stage, message,
          severity, visible_to_user, error_code, latency_ms,
          input_token_count, output_token_count, total_token_count, estimated_cost_minor,
          payload_json
        )
        values (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        """,
        (
            event_id,
            tenant_id,
            run_id,
            event.trace_id or standard_trace_id(run_id),
            EVENT_ENVELOPE_SCHEMA_VERSION,
            cursor.sequence,
            event.event_type,
            event.stage,
            event.message,
            _severity(event),
            _visible(event),
            _error_code(event),
            event.latency_ms,
            int(event.input_token_count or 0),
            int(event.output_token_count or 0),
            int(event.total_token_count or 0),
            int(event.estimated_cost_minor or 0),
            _json(dict(event.payload)),
        ),
    )
    return EventReceipt(event_id=event_id, cursor=cursor)


async def append_batch(
    conn: RunEventSqlConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    events: Sequence[LedgerEvent],
) -> BatchReceipt:
    """Persist one exact callback batch or replay its existing durable receipt."""

    _require_nonempty(tenant_id, field_name="tenant_id")
    _require_nonempty(run_id, field_name="run_id")
    _require_nonempty(attempt_id, field_name="attempt_id")
    _require_nonempty(batch_id, field_name="batch_id")
    receipt_id = _new_id("evb")
    created = await conn.execute(
        """
        insert into run_event_batches(id, tenant_id, run_id, attempt_id, batch_id)
        values (%s, %s, %s, %s, %s)
        on conflict (tenant_id, run_id, attempt_id, batch_id) do nothing
        returning id
        """,
        (receipt_id, tenant_id, run_id, attempt_id, batch_id),
    )
    if await created.fetchone() is None:
        existing = await conn.execute(
            """
            select id, event_ids_json, first_sequence, through_sequence
            from run_event_batches
            where tenant_id = %s and run_id = %s and attempt_id = %s and batch_id = %s
            """,
            (tenant_id, run_id, attempt_id, batch_id),
        )
        row = await existing.fetchone()
        if row is None:
            raise RunEventLedgerConflictError("run_event_batch_receipt_unavailable")
        return _batch_receipt(row, run_id=run_id, duplicate=True)

    receipts = [await append_event(conn, tenant_id=tenant_id, run_id=run_id, event=event) for event in events]
    first_sequence = min((receipt.cursor.sequence for receipt in receipts), default=None)
    through_sequence = max((receipt.cursor.sequence for receipt in receipts), default=None)
    completed = await conn.execute(
        """
        update run_event_batches
        set event_ids_json = %s::jsonb, first_sequence = %s, through_sequence = %s,
            durable_committed_at = now()
        where id = %s
        returning id, event_ids_json, first_sequence, through_sequence
        """,
        (_json([receipt.event_id for receipt in receipts]), first_sequence, through_sequence, receipt_id),
    )
    row = await completed.fetchone()
    if row is None:
        raise RunEventLedgerConflictError("run_event_batch_receipt_unavailable")
    return _batch_receipt(row, run_id=run_id, duplicate=False)


async def acquire_terminal_drain_fence(
    conn: RunEventSqlConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
) -> TerminalDrainReceipt:
    """Reserve one replayable terminal drain for an exact run attempt and batch."""

    _require_nonempty(tenant_id, field_name="tenant_id")
    _require_nonempty(run_id, field_name="run_id")
    _require_nonempty(attempt_id, field_name="attempt_id")
    _require_nonempty(batch_id, field_name="batch_id")
    created = await conn.execute(
        """
        insert into run_event_terminal_drains(tenant_id, run_id, attempt_id, batch_id)
        values (%s, %s, %s, %s)
        on conflict (tenant_id, run_id, attempt_id) do nothing
        returning batch_id
        """,
        (tenant_id, run_id, attempt_id, batch_id),
    )
    if await created.fetchone() is not None:
        return TerminalDrainReceipt(duplicate=False)
    existing = await conn.execute(
        """
        select batch_id
        from run_event_terminal_drains
        where tenant_id = %s and run_id = %s and attempt_id = %s
        for update
        """,
        (tenant_id, run_id, attempt_id),
    )
    row = await existing.fetchone()
    if row is None:
        raise RunEventLedgerConflictError("terminal_drain_fence_unavailable")
    if row.get("batch_id") != batch_id:
        raise RunEventLedgerConflictError("terminal_drain_already_consumed")
    return TerminalDrainReceipt(duplicate=True)


async def read_event_rows(
    conn: RunEventSqlConnection,
    *,
    tenant_id: str,
    cursor: RunCursor,
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    """Read one incremental event page after a run-bound cursor."""

    _require_nonempty(tenant_id, field_name="tenant_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("run_event_page_limit_invalid")
    result = await conn.execute(
        """
        select id, trace_id, schema_version, sequence, event_type, stage, message, severity, visible_to_user,
               error_code, latency_ms, input_token_count, output_token_count, total_token_count,
               estimated_cost_minor, payload_json, created_at
        from run_events
        where tenant_id = %s and run_id = %s and sequence > %s
        order by sequence asc
        limit %s
        """,
        (tenant_id, cursor.run_id, cursor.sequence, limit),
    )
    return tuple(await result.fetchall())

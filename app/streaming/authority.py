"""Pure cursor, projection, and terminal-drain rules for durable run events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


_TERMINAL_TYPES = frozenset({"run_succeeded", "run_failed", "run_cancelled", "run_canceled"})
_CANONICAL_DELTA_PAYLOAD_KEYS = frozenset({"delta", "source", "visible_to_user", "severity"})
_CANONICAL_DELTA_SOURCE = "worker_answer_delta_v1"


@dataclass(frozen=True, slots=True)
class RunCursor:
    """A monotonic cursor whose identity is bound to exactly one run."""

    run_id: str
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_cursor_run_id_invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("run_cursor_sequence_invalid")

    @property
    def event_id(self) -> str:
        return f"{self.run_id}:{self.sequence}"


@dataclass(frozen=True, slots=True)
class PublicDelta:
    """The only durable row data permitted across the public streaming seam."""

    event_id: str
    cursor: RunCursor
    delta: str


@dataclass(frozen=True, slots=True)
class DurableEvent:
    """One ordered persisted row retained for trusted downstream adapters."""

    cursor: RunCursor
    row: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TerminalControl:
    """A control frame emitted after all public deltas through ``drain_through``."""

    event_id: str
    cursor: RunCursor
    event_type: str
    drain_through: RunCursor


@dataclass(frozen=True, slots=True)
class EventPage:
    """A public projection page with a durable cursor frontier."""

    cursor: RunCursor
    durable_rows: tuple[DurableEvent, ...]
    events: tuple[PublicDelta, ...]
    through_cursor: RunCursor
    terminal: TerminalControl | None


def parse_last_event_id(value: str | None, *, run_id: str) -> RunCursor | None:
    """Parse only the exact durable SSE identity ``<run_id>:<sequence>``."""

    if not isinstance(value, str) or not isinstance(run_id, str) or not run_id:
        return None
    prefix, separator, raw_sequence = value.rpartition(":")
    if (
        separator != ":"
        or prefix != run_id
        or not raw_sequence.isdecimal()
        or (len(raw_sequence) > 1 and raw_sequence.startswith("0"))
    ):
        return None
    try:
        return RunCursor(run_id=run_id, sequence=int(raw_sequence))
    except ValueError:
        return None


def _row_sequence(row: Mapping[str, object]) -> int | None:
    value = row.get("sequence")
    if isinstance(value, bool):
        return None
    try:
        sequence = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return sequence if sequence >= 0 else None


def _public_delta(row: Mapping[str, object], cursor: RunCursor) -> PublicDelta | None:
    event_id = row.get("id")
    payload = row.get("payload_json")
    if (
        not isinstance(event_id, str)
        or not event_id
        or row.get("event_type") != "assistant_delta"
        or row.get("stage") != "answer"
        or row.get("message") != ""
        or row.get("severity") != "info"
        or row.get("visible_to_user") is not True
        or not isinstance(payload, Mapping)
        or set(payload) != _CANONICAL_DELTA_PAYLOAD_KEYS
        or not isinstance(payload.get("delta"), str)
        or not payload["delta"]
        or payload.get("source") != _CANONICAL_DELTA_SOURCE
        or payload.get("visible_to_user") is not True
        or payload.get("severity") != "info"
    ):
        return None
    return PublicDelta(event_id=event_id, cursor=cursor, delta=payload["delta"])


def event_page(*, cursor: RunCursor, rows: Iterable[Mapping[str, object]]) -> EventPage:
    """Project one durable page while retaining hidden cursor gaps and drain order.

    Duplicate or malformed persisted rows never produce a public event. Their
    valid sequence still advances the returned cursor, so reconnects do not
    repeatedly inspect them. A terminal control is returned separately and is
    emitted only after all returned deltas through ``drain_through``.
    """

    ordered_rows: list[tuple[int, int, Mapping[str, object]]] = []
    for position, row in enumerate(rows):
        sequence = _row_sequence(row)
        if sequence is not None and sequence > cursor.sequence:
            ordered_rows.append((sequence, position, row))

    durable_rows: list[DurableEvent] = []
    events: list[PublicDelta] = []
    terminal_data: tuple[str, int, str] | None = None
    through_sequence = cursor.sequence
    seen_sequences: set[int] = set()
    for sequence, _, row in sorted(ordered_rows):
        through_sequence = max(through_sequence, sequence)
        if sequence in seen_sequences:
            continue
        seen_sequences.add(sequence)
        row_cursor = RunCursor(run_id=cursor.run_id, sequence=sequence)
        durable_rows.append(DurableEvent(cursor=row_cursor, row=row))
        event_type = row.get("event_type")
        event_id = row.get("id")
        if terminal_data is None and isinstance(event_type, str) and event_type in _TERMINAL_TYPES:
            if isinstance(event_id, str) and event_id:
                terminal_data = (event_id, sequence, event_type)
            continue
        public_delta = _public_delta(row, row_cursor)
        if public_delta is not None:
            events.append(public_delta)

    through_cursor = RunCursor(run_id=cursor.run_id, sequence=through_sequence)
    terminal = None
    if terminal_data is not None:
        event_id, sequence, event_type = terminal_data
        terminal = TerminalControl(
            event_id=event_id,
            cursor=RunCursor(run_id=cursor.run_id, sequence=sequence),
            event_type=event_type,
            drain_through=through_cursor,
        )
    return EventPage(
        cursor=cursor,
        durable_rows=tuple(durable_rows),
        events=tuple(events),
        through_cursor=through_cursor,
        terminal=terminal,
    )

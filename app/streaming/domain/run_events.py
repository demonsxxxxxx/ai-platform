"""Identity and cursor rules for the durable run-event ledger."""

from __future__ import annotations

from dataclasses import dataclass


EVENT_ENVELOPE_SCHEMA_VERSION = "ai-platform.event-envelope.v1"


def standard_error_code(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or "unknown_error"


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

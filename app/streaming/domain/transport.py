"""Shared Streaming transport values and canonical encoding."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from app.streaming.domain.live import (
    REDIS_ID_PATTERN,
    RUN_ID_PATTERN,
    StreamContractError,
)

STREAM_GAP_SCHEMA = "ai-platform.stream-gap.v3"


@dataclass(frozen=True, slots=True)
class StreamCursor:
    run_id: str
    stream_incarnation: int
    redis_id: str

    def __post_init__(self) -> None:
        if (
            not RUN_ID_PATTERN.fullmatch(self.run_id)
            or isinstance(self.stream_incarnation, bool)
            or self.stream_incarnation < 1
            or not REDIS_ID_PATTERN.fullmatch(self.redis_id)
        ):
            raise StreamContractError("stream_cursor_invalid")

    @property
    def event_id(self) -> str:
        return f"{self.run_id}:{self.stream_incarnation}:{self.redis_id}"

    @classmethod
    def parse(cls, value: str, *, run_id: str) -> StreamCursor:
        if not isinstance(value, str) or not value or value != value.strip():
            raise StreamContractError("stream_cursor_invalid")
        prefix, separator, redis_id = value.rpartition(":")
        parsed_run, separator2, incarnation = prefix.rpartition(":")
        if separator != ":" or separator2 != ":" or parsed_run != run_id:
            raise StreamContractError("stream_cursor_foreign_run")
        if not incarnation.isdecimal() or incarnation.startswith("0"):
            raise StreamContractError("stream_cursor_incarnation_invalid")
        return cls(run_id, int(incarnation), redis_id)


@dataclass(frozen=True, slots=True)
class StreamGap:
    reason: Literal[
        "retained_history_unavailable",
        "stream_missing",
        "stream_continuity_unproven",
        "stream_incarnation_mismatch",
    ]
    requested_event_id: str | None
    requested_stream_incarnation: int | None
    current_stream_incarnation: int
    earliest_available_event_id: str | None = None
    latest_available_event_id: str | None = None

    def as_public_dict(self) -> dict[str, object]:
        result = {
            "schema": STREAM_GAP_SCHEMA,
            "reason": self.reason,
            "current_stream_incarnation": self.current_stream_incarnation,
            "recovery": "reload_durable_state",
        }
        result.update(
            {
                key: value
                for key, value in asdict(self).items()
                if key != "reason"
                and key != "current_stream_incarnation"
                and value is not None
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    after_redis_id: str | None
    gap: StreamGap | None


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise StreamContractError("stream_json_not_canonicalizable") from exc

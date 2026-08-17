"""Pure SSE wire/value contracts and deterministic projections."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from app.control_plane_contracts import EVENT_ENVELOPE_SCHEMA_VERSION
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    public_execution_event_from_row,
    validate_public_agent_progress_payload,
)
from app.run_projection import CHAT_PUBLIC_PROJECTION_VERSION
from app.streaming.api import (
    REDIS_ID_PATTERN as _REDIS_ID_RE,
    RUN_ID_PATTERN as _RUN_ID_RE,
    TENANT_SCOPE_PATTERN as _TENANT_SCOPE_RE,
    StreamContractError,
)
from app.streaming.events import (
    INTERNAL_STREAM_EVENT_SCHEMA,
    PUBLIC_STREAM_EVENT_TYPES,
    STREAM_DESIGN_ID as GENERATED_STREAM_DESIGN_ID,
    STREAM_PROJECTION_VERSION as GENERATED_STREAM_PROJECTION_VERSION,
)

STREAM_EVENT_SCHEMA = INTERNAL_STREAM_EVENT_SCHEMA
STREAM_GAP_SCHEMA = "ai-platform.stream-gap.v3"
STREAM_PROJECTION_VERSION = GENERATED_STREAM_PROJECTION_VERSION
STREAM_DESIGN_ID = GENERATED_STREAM_DESIGN_ID
PUBLIC_EVENT_TYPES = PUBLIC_STREAM_EVENT_TYPES
_FORBIDDEN = frozenset(
    {
        "args",
        "arguments",
        "authorization",
        "callback_token",
        "callback_token_id",
        "command",
        "credentials",
        "cwd",
        "hidden_reasoning",
        "local_path",
        "output",
        "prompt",
        "raw_payload",
        "reasoning",
        "stderr",
        "stdout",
        "storage_key",
        "tool_arguments",
        "tool_input",
        "tool_output",
        "tool_result",
        "trace_id",
    }
)
_WRAPPED_PUBLIC_EVENTS = {
    "semantic_stage": frozenset({"run_event"}),
    "semantic_progress": frozenset(
        {
            "execution_step",
            "execution_progress",
            "execution_step_completed",
            "execution_step_failed",
        }
    ),
}


class StreamProjectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StreamCursor:
    run_id: str
    stream_incarnation: int
    redis_id: str

    def __post_init__(self) -> None:
        if (
            not _RUN_ID_RE.fullmatch(self.run_id)
            or isinstance(self.stream_incarnation, bool)
            or self.stream_incarnation < 1
            or not _REDIS_ID_RE.fullmatch(self.redis_id)
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
class StreamEnvelope:
    event_id: str
    tenant_scope: str
    run_id: str
    attempt_id: str
    stream_incarnation: int
    event_type: str
    payload: Mapping[str, object]
    emitted_at: str
    schema: str = STREAM_EVENT_SCHEMA
    projection_version: str = STREAM_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str) and value and len(value) <= 256
                for value in (self.event_id, self.attempt_id)
            )
            or not _TENANT_SCOPE_RE.fullmatch(self.tenant_scope)
            or not _RUN_ID_RE.fullmatch(self.run_id)
            or isinstance(self.stream_incarnation, bool)
            or self.stream_incarnation < 1
            or self.schema != STREAM_EVENT_SCHEMA
            or self.projection_version != STREAM_PROJECTION_VERSION
            or not _is_rfc3339(self.emitted_at)
        ):
            raise StreamContractError("stream_envelope_invalid")
        validate_public_payload(self.event_type, self.payload)
        if (
            self.event_type == "terminal"
            and self.payload.get("event_id") != self.event_id
        ):
            raise StreamContractError("stream_terminal_event_id_mismatch")

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["payload"] = dict(self.payload)
        return value

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    @classmethod
    def from_json(cls, value: str | bytes) -> StreamEnvelope:
        try:
            raw = json.loads(value)
            if not isinstance(raw, dict) or not isinstance(raw.get("payload"), dict):
                raise TypeError
            return cls(**raw)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise StreamContractError("stream_envelope_json_invalid") from exc


@dataclass(frozen=True, slots=True)
class StreamEntry:
    cursor: StreamCursor
    envelope: StreamEnvelope


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


def tenant_scope(tenant_id: str, *, secret: str) -> str:
    if not tenant_id or not secret:
        raise StreamContractError("stream_tenant_scope_authority_missing")
    return hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[
        :32
    ]


def stable_event_id(
    *,
    tenant_scope_value: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    item_index: int,
    projection_version: str = STREAM_PROJECTION_VERSION,
) -> str:
    if (
        isinstance(item_index, bool)
        or not isinstance(item_index, int)
        or item_index < 0
    ):
        raise StreamContractError("stream_event_item_index_invalid")
    return f"sev_{hashlib.sha256(canonical_json_bytes(['ai-platform-stream-event-id-v3', tenant_scope_value, run_id, attempt_id, batch_id, item_index, projection_version])).hexdigest()}"


def _is_rfc3339(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _rfc3339_utc(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_envelope(
    *,
    event_id: str,
    tenant_scope_value: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    event_type: str,
    payload: Mapping[str, object],
    emitted_at: str | None = None,
) -> StreamEnvelope:
    return StreamEnvelope(
        event_id,
        tenant_scope_value,
        run_id,
        attempt_id,
        stream_incarnation,
        event_type,
        payload,
        emitted_at or _rfc3339_utc(datetime.now(timezone.utc)),
    )


def _validate_value(value: object, depth: int = 0) -> None:
    if depth > 6:
        raise StreamProjectionError("stream_payload_depth_exceeded")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value.encode()) > 8192:
            raise StreamProjectionError("stream_payload_string_too_large")
        return
    if isinstance(value, list) and len(value) <= 64:
        for item in value:
            _validate_value(item, depth + 1)
        return
    if isinstance(value, Mapping) and len(value) <= 64:
        for key, item in value.items():
            if not isinstance(key, str) or not key or key.strip().lower() in _FORBIDDEN:
                raise StreamProjectionError("stream_payload_forbidden_key")
            _validate_value(item, depth + 1)
        return
    raise StreamProjectionError("stream_payload_value_invalid")


def validate_public_payload(event_type: str, payload: Mapping[str, object]) -> None:
    if event_type not in PUBLIC_EVENT_TYPES:
        raise StreamProjectionError("stream_event_type_not_public")
    if not isinstance(payload, Mapping):
        raise StreamProjectionError("stream_payload_invalid")
    _validate_value(payload)
    if event_type == "stream_open" and dict(payload) != {"design_id": STREAM_DESIGN_ID}:
        raise StreamProjectionError("stream_open_payload_invalid")
    if event_type == "assistant_text_delta" and (
        set(payload) != {"delta"}
        or not isinstance(payload.get("delta"), str)
        or not payload["delta"]
    ):
        raise StreamProjectionError("stream_assistant_delta_payload_invalid")
    if event_type == "terminal" and (
        set(payload) != {"event_id", "hydrate_required", "status"}
        or not isinstance(payload.get("event_id"), str)
        or not payload["event_id"]
        or payload.get("hydrate_required") is not True
        or payload.get("status") not in {"succeeded", "failed", "cancelled"}
    ):
        raise StreamProjectionError("stream_terminal_payload_invalid")
    if event_type == "end" and (
        set(payload) != {"terminal_event_id"}
        or not isinstance(payload.get("terminal_event_id"), str)
        or not payload["terminal_event_id"]
    ):
        raise StreamProjectionError("stream_end_payload_invalid")
    if event_type in _WRAPPED_PUBLIC_EVENTS:
        target = payload.get("event")
        data = payload.get("data")
        if (
            set(payload) != {"event", "data"}
            or target not in _WRAPPED_PUBLIC_EVENTS[event_type]
            or not isinstance(data, Mapping)
        ):
            raise StreamProjectionError("stream_wrapped_public_payload_invalid")
    if len(canonical_json_bytes(dict(payload))) > 16384:
        raise StreamProjectionError("stream_payload_too_large")


def committed_public_stream_event(
    row: Mapping[str, object],
) -> tuple[str, dict[str, object]] | None:
    """Project only a committed PostgreSQL row through closed public schemas."""

    if row.get("visible_to_user") is not True:
        return None
    run_id = row.get("run_id")
    execution = public_execution_event_from_row(run_id, row)
    if execution is not None:
        return "semantic_progress", {
            "event": str(row["event_type"]),
            "data": execution,
        }
    if row.get("event_type") != PUBLIC_AGENT_PROGRESS_EVENT_TYPE:
        return None
    progress = validate_public_agent_progress_payload(row.get("payload_json"))
    event_id = row.get("id")
    sequence = row.get("sequence")
    created_at = row.get("created_at")
    if (
        progress is None
        or not isinstance(event_id, str)
        or not event_id
        or not isinstance(run_id, str)
        or not run_id
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(created_at, str)
        or not created_at
    ):
        return None
    lifecycle = progress["lifecycle"]
    return "semantic_stage", {
        "event": "run_event",
        "data": {
            "id": event_id,
            "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
            "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
            "event_id": event_id,
            "sequence": sequence,
            "run_id": run_id,
            "event_type": PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            "type": PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            "stage": progress["phase"],
            "message": progress["message"],
            "severity": "error" if lifecycle == "failed" else "info",
            "visible_to_user": True,
            "progress_kind": (
                "failed"
                if lifecycle == "failed"
                else "completed"
                if lifecycle == "completed"
                else "active"
            ),
            "wait_reason": None,
            "payload": progress,
            "created_at": created_at,
        },
    }

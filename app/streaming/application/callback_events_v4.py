"""Application-owned callback values for durable v4 Streaming events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.streaming.domain.public_events_v4 import (
    V4ProjectionError,
    _MESSAGE_EVENT_TYPES,
    _safe_ref,
    _validate_payload,
)


@dataclass(frozen=True, slots=True)
class V4CallbackItem:
    callback_index: int
    batch_index: int
    event_type: str
    payload: Mapping[str, object]
    message_id: str | None = None
    trace_ref: str | None = None
    causation_event_id: str | None = None
    source_event_id: str | None = None
    source_run_id: str | None = None


def callback_item_to_v4(
    item: Mapping[str, object],
    *,
    callback_index: int,
    batch_index: int,
    message_id: str | None = None,
) -> V4CallbackItem | None:
    """Convert one already-validated bridge mapping into a v4 callback item.

    The bridge is the trust boundary. This mapper accepts only the executor
    lifecycle subset that the callback is allowed to persist; platform-owned
    run, artifact, and policy events deliberately return ``None``.
    """

    event_type = item.get("event_type")
    payload = item.get("payload")
    if event_type not in _MESSAGE_EVENT_TYPES or not isinstance(payload, Mapping):
        return None
    source_event_id = item.get("event_id")
    source_run_id = item.get("run_id")
    if not isinstance(source_event_id, str) or not source_event_id:
        return None
    if not isinstance(source_run_id, str) or not source_run_id:
        return None
    try:
        _safe_ref(source_event_id, name="source_event_id")
        _safe_ref(source_run_id, name="source_run_id")
    except V4ProjectionError:
        return None
    resolved_message_id = item.get("message_id", message_id)
    if event_type in _MESSAGE_EVENT_TYPES:
        if not isinstance(resolved_message_id, str):
            return None
        try:
            _safe_ref(resolved_message_id, name="message_id")
        except V4ProjectionError:
            return None
    elif resolved_message_id is not None:
        try:
            _safe_ref(resolved_message_id, name="message_id")
        except V4ProjectionError:
            return None
    trace_ref = item.get("trace_ref")
    if trace_ref is not None:
        try:
            _safe_ref(trace_ref, name="trace_ref")
        except V4ProjectionError:
            return None
    causation_event_id = item.get("causation_event_id")
    if causation_event_id is not None:
        try:
            _safe_ref(causation_event_id, name="causation_event_id")
        except V4ProjectionError:
            return None
    try:
        _validate_payload(str(event_type), payload)
    except V4ProjectionError:
        return None
    return V4CallbackItem(
        callback_index=callback_index,
        batch_index=batch_index,
        event_type=str(event_type),
        payload=dict(payload),
        message_id=resolved_message_id,
        trace_ref=trace_ref,
        causation_event_id=causation_event_id,
        source_event_id=source_event_id,
        source_run_id=source_run_id,
    )

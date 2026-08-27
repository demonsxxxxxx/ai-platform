"""Application-owned callback values for durable v4 Streaming events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib

from app.streaming.domain.public_events_v4 import (
    MAX_PUBLIC_THINKING_DELTA_CODEPOINTS,
    V4ProjectionError,
    _CALLBACK_EVENT_TYPES,
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
    if event_type not in _CALLBACK_EVENT_TYPES or not isinstance(payload, Mapping):
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


def callback_thinking_summary_to_v4(
    event: Mapping[str, object],
    *,
    callback_index: int,
    first_batch_index: int,
    callback_batch_id: str,
    expected_event_type: str,
    sanitizer: Callable[[object], str],
) -> tuple[V4CallbackItem, ...]:
    """Project one complete authenticated SDK summary after whole-block sanitization."""

    if (
        event.get("type") != expected_event_type
        or event.get("admin_only") is not True
        or event.get("message") not in {None, ""}
        or event.get("causation_event_id") is not None
    ):
        return ()
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {"summary"}:
        return ()
    source_event_id = event.get("event_id")
    source_run_id = event.get("run_id")
    message_id = event.get("message_id")
    raw_summary = payload.get("summary")
    if (
        not isinstance(source_event_id, str)
        or not isinstance(source_run_id, str)
        or not isinstance(message_id, str)
        or not isinstance(callback_batch_id, str)
        or not isinstance(raw_summary, str)
        or not raw_summary
        or len(raw_summary) > 262_144
    ):
        return ()
    try:
        _safe_ref(source_event_id, name="source_event_id")
        _safe_ref(source_run_id, name="source_run_id")
        _safe_ref(message_id, name="message_id")
        _safe_ref(callback_batch_id, name="callback_batch_id")
    except V4ProjectionError:
        return ()
    thinking_id = "thinking_" + hashlib.sha256(
        f"{source_run_id}:{callback_batch_id}:{callback_index}".encode("utf-8")
    ).hexdigest()[:24]
    summary = sanitizer(raw_summary)
    if (
        not isinstance(summary, str)
        or not summary
        or len(summary) > 262_144
        or sanitizer(summary) != summary
    ):
        return ()
    chunks = tuple(
        summary[offset : offset + MAX_PUBLIC_THINKING_DELTA_CODEPOINTS]
        for offset in range(0, len(summary), MAX_PUBLIC_THINKING_DELTA_CODEPOINTS)
    )
    payloads: tuple[tuple[str, dict[str, object]], ...] = (
        ("thinking.started", {"thinking_id": thinking_id}),
        *(
            ("thinking.delta", {"thinking_id": thinking_id, "delta": chunk})
            for chunk in chunks
        ),
        ("thinking.completed", {"thinking_id": thinking_id}),
    )
    items: list[V4CallbackItem] = []
    for offset, (event_type, public_payload) in enumerate(payloads):
        try:
            _validate_payload(event_type, public_payload)
        except V4ProjectionError:
            return ()
        items.append(
            V4CallbackItem(
                callback_index=callback_index,
                batch_index=first_batch_index + offset,
                event_type=event_type,
                payload=public_payload,
                message_id=message_id,
                source_event_id=source_event_id,
                source_run_id=source_run_id,
            )
        )
    return tuple(items)

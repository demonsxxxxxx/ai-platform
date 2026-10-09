"""Read-only historical text selection using the canonical part ledger."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.streaming.domain.assistant_text_parts import (
    ASSISTANT_TEXT_PART_EVENT_TYPES,
    AssistantTextPartLedgerError,
    reduce_assistant_text_part_message,
)
from app.streaming.domain.public_events_v4 import (
    V4_METADATA_KEY,
    project_persisted_assistant_message_v4,
)

_MESSAGE_FACT_TYPES = ASSISTANT_TEXT_PART_EVENT_TYPES | {
    "message.started",
    "message.delta",
    "message.completed",
}


@dataclass(frozen=True, slots=True)
class AssistantTextMessageProjection:
    status: str
    text: str
    sequence: int
    created_at: Any


def project_persisted_assistant_text_messages(
    rows: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    run_id: str,
) -> tuple[AssistantTextMessageProjection, ...]:
    """Keep each authorized Attempt/incarnation/message ledger separate.

    Only completed final-answer selections contain text. Missing/invalid facts
    remain explicit evidence gaps; this reader neither repairs a receipt nor
    changes Run success. Raw private rows and foreign scopes are never inputs.
    """
    groups: dict[tuple[str, str, int, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        if (
            row.get("visible_to_user") is not True
            or row.get("tenant_id") != tenant_id
            or row.get("run_id") != run_id
            or row.get("event_type") not in _MESSAGE_FACT_TYPES
        ):
            continue
        payload = row.get("payload_json")
        metadata = (
            payload.get(V4_METADATA_KEY) if isinstance(payload, Mapping) else None
        )
        if not isinstance(metadata, Mapping):
            metadata = {}
        # Invalid identity values are grouped without interpreting or exposing them.
        key = (
            metadata.get("attempt_id")
            if isinstance(metadata.get("attempt_id"), str)
            else "",
            metadata.get("message_id")
            if isinstance(metadata.get("message_id"), str)
            else "",
            metadata.get("stream_incarnation")
            if type(metadata.get("stream_incarnation")) is int
            else 0,
            metadata.get("authorization_epoch")
            if type(metadata.get("authorization_epoch")) is int
            else 0,
        )
        groups.setdefault(key, []).append(row)

    messages = []
    for rows_for_message in groups.values():
        if not any(
            row.get("event_type") in ASSISTANT_TEXT_PART_EVENT_TYPES
            for row in rows_for_message
        ):
            continue  # The identified legacy readers retain their existing semantics.
        facts: list[Mapping[str, object]] = []
        for row in rows_for_message:
            public = project_persisted_assistant_message_v4(
                row, tenant_id=tenant_id, run_id=run_id
            )
            if public is None:
                break
            metadata = row["payload_json"][V4_METADATA_KEY]
            facts.append({**public, "source_event_id": metadata.get("source_event_id")})
        status, text = "invalid", ""
        if len(facts) == len(rows_for_message):
            try:
                state = reduce_assistant_text_part_message(facts)
                status = "complete" if state.completed else "incomplete"
                text = state.answer.text if state.answer is not None else ""
            except AssistantTextPartLedgerError:
                pass
        last = facts[-1] if facts else {}
        messages.append(
            AssistantTextMessageProjection(
                status=status,
                text=text,
                sequence=int(last.get("seq") or 0),
                created_at=last.get("emitted_at"),
            )
        )
    return tuple(messages)

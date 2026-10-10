"""Closed ledger semantics for versioned assistant text parts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


ASSISTANT_TEXT_PART_SCHEMA = "ai-platform.assistant-text-part.v1"
ASSISTANT_TEXT_PART_EVENT_TYPES = frozenset(
    {"message.part.delta", "message.part.classified"}
)
_ANSWER_FACT_TYPES = frozenset(
    {
        "message.started",
        "message.delta",
        "message.part.delta",
        "message.part.classified",
        "message.completed",
    }
)
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


class AssistantTextPartLedgerError(ValueError):
    """Persisted answer-part facts do not form one valid message lifecycle."""


@dataclass(frozen=True, slots=True)
class AssistantTextPartAnswer:
    message_id: str
    text: str
    delta_count: int
    text_length: int
    last_delta_event_id: str


@dataclass(frozen=True, slots=True)
class AssistantTextPartState:
    has_parts: bool
    completed: bool
    answer: AssistantTextPartAnswer | None


@dataclass(frozen=True, slots=True)
class AssistantTextPartAppendState:
    owner_message_id: str | None = None
    has_delta: bool = False
    latest_role: str | None = None
    role_reversed: bool = False


def _invalid(reason: str) -> AssistantTextPartLedgerError:
    return AssistantTextPartLedgerError(f"assistant_text_part_{reason}")


def _safe_ref(value: object) -> str:
    if not isinstance(value, str) or _SAFE_REF.fullmatch(value) is None:
        raise _invalid("reference_invalid")
    return value


def apply_assistant_text_part_append_fact(
    state: AssistantTextPartAppendState,
    *,
    message_id: str,
    event_type: str,
    payload: Mapping[str, object],
    source_event_id: object = None,
) -> tuple[str, AssistantTextPartAppendState]:
    """Advance one new part fact using compact persisted ownership/role state."""

    message_id = _safe_ref(message_id)
    if event_type not in ASSISTANT_TEXT_PART_EVENT_TYPES:
        raise _invalid("event_type_invalid")
    if not isinstance(payload, Mapping):
        raise _invalid("payload_invalid")
    if payload.get("schema_version") != ASSISTANT_TEXT_PART_SCHEMA:
        raise _invalid("schema_version_invalid")
    part_id = _safe_ref(payload.get("part_id"))
    if state.role_reversed:
        raise _invalid("work_role_reversed")
    if state.owner_message_id is not None and state.owner_message_id != message_id:
        raise _invalid("part_owner_mismatch")

    if event_type == "message.part.delta":
        delta = payload.get("delta")
        if not isinstance(delta, str) or not 1 <= len(delta) <= 8192:
            raise _invalid("delta_invalid")
        _safe_ref(source_event_id)
        return part_id, AssistantTextPartAppendState(
            owner_message_id=message_id,
            has_delta=True,
            latest_role=state.latest_role,
            role_reversed=state.role_reversed,
        )

    role = payload.get("role")
    if not isinstance(role, str) or role not in ("answer", "work"):
        raise _invalid("role_invalid")
    if not state.has_delta:
        raise _invalid("classification_without_delta")
    if state.latest_role == "work" and role != "work":
        raise _invalid("work_role_reversed")
    return part_id, AssistantTextPartAppendState(
        owner_message_id=message_id,
        has_delta=state.has_delta,
        latest_role=role,
        role_reversed=state.role_reversed,
    )


def reduce_assistant_text_part_message(
    facts: Sequence[Mapping[str, object]],
    *,
    expected_message_id: str | None = None,
) -> AssistantTextPartState:
    """Validate one ordered message ledger and derive its final answer selection.

    Input facts are public v4 envelopes with the ledger-only ``source_event_id``
    attached by the caller. Other event families are intentionally omitted.
    Incomplete messages are valid during streaming and failure/cancellation; a
    successful message completion requires every part to have a final role.
    """

    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        raise _invalid("facts_invalid")
    if not facts:
        return AssistantTextPartState(has_parts=False, completed=False, answer=None)

    part_text: dict[str, list[str]] = {}
    part_roles: dict[str, str] = {}
    part_order: list[str] = []
    delta_source_ids: set[str] = set()
    message_id: str | None = None
    previous_sequence = 0
    started = False
    completed_fact: Mapping[str, object] | None = None
    has_parts = False

    event_types: list[str] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise _invalid("fact_invalid")
        event_type = fact.get("event_type")
        if not isinstance(event_type, str) or event_type not in _ANSWER_FACT_TYPES:
            raise _invalid("event_type_invalid")
        event_types.append(event_type)
    if "message.delta" in event_types and ASSISTANT_TEXT_PART_EVENT_TYPES.intersection(
        event_types
    ):
        raise _invalid("mixed_legacy_and_part_events")

    for fact_index, fact in enumerate(facts):
        event_type = str(fact["event_type"])
        sequence = fact.get("seq")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= previous_sequence
        ):
            raise _invalid("sequence_invalid")
        previous_sequence = sequence
        current_message_id = _safe_ref(fact.get("message_id"))
        if message_id is None:
            message_id = current_message_id
        elif current_message_id != message_id:
            raise _invalid("message_id_mismatch")
        if (
            expected_message_id is not None
            and current_message_id != expected_message_id
        ):
            raise _invalid("message_id_mismatch")
        if completed_fact is not None:
            raise _invalid("event_after_completion")

        payload = fact.get("payload")
        if not isinstance(payload, Mapping):
            raise _invalid("payload_invalid")

        if event_type == "message.started":
            if started or fact_index != 0:
                raise _invalid("started_invalid")
            started = True
            continue

        if not started:
            raise _invalid("started_missing")

        if event_type == "message.part.delta":
            has_parts = True
            if payload.get("schema_version") != ASSISTANT_TEXT_PART_SCHEMA:
                raise _invalid("schema_version_invalid")
            part_id = _safe_ref(payload.get("part_id"))
            delta = payload.get("delta")
            if not isinstance(delta, str) or not 1 <= len(delta) <= 8192:
                raise _invalid("delta_invalid")
            source_event_id = _safe_ref(fact.get("source_event_id"))
            if source_event_id in delta_source_ids:
                raise _invalid("source_event_duplicate")
            delta_source_ids.add(source_event_id)
            if part_id not in part_text:
                part_text[part_id] = []
                part_roles[part_id] = "pending"
                part_order.append(part_id)
            part_text[part_id].append(delta)
            continue

        if event_type == "message.part.classified":
            has_parts = True
            if payload.get("schema_version") != ASSISTANT_TEXT_PART_SCHEMA:
                raise _invalid("schema_version_invalid")
            part_id = _safe_ref(payload.get("part_id"))
            role = payload.get("role")
            if not isinstance(role, str) or role not in ("answer", "work"):
                raise _invalid("role_invalid")
            if part_id not in part_text:
                raise _invalid("classification_without_delta")
            prior_role = part_roles[part_id]
            if prior_role == "work" and role != "work":
                raise _invalid("work_role_reversed")
            part_roles[part_id] = str(role)
            continue

        if event_type == "message.delta":
            # A legacy-only lifecycle is deliberately validated by the v1
            # receipt path; it cannot be mixed into a part lifecycle.
            continue

        if event_type == "message.completed":
            completed_fact = fact

    if not has_parts:
        return AssistantTextPartState(
            has_parts=False, completed=completed_fact is not None, answer=None
        )
    if not started:
        raise _invalid("started_missing")
    if completed_fact is None:
        return AssistantTextPartState(has_parts=True, completed=False, answer=None)
    if any(role == "pending" for role in part_roles.values()):
        raise _invalid("unclassified_at_completion")

    selected_parts = [
        part_id for part_id in part_order if part_roles[part_id] == "answer"
    ]
    answer_delta_facts = [
        fact
        for fact in facts
        if fact.get("event_type") == "message.part.delta"
        and isinstance(fact.get("payload"), Mapping)
        and part_roles.get(str(fact["payload"].get("part_id"))) == "answer"
    ]
    if not selected_parts or not answer_delta_facts:
        raise _invalid("answer_missing")
    text = "\n\n".join("".join(part_text[part_id]) for part_id in selected_parts)
    if not text:
        raise _invalid("answer_empty")
    selected_sources = [
        _safe_ref(fact.get("source_event_id")) for fact in answer_delta_facts
    ]
    if len(selected_sources) != len(set(selected_sources)):
        raise _invalid("source_event_duplicate")
    last_delta_event_id = selected_sources[-1]
    completed_payload = completed_fact.get("payload")
    if not isinstance(completed_payload, Mapping):
        raise _invalid("completed_payload_invalid")
    if (
        isinstance(completed_payload.get("delta_count"), bool)
        or completed_payload.get("delta_count") != len(answer_delta_facts)
        or isinstance(completed_payload.get("text_length"), bool)
        or completed_payload.get("text_length") != len(text)
        or completed_fact.get("causation_event_id") != last_delta_event_id
    ):
        raise _invalid("completed_selection_mismatch")
    answer = AssistantTextPartAnswer(
        message_id=message_id or "",
        text=text,
        delta_count=len(answer_delta_facts),
        text_length=len(text),
        last_delta_event_id=last_delta_event_id,
    )
    return AssistantTextPartState(has_parts=True, completed=True, answer=answer)


__all__ = [
    "AssistantTextPartAppendState",
    "ASSISTANT_TEXT_PART_EVENT_TYPES",
    "ASSISTANT_TEXT_PART_SCHEMA",
    "AssistantTextPartAnswer",
    "AssistantTextPartLedgerError",
    "AssistantTextPartState",
    "apply_assistant_text_part_append_fact",
    "reduce_assistant_text_part_message",
]

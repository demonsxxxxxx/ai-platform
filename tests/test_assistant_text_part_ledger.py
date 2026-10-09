from __future__ import annotations

import pytest

from app.streaming.domain.assistant_text_parts import (
    AssistantTextPartAppendState,
    AssistantTextPartLedgerError,
    apply_assistant_text_part_append_fact,
    reduce_assistant_text_part_message,
)


MESSAGE_ID = "msg_part_test"


def _fact(
    sequence: int,
    event_type: str,
    payload: dict[str, object],
    *,
    source_event_id: str | None = None,
    causation_event_id: str | None = None,
) -> dict[str, object]:
    return {
        "event_id": f"evt_{sequence}",
        "seq": sequence,
        "event_type": event_type,
        "message_id": MESSAGE_ID,
        "payload": payload,
        "source_event_id": source_event_id,
        "causation_event_id": causation_event_id,
    }


def _part_delta(sequence: int, part_id: str, delta: str, source: str):
    return _fact(
        sequence,
        "message.part.delta",
        {
            "schema_version": "ai-platform.assistant-text-part.v1",
            "part_id": part_id,
            "delta": delta,
        },
        source_event_id=source,
    )


def _classified(sequence: int, part_id: str, role: str):
    return _fact(
        sequence,
        "message.part.classified",
        {
            "schema_version": "ai-platform.assistant-text-part.v1",
            "part_id": part_id,
            "role": role,
        },
    )


def test_complete_selection_groups_parts_and_excludes_final_work_role():
    facts = [
        _fact(1, "message.started", {}),
        _part_delta(2, "part_answer", "hello", "source_answer_a"),
        _part_delta(3, "part_work", "internal narration", "source_work_a"),
        _classified(4, "part_answer", "answer"),
        _classified(5, "part_work", "work"),
        _part_delta(6, "part_answer", " world", "source_answer_b"),
        _fact(
            7,
            "message.completed",
            {"delta_count": 2, "text_length": 11},
            causation_event_id="source_answer_b",
        ),
    ]

    state = reduce_assistant_text_part_message(facts)

    assert state.has_parts is True
    assert state.completed is True
    assert state.answer is not None
    assert state.answer.text == "hello world"
    assert state.answer.delta_count == 2
    assert state.answer.text_length == 11
    assert state.answer.last_delta_event_id == "source_answer_b"


def test_distinct_answer_parts_join_with_two_line_feeds_in_first_seen_order():
    facts = [
        _fact(1, "message.started", {}),
        _part_delta(2, "part_first", "first", "source_first"),
        _part_delta(3, "part_second", "second", "source_second"),
        _classified(4, "part_first", "answer"),
        _classified(5, "part_second", "answer"),
        _fact(
            6,
            "message.completed",
            {"delta_count": 2, "text_length": 13},
            causation_event_id="source_second",
        ),
    ]

    state = reduce_assistant_text_part_message(facts)

    assert state.answer is not None
    assert state.answer.text == "first\n\nsecond"
    assert state.answer.text_length == 13


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (
            [
                _fact(1, "message.started", {}),
                _classified(2, "part_orphan", "answer"),
            ],
            "classification_without_delta",
        ),
        (
            [
                _fact(1, "message.started", {}),
                _part_delta(2, "part_a", "x", "source_a"),
                _classified(3, "part_a", "work"),
                _classified(4, "part_a", "answer"),
            ],
            "work_role_reversed",
        ),
        (
            [
                _fact(1, "message.started", {}),
                _part_delta(2, "part_a", "x", "source_a"),
                _fact(3, "message.delta", {"delta": "legacy"}),
            ],
            "mixed_legacy_and_part_events",
        ),
        (
            [
                _fact(1, "message.started", {}),
                _part_delta(2, "part_a", "x", "source_a"),
                _classified(3, "part_a", "answer"),
                _fact(
                    4,
                    "message.completed",
                    {"delta_count": 1, "text_length": 1},
                    causation_event_id="source_a",
                ),
                _part_delta(5, "part_a", "late", "source_late"),
            ],
            "event_after_completion",
        ),
        (
            [
                _fact(1, "message.started", {}),
                _part_delta(2, "part_a", "x", "source_a"),
                _fact(
                    3,
                    "message.completed",
                    {"delta_count": 1, "text_length": 1},
                    causation_event_id="source_a",
                ),
            ],
            "unclassified_at_completion",
        ),
        (
            [
                _fact(1, "message.started", {}),
                _part_delta(2, "part_a", "x", "source_same"),
                _part_delta(3, "part_b", "y", "source_same"),
            ],
            "source_event_duplicate",
        ),
    ],
)
def test_invalid_part_ledger_facts_fail_closed(facts, reason):
    with pytest.raises(AssistantTextPartLedgerError, match=reason):
        reduce_assistant_text_part_message(facts)


def test_pending_part_is_retained_for_incomplete_failure_or_cancellation_history():
    state = reduce_assistant_text_part_message(
        [
            _fact(1, "message.started", {}),
            _part_delta(2, "part_pending", "preview", "source_pending"),
        ]
    )

    assert state.has_parts is True
    assert state.completed is False
    assert state.answer is None


def test_incremental_part_append_uses_prior_owner_delta_and_latest_role():
    part_id, state = apply_assistant_text_part_append_fact(
        AssistantTextPartAppendState(),
        message_id=MESSAGE_ID,
        event_type="message.part.delta",
        payload={
            "schema_version": "ai-platform.assistant-text-part.v1",
            "part_id": "part_answer",
            "delta": "hello",
        },
        source_event_id="source_answer",
    )
    assert part_id == "part_answer"
    assert state == AssistantTextPartAppendState(
        owner_message_id=MESSAGE_ID,
        has_delta=True,
    )

    _part_id, state = apply_assistant_text_part_append_fact(
        state,
        message_id=MESSAGE_ID,
        event_type="message.part.classified",
        payload={
            "schema_version": "ai-platform.assistant-text-part.v1",
            "part_id": "part_answer",
            "role": "work",
        },
    )
    assert state.latest_role == "work"

    with pytest.raises(AssistantTextPartLedgerError, match="part_owner_mismatch"):
        apply_assistant_text_part_append_fact(
            state,
            message_id="msg_other",
            event_type="message.part.delta",
            payload={
                "schema_version": "ai-platform.assistant-text-part.v1",
                "part_id": "part_answer",
                "delta": "foreign",
            },
            source_event_id="source_foreign",
        )

    with pytest.raises(AssistantTextPartLedgerError, match="work_role_reversed"):
        apply_assistant_text_part_append_fact(
            state,
            message_id=MESSAGE_ID,
            event_type="message.part.classified",
            payload={
                "schema_version": "ai-platform.assistant-text-part.v1",
                "part_id": "part_answer",
                "role": "answer",
            },
        )

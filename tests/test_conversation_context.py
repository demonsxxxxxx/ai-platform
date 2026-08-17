import pytest

from app.context.conversation import (
    ConversationContextError,
    build_executor_conversation_context,
)


def _row(message_id, run_id, role, content, order):
    return {
        "id": message_id,
        "run_id": run_id,
        "role": role,
        "content": content,
        "created_at": f"2026-08-17T00:00:{order:02d}Z",
    }


def test_latest_long_assistant_choice_is_materialized_without_per_message_omission():
    assistant = "analysis " + ("x" * 900) + "\nA. continue now\nB. wait for more files"
    rows = [
        _row("msg-user-prior", "run-prior", "user", "Is this file enough?", 1),
        _row("msg-assistant-prior", "run-prior", "assistant", assistant, 2),
        _row("msg-user-current", "run-current", "user", "A", 3),
    ]

    context = build_executor_conversation_context(
        rows,
        selected_message_ids=[row["id"] for row in rows],
        current_run_id="run-current",
    )

    assert context["selected_turn_count"] == 1
    assert [message["role"] for message in context["messages"]] == [
        "user",
        "assistant",
    ]
    assert context["messages"][1]["content"] == assistant
    assert "A. continue now" in context["messages"][1]["content"]
    assert all(message["content"] != "A" for message in context["messages"])


def test_context_budget_drops_oldest_complete_turn_before_recent_pairs():
    rows = []
    for turn in range(1, 4):
        rows.extend(
            [
                _row(
                    f"msg-user-{turn}",
                    f"run-{turn}",
                    "user",
                    f"user-{turn}-" + ("u" * 30),
                    turn * 2 - 1,
                ),
                _row(
                    f"msg-assistant-{turn}",
                    f"run-{turn}",
                    "assistant",
                    f"assistant-{turn}-" + ("a" * 30),
                    turn * 2,
                ),
            ]
        )

    context = build_executor_conversation_context(
        rows,
        selected_message_ids=[row["id"] for row in rows],
        current_run_id="run-current",
        max_history_bytes=420,
    )

    assert context["selected_turn_count"] == 2
    assert context["dropped_turn_count"] == 1
    assert [message["run_id"] for message in context["messages"]] == [
        "run-2",
        "run-2",
        "run-3",
        "run-3",
    ]
    assert [message["role"] for message in context["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_latest_turn_remains_complete_when_it_alone_exceeds_history_budget():
    rows = [
        _row("msg-user-old", "run-old", "user", "old", 1),
        _row("msg-assistant-old", "run-old", "assistant", "old answer", 2),
        _row("msg-user-latest", "run-latest", "user", "latest", 3),
        _row(
            "msg-assistant-latest",
            "run-latest",
            "assistant",
            "latest answer " + ("z" * 500),
            4,
        ),
    ]

    context = build_executor_conversation_context(
        rows,
        selected_message_ids=[row["id"] for row in rows],
        current_run_id="run-current",
        max_history_bytes=10,
    )

    assert context["selected_turn_count"] == 1
    assert context["dropped_turn_count"] == 1
    assert [message["message_id"] for message in context["messages"]] == [
        "msg-user-latest",
        "msg-assistant-latest",
    ]


def test_materialization_fails_closed_for_missing_or_reordered_snapshot_messages():
    rows = [
        _row("msg-user", "run-prior", "user", "question", 1),
        _row("msg-assistant", "run-prior", "assistant", "answer", 2),
    ]

    with pytest.raises(
        ConversationContextError,
        match="conversation_context_materialization_incomplete",
    ):
        build_executor_conversation_context(
            rows[:1],
            selected_message_ids=["msg-user", "msg-assistant"],
            current_run_id="run-current",
        )

    with pytest.raises(
        ConversationContextError,
        match="conversation_context_materialization_reordered",
    ):
        build_executor_conversation_context(
            list(reversed(rows)),
            selected_message_ids=["msg-user", "msg-assistant"],
            current_run_id="run-current",
        )

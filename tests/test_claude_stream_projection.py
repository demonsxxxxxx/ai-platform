import pytest

from app.executors.claude_stream_projection import (
    AssistantAnswerTimeline,
    ClaudeStreamProjector,
)


@pytest.mark.parametrize("result", ["", "Done.", "Done. More.", "Different final."])
def test_answer_timeline_preserves_distinct_assistant_sources_and_terminal(result):
    timeline = AssistantAnswerTimeline()
    visible = [
        timeline.accept_delta("Checking. "),
        timeline.accept_delta("Please wait."),
    ]
    visible.append(timeline.accept_assistant("Checking. Please wait."))
    visible.append(timeline.accept_assistant("Done."))
    visible.append(timeline.accept_result(result))
    expected = "Checking. Please wait.\n\nDone."
    if result == "Done. More.":
        expected += " More."
    elif result == "Different final.":
        expected += "\n\nDifferent final."
    assert "".join(visible) == timeline.text == expected


def test_answer_timeline_does_not_deduplicate_equal_text_from_distinct_messages():
    timeline = AssistantAnswerTimeline()
    assert timeline.accept_assistant("Same.") == "Same."
    assert timeline.accept_assistant("Same.") == "\n\nSame."
    assert timeline.accept_result("Same.") == ""
    assert timeline.text == "Same.\n\nSame."


def test_complete_message_preserves_a_different_already_streamed_delta():
    timeline = AssistantAnswerTimeline()
    assert timeline.accept_assistant("Earlier.") == "Earlier."
    assert timeline.accept_delta("Provisional.") == "\n\nProvisional."
    assert timeline.accept_assistant("Corrected.") == "\n\nCorrected."
    assert timeline.accept_result("") == ""
    assert timeline.text == "Earlier.\n\nProvisional.\n\nCorrected."


def _projector():
    return ClaudeStreamProjector()


def _message_start(message_id="sdk-message"):
    return {
        "type": "message_start",
        "message": {"id": message_id, "stop_reason": None},
    }


def _start(index=0, content_type="text"):
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": content_type},
    }


def _text_delta(text, index=0):
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }


def _stop(index=0):
    return {"type": "content_block_stop", "index": index}


@pytest.mark.parametrize(
    "text",
    ["没有标点的中文", "，继续输出", "x" * 4097, "中" * 262_145],
    ids=["chinese", "comma", "former-lexical-limit", "long-fragment"],
)
def test_projector_returns_text_immediately_without_a_lexical_or_length_gate(text):
    projector = _projector()
    assert projector.accept(_start()) == ()
    assert projector.accept(_text_delta(text)) == (text,)
    assert projector.accept(_text_delta("后续")) == ("后续",)
    assert projector.accept(_stop()) == ()
    assert projector.disabled is False
    assert projector.partial_emitted is True


def test_projector_follows_real_message_order_and_ignores_non_text_blocks():
    projector = _projector()

    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, "thinking")) == ()
    assert (
        projector.accept(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "private reasoning"},
            }
        )
        == ()
    )
    assert projector.accept(_stop(0)) == ()
    assert projector.accept(_start(1)) == ()
    assert projector.accept(_text_delta("visible immediately", index=1)) == (
        "visible immediately",
    )
    # A typed AssistantMessage can arrive here. It is intentionally not a raw
    # framing event and therefore does not close this block.
    assert projector.accept(_stop(1)) == ()
    assert projector.accept(_start(2, "tool_use")) == ()
    assert (
        projector.accept(
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"path":"private"}',
                },
            }
        )
        == ()
    )
    assert projector.accept(_stop(2)) == ()
    assert (
        projector.accept(
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
        )
        == ()
    )
    assert projector.accept({"type": "message_stop"}) == ()
    assert projector.disabled is False

    assert projector.accept(_message_start("sdk-message-2")) == ()
    assert projector.accept(_start(0)) == ()
    assert projector.accept(_text_delta("next message")) == ("next message",)
    assert projector.accept(_stop()) == ()
    assert projector.accept({"type": "message_stop"}) == ()


def test_projector_allows_legacy_unenveloped_blocks_to_reuse_indexes():
    projector = _projector()

    for text in ("first", "second"):
        assert projector.accept(_start()) == ()
        assert projector.accept(_text_delta(text)) == (text,)
        assert projector.accept(_stop()) == ()
    projector.close_unfinished()
    assert projector.disabled is False


def test_projector_rejects_reused_block_index_inside_explicit_message():
    projector = _projector()
    projector.accept(_message_start())
    projector.accept(_start())
    projector.accept(_text_delta("first"))
    projector.accept(_stop())

    assert projector.accept(_start()) == ()
    assert projector.disabled is True


def test_projector_and_answer_gate_redact_private_tokens_split_across_deltas():
    from app.executors.public_answer_stream import PublicAnswerStreamGate

    projector = _projector()
    gate = PublicAnswerStreamGate(
        private_replacements={"synthetic-secret": "[redacted]"},
        sanitizer=lambda text: text,
    )
    projector.accept(_start())
    visible = []
    for text in ("正常内容 synthetic-", "secret 后续内容"):
        for fragment in projector.accept(_text_delta(text)):
            visible.extend(gate.accept(fragment))
    projector.accept(_stop())
    visible.extend(
        gate.finish(
            final_text="正常内容 synthetic-secret 后续内容",
            release=True,
        ).chunks
    )
    assert "".join(visible) == "正常内容 [redacted] 后续内容"
    assert not gate.failed


@pytest.mark.parametrize(
    "conflict",
    [
        _start(1, "tool_use"),
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "private"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "unknown_delta"},
        },
        {
            "type": "content_block_delta",
            "index": True,
            "delta": {"type": "text_delta", "text": "wrong"},
        },
        "malformed event",
    ],
)
def test_projector_permanently_disables_active_text_conflicts(conflict):
    projector = _projector()

    projector.accept(_start())
    assert projector.accept(conflict) == ()
    assert projector.disabled is True
    assert projector.accept(_text_delta("safe later text")) == ()
    assert projector.accept(_stop()) == ()


def test_projector_rejects_wrong_stop_for_ignored_non_text_block():
    projector = _projector()

    assert projector.accept(_start(2, "tool_use")) == ()
    assert projector.accept(_stop(3)) == ()
    assert projector.disabled is True


def test_projector_rejects_text_delta_without_matching_start():
    projector = _projector()

    assert projector.accept(_text_delta("unexpected")) == ()
    assert projector.disabled is True


def test_projector_rejects_wrong_and_duplicate_stop_permanently():
    wrong_stop = _projector()
    wrong_stop.accept(_start())
    wrong_stop.accept(_text_delta("short answer"))
    assert wrong_stop.accept(_stop(1)) == ()
    assert wrong_stop.disabled is True

    duplicate_stop = _projector()
    duplicate_stop.accept(_start())
    assert duplicate_stop.accept(_text_delta("short answer")) == ("short answer",)
    assert duplicate_stop.accept(_stop()) == ()
    assert duplicate_stop.accept(_stop()) == ()
    assert duplicate_stop.disabled is True


@pytest.mark.parametrize(
    "event",
    [
        {"type": "message_start", "message": {}},
        {"type": "message_start", "message": {"id": True}},
        {"type": "message_delta", "delta": "invalid"},
    ],
)
def test_projector_rejects_malformed_message_events(event):
    projector = _projector()
    assert projector.accept(event) == ()
    assert projector.disabled is True


def test_projector_close_unfinished_is_a_permanent_disable():
    open_block = _projector()
    open_block.accept(_start())
    open_block.accept(_text_delta("unfinished"))
    open_block.close_unfinished()
    assert open_block.disabled is True
    assert open_block.accept(_stop()) == ()

    open_message = _projector()
    open_message.accept(_message_start())
    open_message.accept(_start())
    open_message.accept(_text_delta("complete block"))
    open_message.accept(_stop())
    open_message.close_unfinished()
    assert open_message.disabled is True

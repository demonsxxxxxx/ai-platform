import pytest

from app.executors.claude_stream_projection import AssistantAnswerTimeline, ClaudeStreamProjector


@pytest.mark.parametrize("result", ["", "Done.", "Done. More.", "Different final."])
def test_answer_timeline_preserves_distinct_assistant_sources_and_terminal(result):
    timeline = AssistantAnswerTimeline()
    visible = [timeline.accept_delta("Checking. "), timeline.accept_delta("Please wait.")]
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


def _start(index=0, content_type="text"):
    return {"type": "content_block_start", "index": index, "content_block": {"type": content_type}}


def _text_delta(text, index=0):
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}


def _stop(index=0):
    return {"type": "content_block_stop", "index": index}


@pytest.mark.parametrize(
    "text", ["没有标点的中文", "，继续输出", "x" * 4097, "中" * 262_145],
    ids=["chinese", "comma", "former-lexical-limit", "long-fragment"],
)
def test_projector_forwards_text_before_stop_without_a_lexical_or_length_gate(text):
    projector = _projector()
    assert projector.accept(_start()) == ()
    assert projector.accept(_text_delta(text)) == (text,)
    assert projector.accept(_text_delta("后续")) == ("后续",)
    assert projector.accept(_stop()) == ()
    assert projector.disabled is False
    assert projector.partial_emitted is True


def test_parser_output_passes_the_public_gate_for_cross_chunk_redaction():
    from app.executors.public_answer_stream import PublicAnswerStreamGate

    parser = _projector()
    gate = PublicAnswerStreamGate(
        private_replacements={"synthetic-secret": "[redacted]"},
        sanitizer=lambda text: text,
    )
    parser.accept(_start())
    visible = []
    for text in ("正常内容 synthetic-", "secret 后续内容"):
        for fragment in parser.accept(_text_delta(text)):
            visible.extend(gate.accept(fragment))
    parser.accept(_stop())
    visible.extend(gate.finish(final_text="正常内容 synthetic-secret 后续内容", release=True).chunks)
    assert "".join(visible) == "正常内容 [redacted] 后续内容"
    assert not gate.failed


@pytest.mark.parametrize(
    "conflict",
    [
        _start(1, "tool_use"),
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"command\":\"private\"}"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "unknown_delta"}},
        {"type": "content_block_delta", "index": True, "delta": {"type": "text_delta", "text": "wrong"}},
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


def test_projector_tracks_and_closes_non_text_before_a_text_block():
    projector = _projector()

    assert projector.accept(_start(0, "thinking")) == ()
    assert projector.accept({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}}) == ()
    assert projector.accept(_stop(0)) == ()
    assert projector.accept(_start(1)) == ()
    assert projector.accept(_text_delta("safe answer", index=1)) == ("safe answer",)
    assert projector.accept(_stop(1)) == ()
    assert projector.disabled is False
    assert projector.partial_emitted is True


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


def test_projector_close_unfinished_is_a_permanent_disable():
    projector = _projector()

    projector.accept(_start())
    projector.accept(_text_delta("unfinished"))
    projector.close_unfinished()
    assert projector.disabled is True
    assert projector.accept(_stop()) == ()

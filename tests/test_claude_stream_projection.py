import pytest

from app.control_plane_contracts import sanitize_public_payload
from app.executors.claude_stream_projection import ClaudeStreamProjector


def _projector(**kwargs):
    return ClaudeStreamProjector(sanitizer=sanitize_public_payload, **kwargs)


def _start(index=0, content_type="text"):
    return {"type": "content_block_start", "index": index, "content_block": {"type": content_type}}


def _text_delta(text, index=0):
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}


def _stop(index=0):
    return {"type": "content_block_stop", "index": index}


def test_projector_publishes_safe_prefix_before_matching_stop():
    projector = _projector()

    assert projector.accept(_start()) == ()
    assert projector.accept(_text_delta("Short safe public answer.")) == ("Short safe public ",)
    assert projector.accept(_stop()) == ("answer.",)
    assert projector.partial_emitted is True


def test_projector_rejects_split_sensitive_marker_without_publication():
    projector = _projector()

    projector.accept(_start())
    assert projector.accept(_text_delta("C:")) == ()
    assert projector.accept(_text_delta("\\private\\token.txt")) == ()
    assert projector.disabled is True
    assert projector.accept(_stop()) == ()
    assert projector.partial_emitted is False


def test_projector_preserves_only_prior_safe_prefix_after_later_sensitive_text():
    projector = _projector()
    safe_text = "safe " * 120

    projector.accept(_start())
    assert projector.accept(_text_delta(safe_text)) == (safe_text,)
    assert projector.accept(_text_delta("C:\\private\\token.txt")) == ()
    assert projector.disabled is True
    assert projector.partial_emitted is True


def test_projector_disables_at_max_pending_bound():
    projector = _projector(max_pending_chars=8)

    projector.accept(_start())
    assert projector.accept(_text_delta("x" * 9)) == ()
    assert projector.disabled is True


def test_projector_accepts_large_fragment_with_stable_boundary_near_end():
    projector = _projector(max_pending_chars=8)
    safe_text = "safe " * 4

    projector.accept(_start())
    assert projector.accept(_text_delta(safe_text)) == (safe_text,)
    assert projector.accept(_stop()) == ()
    assert projector.disabled is False


def test_projector_accepts_full_size_whitespace_fragment_in_constant_sanitizer_calls():
    sanitizer_calls = 0

    def counting_sanitizer(value):
        nonlocal sanitizer_calls
        sanitizer_calls += 1
        return sanitize_public_payload(value)

    projector = ClaudeStreamProjector(
        sanitizer=counting_sanitizer,
        max_pending_chars=4_096,
    )
    safe_text = "a " * 131_072

    projector.accept(_start())
    assert projector.accept(_text_delta(safe_text)) == (safe_text,)
    assert sanitizer_calls <= 4


def test_projector_rejects_oversized_unbroken_suffix_before_publication():
    projector = _projector(max_pending_chars=8)

    projector.accept(_start())
    assert projector.accept(_text_delta("ok " + "x" * 16)) == ()
    assert projector.disabled is True
    assert projector.partial_emitted is False


def test_projector_rejects_email_continuation_without_partial_publication():
    projector = _projector(max_pending_chars=64)

    projector.accept(_start())
    assert projector.accept(_text_delta("x" * 20)) == ()
    assert projector.accept(_text_delta("@example.com ")) == ()
    assert projector.disabled is True
    assert projector.partial_emitted is False


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
    assert projector.accept(_text_delta("safe answer", index=1)) == ("safe ",)
    assert projector.accept(_stop(1)) == ("answer",)
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
    assert duplicate_stop.accept(_text_delta("short answer")) == ("short ",)
    assert duplicate_stop.accept(_stop()) == ("answer",)
    assert duplicate_stop.accept(_stop()) == ()
    assert duplicate_stop.disabled is True


def test_projector_close_unfinished_is_a_permanent_disable():
    projector = _projector()

    projector.accept(_start())
    projector.accept(_text_delta("unfinished"))
    projector.close_unfinished()
    assert projector.disabled is True
    assert projector.accept(_stop()) == ()

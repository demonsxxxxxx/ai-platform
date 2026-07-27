import pytest

from app.control_plane_contracts import sanitize_public_payload
from app.executors.claude_stream_projection import TrustedInternalClaudeStreamProjector


def _projector(**kwargs):
    return TrustedInternalClaudeStreamProjector(sanitizer=sanitize_public_payload, **kwargs)


def _start(index=0, content_type="text"):
    return {"type": "content_block_start", "index": index, "content_block": {"type": content_type}}


def _text_delta(text, index=0):
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}


def _stop(index=0):
    return {"type": "content_block_stop", "index": index}


def test_projector_flushes_short_text_only_on_matching_stop():
    projector = _projector()

    assert projector.accept(_start()) == ()
    assert projector.accept(_text_delta("short answer")) == ()
    assert projector.accept(_stop()) == ("short answer",)
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
    assert projector.accept(_text_delta(safe_text)) == (safe_text[:-512],)
    assert projector.accept(_text_delta("C:\\private\\token.txt")) == ()
    assert projector.disabled is True
    assert projector.partial_emitted is True


def test_projector_disables_at_max_pending_bound():
    projector = _projector(max_pending_chars=8)

    projector.accept(_start())
    assert projector.accept(_text_delta("x" * 9)) == ()
    assert projector.disabled is True


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


def test_projector_ignores_non_text_without_an_active_text_block():
    projector = _projector()

    assert projector.accept(_start(0, "thinking")) == ()
    assert projector.accept({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}}) == ()
    assert projector.disabled is False
    assert projector.partial_emitted is False


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
    duplicate_stop.accept(_text_delta("short answer"))
    assert duplicate_stop.accept(_stop()) == ("short answer",)
    assert duplicate_stop.accept(_stop()) == ()
    assert duplicate_stop.disabled is True


def test_projector_close_unfinished_is_a_permanent_disable():
    projector = _projector()

    projector.accept(_start())
    projector.accept(_text_delta("unfinished"))
    projector.close_unfinished()
    assert projector.disabled is True
    assert projector.accept(_stop()) == ()

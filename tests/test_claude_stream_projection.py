import pytest

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES
from app.executors.claude_stream_projection import (
    AssistantAnswerTimeline,
    ClaudeStreamProjector,
)


@pytest.mark.parametrize("result", ["", "Done.", "Done. More."])
def test_answer_timeline_preserves_distinct_assistant_sources_and_terminal(result):
    timeline = AssistantAnswerTimeline()
    raw_source = ("message-1", 0)
    message_key = ("message-1", None)
    visible = [
        timeline.accept_delta(
            "Checking. ",
            source_identity=raw_source,
            message_identity=message_key,
        ),
        timeline.accept_delta(
            "Please wait.",
            source_identity=raw_source,
            message_identity=message_key,
        ),
    ]
    timeline.close_raw_source(raw_source)
    visible.append(
        timeline.accept_assistant(
            "Checking. Please wait.",
            source_identity=raw_source,
            message_identity=message_key,
        )
    )
    second_source = ("message-2", 0)
    second_message = ("message-2", None)
    visible.append(
        timeline.accept_assistant(
            "Done.",
            source_identity=second_source,
            message_identity=second_message,
        )
    )
    visible.append(
        timeline.accept_result(
            result,
            source_identity=second_source,
            message_identity=second_message,
            result_identity="result-2",
            terminal_reason="end_turn",
        )
    )
    expected = "Checking. Please wait.\n\nDone."
    if result == "Done. More.":
        expected += " More."
    assert "".join(visible) == timeline.text == expected


def test_answer_timeline_reconciles_multiple_text_sources_in_one_message():
    timeline = AssistantAnswerTimeline()
    message = ("message-1", None)
    first_source = ("message-1", 0)
    second_source = ("message-1", 1)

    assert (
        timeline.accept_delta(
            "first",
            source_identity=first_source,
            message_identity=message,
        )
        == "first"
    )
    timeline.close_raw_source(first_source)
    assert (
        timeline.accept_assistant(
            "first",
            source_identity=first_source,
            message_identity=message,
        )
        == ""
    )
    assert (
        timeline.accept_delta(
            "second",
            source_identity=second_source,
            message_identity=message,
        )
        == "second"
    )
    timeline.close_raw_source(second_source)
    assert (
        timeline.accept_assistant(
            "second",
            source_identity=second_source,
            message_identity=message,
        )
        == ""
    )
    assert timeline.disabled is False
    assert timeline.text == "firstsecond"


def test_answer_timeline_rejects_out_of_order_overlapping_prefix_suffixes():
    timeline = AssistantAnswerTimeline()
    message = ("message-1", None)
    first = ("message-1", 0)
    second = ("message-1", 1)

    assert timeline.accept_delta("a", source_identity=first, message_identity=message) == "a"
    timeline.close_raw_source(first)
    assert timeline.accept_delta("ab", source_identity=second, message_identity=message) == "ab"
    timeline.close_raw_source(second)

    assert (
        timeline.validate_assistant_observations(
            [
                ("abc", first, message, None),
                ("ab", second, message, None),
            ]
        )
        is False
    )
    assert timeline.disabled is True
    assert timeline.text == "aab"


def test_answer_timeline_fails_closed_on_conflicting_terminal_result():
    timeline = AssistantAnswerTimeline()
    source = ("message-1", 0)
    message = ("message-1", None)
    assert (
        timeline.accept_delta(
            "Visible body",
            source_identity=source,
            message_identity=message,
        )
        == "Visible body"
    )
    timeline.close_raw_source(source)
    assert timeline.accept_result("Conflicting terminal result") == ""
    assert timeline.disabled is True
    assert timeline.text == "Visible body"
    unknown = AssistantAnswerTimeline()
    unknown.close_raw_source(("missing", 1))
    assert unknown.disabled is True


def test_answer_timeline_fails_closed_for_ambiguous_anonymous_source():
    timeline = AssistantAnswerTimeline()
    first = ("message-1", 0)
    second = ("message-1", 1)
    message = ("message-1", None)
    timeline.accept_delta("a", source_identity=first, message_identity=message)
    timeline.close_raw_source(first)
    timeline.accept_delta("ab", source_identity=second, message_identity=message)
    timeline.close_raw_source(second)

    assert timeline.accept_assistant("aabX") == ""
    assert timeline.disabled is True


def test_complete_message_rejects_a_conflicting_already_streamed_delta():
    timeline = AssistantAnswerTimeline()
    first_source = ("message-1", 0)
    first_message = ("message-1", None)
    second_source = ("message-2", 0)
    second_message = ("message-2", None)
    assert (
        timeline.accept_assistant(
            "Earlier.",
            source_identity=first_source,
            message_identity=first_message,
        )
        == "Earlier."
    )
    assert (
        timeline.accept_delta(
            "Provisional.",
            source_identity=second_source,
            message_identity=second_message,
        )
        == "\n\nProvisional."
    )
    assert (
        timeline.accept_assistant(
            "Corrected.",
            source_identity=second_source,
            message_identity=second_message,
        )
        == ""
    )
    assert timeline.disabled is True
    assert timeline.text == "Earlier.\n\nProvisional."


def test_answer_timeline_rejects_result_without_bound_source_or_identity():
    timeline = AssistantAnswerTimeline()
    assert timeline.accept_result("invented") == ""
    assert timeline.disabled is True


def test_answer_timeline_accepts_distinct_terminal_observation_identity():
    timeline = AssistantAnswerTimeline()
    source = ("message-1", 0)
    message = ("message-1", None)
    assert (
        timeline.accept_assistant(
            "Observed body",
            source_identity=source,
            message_identity=message,
        )
        == "Observed body"
    )
    assert (
        timeline.accept_result(
            "Observed body",
            source_identity=source,
            message_identity=message,
            result_identity="result-uuid",
            terminal_reason="end_turn",
        )
        == ""
    )
    assert timeline.disabled is False



def test_answer_timeline_allows_none_terminal_reason_but_rejects_unknown_non_null():
    timeline = AssistantAnswerTimeline()
    source = ("message-none-stop", 0)
    message = ("message-none-stop", None)
    assert timeline.accept_assistant(
        "answer",
        source_identity=source,
        message_identity=message,
    ) == "answer"
    assert timeline.accept_result(
        "answer",
        source_identity=source,
        message_identity=message,
        result_identity="result-none-stop",
        terminal_reason=None,
    ) == ""
    assert timeline.disabled is False

    unknown = AssistantAnswerTimeline()
    assert unknown.accept_assistant(
        "answer",
        source_identity=source,
        message_identity=message,
    ) == "answer"
    assert unknown.accept_result(
        "answer",
        source_identity=source,
        message_identity=message,
        result_identity="result-unknown-stop",
        terminal_reason="future-stop-reason",
    ) == ""
    assert unknown.disabled is True


def test_answer_timeline_allows_exact_assistant_observation_replay_only():
    timeline = AssistantAnswerTimeline()
    source = ("message-observation", 0)
    message = ("message-observation", None)
    assert timeline.accept_assistant(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-observation-1",
    ) == "body"
    assert timeline.accept_assistant(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-observation-1",
    ) == ""
    assert timeline.disabled is False
    assert timeline.accept_assistant(
        "changed body",
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-observation-1",
    ) == ""
    assert timeline.disabled is True


def test_answer_timeline_deduplicates_exact_raw_observation_before_reconciliation():
    timeline = AssistantAnswerTimeline()
    source = ("raw-message", 0)
    message = ("raw-message", None)
    assert timeline.accept_delta(
        "raw body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-observation-1",
    ) == "raw body"
    assert timeline.accept_delta(
        "raw body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-observation-1",
    ) == ""
    assert timeline.text == "raw body"
    assert timeline._sources[0].coverage == "raw body"

    bounded = AssistantAnswerTimeline()
    bounded_body = "x" * RUN_RESULT_MAX_BYTES
    assert bounded.accept_delta(
        bounded_body,
        source_identity=source,
        message_identity=message,
        observed_identity="raw-bounded-replay",
    ) == bounded_body
    assert bounded.accept_delta(
        bounded_body,
        source_identity=source,
        message_identity=message,
        observed_identity="raw-bounded-replay",
    ) == ""
    assert bounded.disabled is False

    conflict = AssistantAnswerTimeline()
    assert conflict.accept_delta(
        "raw body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-observation-1",
    ) == "raw body"
    assert conflict.accept_delta(
        "changed body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-observation-1",
    ) == ""
    assert conflict.disabled is True

    for conflicting_source, conflicting_message, conflicting_parent in (
        (("raw-message", 1), message, None),
        (source, ("other-message", None), None),
        (source, message, "other-parent"),
    ):
        identity_conflict = AssistantAnswerTimeline()
        assert identity_conflict.accept_delta(
            "raw body",
            source_identity=source,
            message_identity=message,
            observed_identity="raw-observation-1",
        ) == "raw body"
        assert identity_conflict.accept_delta(
            "raw body",
            source_identity=conflicting_source,
            message_identity=conflicting_message,
            parent_tool_use_id=conflicting_parent,
            observed_identity="raw-observation-1",
        ) == ""
        assert identity_conflict.disabled is True


def test_answer_timeline_rejects_assistant_observation_replay_on_other_source():
    timeline = AssistantAnswerTimeline()
    message = ("message-observation-sources", None)
    first = ("message-observation-sources", 0)
    second = ("message-observation-sources", 1)
    assert timeline.accept_assistant(
        "body",
        source_identity=first,
        message_identity=message,
        observed_identity="assistant-observation-reused",
    ) == "body"
    assert timeline.accept_assistant(
        "body",
        source_identity=second,
        message_identity=message,
        observed_identity="assistant-observation-reused",
    ) == ""
    assert timeline.disabled is True


def test_answer_timeline_rejects_reversed_typed_source_order():
    timeline = AssistantAnswerTimeline()
    message = ("message-order", None)
    first = ("message-order", 0)
    second = ("message-order", 1)
    assert timeline.accept_delta("first", source_identity=first, message_identity=message) == "first"
    timeline.close_raw_source(first)
    assert timeline.accept_delta("second", source_identity=second, message_identity=message) == "second"
    timeline.close_raw_source(second)
    assert timeline.validate_assistant_observations(
        [
            ("second", second, message, None),
            ("first", first, message, None),
        ]
    ) is False
    assert timeline.disabled is True


def test_answer_timeline_bounds_recent_assistant_observations():
    timeline = AssistantAnswerTimeline()
    message = ("message-many-assistant", None)
    for index in range(129):
        source = ("message-many-assistant", index)
        body = f"body-{index}"
        assert timeline.accept_assistant(
            body,
            source_identity=source,
            message_identity=message,
            observed_identity=f"assistant-observation-{index}",
        ) == body
    assert len(timeline._recent_assistant_observations) == 128
    assert timeline.disabled is False
    assert len(timeline._sources) == 128


def test_answer_timeline_treats_evicted_typed_observation_as_new_output():
    timeline = AssistantAnswerTimeline()
    message = ("message-typed-rollovers", None)
    source = ("message-typed-rollovers", 0)
    body = ""
    for index in range(129):
        body += f"typed-{index}"
        assert timeline.accept_assistant(
            body,
            source_identity=source,
            message_identity=message,
            observed_identity=f"typed-observation-{index}",
            observation_scope="typed-message-1",
        ) == f"typed-{index}"

    expanded = body + "after observation window"
    assert timeline.accept_assistant(
        expanded,
        source_identity=source,
        message_identity=message,
        observed_identity="typed-observation-0",
        observation_scope="typed-message-1",
    ) == "after observation window"
    assert timeline.text == expanded
    assert timeline.disabled is False


def test_answer_timeline_allows_one_assistant_uuid_for_multiple_blocks_in_scope():
    timeline = AssistantAnswerTimeline()
    message = ("message-one-assistant", None)
    scope = "typed-message-1"
    for index in range(3):
        source = ("message-one-assistant", index)
        assert timeline.accept_assistant(
            f"body-{index}",
            source_identity=source,
            message_identity=message,
            observed_identity="assistant-one-observation",
            observation_scope=scope,
        ) == f"body-{index}"
    assert len(timeline._recent_assistant_observations) == 3
    assert timeline.disabled is False
    assert timeline.text == "body-0body-1body-2"


def test_answer_timeline_prunes_closed_sources_across_multiple_messages():
    timeline = AssistantAnswerTimeline()
    for index in range(129):
        message = (f"message-total-{index}", None)
        source = (f"message-total-{index}", 0)
        expected = str(index) if index == 0 else f"\n\n{index}"
        assert timeline.accept_assistant(
            str(index),
            source_identity=source,
            message_identity=message,
        ) == expected
    assert timeline.disabled is False
    assert len(timeline._sources) == 128
    assert len(timeline._sources_by_key) == 128
    assert ("message-total-0", 0) not in timeline._sources_by_key
    assert timeline.accept_assistant(
        "late source",
        source_identity=("message-total-0", 0),
        message_identity=("message-total-0", None),
    ) == "\n\nlate source"
    assert timeline.disabled is False


def test_answer_timeline_allows_long_answers_with_bounded_reconciliation_coverage():
    timeline = AssistantAnswerTimeline()
    first = "中" * (RUN_RESULT_MAX_BYTES // len("中".encode("utf-8")))
    first_source = ("message-utf8", 0)
    first_message = ("message-utf8", None)
    assert timeline.accept_assistant(
        first,
        source_identity=first_source,
        message_identity=first_message,
    ) == first
    assert len(timeline.text.encode("utf-8")) == RUN_RESULT_MAX_BYTES - 1

    next_text = "next"
    assert timeline.accept_assistant(
        next_text,
        source_identity=("message-utf8-next", 0),
        message_identity=("message-utf8-next", None),
    ) == "\n\n" + next_text
    assert timeline.disabled is False

    long_source = ("message-long", 0)
    long_message = ("message-long", None)
    long_body = "x" * (RUN_RESULT_MAX_BYTES + 1)
    assert timeline.accept_delta(
        long_body,
        source_identity=long_source,
        message_identity=long_message,
        observed_identity="long-raw",
    ) == "\n\n" + long_body
    assert timeline.accept_delta(
        "tail",
        source_identity=long_source,
        message_identity=long_message,
        observed_identity="long-raw-tail",
    ) == "tail"
    long_source_state = timeline._sources[-1]
    assert long_source_state.coverage_truncated is True
    assert long_source_state.coverage == ""
    assert len(timeline.text.encode("utf-8")) > RUN_RESULT_MAX_BYTES
    timeline.close_raw_source(long_source)
    assert timeline.disabled is False

    assert timeline.accept_assistant(
        long_body + "tail",
        source_identity=long_source,
        message_identity=long_message,
        observed_identity="long-assistant-exact",
    ) == ""
    assert timeline.disabled is False
    exact_source_result = long_body + "tail"
    assert timeline.accept_result(
        exact_source_result,
        source_identity=long_source,
        message_identity=long_message,
        result_identity="long-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False

    conflicting = AssistantAnswerTimeline()
    assert conflicting.accept_delta(
        long_body,
        source_identity=long_source,
        message_identity=long_message,
        observed_identity="long-conflict-raw",
    ) == long_body
    conflicting.close_raw_source(long_source)
    assert conflicting.accept_result(
        long_body[:-1] + "z",
        source_identity=long_source,
        message_identity=long_message,
        result_identity="long-conflict-result",
        terminal_reason="end_turn",
    ) == ""
    assert conflicting.disabled is True

    boundary = AssistantAnswerTimeline()
    boundary_source = ("message-boundary", 0)
    boundary_message = ("message-boundary", None)
    boundary_body = "x" * RUN_RESULT_MAX_BYTES
    assert boundary.accept_assistant(
        boundary_body,
        source_identity=boundary_source,
        message_identity=boundary_message,
    ) == boundary_body
    assert boundary.accept_assistant(
        boundary_body,
        source_identity=boundary_source,
        message_identity=boundary_message,
    ) == ""
    assert boundary.disabled is False
    assert boundary.accept_assistant(
        boundary_body + "unverified typed suffix",
        source_identity=boundary_source,
        message_identity=boundary_message,
    ) == ""
    assert boundary.disabled is True
    assert boundary._sources[0].coverage_truncated is True

    result_only = AssistantAnswerTimeline()
    too_large = "中" * (RUN_RESULT_MAX_BYTES // len("中".encode("utf-8")) + 1)
    assert result_only.accept_result_only(
        too_large,
        result_identity="oversized-result",
        terminal_reason="end_turn",
    ) == too_large
    assert result_only.disabled is False


def test_answer_timeline_allows_more_than_128_adjacent_raw_deltas():
    timeline = AssistantAnswerTimeline()
    source = ("message-raw-long", 0)
    message = ("message-raw-long", None)
    chunks = []
    for index in range(257):
        chunk = f"chunk-{index}"
        chunks.append(chunk)
        assert timeline.accept_delta(
            chunk,
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-long-{index}",
        ) == chunk
    assert timeline.disabled is False
    assert timeline._sources[0].raw_delta_count == 257
    assert timeline.text == "".join(chunks)
    assert len(timeline._recent_raw_observations) == 128
    timeline.close_raw_source(source)
    assert timeline.disabled is False
    assert timeline.accept_delta(
        chunks[-1],
        source_identity=source,
        message_identity=message,
        observed_identity="raw-long-256",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == "".join(chunks)

    timeline = AssistantAnswerTimeline()
    source = ("message-bounded", 0)
    message = ("message-bounded", None)
    body = "x" * 8192
    assert timeline.accept_delta(body, source_identity=source, message_identity=message) == body
    timeline.close_raw_source(source)
    stored = timeline._sources[0]
    assert stored.coverage == ""
    assert stored.coverage_length == len(body)
    assert len(stored.coverage_digest) == 64


def test_answer_timeline_treats_evicted_raw_uuid_as_new_output():
    timeline = AssistantAnswerTimeline()
    source = ("message-raw-replay", 0)
    message = ("message-raw-replay", None)
    chunks = []
    for index in range(129):
        chunk = f"chunk-{index}"
        chunks.append(chunk)
        assert timeline.accept_delta(
            chunk,
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-replay-{index}",
        ) == chunk

    before_replay = timeline.text
    assert timeline.accept_delta(
        chunks[0],
        source_identity=source,
        message_identity=message,
        observed_identity="raw-replay-0",
    ) == chunks[0]
    assert timeline.disabled is False
    assert timeline.text == before_replay + chunks[0]


def test_answer_timeline_retires_binding_before_foreign_result():
    timeline = AssistantAnswerTimeline()
    source = ("message-retire", 0)
    message = ("message-retire", None)
    assert timeline.accept_delta(
        "visible prefix",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-retire",
    ) == "visible prefix"
    timeline.retire_answer_binding()
    assert timeline.latest_binding is None
    assert timeline.accept_result(
        "foreign terminal body",
        source_identity=source,
        message_identity=message,
        result_identity="foreign-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is True
    assert timeline.text == "visible prefix"


def test_answer_timeline_allows_new_raw_source_after_retirement():
    timeline = AssistantAnswerTimeline()
    old_source = ("message-retire-old", 0)
    old_message = ("message-retire-old", None)
    assert timeline.accept_delta(
        "old answer",
        source_identity=old_source,
        message_identity=old_message,
        observed_identity="raw-retire-old",
    ) == "old answer"
    timeline.retire_answer_binding()

    new_source = ("message-retire-new", 0)
    new_message = ("message-retire-new", None)
    assert timeline.accept_delta(
        "new answer",
        source_identity=new_source,
        message_identity=new_message,
        observed_identity="raw-retire-new",
    ) == "\n\nnew answer"
    timeline.close_raw_source(new_source)
    assert timeline.accept_result(
        "old answer\n\nnew answer",
        source_identity=new_source,
        message_identity=new_message,
        result_identity="new-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False


def test_answer_timeline_allows_new_typed_source_after_retirement():
    timeline = AssistantAnswerTimeline()
    old_source = ("message-retire-old-typed", 0)
    old_message = ("message-retire-old-typed", None)
    assert timeline.accept_assistant(
        "old answer",
        source_identity=old_source,
        message_identity=old_message,
        observed_identity="typed-retire-old",
    ) == "old answer"
    timeline.retire_answer_binding()

    new_source = ("message-retire-new-typed", 0)
    new_message = ("message-retire-new-typed", None)
    assert timeline.accept_assistant(
        "new answer",
        source_identity=new_source,
        message_identity=new_message,
        observed_identity="typed-retire-new",
    ) == "\n\nnew answer"
    assert timeline.accept_result(
        "old answer\n\nnew answer",
        source_identity=new_source,
        message_identity=new_message,
        result_identity="new-typed-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False


def test_answer_timeline_accepts_typed_body_for_closed_empty_raw_source():
    projector = _projector()
    timeline = AssistantAnswerTimeline()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start()) == ()
    source = projector.text_source_identity
    assert source is not None
    message = ("sdk-message", None)
    assert timeline.establish_raw_source(source, message_identity=message)
    assert projector.accept(_stop()) == ()
    timeline.close_raw_source(source)
    assert timeline.validate_assistant_observations(
        [("typed answer", source, message, None)]
    )
    assert timeline.accept_assistant(
        "typed answer",
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-empty-raw",
    ) == "typed answer"
    assert timeline.disabled is False


def test_projector_reconciles_typed_body_before_matching_raw_delta_once():
    projector = _projector()
    timeline = AssistantAnswerTimeline()
    message = ("sdk-message", None)
    body = "typed before raw."

    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start()) == ()
    source = projector.text_source_identity
    assert source is not None
    assert timeline.establish_raw_source(source, message_identity=message)
    assert timeline.validate_assistant_observations(
        [(body, source, message, None)]
    )
    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-before-raw",
        observation_scope=1,
    ) == body
    assert timeline.text == body

    assert projector.accept(_text_delta(body)) == (body,)
    assert timeline.accept_delta(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="raw-after-typed",
    ) == ""
    assert timeline.text == body
    assert timeline._sources[0].coverage == body

    assert projector.accept(_stop()) == ()
    timeline.close_raw_source(source)
    assert timeline.accept_result(
        body,
        source_identity=source,
        message_identity=message,
        result_identity="result-after-typed",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False


def test_projector_reconciles_typed_extension_after_raw_prefix_once():
    projector = _projector()
    timeline = AssistantAnswerTimeline()
    message = ("sdk-message", None)

    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start()) == ()
    source = projector.text_source_identity
    assert source is not None
    assert timeline.establish_raw_source(source, message_identity=message)
    assert projector.accept(_text_delta("Hello")) == ("Hello",)
    assert timeline.accept_delta(
        "Hello",
        source_identity=source,
        message_identity=message,
        observed_identity="r1",
    ) == "Hello"

    assert projector.observe_typed(message_id="sdk-message", uuid="a1") is True
    assert projector.typed_text_source_identity(
        text_source_ordinal=0,
    ) == source

    assert timeline.validate_assistant_observations(
        [("Hello world", source, message, None)]
    )
    assert timeline.accept_assistant(
        "Hello world",
        source_identity=source,
        message_identity=message,
        observed_identity="a1",
        observation_scope=1,
    ) == " world"
    assert projector.accept(_text_delta(" world")) == (" world",)
    assert timeline.accept_delta(
        " world",
        source_identity=source,
        message_identity=message,
        observed_identity="r2",
    ) == ""
    assert timeline.text == "Hello world"
    assert timeline.disabled is False

    assert projector.accept(_stop()) == ()
    timeline.close_raw_source(source)
    for event in _message_end():
        assert projector.accept(event) == ()
    assert projector.disabled is False


def test_answer_timeline_reconciles_chunked_long_typed_body_before_raw():
    timeline = AssistantAnswerTimeline()
    source = ("message-long-typed-before-raw", 0)
    message = ("message-long-typed-before-raw", None)
    body = "x" * (RUN_RESULT_MAX_BYTES + 4096)

    assert timeline.establish_raw_source(source, message_identity=message)
    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="assistant-long-before-raw",
    ) == body
    assert timeline._sources[0].coverage_truncated is True

    offset = 0
    chunk_index = 0
    while offset < len(body):
        chunk = body[offset : offset + 4093]
        assert timeline.accept_delta(
            chunk,
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-long-before-raw-{chunk_index}",
        ) == ""
        offset += len(chunk)
        chunk_index += 1

    timeline.close_raw_source(source)
    assert timeline.disabled is False
    assert timeline.text == body
    assert timeline._sources[0].typed_body_replay_pending is False

    conflicting = AssistantAnswerTimeline()
    assert conflicting.establish_raw_source(source, message_identity=message)
    assert conflicting.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
    ) == body
    wrong = body[:-1] + "y"
    assert conflicting.accept_delta(
        wrong,
        source_identity=source,
        message_identity=message,
        observed_identity="raw-long-before-raw-conflict",
    ) == ""
    assert conflicting.disabled is True
    assert conflicting.text == body


def test_answer_timeline_reconciles_truncated_result_for_current_source_only():
    timeline = AssistantAnswerTimeline()
    earlier_source = ("message-earlier", 0)
    earlier_message = ("message-earlier", None)
    current_source = ("message-current-long", 0)
    current_message = ("message-current-long", None)
    body = "y" * (RUN_RESULT_MAX_BYTES + 4096)

    assert timeline.accept_assistant(
        "Earlier narration.",
        source_identity=earlier_source,
        message_identity=earlier_message,
    ) == "Earlier narration."
    assert timeline.accept_assistant(
        body,
        source_identity=current_source,
        message_identity=current_message,
    ) == "\n\n" + body
    assert timeline._sources[-1].coverage_truncated is True

    assert timeline.accept_result(
        body + " verified suffix",
        source_identity=current_source,
        message_identity=current_message,
        result_identity="current-long-result",
        terminal_reason="end_turn",
    ) == " verified suffix"
    assert timeline.disabled is False
    assert timeline.text == "Earlier narration.\n\n" + body + " verified suffix"

    conflicting = AssistantAnswerTimeline()
    assert conflicting.accept_assistant(
        "Earlier narration.",
        source_identity=earlier_source,
        message_identity=earlier_message,
    ) == "Earlier narration."
    assert conflicting.accept_assistant(
        body,
        source_identity=current_source,
        message_identity=current_message,
    ) == "\n\n" + body
    assert conflicting.accept_result(
        "Earlier narration.\n\n" + body,
        source_identity=current_source,
        message_identity=current_message,
        result_identity="aggregate-result",
        terminal_reason="end_turn",
    ) == ""
    assert conflicting.disabled is True


def test_answer_timeline_long_raw_stream_survives_multiple_observation_windows():
    timeline = AssistantAnswerTimeline()
    source = ("message-raw-rollovers", 0)
    message = ("message-raw-rollovers", None)
    for index in range(257):
        chunk = f"chunk-{index}"
        assert timeline.accept_delta(
            chunk,
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-rollover-{index}",
        ) == chunk

    complete_stream = "".join(f"chunk-{index}" for index in range(257))
    assert timeline.text == complete_stream
    assert len(timeline._recent_raw_observations) == 128
    assert timeline.accept_delta(
        "chunk-0",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-rollover-0",
    ) == "chunk-0"
    assert timeline.disabled is False
    assert timeline.text == complete_stream + "chunk-0"


def test_answer_timeline_keeps_bounded_raw_observations_after_close():
    timeline = AssistantAnswerTimeline()
    source = ("message-many-raw", 0)
    message = ("message-many-raw", None)
    for index in range(128):
        assert timeline.accept_delta(
            str(index),
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-observation-{index}",
        ) == str(index)
    timeline.close_raw_source(source)
    assert len(timeline._recent_raw_observations) == 128
    stored = timeline._sources[0]
    assert stored.coverage == ""
    expected_length = sum(len(str(index)) for index in range(128))
    assert stored.coverage_length == expected_length
    assert len(stored.coverage_digest) == 64
    assert stored.raw_coverage == ""


def test_answer_timeline_keeps_recent_raw_replay_guard_after_close():
    timeline = AssistantAnswerTimeline()
    source = ("message-tombstone", 0)
    message = ("message-tombstone", None)
    assert timeline.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone",
    ) == "body"
    timeline.close_raw_source(source)

    assert timeline.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone",
    ) == ""
    assert timeline.disabled is False

    conflict = AssistantAnswerTimeline()
    assert conflict.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone",
    ) == "body"
    conflict.close_raw_source(source)
    assert conflict.accept_delta(
        "changed",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone",
    ) == ""
    assert conflict.disabled is True

    parent_conflict = AssistantAnswerTimeline()
    assert parent_conflict.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        parent_tool_use_id="parent-a",
        observed_identity="raw-tombstone",
    ) == "body"
    parent_conflict.close_raw_source(source)
    assert parent_conflict.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        parent_tool_use_id="parent-b",
        observed_identity="raw-tombstone",
    ) == ""
    assert parent_conflict.disabled is True

    reused = AssistantAnswerTimeline()
    assert reused.accept_delta(
        "body",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone",
    ) == "body"
    reused.close_raw_source(source)
    assert reused.accept_delta(
        "body",
        source_identity=("other-message", 0),
        message_identity=("other-message", None),
        observed_identity="raw-tombstone",
    ) == ""
    assert reused.disabled is True


def test_answer_timeline_recent_raw_window_spans_sources():
    timeline = AssistantAnswerTimeline()
    message = ("message-tombstone-cap", None)
    source = ("message-tombstone-cap", 0)
    for index in range(128):
        assert timeline.accept_delta(
            str(index),
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-tombstone-cap-{index}",
        ) == str(index)
    timeline.close_raw_source(source)
    second_source = ("message-tombstone-cap", 1)
    assert timeline.accept_delta(
        "new",
        source_identity=second_source,
        message_identity=message,
        observed_identity="raw-tombstone-cap-overflow",
    ) == "new"
    assert len(timeline._recent_raw_observations) == 128
    assert timeline.accept_delta(
        "0",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-tombstone-cap-0",
    ) == ""
    assert timeline.disabled is True
    assert timeline.text.endswith("new")


def test_answer_timeline_bounds_recent_raw_observations_without_disabling():
    timeline = AssistantAnswerTimeline()
    source = ("message-raw-cap", 0)
    message = ("message-raw-cap", None)
    for index in range(129):
        assert timeline.accept_delta(
            f"chunk-{index}",
            source_identity=source,
            message_identity=message,
            observed_identity=f"raw-cap-{index}",
        ) == f"chunk-{index}"
    assert timeline.disabled is False
    assert len(timeline._recent_raw_observations) == 128
    assert timeline.text == "".join(f"chunk-{index}" for index in range(129))
    assert timeline.accept_delta(
        "new output",
        source_identity=source,
        message_identity=message,
        observed_identity="raw-cap-129",
    ) == "new output"
    assert timeline.text.endswith("new output")
    assert timeline.disabled is False


def test_answer_timeline_rejects_result_replay_with_foreign_identity():
    timeline = AssistantAnswerTimeline()
    source = ("message-1", 0)
    message = ("message-1", None)
    assert timeline.accept_assistant(
        "Observed body", source_identity=source, message_identity=message
    ) == "Observed body"
    assert timeline.accept_result(
        "Observed body",
        source_identity=source,
        message_identity=message,
        result_identity="result-uuid",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.accept_result(
        "Observed body",
        source_identity=source,
        message_identity=message,
        result_identity="foreign-result-uuid",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is True


def test_answer_timeline_rejects_stale_result_and_accepts_bound_suffix():
    stale = AssistantAnswerTimeline()
    source = ("message-1", 0)
    message = ("message-1", None)
    stale.accept_assistant("Observed body", source_identity=source, message_identity=message)
    assert stale.accept_result(
        "Observed",
        source_identity=source,
        message_identity=message,
        result_identity="stale-result",
        terminal_reason="end_turn",
    ) == ""
    assert stale.disabled is True

    suffix = AssistantAnswerTimeline()
    assert suffix.accept_assistant(
        "Observed", source_identity=source, message_identity=message
    ) == "Observed"
    assert suffix.accept_result(
        "Observed body",
        source_identity=source,
        message_identity=message,
        result_identity="suffix-result",
        terminal_reason="end_turn",
    ) == " body"
    assert suffix.text == "Observed body"



def _projector():
    return ClaudeStreamProjector()


def _message_start(message_id="sdk-message"):
    return {
        "type": "message_start",
        "message": {"id": message_id, "role": "assistant", "stop_reason": None},
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


def _message_end(stop_reason="end_turn"):
    return [
        {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
        {"type": "message_stop"},
    ]


def test_answer_timeline_rejects_foreign_aggregate_suffix_for_bound_source():
    timeline = AssistantAnswerTimeline()
    first_source = ("message-1", 0)
    first_message = ("message-1", None)
    second_source = ("message-2", 0)
    second_message = ("message-2", None)
    assert timeline.accept_assistant(
        "first", source_identity=first_source, message_identity=first_message
    ) == "first"
    assert timeline.accept_assistant(
        "second", source_identity=second_source, message_identity=second_message
    ) == "\n\nsecond"
    assert timeline.accept_result(
        "first second foreign",
        source_identity=second_source,
        message_identity=second_message,
        result_identity="foreign-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is True


def test_projector_accepts_empty_text_deltas_without_emitting_or_changing_source():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, "text")) == ()
    source = projector.text_source_identity
    assert projector.accept(_text_delta("")) == ()
    assert projector.accept(_text_delta("answer")) == ("answer",)
    assert projector.accept(_text_delta("")) == ()
    assert projector.text_source_identity == source
    assert projector.accept(_stop(0)) == ()
    for event in _message_end():
        assert projector.accept(event) == ()
    assert projector.disabled is False
    assert projector.failure_frame is None


@pytest.mark.parametrize("text", [None, False, 0, [], {}])
def test_projector_still_rejects_non_string_text_deltas(text):
    projector = _projector()
    projector.accept(_message_start())
    projector.accept(_start(0, "text"))
    assert projector.accept(_text_delta(text)) == ()
    assert projector.disabled is True
    assert projector.failure_frame["guard"] == "block_delta_text"


def test_projector_captures_only_first_rejected_frame_shape():
    projector = _projector()
    assert projector.accept(_message_start("private-message-id")) == ()
    assert projector.accept(_start(0, "tool_use")) == ()
    assert projector.accept({
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "private-prompt-and-token"},
    }) == ()
    assert projector.disabled
    assert projector.failure_frame == {
        "event_type": "content_block_delta",
        "block_type": "other",
        "delta_type": "text_delta",
        "message_state": "open",
        "open_block_type": "tool_use",
        "index_state": "ignored",
        "guard": "block_delta_type",
    }
    assert projector.accept({"type": "private-second-event"}) == ()
    assert "private" not in str(projector.failure_frame)


def test_projector_captures_preceding_structural_frames_for_overlapping_tool_starts():
    projector = _projector()
    assert projector.accept(_message_start("private-message-id")) == ()
    assert projector.accept(_start(0, "tool_use")) == ()
    assert projector.accept({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": "private-tool-input"},
    }) == ()
    assert projector.accept(_start(1, "tool_use")) == ()

    assert projector.disabled is True
    assert projector.failure_frame == {
        "event_type": "content_block_start", "block_type": "tool_use",
        "delta_type": "other", "message_state": "open",
        "open_block_type": "tool_use", "index_state": "other",
        "guard": "block_start_state",
    }
    assert projector.failure_frame_history == (
        {
            "event_type": "message_start", "block_type": "other",
            "delta_type": "other", "message_state": "closed",
            "open_block_type": "none", "index_state": "invalid",
        },
        {
            "event_type": "content_block_start", "block_type": "tool_use",
            "delta_type": "other", "message_state": "open",
            "open_block_type": "none", "index_state": "other",
        },
        {
            "event_type": "content_block_delta", "block_type": "other",
            "delta_type": "input_json_delta", "message_state": "open",
            "open_block_type": "tool_use", "index_state": "ignored",
        },
    )
    assert "private" not in str(projector.failure_frame_history)
    assert projector.accept(_stop(0)) == ()
    assert len(projector.failure_frame_history) == 3


def test_projector_bounds_preceding_frames_to_four():
    projector = _projector()
    projector.accept(_message_start())
    projector.accept(_start(0, "tool_use"))
    for _ in range(9):
        assert projector.accept({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "private-data"},
        }) == ()
    projector.accept(_start(1, "tool_use"))

    assert len(projector.failure_frame_history) == 4
    assert all(frame["event_type"] == "content_block_delta" for frame in projector.failure_frame_history)
    assert "private-data" not in str(projector.failure_frame_history)


def test_projector_classifies_unknown_unhashable_block_type_without_leakage():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, {"secret": "private-token"})) == ()
    assert projector.failure_frame["block_type"] == "other"
    assert projector.failure_frame["guard"] == "block_start_type"
    assert "private-token" not in str(projector.failure_frame)


def test_projector_ignores_pinned_server_tool_result_blocks_before_public_text():
    for content_type in (
        "server_tool_result",
        "advisor_tool_result",
        "mcp_tool_result",
        "tool_search_tool_result",
        "web_search_tool_result",
        "web_fetch_tool_result",
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
        "mcp_tool_use",
        "tool_result",
        "redacted_thinking",
    ):
        projector = _projector()
        assert projector.accept(_message_start()) == ()
        assert projector.accept(_start(0, content_type)) == ()
        assert projector.accept(_stop(0)) == ()
        assert projector.accept(_start(1, "text")) == ()
        assert projector.accept(_text_delta("public after server result", index=1)) == (
            "public after server result",
        )
        assert projector.accept(_stop(1)) == ()
        for event in _message_end():
            assert projector.accept(event) == ()
        assert projector.disabled is False


def test_projector_binds_typed_text_to_ordered_raw_text_source_after_omitted_non_text():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, "web_search_tool_result")) == ()
    assert projector.accept(_stop(0)) == ()
    assert projector.accept(_start(1, "text")) == ()
    source = projector.text_source_identity
    assert source is not None
    assert projector.accept(_text_delta("safe text", index=1)) == ("safe text",)
    assert projector.validate_typed_text_source_count(1) is True
    assert projector.observe_typed(message_id="sdk-message", uuid="typed-observation") is True
    assert projector.typed_text_source_identity(
        text_source_ordinal=0,
    ) == source
    assert projector.accept(_stop(1)) == ()
    for event in _message_end():
        assert projector.accept(event) == ()
    assert projector.disabled is False


def test_projector_binds_each_per_block_typed_text_to_next_raw_window():
    for omitted_type in ("web_search_tool_result", "redacted_thinking"):
        projector = _projector()
        assert projector.accept(_message_start()) == ()

        assert projector.accept(_start(0, "text")) == ()
        first_source = projector.text_source_identity
        assert first_source is not None
        assert projector.accept(_text_delta("A", index=0)) == ("A",)
        assert projector.accept(_stop(0)) == ()

        assert projector.accept(_start(1, omitted_type)) == ()
        assert projector.accept(_stop(1)) == ()

        assert projector.accept(_start(2, "text")) == ()
        second_source = projector.text_source_identity
        assert second_source is not None
        assert projector.accept(_text_delta("B", index=2)) == ("B",)
        assert projector.accept(_stop(2)) == ()

        assert projector.validate_typed_text_source_count(1) is True
        assert projector.observe_typed(message_id="sdk-message", uuid="typed-A") is True
        assert projector.typed_text_source_identity(
            text_source_ordinal=0,
            text_source_count=1,
        ) == first_source
        assert projector.validate_typed_text_source_count(1) is True
        assert projector.observe_typed(message_id="sdk-message", uuid="typed-B") is True
        assert projector.typed_text_source_identity(
            text_source_ordinal=0,
            text_source_count=1,
        ) == second_source
        assert projector.disabled is False


def test_projector_rejects_ambiguous_typed_raw_text_source_count():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, "text")) == ()
    assert projector.validate_typed_text_source_count(2) is False
    assert projector.disabled is True


    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.observe_typed(
        message_id="sdk-message",
        uuid="typed-observation",
        stop_reason="end_turn",
    ) is True
    assert projector.accept(
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
    ) == ()
    assert projector.disabled is True


def test_projector_ignores_advisor_tool_result_without_public_text():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(0, "advisor_tool_result")) == ()
    assert projector.accept(_stop(0)) == ()
    assert projector.accept(_message_end("tool_use")[0]) == ()
    assert projector.accept(_message_end("tool_use")[1]) == ()
    assert projector.disabled is False


@pytest.mark.parametrize(
    "text",
    ["没有标点的中文", "，继续输出", "x" * 4097, "中" * 262_145],
    ids=["chinese", "comma", "former-lexical-limit", "long-fragment"],
)
def test_projector_returns_text_immediately_without_a_lexical_or_length_gate(text):
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start()) == ()
    assert projector.accept(_text_delta(text)) == (text,)
    assert projector.accept(_text_delta("后续")) == ("后续",)
    assert projector.accept(_stop()) == ()
    for event in _message_end():
        assert projector.accept(event) == ()
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
    assert projector.accept(
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
    ) == ()
    assert projector.accept({"type": "message_stop"}) == ()
    assert projector.disabled is False


def test_projector_ignores_ping_between_valid_framing_events():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept({"type": "ping"}) == ()
    assert projector.accept(_start()) == ()
    assert projector.accept({"type": "ping"}) == ()
    assert projector.accept(_text_delta("visible")) == ("visible",)
    assert projector.accept({"type": "ping"}) == ()
    assert projector.accept(_stop()) == ()
    for event in _message_end():
        assert projector.accept(event) == ()
    assert projector.disabled is False


def test_projector_bounds_raw_block_sources_per_message():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    for index in range(128):
        assert projector.accept(_start(index, "thinking")) == ()
        assert projector.accept(_stop(index)) == ()
    assert len(projector._raw_sources) == 128
    assert projector.accept(_start(128, "thinking")) == ()
    assert projector.disabled is True
    assert len(projector._raw_sources) == 128


def test_projector_rejects_typed_observation_after_message_stop():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.accept(
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
    ) == ()
    assert projector.accept({"type": "message_stop"}) == ()
    assert projector.observe_typed(
        message_id="different-message",
        stop_reason="end_turn",
    ) is False
    assert projector.disabled is True


def test_projector_accepts_distinct_provider_and_observation_identities():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.observe_typed(
        message_id="sdk-message",
        uuid="sdk-observation",
    ) is True
    assert projector.disabled is False


def test_projector_rejects_foreign_typed_provider_message_identity():
    projector = _projector()
    assert projector.accept(_message_start()) == ()
    assert projector.observe_typed(
        message_id="foreign-provider-message",
        uuid="sdk-observation",
    ) is False
    assert projector.disabled is True


def test_projector_rejects_typed_text_for_thinking_or_tool_sources():
    for content_type in ("thinking", "tool_use"):
        projector = _projector()
        assert projector.accept(_message_start()) == ()
        assert projector.accept(_start(0, content_type)) == ()
        assert projector.observe_typed(message_id="sdk-message", uuid="sdk-observation") is True
        assert projector.typed_text_source_identity(
            text_source_ordinal=0,
        ) is None

        assert projector.disabled is True


def test_projector_requires_role_and_typed_message_identity():
    roleless = _projector()
    assert roleless.accept(
        {
            "type": "message_start",
            "message": {"id": "sdk-message", "stop_reason": None},
        }
    ) == ()
    assert roleless.disabled is True

    missing_identity = _projector()
    assert missing_identity.accept(_message_start()) == ()
    assert missing_identity.accept(_start()) == ()
    assert missing_identity.observe_typed() is False

    assert missing_identity.disabled is True


def test_projector_rejects_unframed_content_block_start():
    projector = _projector()

    assert projector.accept(_start()) == ()
    assert projector.disabled is True


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
    projector.accept(_message_start())
    projector.accept(_start())
    visible = []
    for text in ("正常内容 synthetic-", "secret 后续内容"):
        for fragment in projector.accept(_text_delta(text)):
            visible.extend(gate.accept(fragment))
    projector.accept(_stop())
    for event in _message_end():
        projector.accept(event)
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

    projector.accept(_message_start())
    projector.accept(_start())
    assert projector.accept(conflict) == ()
    assert projector.disabled is True
    assert projector.accept(_text_delta("safe later text")) == ()
    assert projector.accept(_stop()) == ()


def test_projector_rejects_wrong_stop_for_ignored_non_text_block():
    projector = _projector()

    assert projector.accept(_message_start()) == ()
    assert projector.accept(_start(2, "tool_use")) == ()
    assert projector.accept(_stop(3)) == ()
    assert projector.disabled is True


def test_projector_rejects_text_delta_without_matching_start():
    projector = _projector()

    assert projector.accept(_text_delta("unexpected")) == ()
    assert projector.disabled is True


def test_projector_rejects_wrong_and_duplicate_stop_permanently():
    wrong_stop = _projector()
    wrong_stop.accept(_message_start())
    wrong_stop.accept(_start())
    wrong_stop.accept(_text_delta("short answer"))
    assert wrong_stop.accept(_stop(1)) == ()
    assert wrong_stop.disabled is True

    duplicate_stop = _projector()
    duplicate_stop.accept(_message_start())
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


def test_answer_timeline_accepts_cli_stripped_result_after_split_raw_tags():
    timeline = AssistantAnswerTimeline()
    source = ("wrapped-message", 0)
    message = ("wrapped-message", None)
    body = '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>'

    for index, fragment in enumerate(
        ("<cc-mem", 'ory filenames="preferences.md">Use UTF-8.</cc-mem', "ory>")
    ):
        assert timeline.accept_delta(
            fragment,
            source_identity=source,
            message_identity=message,
            observed_identity=f"wrapped-raw-{index}",
        ) == fragment
    timeline.close_raw_source(source)

    assert timeline.validate_assistant_observations(
        [(body, source, message, None)]
    )
    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="wrapped-assistant",
    ) == ""
    assert timeline.accept_result(
        "Use UTF-8.",
        source_identity=source,
        message_identity=message,
        result_identity="wrapped-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == body


@pytest.mark.parametrize("body", [
    '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>',
    '<cc-memory中文>Use UTF-8.</cc-memory中文>',
])
def test_answer_timeline_accepts_cli_stripped_result_for_typed_only_body(body):
    timeline = AssistantAnswerTimeline()
    source = ("typed-only-message", 0)
    message = ("typed-only-message", None)
    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
    ) == body
    assert timeline.accept_result(
        "Use UTF-8.",
        source_identity=source,
        message_identity=message,
        result_identity="typed-only-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == body


def test_answer_timeline_accepts_cli_stripped_result_when_typed_precedes_raw():
    timeline = AssistantAnswerTimeline()
    source = ("typed-before-raw-message", 0)
    message = ("typed-before-raw-message", None)
    body = '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>'

    assert timeline.establish_raw_source(source, message_identity=message)
    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="typed-before-raw-assistant",
    ) == body
    assert timeline.accept_delta(
        body,
        source_identity=source,
        message_identity=message,
        observed_identity="typed-before-raw-delta",
    ) == ""
    timeline.close_raw_source(source)
    assert timeline.accept_result(
        "Use UTF-8.",
        source_identity=source,
        message_identity=message,
        result_identity="typed-before-raw-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == body


def test_answer_timeline_normalizes_result_against_only_latest_text_source():
    timeline = AssistantAnswerTimeline()
    message = ("multiple-text-message", None)
    first_source = ("multiple-text-message", 0)
    second_source = ("multiple-text-message", 1)
    body = '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>'

    assert timeline.accept_assistant(
        "Earlier text.", source_identity=first_source, message_identity=message
    ) == "Earlier text."
    assert timeline.accept_assistant(
        body, source_identity=second_source, message_identity=message
    ) == body
    assert timeline.accept_result(
        "Use UTF-8.",
        source_identity=second_source,
        message_identity=message,
        result_identity="multiple-text-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == "Earlier text." + body


def test_answer_timeline_normalizes_long_typed_result_with_bounded_coverage():
    timeline = AssistantAnswerTimeline()
    source = ("long-wrapped-message", 0)
    message = ("long-wrapped-message", None)
    body_text = "x" * (RUN_RESULT_MAX_BYTES + 4096)
    body = f'<cc-memory filenames="preferences.md">{body_text}</cc-memory>'

    assert timeline.accept_assistant(
        body,
        source_identity=source,
        message_identity=message,
    ) == body
    assert timeline._sources[0].coverage_truncated is True
    assert timeline.accept_result(
        body_text,
        source_identity=source,
        message_identity=message,
        result_identity="long-wrapped-result",
        terminal_reason="end_turn",
    ) == ""
    assert timeline.disabled is False
    assert timeline.text == body
    assert timeline._sources[0].normalized_result_length == len(body_text)
    assert timeline._sources[0].normalized_result_digest


def test_answer_timeline_rejects_changed_or_foreign_cli_normalized_result():
    body = '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>'
    source = ("bound-message", 0)
    message = ("bound-message", None)

    changed = AssistantAnswerTimeline()
    assert changed.accept_assistant(
        body, source_identity=source, message_identity=message
    ) == body
    assert changed.accept_result(
        "Use UTF-8!",
        source_identity=source,
        message_identity=message,
        result_identity="changed-result",
        terminal_reason="end_turn",
    ) == ""
    assert changed.disabled is True

    foreign = AssistantAnswerTimeline()
    assert foreign.accept_assistant(
        body, source_identity=source, message_identity=message
    ) == body
    assert foreign.accept_result(
        "Use UTF-8.",
        source_identity=("foreign-message", 0),
        message_identity=("foreign-message", None),
        result_identity="foreign-result",
        terminal_reason="end_turn",
    ) == ""
    assert foreign.disabled is True

    extended = AssistantAnswerTimeline()
    assert extended.accept_assistant(
        body, source_identity=source, message_identity=message
    ) == body
    assert extended.accept_delta(
        " extra",
        source_identity=source,
        message_identity=message,
        observed_identity="body-extension",
    ) == " extra"
    assert extended.accept_result(
        "Use UTF-8.",
        source_identity=source,
        message_identity=message,
        result_identity="stale-normalized-result",
        terminal_reason="end_turn",
    ) == ""
    assert extended.disabled is True

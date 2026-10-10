import asyncio

import pytest

from app.executors.public_answer_stream import (
    PublicAnswerCoalescer,
    PublicAnswerStreamGate,
)
from app.platform.public_payload import sanitize_public_text


IDENTITY = "mcp__tenant-server__search"
CALL_ID = "mcp-call-1"


SPLIT_SECRET_TEXTS = (
    'client_secret="opaque12345"',
    "api-key='opaque12345'",
    "access_token=opaque12345",
    'refresh-token: "opaque12345"',
    "auth_header='opaque12345'",
    'authorization: "opaque12345"',
    "private_key=opaque12345",
    "Bearer abcdefgh1",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature12345",
)


def test_sanitizer_prefix_and_token_splits_are_fail_closed_and_parity_safe():
    for secret in SPLIT_SECRET_TEXTS:
        for split in range(1, len(secret)):
            gate = PublicAnswerStreamGate(
                private_replacements={},
                sanitizer=sanitize_public_text,
                max_private_token_chars=64,
            )
            first = gate.accept(f"Before {secret[:split]}")
            second = gate.accept(f"{secret[split:]} after")
            finished = gate.finish(final_text=f"Before {secret} after", release=True)
            public_text = "".join((*first, *second, *finished.chunks))
            assert secret not in public_text
            assert "Before" in public_text
            assert "after" in public_text
            assert "[redacted-secret]" in public_text


def test_stateful_assignment_sanitizer_holds_split_secret_values_and_matches_terminal():
    cases = (
        (
            'client_secret => "opaque value!/$-with.punctuation"',
            "opaque value!/$-with.punctuation",
        ),
        ("'authorization' -> 'opaque,value;with spaces'", "opaque,value;with spaces"),
        ("access_token=opaque.value-with.punctuation", "opaque.value-with.punctuation"),
    )
    for secret, raw_value in cases:
        for first_split in range(1, len(secret) - 1):
            for second_split in range(first_split + 1, len(secret)):
                gate = PublicAnswerStreamGate(
                    private_replacements={},
                    sanitizer=sanitize_public_text,
                    max_private_token_chars=128,
                )
                outputs = [
                    *gate.accept(f"Before {secret[:first_split]}"),
                    *gate.accept(secret[first_split:second_split]),
                    *gate.accept(f"{secret[second_split:]} after"),
                ]
                finished = gate.finish(
                    final_text=f"Before {secret} after", release=True
                )
                outputs.extend(finished.chunks)
                public_text = "".join(outputs)
                assert raw_value not in public_text
                assert secret not in public_text
                assert "Before" in public_text
                assert "after" in public_text
                assert "[redacted-secret]" in public_text
                assert finished.final_text == sanitize_public_text(
                    f"Before {secret} after"
                )


def test_stateful_assignment_sanitizer_recovers_after_bounded_fragment_failure():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=sanitize_public_text,
        max_private_token_chars=32,
    )
    assert gate.accept('access_token="') == ()
    assert gate.accept("x" * 64) == ("[content unavailable]",)
    assert gate.failed is False
    assert gate.failure_reason == "sanitizer_bound_exceeded"
    assert gate.accept(" later") == (" ",)

    finished = gate.finish(
        final_text='access_token="' + ("x" * 64) + " later",
        release=True,
    )

    assert finished.chunks == ("later",)
    assert finished.final_text == "[content unavailable] later"


def _sanitize(value):
    return value if isinstance(value, str) and "raw-secret" not in value else ""


def _gate(**kwargs):
    return PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        **kwargs,
    )


@pytest.mark.parametrize("release_tool", [False, True])
def test_assistant_text_is_preserved_independently_of_tool_completion(release_tool):
    gate = _gate()
    invocation = ("builtin", "Read", CALL_ID)
    before = gate.accept("Before. ")
    gate.seal(invocation_key=invocation)
    during = gate.accept("During. ")
    if release_tool:
        assert gate.release_after_verified_capability(invocation)
    after = gate.accept("After. ")
    finished = gate.finish(final_text="Before. During. After. ", release=True)

    assert "".join((*before, *during, *after, *finished.chunks)) == (
        "Before. During. After. "
    )
    assert finished.final_text == "Before. During. After. "
    assert gate.failed is False


def test_sensitive_value_split_across_tool_boundary_preserves_surrounding_text():
    endpoint = "https://private.example/mcp"
    invocation = ("builtin", "Read", CALL_ID)
    for split in range(1, len(endpoint)):
        gate = PublicAnswerStreamGate(
            private_replacements={endpoint: "[private endpoint]"},
            sanitizer=_sanitize,
            max_private_token_chars=64,
        )
        before = gate.accept(f"Before {endpoint[:split]}")
        gate.seal(invocation_key=invocation)
        during = gate.accept(f"{endpoint[split:]} after")
        assert gate.release_after_verified_capability(invocation)
        finished = gate.finish(final_text=f"Before {endpoint} after", release=True)
        visible = "".join((*before, *during, *finished.chunks))

        assert visible == "Before [private endpoint] after"
        assert finished.final_text == visible
        assert endpoint not in visible
        assert gate.failed is False


def test_unsealed_stream_emits_ordinary_text_and_redacts_full_known_identity():
    gate = _gate()

    first = gate.accept("ordinary answer. ")
    second = gate.accept(f"Used {IDENTITY} safely.")
    finished = gate.finish(
        final_text=f"ordinary answer. Used {IDENTITY} safely.", release=True
    )

    assert first == ("ordinary answer. ",)
    assert second == ("Used external tool ",)
    assert finished.chunks == ("safely.",)
    assert finished.final_text == "ordinary answer. Used external tool safely."
    assert IDENTITY not in "".join((*first, *second, *finished.chunks))


@pytest.mark.parametrize(
    ("secret", "split"),
    [
        ("api_key=sk-abcdefghi12", 9),
        ("Bearer abcdefgh1", 7),
        ("abcdefghij.klmnopqrst.uvwxyzabcd", 21),
    ],
)
def test_sanitizer_owned_secret_split_across_chunks_is_never_published(secret, split):
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=sanitize_public_text,
        max_private_token_chars=64,
    )

    first = gate.accept(f"Before {secret[:split]}")
    second = gate.accept(f"{secret[split:]} after")
    finished = gate.finish(final_text=f"Before {secret} after", release=True)

    public_text = "".join((*first, *second, *finished.chunks))
    assert secret not in public_text
    assert "Before" in public_text
    assert "after" in public_text
    assert "[redacted-secret]" in public_text


def test_progressive_stream_keeps_delivered_text_when_terminal_text_differs():
    gate = _gate()

    first = gate.accept("safe prefix mcp__")
    second = gate.accept("not-the-private-token ")
    finished = gate.finish(final_text="different terminal summary", release=True)

    assert first == ("safe prefix ",)
    assert second == ("mcp__not-the-private-token ",)
    assert finished.chunks == ()
    assert finished.final_text == "safe prefix mcp__not-the-private-token "
    assert gate.failed is False


def test_progressive_stream_appends_terminal_suffix_without_replay():
    gate = _gate()

    published = gate.accept("progressive ")
    finished = gate.finish(final_text="progressive answer", release=True)

    assert published == ("progressive ",)
    assert finished.chunks == ("answer",)
    assert finished.final_text == "progressive answer"


def test_progressive_stream_preserves_delivered_terminal_edge_whitespace():
    gate = _gate()

    published = gate.accept("progressive answer \n")
    finished = gate.finish(final_text="progressive answer", release=True)

    assert published == ("progressive answer \n",)
    assert gate.failed is False
    assert finished.chunks == ()
    assert finished.final_text == "progressive answer \n"


def test_progressive_stream_continues_past_previous_cumulative_bound():
    gate = PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    first = gate.accept("safe prefix ")
    second = gate.accept("crosses the old bound")
    finished = gate.finish(final_text="safe prefix crosses the old bound", release=True)

    assert "".join((*first, *second, *finished.chunks)) == "safe prefix crosses the old bound"
    assert finished.final_text == "safe prefix crosses the old bound"
    assert gate.failed is False


def test_private_token_learned_before_later_text_is_redacted_progressively():
    gate = _gate()

    before = gate.accept("Safe answer before invocation. ")
    gate.register_private_replacements({CALL_ID: "tool invocation"})
    after = gate.accept(f"Used {CALL_ID} safely. ")
    finished = gate.finish(
        final_text=f"Safe answer before invocation. Used {CALL_ID} safely. ",
        release=True,
    )

    public_text = "".join((*before, *after, *finished.chunks))
    assert CALL_ID not in public_text
    assert public_text == (
        "Safe answer before invocation. Used tool invocation safely. "
    )


def test_private_token_learned_across_published_boundary_is_not_reconstructed():
    gate = _gate()
    dynamic_call_id = "call/id"

    published = gate.accept("Before call/")
    gate.register_private_replacements({dynamic_call_id: "tool invocation"})
    later = gate.accept("id after")
    finished = gate.finish(final_text="different terminal", release=True)

    public_text = "".join((*published, *later, *finished.chunks))
    assert public_text == "Before call/tool invocation after"
    assert dynamic_call_id not in public_text
    assert gate.failed is False


def test_withheld_private_suffix_keeps_its_provider_part_owner_across_source_switch():
    first_source = ("provider-a", None)
    second_source = ("provider-b", None)
    gate = PublicAnswerStreamGate(
        private_replacements={"call/id": "tool invocation"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    before = gate.accept_routed("Before call/", source_identity=first_source)
    after = gate.accept_routed("id after", source_identity=second_source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=second_source,
    )

    routed = (*before, *after, *terminal)
    by_source = {
        first_source: "".join(text for owner, text in routed if owner == first_source),
        second_source: "".join(text for owner, text in routed if owner == second_source),
    }
    assert by_source == {
        first_source: "Before tool invocation",
        second_source: " after",
    }
    assert "".join(text for _owner, text in routed) == "Before tool invocation after"
    assert "call/id" not in "".join(text for _owner, text in routed)
    assert finished.final_text == "Before tool invocation after"
    assert gate.failed is False


def test_routed_source_switch_preserves_safe_suffix_spans_and_terminal_tail():
    work_source = ("provider-a", "work-message")
    answer_source = ("provider-b", "answer-message")
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    work = gate.accept_routed("Checking sources.", source_identity=work_source)
    answer = gate.accept_routed("Final answer.", source_identity=answer_source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=answer_source,
    )
    routed = (*work, *answer, *terminal)
    by_source = {
        work_source: "".join(text for owner, text in routed if owner == work_source),
        answer_source: "".join(text for owner, text in routed if owner == answer_source),
    }

    assert work == ((work_source, "Checking "),)
    assert by_source == {
        work_source: "Checking sources.",
        answer_source: "Final answer.",
    }
    assert finished.final_text == "Checking sources.Final answer."
    assert gate.failed is False


def test_source_switch_sanitizes_with_following_text_context():
    work_source = ("provider-a", "work-message")
    answer_source = ("provider-b", "answer-message")
    safe_token = "x" * 80
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    work = gate.accept_routed("Checking", source_identity=work_source)
    answer = gate.accept_routed(
        safe_token + " before the next step.",
        source_identity=answer_source,
    )
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=answer_source,
    )
    routed = (*work, *answer, *terminal)

    assert "".join(text for owner, text in routed if owner == work_source) == "Checking"
    assert "".join(text for owner, text in routed if owner == answer_source) == (
        safe_token + " before the next step."
    )
    assert finished.final_text == "Checking" + safe_token + " before the next step."
    assert gate.failed is False


def test_routed_suffix_can_span_multiple_provider_sources():
    first = ("provider-a", "message-1")
    second = ("provider-b", "message-2")
    third = ("provider-c", "message-3")
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    routed = [
        *gate.accept_routed("Checking sources.", source_identity=first),
        *gate.accept_routed("Final", source_identity=second),
        *gate.accept_routed(" answer.", source_identity=third),
    ]
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=third,
    )
    routed.extend(terminal)

    by_source = {
        source: "".join(text for owner, text in routed if owner == source)
        for source in (first, second, third)
    }
    assert by_source == {
        first: "Checking sources.",
        second: "Final",
        third: " answer.",
    }
    assert finished.final_text == "Checking sources.Final answer."


def test_routed_full_short_suffix_is_released_to_its_source():
    source = ("provider-a", "short-answer")
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    published = gate.accept_routed("Answer.", source_identity=source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=("fallback", None),
    )

    assert not published
    assert terminal == ((source, "Answer."),)
    assert finished.final_text == "Answer."


def test_variable_length_private_replacement_stays_with_token_start_source():
    work_source = ("provider-a", "work-message")
    answer_source = ("provider-b", "answer-message")
    replacement = "private invocation identity"
    gate = PublicAnswerStreamGate(
        private_replacements={"call/id": replacement},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    routed = [
        *gate.accept_routed("Before call/", source_identity=work_source),
        *gate.accept_routed("id after", source_identity=answer_source),
    ]
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=answer_source,
    )
    routed.extend(terminal)

    by_source = {
        work_source: "".join(text for owner, text in routed if owner == work_source),
        answer_source: "".join(text for owner, text in routed if owner == answer_source),
    }
    assert by_source == {
        work_source: "Before " + replacement,
        answer_source: " after",
    }
    assert "call/id" not in "".join(text for _owner, text in routed)
    assert finished.final_text == "Before " + replacement + " after"
    assert gate.failed is False


def test_dynamic_private_replacement_keeps_cross_source_pending_owner():
    first_source = ("provider-a", "work-message")
    second_source = ("provider-b", "tool-message")
    third_source = ("provider-c", "answer-message")
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    before = gate.accept_routed("Before toolu_", source_identity=first_source)
    split = gate.accept_routed("private", source_identity=second_source)
    gate.register_private_replacements({"toolu_private": "private invocation"})
    after = gate.accept_routed(" after.", source_identity=third_source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=third_source,
    )
    routed = (*before, *split, *after, *terminal)

    assert "toolu_private" not in "".join(text for _owner, text in routed)
    assert "".join(text for owner, text in routed if owner == first_source) == (
        "Before private invocation"
    )
    assert "".join(text for owner, text in routed if owner == third_source) == " after."
    assert finished.final_text == "Before private invocation after."
    assert gate.failed is False


def test_cross_source_generic_sanitizer_length_change_fails_closed():
    first_source = ("provider-a", "work-message")
    second_source = ("provider-b", "answer-message")

    def redact_private_value(value):
        return value.replace("private-value", "[redacted]")

    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=redact_private_value,
        max_private_token_chars=64,
    )

    before = gate.accept_routed("Prior private-", source_identity=first_source)
    ambiguous = gate.accept_routed("value after.", source_identity=second_source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=second_source,
    )
    routed = (*before, *ambiguous, *terminal)

    assert "private-value" not in "".join(text for _owner, text in routed)
    assert not ambiguous and not terminal
    assert gate.failed is True
    assert gate.failure_reason == "upstream_projection_failed"
    assert finished.final_text == "Prior "


def test_terminal_generic_sanitizer_length_change_across_sources_fails_closed():
    first_source = ("provider-a", "work-message")
    second_source = ("provider-b", "answer-message")

    def redact_private_value(value):
        return value.replace("private-value", "[redacted]")

    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=redact_private_value,
        max_private_token_chars=64,
    )

    assert not gate.accept_routed("private-", source_identity=first_source)
    assert not gate.accept_routed("value", source_identity=second_source)
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=second_source,
    )

    assert not terminal
    assert finished.final_text == ""
    assert gate.failed is True
    assert gate.failure_reason == "upstream_projection_failed"


def test_routed_private_token_split_within_one_source_never_leaks():
    source = ("provider-a", "answer-message")
    gate = PublicAnswerStreamGate(
        private_replacements={"call/id": "private invocation"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    routed = [
        *gate.accept_routed("Before call/", source_identity=source),
        *gate.accept_routed("id after", source_identity=source),
    ]
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=("fallback", None),
    )
    routed.extend(terminal)

    assert "".join(text for owner, text in routed if owner == source) == (
        "Before private invocation after"
    )
    assert "call/id" not in "".join(text for _owner, text in routed)
    assert all(owner == source for owner, _text in routed)
    assert finished.final_text == "Before private invocation after"


def test_dynamic_private_identity_registration_checks_all_routed_public_parts():
    gate = _gate()
    source = ("provider-a", None)

    published = gate.accept_routed("Publicly visible call/id. ", source_identity=source)
    gate.register_private_replacements({"call/id": "tool invocation"})
    later = gate.accept_routed("id after", source_identity=("provider-b", None))
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=source,
    )

    assert "call/id" in "".join(text for _owner, text in published)
    assert gate.failed is True
    assert gate.failure_reason == "private_token_already_published"
    assert not later and not terminal
    assert finished.chunks == ()


def test_dynamic_private_identity_detection_survives_a_projection_failure():
    gate = _gate()
    source = ("provider-a", None)
    published = gate.accept_routed("Publicly visible call/id. ", source_identity=source)
    assert "call/id" in "".join(text for _owner, text in published)

    gate.fail_closed()
    gate.register_private_replacements({"call/id": "tool invocation"})

    assert gate.failed is True
    assert gate.failure_reason == "upstream_projection_failed"
    assert gate.private_token_exposed is True
    assert gate.accept("must stay closed") == ()
    finished, terminal = gate.finish_routed(
        final_text="", release=True, fallback_source_identity=source,
    )
    assert not terminal and not finished.chunks and not finished.final_text


def test_unrelated_dynamic_token_prefix_does_not_fail_publication():
    gate = _gate()

    before = gate.accept("inspect")
    gate.register_private_replacements({"toolu_private": "tool invocation"})
    after = gate.accept(" the workspace")
    finished = gate.finish(final_text="different terminal", release=True)

    assert "".join((*before, *after, *finished.chunks)) == "inspect the workspace"
    assert gate.failed is False


def test_known_endpoint_split_across_initial_chunks_is_never_public():
    endpoint = "https://private.example/mcp"

    for split in range(1, len(endpoint)):
        candidate = PublicAnswerStreamGate(
            private_replacements={endpoint: "external tool endpoint"},
            sanitizer=_sanitize,
            max_private_token_chars=64,
        )
        before = candidate.accept(f"Before {endpoint[:split]}")
        after = candidate.accept(f"{endpoint[split:]} after")
        finished = candidate.finish(final_text="different terminal", release=True)
        public_text = "".join((*before, *after, *finished.chunks))
        assert public_text == "Before external tool endpoint after"
        assert endpoint not in public_text
        assert candidate.failed is False


@pytest.mark.parametrize("split", range(1, len(IDENTITY)))
def test_known_identity_split_across_initial_chunks_is_never_public(split):
    gate = _gate()

    before = gate.accept(f"Before {IDENTITY[:split]}")
    after = gate.accept(f"{IDENTITY[split:]} after")
    finished = gate.finish(final_text=f"Before {IDENTITY} after", release=True)

    public_text = "".join((*before, *after, *finished.chunks))
    assert public_text == "Before external tool after"
    assert finished.final_text == "Before external tool after"
    assert IDENTITY not in public_text


@pytest.mark.parametrize(
    ("token_kind", "split"),
    [
        ("identity", 1),
        ("identity", len(IDENTITY) // 2),
        ("identity", len(IDENTITY) - 1),
        ("call_id", 1),
        ("call_id", len(CALL_ID) // 2),
        ("call_id", len(CALL_ID) - 1),
    ],
)
def test_private_token_split_at_capability_boundary_never_replays_published_bytes(
    token_kind, split
):
    gate = _gate()
    token = IDENTITY if token_kind == "identity" else CALL_ID

    published = gate.accept(f"Before {token[:split]}")
    gate.seal(
        {CALL_ID: "tool invocation"},
        invocation_key=("mcp", IDENTITY, CALL_ID),
    )
    later = gate.accept(f"{token[split:]} after")
    gate.release_after_verified_capability(("mcp", IDENTITY, CALL_ID))
    finished = gate.finish(final_text=f"Before {token} after", release=True)

    public_text = "".join((*published, *later, *finished.chunks))
    replacement = "external tool" if token_kind == "identity" else "tool invocation"
    assert public_text == f"Before {replacement} after"
    assert finished.final_text == public_text
    assert token not in public_text
    assert token not in finished.final_text


def test_multiple_overlapping_calls_added_during_stream_project_safely_once():
    gate = _gate()
    first_call, second_call = "call-alpha", "call-alphabet"

    published = gate.accept("Before call-al")
    gate.seal(
        {first_call: "tool invocation"},
        invocation_key=("mcp", IDENTITY, first_call),
    )
    gate.seal(
        {second_call: "tool invocation"},
        invocation_key=("mcp", IDENTITY, second_call),
    )
    later = gate.accept("phabet after")
    gate.release_after_verified_capability(("mcp", IDENTITY, first_call))
    gate.release_after_verified_capability(("mcp", IDENTITY, second_call))
    finished = gate.finish(final_text=f"Before {second_call} after", release=True)
    repeated = gate.finish(final_text="must not replay", release=True)

    public_text = "".join((*published, *later, *finished.chunks))
    assert public_text == "Before tool invocation after"
    assert first_call not in public_text and second_call not in public_text
    assert finished.final_text == public_text
    assert repeated.chunks == () and repeated.final_text == ""


def test_capability_lifecycle_does_not_defer_safe_assistant_narration():
    gate = _gate()

    before = gate.accept("I will inspect the workspace. ")
    gate.seal(
        {CALL_ID: "tool invocation"},
        capability_boundary=True,
        invocation_key=("mcp", IDENTITY, CALL_ID),
    )
    during = gate.accept("Inspection is in progress. ")
    gate.release_after_verified_capability(("mcp", IDENTITY, CALL_ID))
    after = gate.accept(f"The {CALL_ID} completed safely.")
    finished = gate.finish(
        final_text="A different structured terminal summary.",
        release=True,
    )

    public_text = "".join((*before, *during, *after, *finished.chunks))
    assert public_text == (
        "I will inspect the workspace. Inspection is in progress. "
        "The tool invocation completed safely."
    )
    assert finished.final_text == public_text


def test_capability_boundary_preserves_safe_sanitizer_pending_text():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=sanitize_public_text,
    )
    invocation_key = ("builtin", "Read", CALL_ID)

    before = gate.accept("safe answer")
    gate.seal({CALL_ID: "tool invocation"}, invocation_key=invocation_key)
    assert gate.release_after_verified_capability(invocation_key) is True
    finished = gate.finish(final_text="safe answer", release=True)

    assert before == ("safe ",)
    assert finished.chunks == ("answer",)
    assert finished.final_text == "safe answer"


def test_overlapping_capability_invocations_preserve_each_assistant_fragment():
    gate = _gate()

    before = gate.accept("Before tools. ")
    gate.seal(
        {"call-one": "tool invocation"},
        invocation_key=("builtin", "Read", "call-one"),
    )
    gate.seal(
        {"call-two": "tool invocation"},
        invocation_key=("builtin", "Read", "call-two"),
    )
    during_first = gate.accept("Both tools running. ")
    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-one")) is True
    )
    during_second = gate.accept("One tool running. ")
    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-two")) is True
    )
    after = gate.accept("After tools.")
    body = "Before tools. Both tools running. One tool running. After tools."
    finished = gate.finish(final_text=body, release=True)

    assert "".join((*before, *during_first, *during_second, *after, *finished.chunks)) == body
    assert finished.final_text == body


def test_failed_projection_still_releases_exact_tool_ownership():
    gate = _gate()
    invocation_key = ("builtin", "Read", "call-one")

    gate.fail_closed()
    gate.seal(
        {"call-one": "tool invocation"},
        invocation_key=invocation_key,
    )

    assert gate.release_after_verified_capability(invocation_key) is True
    assert gate.release_after_verified_capability(invocation_key) is False
    assert gate.failed is True
    assert gate.accept("must remain private") == ()
    assert gate.finish(final_text="must remain private", release=True).final_text == ""


def test_finished_gate_cannot_acquire_new_tool_ownership():
    gate = _gate()
    invocation_key = ("builtin", "Read", "call-one")

    gate.finish(final_text="done", release=True)
    gate.seal(
        {"call-one": "tool invocation"},
        invocation_key=invocation_key,
    )

    assert gate.release_after_verified_capability(invocation_key) is False


def test_unmatched_completion_does_not_release_another_invocation():
    gate = _gate()
    active_key = ("builtin", "Read", "call-one")

    gate.seal(
        {"call-one": "tool invocation"},
        invocation_key=active_key,
    )

    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-two")) is False
    )
    assert gate.release_after_verified_capability(active_key) is True
    assert gate.release_after_verified_capability(active_key) is False
    after = gate.accept("safe after")
    finished = gate.finish(final_text="safe after", release=True)

    assert "".join((*after, *finished.chunks)) == "safe after"


def test_failed_terminal_does_not_retract_already_published_narration():
    gate = _gate()

    published = gate.accept("I will inspect the workspace. ")
    finished = gate.finish(final_text="", release=False)

    assert published == ("I will inspect the workspace. ",)
    assert finished.chunks == ()
    assert finished.final_text == ""


def test_capability_bound_does_not_disable_long_public_timeline():
    gate = PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )

    parts = [gate.accept("before tool ")]
    gate.seal(
        capability_boundary=True,
        invocation_key=("builtin", "Read", "call-one"),
    )
    parts.append(gate.accept("during " + ("x" * 64)))
    gate.release_after_verified_capability(("builtin", "Read", "call-one"))
    parts.append(gate.accept("end " + ("y" * 64)))
    body = "before tool " + "during " + ("x" * 64) + "end " + ("y" * 64)
    finished = gate.finish(final_text=body, release=True)

    assert "".join((*parts[0], *parts[1], *parts[2], *finished.chunks)) == body
    assert gate.failed is False


def test_dynamic_boundary_projection_does_not_disable_long_public_answer():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )
    published: list[str] = []

    for index in range(4):
        token = f"call-{index}/secret"
        published.extend(gate.accept(f"call-{index}/"))
        gate.register_private_replacements({token: "x"})
        published.extend(gate.accept("secret " + ("z" * 32)))

    assert len("".join(published)) > 20
    assert gate.failed is False


def test_over_bound_initial_or_dynamic_private_token_fails_closed():
    initial = PublicAnswerStreamGate(
        private_replacements={"x" * 65: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
    )
    dynamic = _gate()
    published = dynamic.accept("ordinary pre-hook text")
    dynamic.seal(
        {"y" * 65: "tool invocation"},
        invocation_key=("builtin", "Read", "call-one"),
    )

    assert initial.failed is True
    assert initial.failure_reason == "private_replacement_invalid"
    assert initial.accept("must not publish") == ()
    assert initial.finish(final_text="must not publish", release=True).chunks == ()
    assert published == ("ordinary pre-hook ",)
    assert dynamic.failed is True
    assert dynamic.failure_reason == "private_replacement_invalid"
    assert dynamic.accept("sealed private text") == ()
    assert dynamic.finish(final_text="sealed private text", release=True).chunks == ()


def test_inflight_assistant_text_is_not_rejected_by_answer_length():
    gate = _gate()
    gate.seal(
        {CALL_ID: "tool invocation"},
        invocation_key=("mcp", IDENTITY, CALL_ID),
    )

    long_text = "long answer " * 20

    assert gate.accept(long_text) == (long_text,)
    gate.release_after_verified_capability(("mcp", IDENTITY, CALL_ID))
    published = gate.accept("safe answer")
    finished = gate.finish(final_text=long_text + "safe answer", release=True)

    assert gate.failed is False
    assert published == ("safe ",)
    assert finished.chunks == ("answer",)
    assert finished.final_text == long_text + "safe answer"


def test_terminal_sanitizer_fault_keeps_already_published_text():
    gate = _gate()

    assert gate.accept("safe partial") == ("safe ",)
    finished = gate.finish(final_text="raw-secret", release=True)

    assert gate.failed is False
    assert gate.failure_reason == "sanitizer_rejected"
    assert gate.projection_omissions == 1
    assert finished.chunks == ("partial",)
    assert finished.final_text == "safe partial"


def test_sanitizer_fault_replaces_fragment_and_continues():
    gate = _gate()

    assert gate.accept("raw-secret") == ("[content unavailable]",)
    assert gate.accept("safe after.") == ("safe ",)
    finished = gate.finish(final_text="raw-secretsafe after.", release=True)

    assert gate.failed is False
    assert gate.failure_reason == "sanitizer_rejected"
    assert gate.projection_omissions == 2
    assert finished.chunks == ("after.",)
    assert finished.final_text == "[content unavailable]safe after."


def test_sanitizer_exception_replaces_faulted_fragment_and_continues():
    failed_once = False

    def flaky_sanitizer(value):
        nonlocal failed_once
        if not failed_once and "omit me" in value:
            failed_once = True
            raise RuntimeError("synthetic sanitizer failure")
        return value

    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=flaky_sanitizer,
        max_private_token_chars=64,
    )

    before = gate.accept("before ")
    omitted = gate.accept("omit me")
    after = gate.accept("after.")
    finished = gate.finish(final_text="before omit meafter.", release=True)

    assert "".join((*before, *omitted, *after, *finished.chunks)) == (
        "before [content unavailable]after."
    )
    assert finished.final_text == "before [content unavailable]after."
    assert gate.failed is False
    assert gate.failure_reason == "sanitizer_failed"
    assert gate.projection_omissions == 1


def test_public_answer_continues_beyond_262145_codepoints():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=lambda value: value,
        max_private_token_chars=64,
    )
    body = "".join(f"segment-{index:06d}\\n" for index in range(30_000))


    chunks = gate.accept(body)
    finished = gate.finish(final_text=body, release=True)

    assert "".join((*chunks, *finished.chunks)) == body
    assert finished.final_text == body
    assert gate.failed is False


def test_fragmented_public_answer_keeps_projection_work_bounded():
    sanitizer_inputs: list[int] = []

    def counting_sanitizer(value):
        sanitizer_inputs.append(len(value))
        return value

    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=counting_sanitizer,
        max_private_token_chars=64,
    )
    fragments = tuple(f"fragment-{index:05d} " for index in range(20_000))
    body = "".join(fragments)

    published: list[str] = []
    for fragment in fragments:
        published.extend(gate.accept(fragment))
    accept_work = tuple(sanitizer_inputs)
    finished = gate.finish(final_text=body, release=True)

    assert len(body) > 262_145
    assert tuple(published) == fragments
    assert finished.chunks == ()
    assert finished.final_text == body
    assert gate.failed is False
    assert accept_work
    assert max(accept_work) <= len(fragments[0])
    assert sum(accept_work) <= 2 * len(body)


def test_multibyte_public_answer_continues_without_byte_cutoff():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=lambda value: value,
        max_private_token_chars=64,
    )
    body = "界" * 262_146
    chunks = gate.accept(body)
    finished = gate.finish(final_text=body, release=True)

    assert "".join((*chunks, *finished.chunks)) == body
    assert len(finished.final_text.encode("utf-8")) > 262_145
    assert gate.failed is False


@pytest.mark.asyncio
async def test_public_answer_coalescer_merges_only_adjacent_same_source_text():
    emitted: list[str] = []

    async def emit(value: str) -> bool:
        emitted.append(value)
        return True

    coalescer = PublicAnswerCoalescer(emit, window_seconds=60)
    assert await coalescer.push("one ", source_identity=("message-1", 0))
    assert await coalescer.push("two", source_identity=("message-1", 0))
    assert emitted == []

    assert await coalescer.push("three", source_identity=("message-1", 1))
    assert emitted == ["one two"]
    assert await coalescer.close(flush=True)
    assert emitted == ["one two", "three"]


@pytest.mark.asyncio
async def test_public_answer_coalescer_flushes_on_window_and_codepoint_limit():
    emitted: list[str] = []

    async def emit(value: str) -> bool:
        emitted.append(value)
        return True

    coalescer = PublicAnswerCoalescer(emit, window_seconds=0)
    assert await coalescer.push("界" * 8_193, source_identity="source")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert emitted == ["界" * 8_192, "界"]
    assert all(len(value) <= 8_192 for value in emitted)
    assert await coalescer.close(flush=True)


@pytest.mark.asyncio
async def test_public_answer_coalescer_seals_after_rejected_emission():
    emitted: list[str] = []

    async def reject(value: str) -> bool:
        emitted.append(value)
        return False

    coalescer = PublicAnswerCoalescer(reject, window_seconds=60)
    assert not await coalescer.push("x" * 8_192, source_identity="source")
    assert not await coalescer.push("late", source_identity="source")
    assert emitted == ["x" * 8_192]
    assert await coalescer.close(flush=True)


@pytest.mark.asyncio
async def test_public_answer_coalescer_rethrows_timer_emission_failure_at_barrier():
    async def fail(_value: str) -> bool:
        raise RuntimeError("synthetic callback failure")

    coalescer = PublicAnswerCoalescer(fail, window_seconds=0)
    assert await coalescer.push("pending", source_identity="source")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="synthetic callback failure"):
        await coalescer.close(flush=True)


@pytest.mark.asyncio
async def test_public_answer_coalescer_reports_cancelled_inflight_emission():
    started = asyncio.Event()
    never_release = asyncio.Event()
    emitted: list[str] = []

    async def emit(value: str) -> bool:
        started.set()
        await never_release.wait()
        emitted.append(value)
        return True

    coalescer = PublicAnswerCoalescer(emit, window_seconds=0)
    assert await coalescer.push("pending", source_identity="source")
    await asyncio.wait_for(started.wait(), timeout=1)

    assert not await asyncio.wait_for(coalescer.close(flush=True), timeout=1)
    assert emitted == []
    assert not await coalescer.push("late", source_identity="source")


def test_terminal_safe_suffix_matching_only_private_prefix_is_preserved():
    gate = PublicAnswerStreamGate(private_replacements={"report-private": "private value"}, sanitizer=_sanitize)
    source = ("answer", None)
    chunks = gate.accept_routed("Final user answer", source_identity=source)
    finish, tail = gate.finish_routed(final_text="", release=True)
    assert "".join(text for _owner, text in (*chunks, *tail)) == "Final user answer"
    assert finish.final_text == "Final user answer"

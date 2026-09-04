import pytest

from app.executors.public_answer_stream import PublicAnswerStreamGate
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
                max_sealed_chars=256,
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
                    max_sealed_chars=512,
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


def test_stateful_assignment_sanitizer_fails_closed_at_bounded_ceiling():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=sanitize_public_text,
        max_private_token_chars=32,
        max_sealed_chars=256,
    )
    assert gate.accept('access_token="') == ()
    assert gate.accept("x" * 64) == ()
    assert gate.failed is True
    assert gate.failure_reason == "sanitizer_bound_exceeded"
    assert (
        gate.finish(final_text='access_token="' + ("x" * 64), release=True).chunks == ()
    )


def _sanitize(value):
    return value if isinstance(value, str) and "raw-secret" not in value else ""


def _gate(**kwargs):
    return PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=128,
        **kwargs,
    )


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
        max_sealed_chars=256,
    )

    first = gate.accept(f"Before {secret[:split]}")
    second = gate.accept(f"{secret[split:]} after")
    finished = gate.finish(final_text=f"Before {secret} after", release=True)

    public_text = "".join((*first, *second, *finished.chunks))
    assert secret not in public_text
    assert "Before" in public_text
    assert "after" in public_text
    assert "[redacted-secret]" in public_text


def test_progressive_stream_keeps_public_timeline_when_terminal_text_differs():
    gate = _gate()

    first = gate.accept("safe prefix mcp__")
    second = gate.accept("not-the-private-token ")
    finished = gate.finish(final_text="different terminal summary", release=True)

    assert first == ("safe prefix ",)
    assert second == ("mcp__not-the-private-token ",)
    assert finished.chunks == ()
    assert finished.final_text == "safe prefix mcp__not-the-private-token "
    assert gate.failed is False


def test_progressive_stream_does_not_replay_terminal_result_as_body():
    gate = _gate()

    published = gate.accept("progressive ")
    finished = gate.finish(final_text="progressive answer", release=True)

    assert published == ("progressive ",)
    assert finished.chunks == ()
    assert finished.final_text == "progressive "


def test_progressive_stream_accepts_terminal_edge_whitespace_normalization():
    gate = _gate()

    published = gate.accept("progressive answer \n")
    finished = gate.finish(final_text="progressive answer", release=True)

    assert published == ("progressive answer \n",)
    assert gate.failed is False
    assert finished.chunks == ()
    assert finished.final_text == "progressive answer \n"


def test_progressive_stream_enforces_cumulative_bound_before_publication():
    gate = PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=16,
    )

    published = gate.accept("safe prefix ")
    rejected = gate.accept("crosses the bound")

    assert published == ("safe prefix ",)
    assert rejected == ()
    assert gate.failed is True
    assert (
        gate.finish(final_text="safe prefix crosses the bound", release=True).chunks
        == ()
    )


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
            max_sealed_chars=128,
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
    assert public_text == "Before "
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
    assert public_text == "Before "
    assert first_call not in public_text and second_call not in public_text
    assert finished.final_text == "Before "
    assert repeated.chunks == () and repeated.final_text == ""


def test_capability_lifecycle_does_not_defer_safe_assistant_narration():
    gate = _gate()

    before = gate.accept("I will inspect the workspace. ")
    gate.seal(
        {CALL_ID: "tool invocation"},
        capability_boundary=True,
        invocation_key=("mcp", IDENTITY, CALL_ID),
    )
    assert gate.accept("private tool output") == ()
    gate.release_after_verified_capability(("mcp", IDENTITY, CALL_ID))
    after = gate.accept(f"The {CALL_ID} completed safely.")
    finished = gate.finish(
        final_text="A different structured terminal summary.",
        release=True,
    )

    public_text = "".join((*before, *after, *finished.chunks))
    assert public_text == (
        "I will inspect the workspace. The tool invocation completed safely."
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
    assert gate.accept("private tool output") == ()
    assert gate.release_after_verified_capability(invocation_key) is True
    finished = gate.finish(final_text="safe answer", release=True)

    assert before == ("safe ",)
    assert finished.chunks == ("answer",)
    assert finished.final_text == "safe answer"


def test_overlapping_capability_invocations_keep_disclosure_closed_until_all_complete():
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
    assert gate.accept("private concurrent output") == ()
    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-one")) is True
    )
    assert gate.accept("still private") == ()
    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-two")) is True
    )
    after = gate.accept("After tools.")
    finished = gate.finish(final_text="different terminal", release=True)

    assert "".join((*before, *after, *finished.chunks)) == (
        "Before tools. After tools."
    )


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


def test_unmatched_completion_cannot_reopen_an_active_invocation():
    gate = _gate()
    active_key = ("builtin", "Read", "call-one")

    gate.seal(
        {"call-one": "tool invocation"},
        invocation_key=active_key,
    )

    assert (
        gate.release_after_verified_capability(("builtin", "Read", "call-two")) is False
    )
    assert gate.accept("private file content") == ()
    assert gate.release_after_verified_capability(active_key) is True
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


def test_capability_bound_is_cumulative_across_the_public_timeline():
    gate = PublicAnswerStreamGate(
        private_replacements={IDENTITY: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=24,
    )

    assert gate.accept("before tool ") == ("before tool ",)
    gate.seal(
        capability_boundary=True,
        invocation_key=("builtin", "Read", "call-one"),
    )
    assert gate.accept("private tool output") == ()
    gate.release_after_verified_capability(("builtin", "Read", "call-one"))
    assert gate.accept("after tool ") == ("after tool ",)
    assert gate.accept("overflow") == ()
    assert gate.failed is True


def test_dynamic_boundary_projection_cannot_bypass_actual_public_bound():
    gate = PublicAnswerStreamGate(
        private_replacements={},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=20,
    )
    published: list[str] = []

    for index in range(4):
        token = f"call-{index}/secret"
        published.extend(gate.accept(f"call-{index}/"))
        gate.register_private_replacements({token: "x"})
        published.extend(gate.accept("secret "))

    assert len("".join(published)) <= 20
    assert gate.failed is True
    assert gate.accept("must not publish") == ()


def test_over_bound_initial_or_dynamic_private_token_fails_closed():
    initial = PublicAnswerStreamGate(
        private_replacements={"x" * 65: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=128,
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


def test_inflight_text_is_discarded_without_consuming_the_public_bound():
    gate = _gate()
    gate.seal(
        {CALL_ID: "tool invocation"},
        invocation_key=("mcp", IDENTITY, CALL_ID),
    )

    assert gate.accept("x" * 129) == ()
    gate.release_after_verified_capability(("mcp", IDENTITY, CALL_ID))
    published = gate.accept("safe answer")
    finished = gate.finish(final_text="safe answer", release=True)

    assert gate.failed is False
    assert "".join((*published, *finished.chunks)) == "safe answer"
    assert finished.final_text == "safe answer"


def test_unsafe_sanitizer_result_fails_closed_without_raw_text():
    gate = _gate()

    assert gate.accept("raw-secret") == ()
    assert gate.failed is True
    assert gate.failure_reason == "sanitizer_rejected"
    assert gate.finish(final_text="raw-secret", release=True).chunks == ()

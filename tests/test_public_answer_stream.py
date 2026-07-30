import pytest

from app.executors.public_answer_stream import PublicAnswerStreamGate


IDENTITY = "mcp__tenant-server__search"
CALL_ID = "mcp-call-1"


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
    assert second == ("Used external tool safely.",)
    assert finished.chunks == ()
    assert finished.final_text == "ordinary answer. Used external tool safely."
    assert IDENTITY not in "".join((*first, *second, *finished.chunks))


def test_unsealed_stream_withholds_only_a_possible_private_token_prefix():
    gate = _gate()

    first = gate.accept("safe prefix mcp__")
    second = gate.accept("not-the-private-token ")
    finished = gate.finish(final_text="safe terminal", release=True)

    assert first == ("safe prefix ",)
    assert second == ("mcp__not-the-private-token ",)
    assert finished.chunks == ()
    assert finished.final_text == "safe terminal"


@pytest.mark.parametrize("split", [1, len(IDENTITY) // 2, len(IDENTITY) - 1])
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
def test_private_token_split_at_seal_boundary_never_replays_published_bytes(
    token_kind, split
):
    gate = _gate()
    token = IDENTITY if token_kind == "identity" else CALL_ID
    replacement = "external tool" if token_kind == "identity" else "tool invocation"

    published = gate.accept(f"Before {token[:split]}")
    gate.seal({CALL_ID: "tool invocation"})
    assert gate.accept(f"{token[split:]} after") == ()
    finished = gate.finish(final_text=f"Before {token} after", release=True)

    public_text = "".join((*published, *finished.chunks))
    assert public_text.count("Before ") == 1
    assert replacement in public_text
    assert token not in public_text
    assert token not in finished.final_text


def test_multiple_overlapping_calls_added_while_sealed_release_safely_once():
    gate = _gate()
    first_call, second_call = "call-alpha", "call-alphabet"

    published = gate.accept("Before call-al")
    gate.seal({first_call: "tool invocation"})
    gate.seal({second_call: "tool invocation"})
    assert gate.accept("phabet after") == ()
    finished = gate.finish(final_text=f"Before {second_call} after", release=True)
    repeated = gate.finish(final_text="must not replay", release=True)

    public_text = "".join((*published, *finished.chunks))
    assert first_call not in public_text and second_call not in public_text
    assert finished.final_text == "Before tool invocation after"
    assert repeated.chunks == () and repeated.final_text == ""


@pytest.mark.parametrize("release", [True, False])
def test_sealed_text_is_released_once_or_discarded(release):
    gate = _gate()
    gate.seal({CALL_ID: "tool invocation"})
    assert gate.accept(f"Answer from {CALL_ID}.") == ()

    finished = gate.finish(final_text=f"Answer from {CALL_ID}.", release=release)

    assert finished.chunks == (("Answer from tool invocation.",) if release else ())
    assert finished.final_text == ("Answer from tool invocation." if release else "")
    assert gate.finish(final_text="repeat", release=True).chunks == ()


def test_verified_capability_release_discards_pre_evidence_text_and_streams_later_answer():
    gate = _gate()
    pre_evidence = "raw tool output must remain private"

    gate.seal({CALL_ID: "tool invocation"})
    assert gate.accept(pre_evidence) == ()
    gate.release_after_verified_capability()
    first = gate.accept("Safe final ")
    second = gate.accept("answer.")
    finished = gate.finish(
        final_text=f"{pre_evidence} Safe final answer.",
        release=True,
    )

    public_text = "".join((*first, *second, *finished.chunks))
    assert public_text == "Safe final answer."
    assert pre_evidence not in public_text
    assert finished.final_text == "Safe final answer."


def test_verified_capability_release_never_falls_back_to_cumulative_terminal_text():
    gate = _gate()
    pre_evidence = "raw tool output must remain private"

    gate.seal({CALL_ID: "tool invocation"})
    assert gate.accept(pre_evidence) == ()
    gate.release_after_verified_capability()
    finished = gate.finish(
        final_text=f"{pre_evidence} cumulative terminal answer",
        release=True,
    )

    assert finished.chunks == ()
    assert finished.final_text == ""


def test_over_bound_initial_or_dynamic_private_token_fails_closed():
    initial = PublicAnswerStreamGate(
        private_replacements={"x" * 65: "external tool"},
        sanitizer=_sanitize,
        max_private_token_chars=64,
        max_sealed_chars=128,
    )
    dynamic = _gate()
    published = dynamic.accept("ordinary pre-hook text")
    dynamic.seal({"y" * 65: "tool invocation"})

    assert initial.failed is True
    assert initial.accept("must not publish") == ()
    assert initial.finish(final_text="must not publish", release=True).chunks == ()
    assert published == ("ordinary pre-hook text",)
    assert dynamic.failed is True
    assert dynamic.accept("sealed private text") == ()
    assert dynamic.finish(final_text="sealed private text", release=True).chunks == ()


def test_sealed_answer_over_bound_fails_closed_without_truncation():
    gate = _gate()
    gate.seal({CALL_ID: "tool invocation"})

    assert gate.accept("x" * 129) == ()
    finished = gate.finish(final_text="x" * 129, release=True)

    assert gate.failed is True
    assert finished.chunks == () and finished.final_text == ""


def test_unsafe_sanitizer_result_fails_closed_without_raw_text():
    gate = _gate()

    assert gate.accept("raw-secret") == ()
    assert gate.failed is True
    assert gate.finish(final_text="raw-secret", release=True).chunks == ()

"""Runner-level regressions for PR 1651's source classification.

These use installed SDK message types and the existing native-client test seam.
There is no real provider call. Run under the repository's locked test extra.
"""

from types import SimpleNamespace

import pytest

from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from app.required_tool_contract import with_sandbox_local_tool_capability_subjects
from tests.support.claude_sdk import native_client_factory


@pytest.fixture
def source_routing_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        lambda: SimpleNamespace(
            claude_agent_sdk_enabled=True,
            claude_agent_sdk_max_turns=4,
            claude_agent_sdk_timeout_seconds=10,
            claude_agent_sdk_skills="",
            claude_agent_permission_mode="dontAsk",
            claude_agent_allowed_tools="Read",
            claude_agent_disallowed_tools="",
            claude_agent_model="model-a",
            anthropic_model="",
            anthropic_base_url="",
            anthropic_auth_token="",
            openai_api_key="",
        ),
    )
    (tmp_path / "input.txt").write_text("synthetic input", encoding="utf-8")
    return tmp_path


def frame(sdk, message_id, ordinal, event):
    return sdk.StreamEvent(
        uuid=f"{message_id}-frame-{ordinal}",
        session_id="synthetic-session",
        parent_tool_use_id=None,
        event=event,
    )


def text_source(sdk, message_id, text, *, typed=True, stop="end_turn", tool=False):
    """One complete raw provider message with optional typed block observations."""
    yield frame(sdk, message_id, 0, {
        "type": "message_start",
        "message": {"id": message_id, "role": "assistant", "stop_reason": None},
    })
    block_index = 0
    if text:
        yield frame(sdk, message_id, 1, {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text"},
        })
        yield frame(sdk, message_id, 2, {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text},
        })
        if typed:
            yield sdk.AssistantMessage(
                content=[sdk.TextBlock(text=text)], model="model-a",
                message_id=message_id, uuid=f"{message_id}-typed-text", stop_reason=None,
            )
        yield frame(sdk, message_id, 3, {"type": "content_block_stop", "index": 0})
        block_index = 1
    if tool:
        yield frame(sdk, message_id, 4, {
            "type": "content_block_start", "index": block_index,
            "content_block": {"type": "tool_use", "id": "synthetic-call", "name": "Read"},
        })
        yield sdk.AssistantMessage(
            content=[sdk.ToolUseBlock(
                id="synthetic-call", name="Read", input={"file_path": "input.txt"},
            )],
            model="model-a", message_id=message_id,
            uuid=f"{message_id}-typed-tool", stop_reason="tool_use",
        )
        yield frame(sdk, message_id, 5, {"type": "content_block_stop", "index": block_index})
    yield frame(sdk, message_id, 6, {"type": "message_delta", "delta": {"stop_reason": stop}})
    yield frame(sdk, message_id, 7, {"type": "message_stop"})


def terminal(sdk, answer, *, stop="end_turn"):
    return sdk.ResultMessage(
        subtype="success", duration_ms=12, duration_api_ms=10,
        is_error=False, num_turns=2, session_id="synthetic-session",
        stop_reason=stop, result=answer, uuid="synthetic-result",
    )


async def finish_read(options):
    hook_input = {
        "tool_name": "Read", "tool_use_id": "synthetic-call",
        "tool_input": {"file_path": "input.txt"},
    }
    admitted = await options.hooks["PreToolUse"][0].hooks[0](
        hook_input, "synthetic-call", {},
    )
    assert admitted["hookSpecificOutput"]["permissionDecision"] == "allow"
    await options.hooks["PostToolUse"][-1].hooks[0](hook_input, "synthetic-call", {})


async def execute(workspace, source, *, partial):
    events = []
    texts = []

    async def on_text(text):
        texts.append(text)

    async def acknowledge(_fact):
        return True

    subjects = with_sandbox_local_tool_capability_subjects(
        [], sandbox_provider="docker", authorized_sandbox_tool_identities={"Read"},
    )
    result = await run_claude_agent_sdk(
        prompt="Inspect the synthetic input and answer.", cwd=workspace, skill_id=None,
        client_fn=native_client_factory(source), on_text=on_text if partial else None,
        on_tool_lifecycle=acknowledge, on_agent_event=lambda batch: events.extend(batch) or True,
        run_id="run-1651", attempt_id="attempt-1651", tool_policy_subjects=subjects,
        execution_policy="sandbox_brokered",
    )
    return result, events, texts


def assert_answer(result, events, expected):
    assert result.error is None, result.runtime_diagnostics
    roles = {
        event.payload["part_id"]: event.payload["role"]
        for event in events
        if event.event_type == "message.part.classified"
    }
    parts = {}
    for event in events:
        if event.event_type != "message.part.delta":
            continue
        part_id = event.payload["part_id"]
        parts.setdefault(part_id, []).append(event)
    answer_parts = [
        deltas for part_id, deltas in parts.items() if roles.get(part_id) == "answer"
    ]
    selected_deltas = [event for deltas in answer_parts for event in deltas]
    answer = "\n\n".join(
        "".join(event.payload["delta"] for event in deltas)
        for deltas in answer_parts
    )
    assert answer == expected
    assert result.message == ""
    assert result.answer_receipt == {
        "schema_version": "ai-platform.assistant-answer-receipt.v2",
        "message_id": selected_deltas[0].message_id,
        "delta_count": len(selected_deltas), "text_length": len(expected),
        "last_delta_event_id": selected_deltas[-1].event_id,
    }


def commentary(events):
    roles = {
        event.payload["part_id"]: event.payload["role"]
        for event in events
        if event.event_type == "message.part.classified"
    }
    return "".join(
        event.payload["delta"]
        for event in events
        if event.event_type == "message.part.delta"
        and roles.get(event.payload["part_id"]) == "work"
    )


@pytest.mark.asyncio
async def test_empty_tool_turn_cannot_classify_the_next_answer_as_commentary(source_routing_settings):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt
        for event in text_source(sdk, "provider-tool", "", stop="tool_use", tool=True):
            yield event
        await finish_read(options)
        for event in text_source(sdk, "provider-answer", "Final answer."):
            yield event
        yield terminal(sdk, "Final answer.")

    result, events, _ = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Final answer.")
    assert commentary(events) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_tool_narration_then_completed_source_keeps_the_answer(source_routing_settings, partial):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt
        for event in text_source(sdk, "provider-work", "Checking sources.", stop="tool_use", tool=True):
            yield event
        await finish_read(options)
        for event in text_source(sdk, "provider-answer", "Final answer."):
            yield event
        yield terminal(sdk, "Final answer.")

    result, events, _ = await execute(source_routing_settings, source, partial=partial)
    assert_answer(result, events, "Final answer.")
    assert commentary(events) == "Checking sources."


@pytest.mark.asyncio
async def test_closed_typed_answer_replay_does_not_append_the_body_again(source_routing_settings):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        answer = sdk.AssistantMessage(
            content=[sdk.TextBlock(text="Answer.")], model="model-a",
            message_id="provider-answer", uuid="same-observation", stop_reason="end_turn",
        )
        yield answer
        yield answer
        yield terminal(sdk, "Answer.")

    result, events, _ = await execute(source_routing_settings, source, partial=False)
    assert_answer(result, events, "Answer.")
    assert commentary(events) == ""


@pytest.mark.asyncio
async def test_split_typed_text_and_tool_share_the_same_classification(source_routing_settings):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt
        yield sdk.AssistantMessage(
            content=[sdk.TextBlock(text="Checking sources.")], model="model-a",
            message_id="provider-work", uuid="work-text", stop_reason=None,
        )
        yield sdk.AssistantMessage(
            content=[sdk.ToolUseBlock(id="synthetic-call", name="Read", input={"file_path": "input.txt"})],
            model="model-a", message_id="provider-work", uuid="work-tool", stop_reason="tool_use",
        )
        await finish_read(options)
        yield sdk.AssistantMessage(
            content=[sdk.TextBlock(text="Final answer.")], model="model-a",
            message_id="provider-answer", uuid="answer-text", stop_reason="end_turn",
        )
        yield terminal(sdk, "Final answer.")

    result, events, _ = await execute(source_routing_settings, source, partial=False)
    assert_answer(result, events, "Final answer.")
    assert commentary(events) == "Checking sources."


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
async def test_whitespace_or_raw_only_source_has_no_answer_but_preserves_sdk_success(source_routing_settings, raw):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        if raw:
            for event in text_source(sdk, "provider-blank", " \t\n", typed=False):
                yield event
        else:
            yield sdk.AssistantMessage(
                content=[sdk.TextBlock(text=" \t\n")], model="model-a",
                message_id="provider-blank", uuid="blank-text", stop_reason="end_turn",
            )
        yield terminal(sdk, "")

    result, events, _ = await execute(source_routing_settings, source, partial=raw)
    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.answer_receipt is None
    assert not any(event.event_type == "message.completed" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [("Prior private-", "value after."), ("private-", "value")])
async def test_cross_source_gate_failure_withholds_answer_without_overriding_sdk_success(
    source_routing_settings, monkeypatch, first, second,
):
    import claude_agent_sdk as sdk

    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.sanitize_public_answer_text",
        lambda value: value.replace("private-value", "[redacted]"),
    )

    async def source(*, prompt, options):
        del prompt, options
        for event in text_source(sdk, "provider-first", first):
            yield event
        for event in text_source(sdk, "provider-second", second):
            yield event
        yield terminal(sdk, first + "\n\n" + second)

    result, events, _ = await execute(source_routing_settings, source, partial=True)
    assert result.error is None
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.answer_receipt is None
    assert not any(event.event_type == "message.completed" for event in events)
    preview = "".join(event.payload["delta"] for event in events if event.event_type == "message.part.delta")
    assert preview == ("Prior " if first.startswith("Prior ") else "")
    assert "private-value" not in preview

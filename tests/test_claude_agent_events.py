import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.executors.claude.agent_events import (
    ClaudeAgentEventCandidate,
    ClaudeSdkAgentEventAdapter,
)
from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from app.runtime.event_bridge import agent_event_to_executor_event


def _adapter():
    return ClaudeSdkAgentEventAdapter(
        run_id="run-1187",
        attempt_id="attempt-1",
        authorized_capabilities={
            "Read": ("read", "Read file"),
            "mcp__server__search": ("mcp", "Tenant search"),
            "qa-review": ("skill", "QA review"),
        },
    )


def test_answer_candidates_are_gated_and_have_one_stable_message_identity():
    adapter = _adapter()

    events = adapter.accept_answer_text("safe answer", already_gated=True)
    events += adapter.complete_answer("safe answer")

    assert [event.event_type for event in events] == [
        "message.started",
        "message.delta",
        "message.completed",
    ]
    assert len({event.message_id for event in events}) == 1
    assert all("attempt-1" not in str(event.as_dict()["payload"]) for event in events)


def test_thinking_has_lifecycle_only_and_tool_hooks_dedupe_exact_sdk_identity():
    adapter = _adapter()
    class ThinkingBlock:
        pass

    class ToolUseBlock:
        pass

    thinking = ThinkingBlock()
    thinking.thinking = "private reasoning"
    thinking.signature = "private"
    block = ToolUseBlock()
    block.id = "sdk-tool-1"
    block.name = "Read"
    block.input = {"file_path": "C:\\private\\x"}

    thinking_events = adapter.accept_content_block(thinking, block_index=0)
    assert [event.event_type for event in thinking_events] == [
        "thinking.started",
        "thinking.completed",
    ]
    assert all("private" not in repr(event.as_dict()) for event in thinking_events)

    assert adapter.accept_content_block(block) == ()
    pre = {"tool_name": "Read", "tool_use_id": "sdk-tool-1", "tool_input": {}}
    started = adapter.accept_hook("PreToolUse", pre, tool_use_id="sdk-tool-1")
    assert [event.event_type for event in started] == ["tool.started"]
    assert adapter.accept_hook("PreToolUse", pre, tool_use_id="sdk-tool-1") == ()

    completed = adapter.accept_hook("PostToolUse", pre, tool_use_id="sdk-tool-1")
    assert [event.event_type for event in completed] == ["tool.completed"]
    assert adapter.accept_hook("PostToolUse", pre, tool_use_id="sdk-tool-1") == ()
    assert "sdk-tool-1" not in repr(started + completed)


def test_unknown_sdk_tool_and_mismatched_hook_fail_closed_without_public_candidate():
    adapter = _adapter()
    class ToolUseBlock:
        pass

    unknown = ToolUseBlock()
    unknown.id = "private-unknown"
    unknown.name = "UnknownPrivateTool"
    unknown.input = {"secret": "value"}
    assert adapter.accept_content_block(unknown) == ()
    assert adapter.accept_hook(
        "PreToolUse",
        {"tool_name": "UnknownPrivateTool", "tool_use_id": "private-unknown", "tool_input": {}},
        tool_use_id="private-unknown",
    ) == ()

    known = ToolUseBlock()
    known.id = "known"
    known.name = "Read"
    known.input = {}
    adapter.accept_content_block(known)
    assert adapter.accept_hook(
        "PreToolUse",
        {"tool_name": "Read", "tool_use_id": "different", "tool_input": {}},
        tool_use_id="known",
    ) == ()


def test_task_events_are_opaque_parented_and_terminal_status_is_bounded():
    adapter = _adapter()
    class TaskStartedMessage:
        pass

    class TaskProgressMessage:
        pass

    class TaskNotificationMessage:
        pass

    started_message = TaskStartedMessage()
    started_message.task_id = "task-private"
    started_message.tool_use_id = "sdk-tool-1"
    started_message.uuid = "task-u"
    progress_message = TaskProgressMessage()
    progress_message.task_id = "task-private"
    progress_message.uuid = "task-p"
    progress_message.usage = {"progress_percent": 140}
    progress_message.last_tool_name = "Read"
    done_message = TaskNotificationMessage()
    done_message.task_id = "task-private"
    done_message.status = "stopped"
    done_message.uuid = "task-d"

    started = adapter.accept_task_message(started_message)
    progress = adapter.accept_task_message(progress_message)
    done = adapter.accept_task_message(done_message)

    assert started[0].event_type == "subagent.started"
    assert started[0].causation_event_id is not None
    assert progress[0].payload["progress_percent"] == 100
    assert done[0].event_type == "subagent.cancelled"
    assert all("task-private" not in repr(event.as_dict()) for event in started + progress + done)


def test_result_and_cancel_seal_late_candidates():
    adapter = _adapter()
    adapter.accept_answer_text("safe", already_gated=True)
    result = SimpleNamespace(duration_ms=10, num_turns=2, is_error=False, stop_reason="end_turn")
    completed = adapter.accept_result(result, final_content="safe")
    assert completed[-1].payload["stop_category"] == "completed"

    adapter.seal("timeout")
    assert adapter.accept_answer_text("late", already_gated=True) == ()
    assert adapter.accept_content_block(SimpleNamespace()) == ()
    assert adapter.accept_result(result, final_content="late") == ()


def test_bridge_requires_candidate_identity_and_rejects_private_payload_fields():
    event = _adapter().accept_answer_text("safe", already_gated=True)[1]
    bridged = agent_event_to_executor_event(event.to_agent_event())
    assert bridged["event_type"] == "message.delta"
    assert bridged["payload"] == {"delta": "safe"}
    assert bridged["event_id"] == event.event_id

    malformed = ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt-malformed",
        event_type="message.delta",
        message_id="msg-1",
        causation_event_id=None,
        payload={"delta": "safe"},
    ).to_agent_event()
    malformed.event_id = None
    assert agent_event_to_executor_event(malformed)["event_type"] == "executor_private_event"




@pytest.mark.asyncio
async def test_runner_assembles_sdk_text_tool_hooks_and_terminal_model_events(monkeypatch):
    import claude_agent_sdk as sdk

    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        lambda: SimpleNamespace(
            claude_agent_sdk_enabled=True,
            claude_agent_sdk_max_turns=4,
            claude_agent_sdk_timeout_seconds=10,
            claude_agent_sdk_skills="",
            claude_agent_sdk_max_thinking_tokens=128,
            claude_agent_sdk_effort="high",
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
    subject = {
        "identity": "Read",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "allowed_parameter_keys": ["file_path"],
        "required_parameter_keys": ["file_path"],
        "risk_level": "low",
        "write_capable": False,
        "public_tool_label": "Read file",
    }
    candidates = []

    async def query_fn(*, prompt, options):
        del prompt
        yield sdk.StreamEvent(
            uuid="stream-1",
            session_id="sdk-session",
            event={"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        )
        yield sdk.StreamEvent(
            uuid="stream-2",
            session_id="sdk-session",
            event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "safe answer"}},
        )
        yield sdk.StreamEvent(
            uuid="stream-3",
            session_id="sdk-session",
            event={"type": "content_block_stop", "index": 0},
        )
        yield sdk.AssistantMessage(
            content=[sdk.ToolUseBlock(id="sdk-tool-1", name="Read", input={"file_path": "answer.txt"})],
            model="model-a",
        )
        pre = options.hooks["PreToolUse"][0].hooks[0]
        await pre({"tool_name": "Read", "tool_input": {"file_path": "answer.txt"}, "tool_use_id": "sdk-tool-1"}, "sdk-tool-1", {})
        post = options.hooks["PostToolUse"][-1].hooks[0]
        await post({"tool_name": "Read", "tool_input": {"file_path": "answer.txt"}, "tool_use_id": "sdk-tool-1"}, "sdk-tool-1", {})
        yield sdk.ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="sdk-session",
            stop_reason="end_turn",
            result="safe answer",
        )

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=Path("tests"),
        skill_id=None,
        query_fn=query_fn,
        on_text=lambda value: asyncio.sleep(0),
        on_agent_event=lambda candidate: candidates.append(candidate),
        run_id="run-1187",
        attempt_id="attempt-1",
        tool_policy_subjects=[subject],
        execution_policy="sandbox_brokered",
        require_selected_skill_invocation=False,
    )

    assert result.error is None
    assert [candidate.event_type for candidate in candidates] == [
        "message.started",
        "message.delta",
        "tool.started",
        "tool.completed",
        "message.delta",
        "message.completed",
        "model.completed",
    ]
    assert candidates[1].payload == {"delta": "safe "}
    assert candidates[4].payload == {"delta": "answer"}
    assert all("sdk-tool-1" not in repr(candidate.as_dict()) for candidate in candidates)

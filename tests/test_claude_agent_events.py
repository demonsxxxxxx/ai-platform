import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution.api import (
    ClaudeAgentEventCandidate,
    ClaudeSdkAgentEventAdapter,
)
from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent


def _adapter():
    return ClaudeSdkAgentEventAdapter(
        run_id="run-1187",
        attempt_id="attempt-1",
        sanitizer=sanitize_public_text,
        payload_sanitizer=sanitize_public_payload,
        authorized_capabilities={
            "Read": ("read", "Read file"),
            "WebSearch": ("search", "Web search"),
            "mcp__server__search": ("mcp", "Tenant search"),
            "qa-review": ("skill", "QA review"),
        },
    )


def test_v4_callback_bridge_rejects_private_strings_even_when_shape_is_valid():
    safe = AgentEvent(
        type="message.delta",
        payload={"delta": "safe answer"},
        event_id="event-1",
        run_id="run-1187",
        message_id="message-1",
    )
    private = AgentEvent(
        type="message.delta",
        payload={"delta": r"C:\\agent-workspaces\\run-1187\\secret"},
        event_id="event-2",
        run_id="run-1187",
        message_id="message-1",
    )

    private_envelope = AgentEvent(
        type="message.delta",
        payload={"delta": "safe answer"},
        event_id="event-agent-workspaces-secret",
        run_id="run-1187",
        message_id="message-1",
        causation_event_id="cause-agent-workspaces-secret",
    )

    assert agent_event_to_executor_event(safe)["event_type"] == "message.delta"
    assert agent_event_to_executor_event(private)["event_type"] == "executor_private_event"
    assert agent_event_to_executor_event(private_envelope)["event_type"] == "executor_private_event"


def test_v4_callback_bridge_rejects_private_envelope_message_identity():
    private_message = AgentEvent(
        type="message.delta",
        message="agent-workspaces/private message",
        payload={"delta": "safe answer"},
        event_id="event-3",
        run_id="run-1187",
        message_id="message-agent-workspaces-private",
    )

    assert agent_event_to_executor_event(private_message)["event_type"] == "executor_private_event"


def test_v4_callback_bridge_rejects_non_empty_legacy_message_even_when_safe():
    legacy_message = AgentEvent(
        type="message.delta",
        message="legacy visible text",
        payload={"delta": "safe answer"},
        event_id="event-legacy-message",
        run_id="run-1187",
        message_id="message-1",
    )

    assert agent_event_to_executor_event(legacy_message)["event_type"] == "executor_private_event"


def test_v4_callback_bridge_rejects_admin_only_event_even_when_schema_valid():
    admin_only = AgentEvent(
        type="message.delta",
        payload={"delta": "safe answer"},
        event_id="event-admin-only",
        run_id="run-1187",
        message_id="message-1",
        admin_only=True,
    )

    assert agent_event_to_executor_event(admin_only)["event_type"] == "executor_private_event"


def test_v4_candidate_rejects_private_nested_public_strings():
    with pytest.raises(ValueError, match="private text"):
        ClaudeAgentEventCandidate(
            run_id="run-1187",
            event_id="event-4",
            event_type="message.delta",
            message_id="message-1",
            causation_event_id=None,
            payload={"delta": "agent-workspaces/private nested text"},
            payload_sanitizer=sanitize_public_payload,
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


def test_policy_decision_emits_checking_then_terminal_and_denial_tool_event():
    adapter = _adapter()

    allowed = adapter.accept_policy_decision(
        tool_name="Read",
        tool_input={},
        allowed=True,
        tool_use_id="allowed-call",
    )
    assert [event.event_type for event in allowed] == [
        "policy.checking",
        "policy.allowed",
    ]
    assert allowed[0].payload["decision_id"] == allowed[1].payload["decision_id"]
    assert adapter.accept_policy_decision(
        tool_name="Read",
        tool_input={},
        allowed=True,
        tool_use_id="allowed-call",
    ) == ()

    denied = adapter.accept_policy_decision(
        tool_name="Read",
        tool_input={},
        allowed=False,
        tool_use_id="denied-call",
    )
    assert [event.event_type for event in denied] == [
        "policy.checking",
        "policy.denied",
        "tool.denied",
    ]
    assert denied[-1].causation_event_id == denied[1].event_id
    assert all("allowed-call" not in repr(event.as_dict()) for event in allowed)
    assert all("denied-call" not in repr(event.as_dict()) for event in denied)



def test_thinking_and_tool_hooks_expose_public_lifecycle_without_private_sdk_content():
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
    assert [event.payload for event in thinking_events] == [
        {"public_summary": "Analyzing the request"},
        {"public_summary": "Analysis step completed"},
    ]
    assert all("private" not in repr(event.as_dict()) for event in thinking_events)

    assert adapter.accept_content_block(block) == ()
    pre = {"tool_name": "Read", "tool_use_id": "sdk-tool-1", "tool_input": {}}
    started = adapter.accept_hook("PreToolUse", pre, tool_use_id="sdk-tool-1")
    assert [event.event_type for event in started] == ["tool.started"]
    assert started[0].payload["input_summary"] == "Starting Read file"
    assert adapter.accept_hook("PreToolUse", pre, tool_use_id="sdk-tool-1") == ()

    completed = adapter.accept_hook("PostToolUse", pre, tool_use_id="sdk-tool-1")
    assert [event.event_type for event in completed] == ["tool.completed"]
    assert completed[0].payload["result_summary"] == "Read file completed"

    search = ToolUseBlock()
    search.id = "search-call-1"
    search.name = "WebSearch"
    search.input = {"query": "OpenSandbox workspace limits"}
    assert adapter.accept_content_block(search) == ()
    search_started = adapter.accept_hook(
        "PreToolUse",
        {"tool_use_id": "search-call-1", "tool_name": "WebSearch"},
    )
    assert search_started[0].payload["input_summary"] == (
        "Searching for: OpenSandbox workspace limits"
    )
    assert "query" not in search_started[0].payload

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
    class ToolUseBlock:
        pass

    parent_block = ToolUseBlock()
    parent_block.id = "sdk-tool-1"
    parent_block.name = "Read"
    parent_block.input = {}
    adapter.accept_content_block(parent_block)
    adapter.accept_hook(
        "PreToolUse",
        {"tool_name": "Read", "tool_use_id": "sdk-tool-1", "tool_input": {}},
        tool_use_id="sdk-tool-1",
    )

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
    bridged = agent_event_to_executor_event(AgentEvent(**event.as_agent_event_fields()))
    assert bridged["event_type"] == "message.delta"
    assert bridged["payload"] == {"delta": "safe"}
    assert bridged["event_id"] == event.event_id

    malformed = AgentEvent(
        **ClaudeAgentEventCandidate(
            run_id="run-1187",
            event_id="evt-malformed",
            event_type="message.delta",
            message_id="msg-1",
            causation_event_id=None,
            payload={"delta": "safe"},
            payload_sanitizer=sanitize_public_payload,
        ).as_agent_event_fields()
    )
    malformed.event_id = None
    assert agent_event_to_executor_event(malformed)["event_type"] == "executor_private_event"

    cancelled = ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt-cancelled",
        event_type="run.cancelled",
        message_id=None,
        causation_event_id=None,
        payload={
            "terminal_event_id": "evt-terminal",
            "hydrate_required": True,
            "reason_code": "user_cancelled",
        },
        payload_sanitizer=sanitize_public_payload,
    )
    assert agent_event_to_executor_event(AgentEvent(**cancelled.as_agent_event_fields()))["event_type"] == "run.cancelled"




def test_bridge_enforces_v4_reference_grammar_lengths_and_message_families():
    from app.runtime.kernel_contracts import AgentEvent

    def event(event_type: str, *, run_id: str = "run-1187", message_id: str | None = "msg-1"):
        return AgentEvent(
            type=event_type,
            event_id="e" * 256,
            run_id=run_id,
            message_id=message_id,
            causation_event_id=None,
            payload={"delta": "safe"} if event_type == "message.delta" else {},
        )

    assert agent_event_to_executor_event(event("message.delta"))["event_type"] == "message.delta"
    assert agent_event_to_executor_event(event("model.completed", message_id=None))["event_type"] == "executor_private_event"
    assert agent_event_to_executor_event(event("artifact.created", message_id=None))["event_type"] == "executor_private_event"
    assert agent_event_to_executor_event(event("message.delta", run_id="run.bad"))["event_type"] == "executor_private_event"
    assert agent_event_to_executor_event(event("message.delta", message_id="m" * 257))["event_type"] == "executor_private_event"


def test_task_updated_terminal_is_idempotent_and_seals_late_progress():
    adapter = _adapter()

    class TaskStartedMessage:
        task_id = "task-private"
        tool_use_id = None

    class TaskUpdatedMessage:
        task_id = "task-private"
        status = "completed"
        patch = {}
        uuid = "terminal-1"

    class TaskProgressMessage:
        task_id = "task-private"
        uuid = "late-progress"
        usage = {"progress_percent": 50}
        last_tool_name = "Read"

    assert [event.event_type for event in adapter.accept_task_message(TaskStartedMessage())] == ["subagent.started"]
    terminal = adapter.accept_task_message(TaskUpdatedMessage())
    assert [event.event_type for event in terminal] == ["subagent.completed"]
    assert adapter.accept_task_message(TaskUpdatedMessage()) == ()
    assert adapter.accept_task_message(TaskProgressMessage()) == ()


def test_task_parent_causation_requires_an_accepted_parent_event():
    adapter = _adapter()

    class TaskStartedMessage:
        task_id = "task-private"
        tool_use_id = "unaccepted-tool"

    event = adapter.accept_task_message(TaskStartedMessage())[0]
    assert event.causation_event_id is None


def test_candidate_validation_enforces_required_fields_and_exact_text_bounds():
    delta = "d" * 8192
    final = "f" * 262144
    ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt_delta",
        event_type="message.delta",
        message_id="msg_1",
        causation_event_id=None,
        payload={"delta": delta},
        payload_sanitizer=sanitize_public_payload,
    )
    ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt_final",
        event_type="message.completed",
        message_id="msg_1",
        causation_event_id=None,
        payload={"content": final},
        payload_sanitizer=sanitize_public_payload,
    )
    with pytest.raises(ValueError):
        ClaudeAgentEventCandidate(
            run_id="run-1187",
            event_id="evt_missing",
            event_type="message.delta",
            message_id="msg_1",
            causation_event_id=None,
            payload={},
            payload_sanitizer=sanitize_public_payload,
        )
    with pytest.raises(ValueError):
        ClaudeAgentEventCandidate(
            run_id="run-1187",
            event_id="evt_extra",
            event_type="message.delta",
            message_id="msg_1",
            causation_event_id=None,
            payload={"delta": "ok", "extra": "private"},
            payload_sanitizer=sanitize_public_payload,
        )
    multibyte_delta = "é" * 8_192
    ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt_multibyte_delta",
        event_type="message.delta",
        message_id="msg_1",
        causation_event_id=None,
        payload={"delta": multibyte_delta},
        payload_sanitizer=sanitize_public_payload,
    )
    multibyte_content = "界" * 262_144
    ClaudeAgentEventCandidate(
        run_id="run-1187",
        event_id="evt_multibyte_content",
        event_type="message.completed",
        message_id="msg_1",
        causation_event_id=None,
        payload={"content": multibyte_content},
        payload_sanitizer=sanitize_public_payload,
    )
    with pytest.raises(ValueError):
        ClaudeAgentEventCandidate(
            run_id="run-1187",
            event_id="evt_oversize",
            event_type="message.delta",
            message_id="msg_1",
            causation_event_id=None,
            payload={"delta": "d" * 8_193},
            payload_sanitizer=sanitize_public_payload,
        )


def test_thinking_identity_is_scoped_by_message_and_block():
    adapter = _adapter()

    class ThinkingBlock:
        pass

    block = ThinkingBlock()
    first = adapter.accept_content_block(block, block_index=0, message_identity="message-a")
    second = adapter.accept_content_block(block, block_index=0, message_identity="message-a")
    third = adapter.accept_content_block(block, block_index=0, message_identity="message-b")
    assert [event.event_type for event in first] == ["thinking.started", "thinking.completed"]
    assert second == ()
    assert [event.event_type for event in third] == ["thinking.started", "thinking.completed"]


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
        yield sdk.TaskStartedMessage(
            subtype="started",
            data={},
            task_id="task-private",
            description="delegated work",
            uuid="task-started",
            session_id="sdk-session",
            tool_use_id="sdk-tool-1",
        )
        yield sdk.TaskNotificationMessage(
            subtype="update",
            data={},
            task_id="task-private",
            status="completed",
            output_file="",
            summary="",
            session_id="sdk-session",
            uuid="task-completed",
        )
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
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-1187",
        attempt_id="attempt-1",
        tool_policy_subjects=[subject],
        execution_policy="sandbox_brokered",
        require_selected_skill_invocation=False,
    )

    assert result.error is None
    assert [candidate.event_type for candidate in candidates] == [
        "policy.checking",
        "policy.allowed",
        "tool.started",
        "tool.completed",
        "subagent.started",
        "subagent.completed",
        "message.started",
        "message.delta",
        "message.completed",
        "model.completed",
    ]
    deltas = [candidate.payload["delta"] for candidate in candidates if candidate.event_type == "message.delta"]
    assert deltas == ["safe answer"]
    assert all("sdk-tool-1" not in repr(candidate.as_dict()) for candidate in candidates)


@pytest.mark.asyncio
async def test_runner_buffers_ordinary_stream_until_terminal_bound_is_validated(monkeypatch):
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
    published: list[str] = []
    candidates = []
    answer = "a" * 262_145

    async def query_fn(*, prompt, options):
        del prompt, options
        yield sdk.StreamEvent(
            uuid="stream-start",
            session_id="sdk-session",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
        )
        for index in range(65):
            yield sdk.StreamEvent(
                uuid=f"stream-delta-{index}",
                session_id="sdk-session",
                event={
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "a" * 4_096},
                },
            )
        yield sdk.StreamEvent(
            uuid="stream-stop",
            session_id="sdk-session",
            event={"type": "content_block_stop", "index": 0},
        )
        yield sdk.ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="sdk-session",
            stop_reason="end_turn",
            result=answer,
        )

    async def on_text(value: str) -> None:
        published.append(value)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=Path("tests"),
        skill_id=None,
        query_fn=query_fn,
        on_text=on_text,
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-1187",
        attempt_id="attempt-1",
        execution_policy="sandbox_brokered",
        require_selected_skill_invocation=False,
    )

    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert result.message == ""
    assert published == []
    assert candidates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_failure", ["false", "none", "cancelled", "exception"])
async def test_runner_seals_agent_candidates_when_callback_rejects(monkeypatch, callback_failure):
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
    callback_batches = []

    async def reject_batch(batch):
        callback_batches.append(batch)
        if callback_failure == "exception":
            raise RuntimeError("callback transport failed")
        if callback_failure == "cancelled":
            raise asyncio.CancelledError
        if callback_failure == "none":
            return None
        return False

    async def query_fn(*, prompt, options):
        del prompt, options
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
        prompt="answer",
        cwd=Path("tests"),
        skill_id=None,
        query_fn=query_fn,
        on_agent_event=reject_batch,
        run_id="run-1187",
        attempt_id="attempt-1",
        execution_policy="sandbox_brokered",
        require_selected_skill_invocation=False,
    )

    assert result.error == "agent_event_callback_not_acknowledged"
    assert result.message == ""
    assert len(callback_batches) == 1
    assert [candidate.event_type for candidate in callback_batches[0]] == [
        "message.started",
        "message.delta",
        "message.completed",
        "model.completed",
    ]


@pytest.mark.asyncio
async def test_outer_cancellation_propagates_while_agent_callback_waits(monkeypatch):
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
    callback_started = asyncio.Event()
    callback_never_releases = asyncio.Event()

    async def await_ack(batch):
        del batch
        callback_started.set()
        await callback_never_releases.wait()
        return True

    async def query_fn(*, prompt, options):
        del prompt, options
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

    task = asyncio.create_task(
        run_claude_agent_sdk(
            prompt="answer",
            cwd=Path("tests"),
            skill_id=None,
            query_fn=query_fn,
            on_agent_event=await_ack,
            run_id="run-1187",
            attempt_id="attempt-1",
            execution_policy="sandbox_brokered",
            require_selected_skill_invocation=False,
        )
    )
    await asyncio.wait_for(callback_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("answer", "expected_error"),
    [
        ("a" * 262_144, None),
        ("é" * 262_144, None),
        ("a" * 262_145, "claude_agent_sdk_tool_admission_failed"),
    ],
    ids=["ascii", "multibyte", "max-plus-one"],
)
async def test_runner_frames_governed_completed_answer_for_ascii_and_multibyte_boundaries(
    monkeypatch, answer, expected_error
):
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
        "identity": "mcp__tenant-server__search",
        "mcp_server": "tenant-server",
        "mcp_tool": "search",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "allowed_parameter_keys": ["query"],
        "required_parameter_keys": [],
        "risk_level": "high",
        "write_capable": True,
        "public_tool_label": "Tenant search",
        "mcp_server_config": {"type": "http", "url": "https://private.example/mcp"},
    }
    candidates = []
    callback_batches = []
    published = []

    async def accept_batch(batch):
        callback_batches.append(batch)
        candidates.extend(batch)
        return True

    async def on_text(value: str):
        published.append(value)

    async def query_fn(*, prompt, options):
        del prompt, options
        yield sdk.ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="sdk-session",
            stop_reason="end_turn",
            result=answer,
        )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=Path("tests"),
        skill_id=None,
        query_fn=query_fn,
        on_text=on_text,
        on_agent_event=accept_batch,
        run_id="run-1187",
        attempt_id="attempt-1",
        tool_policy_subjects=[subject],
        execution_policy="sandbox_brokered",
        require_selected_skill_invocation=False,
    )

    deltas = [candidate.payload["delta"] for candidate in candidates if candidate.event_type == "message.delta"]
    if expected_error is not None:
        assert result.error == expected_error
        assert result.message == ""
        assert candidates == []
        assert callback_batches == []
        assert published == []
        return
    assert result.error is None
    assert result.message == answer
    assert len(callback_batches) == 1
    assert callback_batches[0][0].event_type == "message.started"
    assert callback_batches[0][-2].event_type == "message.completed"
    assert callback_batches[0][-1].event_type == "model.completed"
    assert published == [answer]
    assert "".join(deltas) == answer
    assert deltas and all(len(delta) <= 8_192 for delta in deltas)
    assert candidates[-2].event_type == "message.completed"
    assert len(candidates[-2].payload["content"]) == 262_144

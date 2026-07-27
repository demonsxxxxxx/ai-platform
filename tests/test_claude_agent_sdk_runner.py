import sys
import types

import pytest

from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk


def _settings():
    return types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        claude_agent_sdk_max_turns=12,
        claude_agent_sdk_timeout_seconds=5,
        claude_agent_sdk_skills="",
        claude_agent_sdk_max_thinking_tokens=128,
        claude_agent_sdk_effort="high",
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
        claude_agent_model="model-a",
        anthropic_model="",
        anthropic_base_url="",
        anthropic_auth_token="",
        openai_api_key="",
    )


def _subject():
    return {
        "identity": "mcp__tenant-server__search",
        "mcp_server": "tenant-server",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "allowed_parameter_keys": ["private"],
        "required_parameter_keys": [],
        "risk_level": "low",
        "write_capable": False,
        "mcp_server_config": {
            "type": "http",
            "url": "https://private.example/mcp",
        },
    }


def _fake_sdk(captured, *, hook_invocations):
    class AssistantMessage:
        pass

    class TextBlock:
        pass

    class StreamEvent:
        pass

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "done"
        is_error = False
        errors = None
        stop_reason = None
        num_turns = 1
        permission_denials = None

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del prompt, options
        for hook_name, hook_input, tool_call_id in hook_invocations:
            matchers = captured["hooks"][hook_name]
            matcher = (
                matchers[0]
                if hook_name == "PreToolUse"
                else next(item for item in matchers if item.matcher == "mcp__*")
            )
            await matcher.hooks[0](hook_input, tool_call_id, {})
        yield ResultMessage()

    return types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        query=query,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hook_name", "expected_phase", "expected_status"),
    [
        ("PostToolUse", "completed", "succeeded"),
        ("PostToolUseFailure", "failed", "failed"),
    ],
)
async def test_sdk_mcp_hooks_emit_bounded_actual_call_evidence(
    monkeypatch,
    tmp_path,
    hook_name,
    expected_phase,
    expected_status,
):
    captured = {}
    hook_input = {
        "tool_name": "mcp__tenant-server__search",
        "tool_use_id": "tool-call-1",
        "tool_input": {"private": "must-not-leak"},
        "tool_response": {"private": "must-not-leak"},
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[(hook_name, hook_input, "tool-call-1")]),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert len(result.capability_evidence) == 1
    evidence = result.capability_evidence[0]
    assert evidence["canonical_identity"] == "mcp__tenant-server__search"
    assert evidence["tool_call_id"] == "tool-call-1"
    assert evidence["lifecycle_phase"] == expected_phase
    assert evidence["lifecycle_status"] == expected_status
    assert not ({"tool_input", "tool_response", "arguments", "error"} & evidence.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_hook", "terminal_phase"),
    [("PostToolUse", "completed"), ("PostToolUseFailure", "failed")],
)
async def test_sdk_mcp_pre_tool_hook_starts_once_before_matching_terminal_fact(
    monkeypatch,
    tmp_path,
    terminal_hook,
    terminal_phase,
):
    captured = {}
    private_hook_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__tenant-server__search",
        "tool_use_id": "tool-call-1",
        "tool_input": {"private": "must-not-leak"},
    }
    hook_invocations = [
        ("PreToolUse", private_hook_input, "tool-call-1"),
        (
            terminal_hook,
            {**private_hook_input, "hook_event_name": terminal_hook},
            "tool-call-1",
        ),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=hook_invocations),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        terminal_phase,
    ]
    assert {item["tool_call_id"] for item in result.capability_evidence} == {"tool-call-1"}
    assert {item["canonical_identity"] for item in result.capability_evidence} == {
        "mcp__tenant-server__search"
    }
    assert "must-not-leak" not in str(result.capability_evidence)


@pytest.mark.asyncio
async def test_sdk_mcp_selection_or_authorization_without_valid_pre_tool_use_never_starts(
    monkeypatch,
    tmp_path,
):
    captured = {}
    hook_invocations = [
        (
            "PreToolUse",
            {"tool_name": "mcp__foreign__unknown", "tool_use_id": "foreign"},
            "foreign",
        ),
        (
            "PreToolUse",
            {"tool_name": "mcp__tenant-server__search", "tool_use_id": ""},
            "",
        ),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=hook_invocations),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert not result.capability_evidence


@pytest.mark.parametrize(("allowed", "outcome"), [(True, "ask"), (True, "defer"), (False, "allow")])
@pytest.mark.asyncio
async def test_sdk_mcp_pre_tool_use_requires_exact_internal_and_hook_allow(
    monkeypatch, tmp_path, allowed, outcome
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                (
                    "PreToolUse",
                    {
                        "tool_name": "mcp__tenant-server__search",
                        "tool_use_id": "tool-call-1",
                        "tool_input": {"query": "private"},
                    },
                    "tool-call-1",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.evaluate_tool_policy",
        lambda **_kwargs: types.SimpleNamespace(
            allowed=allowed, outcome=outcome, reason="test-decision"
        ),
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert result.capability_evidence == []


@pytest.mark.asyncio
async def test_sdk_mcp_hook_omits_unknown_or_missing_tool_call_identity(monkeypatch, tmp_path):
    captured = {}
    hook_invocations = [
        (
            "PostToolUse",
            {"tool_name": "mcp__foreign__unknown", "tool_use_id": "foreign"},
            "foreign",
        ),
        (
            "PostToolUse",
            {"tool_name": "mcp__tenant-server__search", "tool_use_id": ""},
            "",
        ),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=hook_invocations),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert result.capability_evidence == []


@pytest.mark.asyncio
async def test_sdk_assistant_text_blocks_never_publish_answer_or_delta(monkeypatch, tmp_path):
    captured = {}

    class AssistantMessage:
        def __init__(self):
            self.content = [TextBlock("Bash: cat C:\\private\\token.txt = secret-token")]

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "Trusted structured result"
        is_error = False
        errors = None
        stop_reason = "end_turn"
        num_turns = 1
        permission_denials = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del prompt, options
        yield AssistantMessage()
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        types.SimpleNamespace(
            AssistantMessage=AssistantMessage,
            ClaudeAgentOptions=ClaudeAgentOptions,
            ResultMessage=ResultMessage,
            StreamEvent=type("StreamEvent", (), {}),
            TextBlock=TextBlock,
            query=query,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    deltas = []

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    assert deltas == ["Trusted structured result"]
    assert result.message == "Trusted structured result"
    assert "secret-token" not in result.message


@pytest.mark.asyncio
async def test_sdk_discards_over_cap_diagnostic_text_and_publishes_terminal_result_once(monkeypatch, tmp_path):
    captured = {}

    class AssistantMessage:
        def __init__(self):
            self.content = [TextBlock("x" * 512)]

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "x" * (512 * 33)
        is_error = False
        errors = None
        stop_reason = "end_turn"
        num_turns = 1
        permission_denials = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del prompt, options
        for _ in range(33):
            yield AssistantMessage()
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        types.SimpleNamespace(
            AssistantMessage=AssistantMessage,
            ClaudeAgentOptions=ClaudeAgentOptions,
            ResultMessage=ResultMessage,
            StreamEvent=type("StreamEvent", (), {}),
            TextBlock=TextBlock,
            query=query,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    deltas = []

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    assert deltas == [ResultMessage.result]
    assert result.message == ResultMessage.result


def _streaming_sdk(captured, events, *, on_before_result=None, result_text="terminal final"):
    class AssistantMessage:
        pass

    class TextBlock:
        pass

    class StreamEvent:
        def __init__(self, event):
            self.event = event

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = result_text
        is_error = False
        errors = None
        stop_reason = "end_turn"
        num_turns = 1
        permission_denials = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del prompt, options
        for event in events:
            yield StreamEvent(event)
        if on_before_result is not None:
            on_before_result()
        yield ResultMessage()

    return types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        query=query,
    )


def _trusted_internal_settings():
    settings = _settings()
    settings.sandbox_security_profile = "trusted_internal"
    return settings


@pytest.mark.asyncio
async def test_trusted_internal_streams_safe_raw_text_delta_before_result_without_terminal_replay(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    result_gate = []
    streamed_text = "Short safe public answer."
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": streamed_text}},
        {"type": "content_block_stop", "index": 0},
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _streaming_sdk(captured, events, on_before_result=lambda: result_gate.extend(deltas)),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _trusted_internal_settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result_gate == [streamed_text]
    assert deltas == [streamed_text]
    assert "terminal final" not in deltas
    assert result.message == "terminal final"


@pytest.mark.asyncio
async def test_trusted_internal_stream_ignores_tool_thinking_and_json_events(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"path\":\"C:\\\\private\"}"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "signature_delta", "signature": "private"}},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _trusted_internal_settings)

    await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert deltas == ["terminal final"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "C:"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "\\\\private\\\\token.txt"}},
        ],
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "wrong index"}},
        ],
        [
            {"type": "content_block_start", "index": "0", "content_block": {"type": "text"}},
        ],
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "short answer"}},
        ],
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "short answer"}},
            {"type": "content_block_stop", "index": 1},
        ],
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "short answer"}},
            {"type": "content_block_stop", "index": True},
        ],
    ],
)
async def test_trusted_internal_stream_fails_closed_on_sensitive_or_conflicting_raw_events(
    monkeypatch, tmp_path, events
):
    captured = {}
    deltas = []
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _trusted_internal_settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert result.message == "terminal final"
    assert deltas == ["terminal final"]


@pytest.mark.asyncio
async def test_trusted_internal_stream_duplicate_stop_never_replays_terminal_result(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "short answer"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_stop", "index": 0},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _trusted_internal_settings)

    await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert deltas == ["short answer"]


@pytest.mark.asyncio
async def test_trusted_internal_stream_unsafe_after_partial_never_replays_terminal_result(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    safe_text = "safe " * 120
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": safe_text}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "C:\\private\\token.txt"}},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _trusted_internal_settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert deltas == [safe_text[:-512]]
    assert result.message == "terminal final"


@pytest.mark.asyncio
async def test_governed_stream_events_keep_final_only_behavior(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "must remain private"}},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is False
    assert deltas == ["terminal final"]

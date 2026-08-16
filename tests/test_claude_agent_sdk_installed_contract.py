from importlib.metadata import version
from inspect import signature


def test_installed_claude_agent_sdk_02130_contract(tmp_path):
    installed_version = version("claude-agent-sdk")
    assert installed_version == "0.2.130"

    import claude_agent_sdk as sdk

    assert installed_version == "0.2.130"
    assert {"prompt", "options", "transport"}.issubset(signature(sdk.query).parameters)
    assert {"matcher", "hooks", "timeout"}.issubset(signature(sdk.HookMatcher).parameters)

    async def hook(_input, _tool_use_id, _context):
        return {}

    matcher = sdk.HookMatcher(matcher="Skill", hooks=[hook], timeout=5.0)
    options = sdk.ClaudeAgentOptions(
        cwd=str(tmp_path),
        model="model-a",
        system_prompt={"type": "preset", "preset": "claude_code", "append": "profile"},
        tools=["Read", "Skill"],
        allowed_tools=["Read", "Skill(qa-review)"],
        disallowed_tools=["Write"],
        permission_mode="dontAsk",
        env={"PATH": ""},
        skills=["qa-review"],
        session_id="session-a",
        max_turns=12,
        max_thinking_tokens=128,
        effort="high",
        hooks={"PostToolUse": [matcher]},
        include_partial_messages=True,
        setting_sources=["project"],
    )

    assistant = sdk.AssistantMessage(content=[sdk.TextBlock(text="partial")], model="model-a")
    event = sdk.StreamEvent(uuid="event-a", session_id="session-a", event={"type": "message_start"})
    result = sdk.ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session-a",
        total_cost_usd=0.0,
        usage={},
        result="done",
        stop_reason="end_turn",
        terminal_reason="completed",
    )

    assert options.include_partial_messages is True
    assert options.setting_sources == ["project"]
    assert assistant.content[0].text == "partial"
    assert event.event["type"] == "message_start"
    assert result.terminal_reason == "completed"

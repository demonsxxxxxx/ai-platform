import subprocess
import sys
from importlib.metadata import version
from inspect import signature
from pathlib import Path


def test_installed_claude_agent_sdk_02130_contract(tmp_path):
    installed_version = version("claude-agent-sdk")
    assert installed_version == "0.2.130"

    import claude_agent_sdk as sdk
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )
    from claude_agent_sdk.types import (
        PostToolUseHookSpecificOutput,
        PreToolUseHookSpecificOutput,
        SyncHookJSONOutput,
    )

    assert {"options", "transport"}.issubset(
        signature(sdk.ClaudeSDKClient).parameters
    )
    assert "prompt" in signature(sdk.ClaudeSDKClient.connect).parameters
    assert "prompt" in signature(sdk.ClaudeSDKClient.query).parameters
    assert "session_id" in signature(sdk.ClaudeSDKClient.query).parameters
    assert callable(sdk.ClaudeSDKClient.receive_response)
    assert callable(sdk.ClaudeSDKClient.disconnect)
    assert {"matcher", "hooks", "timeout"}.issubset(signature(sdk.HookMatcher).parameters)
    assert "hookSpecificOutput" in SyncHookJSONOutput.__annotations__
    assert "updatedInput" in PreToolUseHookSpecificOutput.__annotations__
    assert "updatedToolOutput" in PostToolUseHookSpecificOutput.__annotations__

    async def hook(_input, _tool_use_id, _context):
        return {}

    matcher = sdk.HookMatcher(matcher="Skill", hooks=[hook], timeout=5.0)

    class MinimalSessionStore(sdk.SessionStore):
        async def load(
            self, _key: sdk.SessionKey
        ) -> list[sdk.SessionStoreEntry] | None:
            return None

        async def append(
            self, _key: sdk.SessionKey, _entries: list[sdk.SessionStoreEntry]
        ) -> None:
            return None

        async def list_subkeys(self, _key: sdk.SessionListSubkeysKey) -> list[str]:
            return []

    session_store = MinimalSessionStore()
    cli_path = Path(sdk.__file__).parent / "_bundled" / (
        "claude.exe" if sys.platform == "win32" else "claude"
    )
    cli_check = subprocess.run(
        [str(cli_path), "--autocompact", "100000", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli_check.returncode == 0, cli_check.stderr

    bootstrap_options = sdk.ClaudeAgentOptions(
        cwd=str(tmp_path),
        model="model-a",
        system_prompt={"type": "preset", "preset": "claude_code", "append": "profile"},
        tools=["Read", "Skill"],
        allowed_tools=["Read", "Skill(qa-review)"],
        disallowed_tools=["Write"],
        permission_mode="dontAsk",
        env={"PATH": ""},
        cli_path=str(cli_path),
        extra_args={"autocompact": "100000"},
        skills=["qa-review"],
        session_id="session-a",
        session_store=session_store,
        session_store_flush="eager",
        max_turns=12,
        max_thinking_tokens=128,
        thinking={"type": "adaptive", "display": "omitted"},
        effort="high",
        hooks={"PostToolUse": [matcher]},
        include_partial_messages=True,
        setting_sources=["project"],
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    )
    resume_options = sdk.ClaudeAgentOptions(
        cwd=str(tmp_path),
        model="model-a",
        session_store=session_store,
        session_store_flush="eager",
        resume="session-a",
    )

    assert bootstrap_options.session_id == "session-a"
    assert bootstrap_options.resume is None
    assert resume_options.resume == "session-a"
    assert resume_options.session_id is None
    options = bootstrap_options
    command = SubprocessCLITransport(prompt="", options=options)._build_command()
    autocompact_index = command.index("--autocompact")

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
        structured_output={"answer": "done"},
        stop_reason="end_turn",
        terminal_reason="completed",
    )

    assert options.include_partial_messages is True
    assert options.setting_sources == ["project"]
    assert options.extra_args == {"autocompact": "100000"}
    assert command[autocompact_index : autocompact_index + 2] == [
        "--autocompact",
        "100000",
    ]
    assert options.session_store is session_store
    assert options.session_store_flush == "eager"
    assert options.thinking == {"type": "adaptive", "display": "omitted"}
    assert options.effort == "high"
    assert options.output_format["type"] == "json_schema"
    assert assistant.content[0].text == "partial"
    assert event.event["type"] == "message_start"
    assert result.terminal_reason == "completed"
    assert result.structured_output == {"answer": "done"}

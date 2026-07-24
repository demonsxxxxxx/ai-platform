import asyncio
from pathlib import Path
import sys
import types
from typing import Any

import pytest

from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk


def _settings(*, timeout_seconds: float = 5.0):
    return types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        claude_agent_sdk_skills="",
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
        claude_agent_model="model-a",
        anthropic_model="",
        claude_agent_sdk_timeout_seconds=timeout_seconds,
        claude_agent_sdk_max_turns=8,
        claude_agent_sdk_max_thinking_tokens=1024,
        claude_agent_sdk_effort="high",
        anthropic_api_key=None,
        anthropic_base_url=None,
        anthropic_auth_token=None,
        openai_api_key=None,
    )


def _install_sdk(monkeypatch, query):
    class TextBlock:
        def __init__(self, text: str):
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[Any]):
            self.content = content

    class ResultMessage:
        def __init__(
            self,
            *,
            is_error: bool = False,
            subtype: str = "success",
            errors: list[str] | None = None,
            stop_reason: str | None = "end_turn",
            num_turns: int = 1,
        ):
            self.session_id = "sdk-session"
            self.usage = {"input_tokens": 1}
            self.model_usage = {}
            self.result = "done"
            self.is_error = is_error
            self.subtype = subtype
            self.errors = list(errors or [])
            self.stop_reason = stop_reason
            self.num_turns = num_turns
            self.permission_denials = []

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    return fake_sdk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subtype", "errors", "stop_reason"),
    [
        ("error_max_turns", [], None),
        ("error_during_execution", ["Reached maximum number of turns (8)"], None),
        ("error_during_execution", [], "max_turns"),
    ],
)
async def test_sdk_turn_limit_variants_share_one_actionable_public_diagnostic(
    monkeypatch,
    tmp_path: Path,
    subtype,
    errors,
    stop_reason,
):
    sdk_types: dict[str, Any] = {}

    async def query(prompt, options):
        yield sdk_types["ResultMessage"](
            is_error=True,
            subtype=subtype,
            errors=errors,
            stop_reason=stop_reason,
            num_turns=8,
        )

    sdk = _install_sdk(monkeypatch, query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert result.error == "claude_agent_sdk_turn_limit_exceeded"
    assert result.turn_diagnostics == {
        "schema_version": "ai-platform.sdk-turn-diagnostics.v1",
        "terminal_class": "max_turn_exhausted",
        "error_code": "claude_agent_sdk_turn_limit_exceeded",
        "action": "continue_or_narrow_request",
        "retryable": True,
        "counters": {
            "max_turns": 8,
            "turns_observed": 8,
            "assistant_messages": 0,
            "text_blocks": 0,
            "result_messages": 1,
            "tool_admission_denials": 0,
            "skill_invocations": 0,
        },
        "last_public_stage": "runtime",
        "selected_skill": None,
        "used_skills": [],
    }


@pytest.mark.asyncio
async def test_sdk_timeout_and_missing_terminal_are_distinct(monkeypatch, tmp_path: Path):
    async def timeout_query(prompt, options):
        await asyncio.sleep(1)
        if False:
            yield None

    _install_sdk(monkeypatch, timeout_query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        lambda: _settings(timeout_seconds=0.01),
    )
    timed_out = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    async def empty_query(prompt, options):
        if False:
            yield None

    _install_sdk(monkeypatch, empty_query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    missing = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert timed_out.error == "claude_agent_sdk_timeout"
    assert timed_out.turn_diagnostics["terminal_class"] == "timeout"
    assert missing.error == "claude_agent_sdk_missing_structured_terminal"
    assert missing.turn_diagnostics["terminal_class"] == "missing_terminal"


@pytest.mark.asyncio
async def test_selected_skill_not_invoked_and_policy_admission_are_distinct(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}

    async def success_query(prompt, options):
        yield sdk_types["ResultMessage"]()

    sdk = _install_sdk(monkeypatch, success_query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    metadata = {
        "review-skill": {
            "name": "Document review",
            "version": "version-a",
            "availability": "available",
        }
    }
    not_invoked = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
        public_skill_metadata=metadata,
    )
    not_admitted = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
        tool_policy_subjects=[
            {
                "identity": "Skill",
                "declared_identities": ["Skill"],
                "allowed_skill_names": [],
            }
        ],
        execution_policy="sandbox_brokered",
        public_skill_metadata=metadata,
    )

    assert not_invoked.error == "claude_agent_sdk_selected_skill_not_invoked"
    assert not_invoked.turn_diagnostics["terminal_class"] == "selected_skill_not_invoked"
    assert not_invoked.turn_diagnostics["selected_skill"] == metadata["review-skill"]
    assert not_admitted.error == "claude_agent_sdk_selected_skill_not_authorized"
    assert not_admitted.turn_diagnostics["terminal_class"] == "tool_policy_or_admission_failure"


@pytest.mark.asyncio
async def test_sdk_error_terminal_preserves_selected_skill_not_invoked_classification(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}

    async def error_query(prompt, options):
        yield sdk_types["ResultMessage"](
            is_error=True,
            subtype="error_during_execution",
            errors=["private upstream detail"],
        )

    sdk = _install_sdk(monkeypatch, error_query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
    )

    assert result.error == "claude_agent_sdk_selected_skill_not_invoked"
    assert result.turn_diagnostics["terminal_class"] == "selected_skill_not_invoked"
    assert "private upstream detail" not in str(result.turn_diagnostics)


@pytest.mark.asyncio
async def test_generic_upstream_error_never_exposes_private_exception_text(
    monkeypatch,
    tmp_path: Path,
):
    async def query(prompt, options):
        raise RuntimeError("private-token=secret command=do-not-expose")
        if False:
            yield None

    _install_sdk(monkeypatch, query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert result.error == "claude_agent_sdk_upstream_error"
    assert result.turn_diagnostics["terminal_class"] == "upstream_error"
    assert "private-token" not in str(result.turn_diagnostics)
    assert "do-not-expose" not in str(result.turn_diagnostics)


@pytest.mark.asyncio
async def test_success_diagnostics_include_only_public_skill_metadata_and_bounded_counters(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}

    async def query(prompt, options):
        yield sdk_types["AssistantMessage"]([sdk_types["TextBlock"]("working")])
        hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "internal-review-id"},
                "tool_use_id": "tool-secret-id",
            }
        )
        yield sdk_types["ResultMessage"](num_turns=3)

    sdk = _install_sdk(monkeypatch, query)
    sdk_types.update(
        {
            "AssistantMessage": sdk.AssistantMessage,
            "ResultMessage": sdk.ResultMessage,
            "TextBlock": sdk.TextBlock,
        }
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    metadata = {
        "internal-review-id": {
            "name": "Document review",
            "version": "version-a",
            "availability": "available",
        }
    }

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="internal-review-id",
        skills=["internal-review-id"],
        public_skill_metadata=metadata,
    )

    diagnostics = result.turn_diagnostics
    assert result.error is None
    assert diagnostics["terminal_class"] == "completed"
    assert diagnostics["last_public_stage"] == "skills"
    assert diagnostics["selected_skill"] == metadata["internal-review-id"]
    assert diagnostics["used_skills"] == [metadata["internal-review-id"]]
    assert diagnostics["counters"] == {
        "max_turns": 8,
        "turns_observed": 3,
        "assistant_messages": 1,
        "text_blocks": 1,
        "result_messages": 1,
        "tool_admission_denials": 0,
        "skill_invocations": 1,
    }
    assert "internal-review-id" not in str(diagnostics)
    assert "tool-secret-id" not in str(diagnostics)

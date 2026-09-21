import asyncio
import json
import sys
import types

import pytest

from tests.support.claude_mcp import install_mcp_sessions

from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    _canonical_sdk_error,
    _sdk_autocompact_window,
    _sdk_run_timeout_seconds,
    run_claude_agent_sdk,
)
from app.executors.claude.capability_policy import (
    _canonical_tool_policy_subjects,
    _mcp_server_options,
    internal_context_tool_policy_subjects,
)
from app.platform.public_payload import (
    sanitize_public_answer_text,
    sanitize_public_event_candidate,
)
from app.required_tool_contract import (
    SANDBOX_LOCAL_TOOL_IDENTITIES,
    parse_required_tool_declaration,
    with_sandbox_local_tool_capability_subjects,
)


@pytest.fixture(autouse=True)
def synthetic_mcp_sessions(monkeypatch):
    install_mcp_sessions(monkeypatch)


def _full_sandbox_local_tool_capability_subjects(
    existing_subjects,
    *,
    sandbox_provider,
    required_declaration=None,
):
    return with_sandbox_local_tool_capability_subjects(
        existing_subjects,
        sandbox_provider=sandbox_provider,
        required_declaration=required_declaration,
        authorized_sandbox_tool_identities=SANDBOX_LOCAL_TOOL_IDENTITIES,
    )


def test_sdk_timeout_is_unbounded_by_default_and_bounded_when_configured():
    assert (
        _sdk_run_timeout_seconds(
            types.SimpleNamespace(),
            sandbox_brokered=True,
            full_access=False,
        )
        is None
    )
    assert (
        _sdk_run_timeout_seconds(
            types.SimpleNamespace(claude_agent_sdk_timeout_seconds=45),
            sandbox_brokered=True,
            full_access=False,
        )
        == 45.0
    )
    assert (
        _sdk_run_timeout_seconds(
            types.SimpleNamespace(claude_agent_sdk_timeout_seconds=0),
            sandbox_brokered=True,
            full_access=True,
        )
        is None
    )


@pytest.mark.parametrize(
    ("model_max_input_tokens", "expected"),
    [
        (None, None),
        (32_000, 100_000),
        (100_000, 100_000),
        (125_000, 100_000),
        (200_000, 160_000),
        (1_000_000, 800_000),
        (2_000_000, 1_000_000),
    ],
)
def test_sdk_autocompact_window_targets_eighty_percent_within_cli_bounds(
    model_max_input_tokens, expected
):
    assert _sdk_autocompact_window(model_max_input_tokens) == expected


def test_context_limit_error_outranks_prior_tool_denial():
    assert _canonical_sdk_error(
        ["prompt is too long: 100001 tokens > 100000 maximum"],
        terminal_reason="prompt_too_long",
        tool_admission_denials=1,
    ) == "claude_agent_sdk_upstream_error"
    assert _canonical_sdk_error(
        ["Request too large (max 32MB)"],
        terminal_reason="image_error",
        tool_admission_denials=1,
    ) == "claude_agent_sdk_upstream_error"


def _settings():
    return types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        claude_agent_sdk_max_turns=12,
        claude_agent_sdk_timeout_seconds=5,
        claude_agent_sdk_skills="",
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
        claude_agent_model="model-a",
        anthropic_model="",
        anthropic_base_url="",
        anthropic_auth_token="",
        openai_api_key="",
    )


@pytest.mark.asyncio
async def test_sdk_requires_native_client_and_rejects_legacy_query_only(
    monkeypatch, tmp_path
):
    async def legacy_query(*, prompt, options):
        del prompt, options
        if False:
            yield None

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        types.SimpleNamespace(
            AssistantMessage=object,
            ClaudeAgentOptions=object,
            ResultMessage=object,
            TextBlock=object,
            query=legacy_query,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _settings
    )

    with pytest.raises(ClaudeAgentSdkNotAvailable, match="ClaudeSDKClient"):
        await run_claude_agent_sdk(
            prompt="hello",
            cwd=tmp_path,
            skill_id=None,
        )


def _subject(
    *,
    server_id="tenant-server",
    tool_name="search",
    endpoint="https://private.example/mcp",
    public_tool_label="Tenant Search",
):
    return {
        "identity": f"mcp__{server_id}__{tool_name}",
        "mcp_server": server_id,
        "mcp_tool": tool_name,
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
        "public_tool_label": public_tool_label,
        "mcp_server_config": {
            "type": "http",
            "url": endpoint,
        },
    }


def test_mcp_server_options_preserve_runtime_static_and_jwt_headers():
    subject = _subject()
    subject["mcp_server_config"]["headers"] = {
        "X-Static-Key": "configured",
        "JWT-Authorization": "Bearer current.jwt",
    }

    servers = _mcp_server_options(_canonical_tool_policy_subjects([subject]))

    assert servers["tenant-server"] == {
        "type": "http",
        "url": "https://private.example/mcp",
        "headers": {
            "X-Static-Key": "configured",
            "JWT-Authorization": "Bearer current.jwt",
        },
    }


def _skill_subject(skill_name="qa-review"):
    return {
        "identity": "Skill",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "allowed_parameter_keys": ["skill"],
        "required_parameter_keys": ["skill"],
        "allowed_skill_names": [skill_name],
        "risk_level": "low",
        "write_capable": False,
    }


def _captured_sdk_prompt(captured):
    return captured["sdk_user_messages"][0]["message"]["content"]


def _client_sdk(module, captured):
    class FakeClient:
        def __init__(self, options):
            self.options = options
            self.responses = None
            captured["client_mcp_server_types_at_construction"] = {
                name: config.get("type") if isinstance(config, dict) else None
                for name, config in getattr(options, "mcp_servers", {}).items()
            }

        async def connect(self):
            captured["client_connected"] = True

        async def query(self, prompt, session_id="default"):
            assert session_id
            self.responses = module.query(prompt=prompt, options=self.options)

        async def receive_response(self):
            async for message in self.responses:
                yield message

        async def disconnect(self):
            captured["client_disconnected"] = True

    module.ClaudeSDKClient = FakeClient
    return module


def _fake_sdk(
    captured,
    *,
    hook_invocations,
    thinking_text=None,
    mirror_error=False,
    append_provider_session=True,
    append_provider_subpath=None,
    supports_structured_output=False,
):
    class ThinkingBlock:
        def __init__(self, thinking):
            self.thinking = thinking

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class TextBlock:
        pass

    class StreamEvent:
        pass

    class MirrorErrorMessage:
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
        if supports_structured_output:
            structured_output = {"answer": "done", "deliverables": []}

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.__dict__.update(kwargs)

    async def query(*, prompt, options):
        captured["sdk_user_messages"] = [item async for item in prompt]
        if mirror_error:
            yield MirrorErrorMessage()
            return
        if append_provider_session and getattr(options, "session_store", None) is not None:
            session_key = getattr(options, "session_id", None) or getattr(options, "resume", None)
            await options.session_store.append(
                {"session_id": session_key, "subpath": append_provider_subpath}
                if append_provider_subpath
                else session_key,
                [{"uuid": "entry-ack"}],
            )
        for hook_name, hook_input, tool_call_id in hook_invocations:
            matchers = captured["hooks"][hook_name]
            if hook_name == "PreToolUse":
                matcher = matchers[0]
            else:
                tool_name = str(hook_input.get("tool_name") or "")
                matcher_name = (
                    "Skill"
                    if tool_name.lower() == "skill"
                    else "mcp__*"
                    if tool_name.startswith("mcp__")
                    else None
                )
                matcher = next(
                    item for item in matchers if item.matcher == matcher_name
                )
            hook_result = await matcher.hooks[0](hook_input, tool_call_id, {})
            captured.setdefault("hook_results", []).append((hook_name, hook_result))
        if thinking_text is not None:
            yield AssistantMessage([ThinkingBlock(thinking_text)])
        terminal = ResultMessage()
        if getattr(options, "session_store", None) is not None:
            terminal.session_id = getattr(options, "session_id", None) or getattr(options, "resume", None)
        yield terminal

    return _client_sdk(types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        MirrorErrorMessage=MirrorErrorMessage,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        ThinkingBlock=ThinkingBlock,
        query=query,
    ), captured)


def _scripted_sdk(
    captured,
    steps,
    *,
    result_text="done",
    result_error: str | None = None,
    permission_denials=None,
):
    denials = permission_denials

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ThinkingBlock:
        def __init__(self, thinking):
            self.thinking = thinking

    class ToolUseBlock:
        def __init__(self, *, id, name, input):
            self.id = id
            self.name = name
            self.input = input

    class AssistantMessage:
        def __init__(self, text):
            self.content = [TextBlock(text)]

    class StreamEvent:
        def __init__(self, event):
            self.event = event

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = result_text
        is_error = result_error is not None
        errors = [result_error] if result_error is not None else None
        stop_reason = "end_turn"
        num_turns = 1
        permission_denials = denials

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del options
        captured["sdk_user_messages"] = [item async for item in prompt]

        async def invoke_hook(value):
            hook_name, hook_input, tool_call_id = value
            matchers = captured["hooks"][hook_name]
            if hook_name == "PreToolUse":
                matcher = matchers[0]
            else:
                tool_name = str(hook_input.get("tool_name") or "")
                matcher_name = (
                    "Skill"
                    if tool_name.lower() == "skill"
                    else "mcp__*"
                    if tool_name.startswith("mcp__")
                    else None
                )
                matcher = next(
                    item for item in matchers if item.matcher == matcher_name
                )
            hook_result = await matcher.hooks[0](hook_input, tool_call_id, {})
            captured.setdefault("hook_results", []).append((hook_name, hook_result))

        for step in steps:
            kind, value = step
            if kind == "assistant":
                yield AssistantMessage(value)
            elif kind == "assistant_blocks":
                message = AssistantMessage("")
                message.content = value
                yield message
            elif kind == "assistant_tool":
                message = AssistantMessage("")
                message.content = [
                    ThinkingBlock(value["thinking"]),
                    ToolUseBlock(
                        id=value["id"],
                        name=value["name"],
                        input=value["input"],
                    ),
                ]
                yield message
            elif kind == "stream":
                yield StreamEvent(value)
            elif kind in {"hook", "cancel_hook"}:
                if kind == "cancel_hook":
                    hook_task = asyncio.create_task(invoke_hook(value))
                    await asyncio.sleep(0)
                    hook_task.cancel()
                    try:
                        await hook_task
                    except asyncio.CancelledError:
                        pass
                else:
                    await invoke_hook(value)
            elif kind == "concurrent_hooks":
                await asyncio.gather(*(invoke_hook(item) for item in value))
            elif kind == "probe":
                value()
        yield ResultMessage()

    return _client_sdk(types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        ThinkingBlock=ThinkingBlock,
        ToolUseBlock=ToolUseBlock,
        query=query,
    ), captured)


def _stream_steps(text, *, index=0):
    return [
        (
            "stream",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text"},
            },
        ),
        (
            "stream",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("stream", {"type": "content_block_stop", "index": index}),
    ]


async def _acknowledge_capability_evidence(_evidence):
    return True


@pytest.mark.asyncio
async def test_sdk_structured_output_protocol_does_not_bypass_capability_admission(
    monkeypatch,
    tmp_path,
):
    captured = {}
    lifecycle_facts = []
    hook_input = {
        "tool_name": "StructuredOutput",
        "tool_use_id": "structured-output-call-1",
        "tool_input": {"answer": "done", "deliverables": []},
    }

    async def record_lifecycle(fact):
        lifecycle_facts.append(fact)
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                ("PreToolUse", hook_input, hook_input["tool_use_id"]),
            ],
            supports_structured_output=True,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        on_tool_lifecycle=record_lifecycle,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    permission = await captured["can_use_tool"](
        hook_input["tool_name"],
        hook_input["tool_input"],
        {"tool_use_id": hook_input["tool_use_id"]},
    )
    assert pretool_output["permissionDecision"] == "deny"
    assert permission.behavior == "deny"
    assert lifecycle_facts == []
    assert result.error is None
    assert result.capability_evidence == []


@pytest.mark.asyncio
async def test_sdk_structured_output_name_is_not_a_global_tool_allowance(
    monkeypatch,
    tmp_path,
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[]),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
    )

    permission = await captured["can_use_tool"](
        "StructuredOutput",
        {"answer": "done", "deliverables": []},
        {"tool_use_id": "structured-output-call-1"},
    )
    assert permission.behavior == "deny"
    assert permission.message == "tool_identity_malformed"


@pytest.mark.parametrize(
    ("thinking_effort", "expected_thinking", "expected_effort"),
    [
        ("auto", {"type": "adaptive", "display": "omitted"}, None),
        ("off", {"type": "adaptive", "display": "omitted"}, None),
        *[
            (level, {"type": "adaptive", "display": "omitted"}, level)
            for level in ("low", "medium", "high")
        ],
    ],
)
@pytest.mark.asyncio
async def test_sdk_thinking_options_follow_the_run_preference(
    monkeypatch,
    tmp_path,
    thinking_effort,
    expected_thinking,
    expected_effort,
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[]),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        thinking_effort=thinking_effort,
    )

    assert result.error is None
    assert captured.get("thinking") == expected_thinking
    assert captured.get("effort") == expected_effort


@pytest.mark.asyncio
async def test_sdk_auto_does_not_publish_an_unexpected_thinking_block(
    monkeypatch,
    tmp_path,
):
    captured, published = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[],
            thinking_text="Unexpected public summary",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        thinking_effort="auto",
        run_id="run-thinking-auto",
        attempt_id="attempt-1",
        on_agent_event=lambda batch: published.extend(batch) or True,
    )

    assert result.error is None
    assert "Unexpected public summary" not in repr(published)


@pytest.mark.asyncio
async def test_sandbox_bash_subject_is_exposed_and_admitted_with_acknowledged_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts = {}, []
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {
            "command": "python --version",
            "description": "inspect the sandbox",
        },
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                ("PreToolUse", hook_input, hook_input["tool_use_id"]),
                ("PostToolUse", hook_input, hook_input["tool_use_id"]),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    async def acknowledge(fact):
        lifecycle_facts.append((fact["invocation_id"], fact["lifecycle"]))
        return True

    result = await run_claude_agent_sdk(
        prompt="inspect the sandbox",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert result.error is None
    assert result.message == "done"
    assert captured["allowed_tools"] == [
        "Read",
        "Glob",
        "Grep",
        "LS",
        "Bash",
        "Write",
        "Edit",
        "NotebookEdit",
    ]
    assert pretool_output["permissionDecision"] == "allow"
    assert lifecycle_facts == [
        ("bash-call-1", "started"),
        ("bash-call-1", "completed"),
    ]


@pytest.mark.asyncio
async def test_sandbox_grep_is_workspace_bounded_and_records_acknowledged_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts = {}, []
    (tmp_path / "inputs").mkdir()
    (tmp_path / ".pins").mkdir()
    (tmp_path / "inputs" / "public.md").write_text("TODO public", encoding="utf-8")
    (tmp_path / ".pins" / "private.md").write_text("TODO private", encoding="utf-8")
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "grep-call-1",
        "tool_input": {
            "pattern": "TODO",
            "path": str(tmp_path),
            "glob": "**/*.md",
            "-n": True,
        },
    }
    post_hook_input = {
        **hook_input,
        "tool_response": (
            "inputs/public.md:1:TODO public\n"
            ".pins/private.md:1:TODO private"
        ),
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                ("PreToolUse", hook_input, hook_input["tool_use_id"]),
                ("PostToolUse", post_hook_input, hook_input["tool_use_id"]),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    async def acknowledge(fact):
        lifecycle_facts.append((fact["invocation_id"], fact["lifecycle"]))
        return True

    result = await run_claude_agent_sdk(
        prompt="search the workspace",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    filtered_output = captured["hook_results"][1][1]["hookSpecificOutput"][
        "updatedToolOutput"
    ]
    assert result.error is None
    assert pretool_output["permissionDecision"] == "allow"
    assert "inputs/public.md:1:TODO public" in filtered_output
    assert "private.md" not in filtered_output
    assert "filtered or truncated" in filtered_output
    assert lifecycle_facts == [
        ("grep-call-1", "started"),
        ("grep-call-1", "completed"),
    ]


@pytest.mark.asyncio
async def test_sandbox_grep_denies_outside_workspace_path(monkeypatch, tmp_path):
    captured = {}
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "grep-call-1",
        "tool_input": {"pattern": "TODO", "path": str(tmp_path.parent / "outside")},
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, hook_input["tool_use_id"])],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search outside the workspace",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=_acknowledge_capability_evidence,
    )

    assert (
        captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    assert result.turn_diagnostics["counters"] == {
        "max_turns": 12,
        "turns_observed": 1,
        "assistant_messages": 0,
        "text_blocks": 0,
        "result_messages": 1,
        "tool_admission_denials": 1,
        "tool_policy_denials": 1,
        "tool_lifecycle_denials": 0,
        "skill_invocations": 0,
        "public_projection_omissions": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_outcome", ["missing", "false", "exception"])
async def test_autonomous_sandbox_bash_pretool_denies_unacknowledged_lifecycle(
    monkeypatch,
    tmp_path,
    callback_outcome,
):
    captured = {}
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "python --version"},
    }

    async def acknowledge(_fact):
        if callback_outcome == "exception":
            raise RuntimeError("private callback failure")
        return False

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, hook_input["tool_use_id"])],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="inspect the sandbox",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=None if callback_outcome == "missing" else acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert pretool_output["permissionDecision"] == "deny"
    assert (
        pretool_output["permissionDecisionReason"]
        == "required_tool_completion_evidence_mismatch"
    )
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == ""
    assert result.turn_diagnostics["counters"]["tool_policy_denials"] == 0
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 1


@pytest.mark.asyncio
async def test_autonomous_sandbox_bash_preserves_pretool_narration_on_missing_terminal_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "python --version"},
    }
    public_text = "I will inspect the sandbox. The inspection started."
    steps = [
        *_stream_steps("I will inspect the sandbox. ", index=0),
        ("hook", ("PreToolUse", hook_input, "bash-call-1")),
        *_stream_steps("The inspection started.", index=1),
        ("assistant", public_text),
    ]

    async def acknowledge(_fact):
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=public_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="inspect the sandbox",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert "".join(deltas) == public_text
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == public_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["Write", "Edit", "NotebookEdit"],
)
async def test_sandbox_effectful_tool_preserves_inflight_text_without_terminal_lifecycle(
    monkeypatch,
    tmp_path,
    tool_name,
):
    captured, deltas, lifecycle_facts = {}, [], []
    file_path = str(tmp_path / "output" / "output.txt")
    tool_input = {
        "Read": {"file_path": file_path},
        "Glob": {"pattern": "output/*.txt", "path": str(tmp_path)},
        "Grep": {"pattern": "done", "path": str(tmp_path), "glob": "*.txt"},
        "LS": {"path": str(tmp_path)},
        "Write": {"file_path": file_path, "content": "done"},
        "Edit": {
            "file_path": file_path,
            "old_string": "before",
            "new_string": "after",
        },
        "NotebookEdit": {
            "notebook_path": str(tmp_path / "output" / "output.ipynb"),
            "new_source": "print('done')",
        },
    }[tool_name]
    hook_input = {
        "tool_name": tool_name,
        "tool_use_id": "local-call-1",
        "tool_input": tool_input,
    }
    public_text = "The workspace change has started."
    steps = [
        ("hook", ("PreToolUse", hook_input, "local-call-1")),
        *_stream_steps(public_text),
        ("assistant", public_text),
    ]

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=public_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="use one sandbox-local tool",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert lifecycle_facts == [
        {
            "fact_kind": "tool_invocation",
            "tool_name": tool_name,
            "invocation_id": "local-call-1",
            "lifecycle": "started",
        }
    ]
    assert "".join(deltas) == public_text
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == public_text


@pytest.mark.asyncio
async def test_sandbox_effectful_tool_streams_before_and_after_verified_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, deltas, lifecycle_facts, observed_before_result = {}, [], [], []
    hook_input = {
        "tool_name": "Write",
        "tool_use_id": "local-call-1",
        "tool_input": {
            "file_path": str(tmp_path / "output" / "output.txt"),
            "content": "done",
        },
    }
    public_text = "I will update the file. The file was updated."
    steps = [
        *_stream_steps("I will update the file. ", index=0),
        ("hook", ("PreToolUse", hook_input, "local-call-1")),
        ("hook", ("PostToolUse", hook_input, "local-call-1")),
        *_stream_steps("The file was updated.", index=1),
        ("probe", lambda: observed_before_result.extend(deltas)),
        ("assistant", public_text),
    ]

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=public_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="update one sandbox file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert observed_before_result
    assert public_text.startswith("".join(observed_before_result))
    assert [fact["lifecycle"] for fact in lifecycle_facts] == [
        "started",
        "completed",
    ]
    assert result.error is None
    assert result.message == public_text
    assert "".join(deltas) == public_text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "LS"])
async def test_sandbox_read_only_tool_streams_only_outside_verified_lifecycle(
    monkeypatch,
    tmp_path,
    tool_name,
):
    captured, deltas, lifecycle_facts = {}, [], []
    file_path = str(tmp_path / "output" / "output.txt")
    tool_input = {
        "Read": {"file_path": file_path},
        "Glob": {"pattern": "output/*.txt", "path": str(tmp_path)},
        "Grep": {"pattern": "done", "path": str(tmp_path), "glob": "*.txt"},
        "LS": {"path": str(tmp_path)},
    }[tool_name]
    hook_input = {
        "tool_name": tool_name,
        "tool_use_id": "read-only-call-1",
        "tool_input": tool_input,
    }
    steps = [
        *_stream_steps("Before read. ", index=0),
        ("hook", ("PreToolUse", hook_input, "read-only-call-1")),
        *_stream_steps("private file content", index=1),
        ("hook", ("PostToolUse", hook_input, "read-only-call-1")),
        *_stream_steps("After read.", index=2),
        ("assistant", "Before read. private file contentAfter read."),
    ]

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text="Before read. private file contentAfter read.",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="use one sandbox read-only tool",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert [fact["lifecycle"] for fact in lifecycle_facts] == [
        "started",
        "completed",
    ]
    expected_text = "Before read. private file contentAfter read."
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_hook", "terminal_lifecycle", "terminal_event"),
    [
        ("PostToolUse", "completed", "tool.completed"),
        ("PostToolUseFailure", "failed", "tool.failed"),
    ],
)
async def test_failed_answer_projection_does_not_hide_verified_tool_terminal(
    monkeypatch,
    tmp_path,
    terminal_hook,
    terminal_lifecycle,
    terminal_event,
):
    captured, deltas, lifecycle_facts, public_events = {}, [], [], []
    call_id = "read-only-call-1"
    hook_input = {
        "tool_name": "Read",
        "tool_use_id": call_id,
        "tool_input": {"file_path": str(tmp_path / "output.txt")},
    }

    class ToolUseBlock:
        id = call_id
        name = "Read"
        input = hook_input["tool_input"]

    oversized_text = "x " * 131_073
    steps = [
        *_stream_steps(oversized_text),
        ("assistant_blocks", [ToolUseBlock()]),
        ("hook", ("PreToolUse", hook_input, call_id)),
        ("hook", (terminal_hook, hook_input, call_id)),
    ]

    async def acknowledge_lifecycle(fact):
        lifecycle_facts.append(dict(fact))
        return True

    async def acknowledge_public_events(events):
        public_events.extend(events)
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=oversized_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="read one file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge_lifecycle,
        on_text=deltas.append,
        on_agent_event=acknowledge_public_events,
        run_id="run-projection-failed",
        attempt_id="attempt-1",
    )

    assert [fact["lifecycle"] for fact in lifecycle_facts] == [
        "started",
        terminal_lifecycle,
    ]
    assert [
        event.event_type
        for event in public_events
        if event.event_type in {"tool.started", "tool.completed", "tool.failed"}
    ] == ["tool.started", terminal_event]
    assert result.error is None
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 0


@pytest.mark.asyncio
async def test_failed_answer_projection_keeps_skill_and_bash_receipts(
    monkeypatch,
    tmp_path,
):
    captured, deltas, lifecycle_facts, capability_facts = {}, [], [], []
    oversized_text = "x " * 131_073
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    bash_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "printf safe"},
    }
    read_input = {
        "tool_name": "Read",
        "tool_use_id": "read-call-1",
        "tool_input": {"file_path": str(tmp_path / "workspace.txt")},
    }
    steps = [
        *_stream_steps(oversized_text),
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        ("hook", ("PreToolUse", bash_input, "bash-call-1")),
        ("hook", ("PostToolUse", bash_input, "bash-call-1")),
        ("hook", ("PreToolUse", read_input, "read-call-1")),
        ("hook", ("PostToolUse", read_input, "read-call-1")),
    ]

    async def acknowledge_lifecycle(fact):
        lifecycle_facts.append(dict(fact))
        return True

    async def acknowledge_capability(fact):
        capability_facts.append(dict(fact))
        return True

    subjects = [
        _skill_subject("qa-review"),
        *_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=oversized_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review the workspace",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects,
        on_tool_lifecycle=acknowledge_lifecycle,
        on_capability_evidence=acknowledge_capability,
        on_text=deltas.append,
    )

    assert [(fact["tool_name"], fact["lifecycle"]) for fact in lifecycle_facts] == [
        ("Bash", "started"),
        ("Bash", "completed"),
        ("Read", "started"),
        ("Read", "completed"),
    ]
    assert [
        (fact["canonical_identity"], fact["lifecycle_phase"])
        for fact in capability_facts
    ] == [("qa-review", "invocation_requested"), ("qa-review", "completed")]
    assert result.used_skills == ["qa-review"]
    assert result.error is None
    assert result.message == oversized_text
    assert "".join(deltas) == oversized_text
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 0


@pytest.mark.asyncio
async def test_sandbox_read_only_tool_without_terminal_receipt_fails_closed(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    hook_input = {
        "tool_name": "Read",
        "tool_use_id": "read-only-call-1",
        "tool_input": {"file_path": str(tmp_path / "output.txt")},
    }
    steps = [
        ("hook", ("PreToolUse", hook_input, "read-only-call-1")),
        *_stream_steps("private file content"),
        ("assistant", "private file content"),
    ]

    async def acknowledge(_fact):
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="private file content"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="read one file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert "".join(deltas) == "private file content"
    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert result.message == "private file content"


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_mode", ["missing", "false", "exception"])
async def test_sandbox_read_only_tool_denies_unacknowledged_start(
    monkeypatch,
    tmp_path,
    callback_mode,
):
    captured, deltas = {}, []
    hook_input = {
        "tool_name": "Read",
        "tool_use_id": "read-only-call-1",
        "tool_input": {"file_path": str(tmp_path / "output.txt")},
    }
    steps = [
        ("hook", ("PreToolUse", hook_input, "read-only-call-1")),
        *_stream_steps("private file content"),
        ("assistant", "private file content"),
    ]

    async def acknowledge(_fact):
        if callback_mode == "exception":
            raise RuntimeError("private callback failure")
        return False

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="private file content"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="read one file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=None if callback_mode == "missing" else acknowledge,
        on_text=deltas.append,
    )

    hook_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert deltas == []
    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert result.message == ""


@pytest.mark.asyncio
async def test_sandbox_read_only_lifecycle_denial_is_counted_on_sdk_error_terminal(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts = {}, []
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "read-only-call-1",
        "tool_input": {"pattern": "done", "path": str(tmp_path)},
    }

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("hook", ("PreToolUse", hook_input, "read-only-call-1"))],
            result_error="simulated SDK failure",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search the workspace",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert lifecycle_facts == [
        {
            "fact_kind": "tool_invocation",
            "tool_name": "Grep",
            "invocation_id": "read-only-call-1",
            "lifecycle": "started",
        }
    ]
    assert result.error is not None
    assert result.message == ""
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hook_call_id", "callback_call_id"),
    [
        (None, None),
        ("local-call-a", "local-call-b"),
        ("   ", "   "),
        (" local-call ", " local-call "),
        ("x" * 513, "x" * 513),
        ("local\ncall", "local\ncall"),
        ("local\x7fcall", "local\x7fcall"),
        ("local\x85call", "local\x85call"),
        ("local\u202ecall", "local\u202ecall"),
        ("local-\u8c03\u7528", "local-\u8c03\u7528"),
    ],
)
@pytest.mark.parametrize(
    ("tool_name", "expected_error"),
    [
        ("Write", "required_tool_completion_evidence_mismatch"),
        ("Read", "claude_agent_sdk_tool_admission_failed"),
    ],
)
async def test_sandbox_local_tool_denies_missing_or_conflicting_call_id(
    monkeypatch,
    tmp_path,
    hook_call_id,
    callback_call_id,
    tool_name,
    expected_error,
):
    captured = {}
    hook_input = {
        "tool_name": tool_name,
        "tool_input": {
            "file_path": str(tmp_path / "output" / "output.txt"),
            **({"content": "done"} if tool_name == "Write" else {}),
        },
    }
    if hook_call_id is not None:
        hook_input["tool_use_id"] = hook_call_id

    async def acknowledge(_fact):
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, callback_call_id)],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="write one file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert (
        captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    assert result.error == expected_error
    assert result.message == ""


@pytest.mark.asyncio
async def test_sandbox_local_tool_call_id_is_redacted_from_terminal_answer(
    monkeypatch,
    tmp_path,
):
    captured, deltas, lifecycle_facts = {}, [], []
    call_id = "private-write-call-1"
    hook_input = {
        "tool_name": "Write",
        "tool_use_id": call_id,
        "tool_input": {
            "file_path": str(tmp_path / "output" / "output.txt"),
            "content": "done",
        },
    }

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                ("hook", ("PreToolUse", hook_input, call_id)),
                ("hook", ("PostToolUse", hook_input, call_id)),
            ],
            result_text=f"Completed {call_id}.",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="write one file",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert [fact["lifecycle"] for fact in lifecycle_facts] == [
        "started",
        "completed",
    ]
    assert result.error is None
    assert result.message
    assert "".join(deltas) == result.message
    assert call_id not in result.message


@pytest.mark.asyncio
async def test_sandbox_bash_availability_releases_terminal_answer_when_not_invoked(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    direct_answer = "No tool was needed. " + ("ordinary text " * 500)
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _stream_steps(direct_answer),
            result_text=direct_answer,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer directly when no tool is needed",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_text=deltas.append,
    )

    assert result.error is None
    assert result.message == direct_answer
    assert "".join(deltas) == direct_answer


@pytest.mark.asyncio
async def test_prior_mcp_completion_preserves_narration_before_bash_failure_terminal(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    mcp_subject = _subject()
    bash_subject = next(
        subject
        for subject in _full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        )
        if subject["identity"] == "Bash"
    )
    bash_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "false"},
    }
    public_text = "I will inspect the sandbox next. "
    steps = [
        *_mcp_hook_steps(mcp_subject),
        *_stream_steps(public_text),
        ("hook", ("PreToolUse", bash_input, "bash-call-1")),
        ("hook", ("PostToolUseFailure", bash_input, "bash-call-1")),
    ]

    async def acknowledge_tool_lifecycle(_fact):
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text=public_text,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="use the configured capabilities",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[mcp_subject, bash_subject],
        on_capability_evidence=_acknowledge_capability_evidence,
        on_tool_lifecycle=acknowledge_tool_lifecycle,
        on_text=deltas.append,
    )

    assert result.error is None
    assert result.message == public_text
    assert deltas
    assert public_text.startswith("".join(deltas))


@pytest.mark.asyncio
async def test_sandbox_bash_fails_closed_without_sdk_hook_matcher(
    monkeypatch,
    tmp_path,
):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    del sdk.HookMatcher
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="inspect the sandbox",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="docker"
        ),
    )

    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert result.message == ""
    assert captured == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_outcome", ["missing", "false", "exception"])
async def test_required_sandbox_bash_pretool_denies_unacknowledged_lifecycle(
    monkeypatch,
    tmp_path,
    callback_outcome,
):
    captured = {}
    declaration = parse_required_tool_declaration("请执行 Bash 命令 pwd")
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "pwd"},
    }

    async def acknowledge(_fact):
        if callback_outcome == "exception":
            raise RuntimeError("private callback failure")
        return False

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, hook_input["tool_use_id"])],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="run the required command",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=None if callback_outcome == "missing" else acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert pretool_output["permissionDecision"] == "deny"
    assert (
        pretool_output["permissionDecisionReason"]
        == "required_tool_completion_evidence_mismatch"
    )
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == ""


@pytest.mark.asyncio
async def test_required_sandbox_bash_preserves_answer_without_terminal_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    declaration = parse_required_tool_declaration("请执行 Bash 命令 pwd")
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "pwd"},
    }
    steps = [
        ("hook", ("PreToolUse", hook_input, "bash-call-1")),
        *_stream_steps("must remain private"),
        ("assistant", "must remain private"),
    ]

    async def acknowledge(_fact):
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="must remain private"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="run the required command",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert "".join(deltas) == "must remain private"
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == "must remain private"


@pytest.mark.asyncio
async def test_required_sandbox_bash_rejects_duplicate_started_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts = {}, []
    declaration = parse_required_tool_declaration("请执行 Bash 命令 pwd")
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "pwd"},
    }

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                ("PreToolUse", hook_input, "bash-call-1"),
                ("PreToolUse", hook_input, "bash-call-1"),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="run the required command",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert len(lifecycle_facts) == 1
    assert (
        captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"]
        == "allow"
    )
    assert (
        captured["hook_results"][1][1]["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    assert result.error == "required_tool_completion_evidence_mismatch"


@pytest.mark.asyncio
async def test_required_sandbox_bash_releases_only_after_acknowledged_completion(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts, deltas = {}, [], []
    declaration = parse_required_tool_declaration("请执行 Bash 命令 pwd")
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "pwd"},
    }

    async def acknowledge(fact):
        lifecycle_facts.append((fact["invocation_id"], fact["lifecycle"]))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                ("hook", ("PreToolUse", hook_input, "bash-call-1")),
                ("hook", ("PostToolUse", hook_input, "bash-call-1")),
            ],
            result_text="command completed",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="run the required command",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert lifecycle_facts == [
        ("bash-call-1", "started"),
        ("bash-call-1", "completed"),
    ]
    assert result.error is None
    assert result.message == "command completed"
    assert "".join(deltas) == "command completed"


@pytest.mark.asyncio
async def test_required_sandbox_bash_failure_after_success_preserves_published_prefix(
    monkeypatch,
    tmp_path,
):
    captured, lifecycle_facts, deltas = {}, [], []
    declaration = parse_required_tool_declaration("请执行 Bash 命令 pwd")
    first_call = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "pwd"},
    }
    second_call = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-2",
        "tool_input": {"command": "false"},
        "error": "process exited with code 1",
    }

    async def acknowledge(fact):
        lifecycle_facts.append((fact["invocation_id"], fact["lifecycle"]))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                ("hook", ("PreToolUse", first_call, "bash-call-1")),
                ("hook", ("PostToolUse", first_call, "bash-call-1")),
                ("hook", ("PreToolUse", second_call, "bash-call-2")),
                ("hook", ("PostToolUseFailure", second_call, "bash-call-2")),
                ("assistant", "must not be published"),
            ],
            result_text="must not be published",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="run the required command",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert lifecycle_facts == [
        ("bash-call-1", "started"),
        ("bash-call-1", "completed"),
        ("bash-call-2", "started"),
        ("bash-call-2", "failed"),
    ]
    assert "".join(deltas) == "must not be published"
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == "must not be published"
    failed_call = next(
        item
        for item in result.runtime_diagnostics["tool_calls"]
        if item["invocation_id"] == "bash-call-2"
    )
    assert failed_call["tool_input"] == {"command": "false"}
    assert failed_call["last_stage"] == "failed"
    assert failed_call["state"] == "failed"
    assert failed_call["failure"]["error"] == "process exited with code 1"


@pytest.mark.asyncio
async def test_local_sdk_bash_remains_unavailable(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[]),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="inspect without sandbox execution",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="worker_local_legacy",
    )
    denied = await captured["can_use_tool"]("Bash", {"command": "pwd"})

    assert result.error is None
    assert "Bash" not in captured["tools"]
    assert "Bash" not in captured["allowed_tools"]
    assert denied.behavior == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_input", [None, [], "invalid"])
async def test_sdk_pretool_denies_non_mapping_hook_input_without_crashing(
    monkeypatch, tmp_path, hook_input
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, None)],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="reject malformed tool input",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="worker_local_legacy",
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert pretool_output["permissionDecision"] == "deny"
    assert result.used_sdk is True


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_kind", ["skill", "mcp"])
@pytest.mark.parametrize("callback_outcome", ["missing", "false", "exception"])
async def test_sdk_pretool_denies_when_invocation_evidence_is_not_acknowledged(
    monkeypatch,
    tmp_path,
    capability_kind,
    callback_outcome,
):
    captured = {}
    if capability_kind == "skill":
        hook_input = {
            "tool_name": "Skill",
            "tool_use_id": "skill-call-1",
            "tool_input": {"skill": "qa-review"},
        }
        subjects = [_skill_subject()]
        skill_id = "qa-review"
        skills = ["qa-review"]
    else:
        hook_input = {
            "tool_name": "mcp__tenant-server__search",
            "tool_use_id": "mcp-call-1",
            "tool_input": {},
        }
        subjects = [_subject()]
        skill_id = "general-chat"
        skills = None

    async def acknowledge(_evidence):
        if callback_outcome == "exception":
            raise RuntimeError("private callback failure")
        return callback_outcome != "false"

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[("PreToolUse", hook_input, hook_input["tool_use_id"])],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="use the configured capability",
        cwd=tmp_path,
        skill_id=skill_id,
        skills=skills,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects,
        on_capability_evidence=None if callback_outcome == "missing" else acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert pretool_output["permissionDecision"] == "deny"
    assert (
        pretool_output["permissionDecisionReason"]
        == "required_tool_completion_evidence_mismatch"
    )
    assert result.error == "required_tool_completion_evidence_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_policy", ["worker_local_legacy", "sandbox_brokered"]
)
async def test_sdk_profile_system_prompt_appends_to_claude_code_without_entering_user_stream(
    monkeypatch,
    tmp_path,
    execution_policy,
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[]),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    await run_claude_agent_sdk(
        prompt="User supplied question",
        system_prompt="Private profile instruction",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy=execution_policy,
        tool_policy_subjects=[_subject()],
    )

    assert captured["system_prompt"] == {
        "type": "preset",
        "preset": "claude_code",
        "append": "Private profile instruction",
    }
    sdk_prompt = _captured_sdk_prompt(captured)
    assert sdk_prompt.startswith("User supplied question")
    assert "Private profile instruction" not in sdk_prompt
    assert sdk_prompt == "User supplied question"
    if execution_policy == "sandbox_brokered":
        assert set(captured["mcp_servers"]) == {"tenant-server"}
        assert captured["client_mcp_server_types_at_construction"] == {
            "tenant-server": "sdk"
        }
        assert "mcp__tenant-server__search" in captured["allowed_tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror_error", [False, True])
async def test_sdk_disconnects_before_selected_mcp_session_closes(
    monkeypatch, tmp_path, mirror_error
):
    from contextlib import asynccontextmanager

    from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration

    captured = {}
    lifecycle = []

    @asynccontextmanager
    async def session_factory(_config):
        lifecycle.append("mcp_open")
        try:
            yield types.SimpleNamespace()
        finally:
            assert captured.get("client_disconnected") is True
            lifecycle.append("mcp_closed")

    async def list_tools(_session):
        return [types.SimpleNamespace(name="search")]

    def prepare(subjects, configs):
        return ClaudeMcpRegistration(
            subjects,
            configs,
            session_factory=session_factory,
            list_tools=list_tools,
        )

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[], mirror_error=mirror_error),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.prepare_claude_mcp", prepare
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_subject()],
    )

    assert result.error == (
        "claude_agent_sdk_provider_session_failed" if mirror_error else None
    )
    assert lifecycle == ["mcp_open", "mcp_closed"]


def _mcp_hook_steps(subject, *, call_id="mcp-call-1", terminal="completed"):
    hook_input = {
        "tool_name": subject["identity"],
        "tool_use_id": call_id,
        "tool_input": {"private": "safe-synthetic-value"},
    }
    terminal_hook = "PostToolUse" if terminal == "completed" else "PostToolUseFailure"
    return [
        ("hook", ("PreToolUse", hook_input, call_id)),
        ("hook", (terminal_hook, hook_input, call_id)),
    ]


@pytest.mark.asyncio
async def test_sdk_permission_denial_closes_started_internal_mcp_lifecycle(
    monkeypatch, tmp_path
):
    captured, lifecycle_facts = {}, []
    subject = internal_context_tool_policy_subjects(["read_session_messages"])[0]
    call_id = "mcp-call-denied"
    hook_input = {
        "tool_name": subject["identity"],
        "tool_use_id": call_id,
        "tool_input": {"limit": 1, "offset": 0, "max_tokens": 10},
    }
    sdk = _scripted_sdk(
        captured,
        [("hook", ("PreToolUse", hook_input, call_id))],
        permission_denials=[
            {
                "tool_name": subject["identity"],
                "tool_use_id": call_id,
                "tool_input": hook_input["tool_input"],
            },
            {
                "tool_name": "Read",
                "tool_use_id": "read-denied-without-start",
                "tool_input": {},
            },
        ],
    )

    def sdk_tool(name, _description, _schema):
        def decorate(function):
            function.name = name
            return function

        return decorate

    sdk.tool = sdk_tool
    sdk.create_sdk_mcp_server = lambda *_args, **_kwargs: object()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    class Retrieval:
        async def execute(self, _action, _identity, _args):
            return {"messages": []}

    async def acknowledge(fact):
        lifecycle_facts.append(
            (fact["tool_name"], fact["invocation_id"], fact["lifecycle"])
        )
        return True

    result = await run_claude_agent_sdk(
        prompt="use scoped history",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        context_retrieval=Retrieval(),
        context_retrieval_identity=ScopedContextRetrievalIdentity(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            agent_id="general-agent",
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert lifecycle_facts == [
        ("MCP", call_id, "started"),
        ("MCP", call_id, "failed"),
    ]
    assert result.error is None
    assert result.turn_diagnostics["counters"]["tool_admission_denials"] == 2


@pytest.mark.asyncio
async def test_sdk_explicit_skillless_harness_registers_no_skill_tool(
    monkeypatch,
    tmp_path,
):
    captured = {}
    reported = []

    async def on_skill_use(skill_name, metadata):
        reported.append((skill_name, metadata))

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, _stream_steps("done"), result_text="done"),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        skills=[],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[],
        on_skill_use=on_skill_use,
    )

    assert result.error is None
    assert result.used_skills == []
    assert reported == []
    assert captured["skills"] == []
    assert "Skill" not in captured["tools"]
    assert "Skill" not in captured["allowed_tools"]
    assert all(
        matcher.matcher != "Skill" for matcher in captured["hooks"]["PostToolUse"]
    )
    denied = await captured["can_use_tool"]("Skill", {"skill": "untrusted-skill"})
    assert denied.behavior == "deny"


@pytest.mark.asyncio
async def test_sdk_records_public_tool_policy_denial_detail(monkeypatch, tmp_path):
    """Denied tool calls must surface tool name + policy reason in diagnostics.

    This is what lets users (and support) see exactly which tool the model was
    blocked from and why, instead of only a denial counter.
    """

    captured = {}
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "grep-call-1",
        "tool_input": {"pattern": "TODO", "path": str(tmp_path.parent / "outside")},
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("hook", ("PreToolUse", hook_input, hook_input["tool_use_id"]))],
            result_error="tool rejected by policy",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search outside the workspace",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=_full_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=_acknowledge_capability_evidence,
    )

    assert (
        captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    detail = result.turn_diagnostics["tool_policy_denials_detail"]
    assert len(detail) == 1
    assert detail[0]["tool_name"] == "Grep"
    assert detail[0]["reason"]
    private_detail = result.runtime_diagnostics["tool_policy_denials"][0]
    assert private_detail["tool_name"] == "Grep"
    assert private_detail["invocation_id"] == "grep-call-1"
    assert private_detail["tool_input"] == hook_input["tool_input"]


@pytest.mark.asyncio
async def test_sdk_available_external_mcp_streams_without_forced_prompt_or_hooks(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    subjects = [
        _subject(server_id="tenant__server", tool_name="search"),
        _subject(
            server_id="other-server",
            tool_name="fetch",
            endpoint="https://other.private.example/mcp",
        ),
    ]
    current_settings = _settings()
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, _stream_steps("done"), result_text="done"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        lambda: current_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="Answer without using a tool",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects,
        on_text=deltas.append,
    )

    assert result.error is None
    assert result.capability_evidence == []
    assert deltas == ["done"]
    assert _captured_sdk_prompt(captured) == "Answer without using a tool"
    assert "Authoritative platform MCP requirement" not in _captured_sdk_prompt(
        captured
    )
    assert set(captured["mcp_servers"]) == {"tenant__server", "other-server"}
    assert {subject["identity"] for subject in subjects}.issubset(
        captured["allowed_tools"]
    )


@pytest.mark.asyncio
async def test_sdk_mcp_pretool_hook_before_tool_block_is_admitted(
    monkeypatch, tmp_path
):
    captured, candidate_batches = {}, []
    subject = _subject()

    async def acknowledge_candidates(candidates):
        candidate_batches.append(candidates)
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _mcp_hook_steps(subject),
            result_text="done",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_capability_evidence=_acknowledge_capability_evidence,
        on_agent_event=acknowledge_candidates,
        run_id="run-pretool-first",
        attempt_id="attempt-1",
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert pretool_output["permissionDecision"] == "allow"
    assert result.error is None
    event_types = [
        event.event_type for batch in candidate_batches for event in batch
    ]
    assert "tool.started" in event_types
    assert "tool.completed" in event_types
    assert "tool.denied" not in event_types


@pytest.mark.asyncio
@pytest.mark.parametrize("block_after_terminal", [False, True])
async def test_sdk_hook_seed_conflict_fails_as_unknown_execution(
    monkeypatch, tmp_path, block_after_terminal
):
    captured, candidate_batches = {}, []
    subject = _subject()
    call_id = "mcp-call-conflict"
    hook_input = {
        "tool_name": subject["identity"],
        "tool_use_id": call_id,
        "tool_input": {"private": "hook-value"},
    }

    class ToolUseBlock:
        pass

    late_block = ToolUseBlock()
    late_block.id = call_id
    late_block.name = subject["identity"]
    late_block.input = {"private": "conflicting-late-value"}
    pretool_step = ("hook", ("PreToolUse", hook_input, call_id))
    terminal_step = ("hook", ("PostToolUse", hook_input, call_id))
    block_step = ("assistant_blocks", [late_block])
    steps = (
        [pretool_step, terminal_step, block_step]
        if block_after_terminal
        else [pretool_step, block_step, terminal_step]
    )

    async def acknowledge_candidates(candidates):
        candidate_batches.append(candidates)
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="done"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_capability_evidence=_acknowledge_capability_evidence,
        on_agent_event=acknowledge_candidates,
        run_id="run-hook-conflict",
        attempt_id="attempt-1",
    )

    assert result.error == "mcp_execution_outcome_unknown"
    assert result.turn_diagnostics["retryable"] is False
    public_events = [event for batch in candidate_batches for event in batch]
    event_types = [event.event_type for event in public_events]
    assert ("tool.completed" in event_types) is block_after_terminal
    assert "hook-value" not in repr(public_events)
    assert "conflicting-late-value" not in repr(public_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected_terminal_stage", ["answer", "result"])
async def test_sdk_completed_mcp_keeps_receipt_error_on_late_publication_failure(
    monkeypatch, tmp_path, rejected_terminal_stage
):
    captured = {}
    subject = _subject()

    async def acknowledge_candidates(candidates):
        event_types = {event.event_type for event in candidates}
        if rejected_terminal_stage == "answer" and "message.delta" in event_types:
            return False
        if rejected_terminal_stage == "result" and "model.completed" in event_types:
            return False
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _mcp_hook_steps(subject),
            result_text="done",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_capability_evidence=_acknowledge_capability_evidence,
        on_agent_event=acknowledge_candidates,
        run_id="run-late-publication-failure",
        attempt_id="attempt-1",
    )

    assert result.error == "mcp_execution_succeeded_receipt_incomplete"
    assert result.turn_diagnostics["action"] == "reconcile_before_retry"
    assert result.turn_diagnostics["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["completed_then_unmatched", "owner_conflict"])
async def test_sdk_unmatched_mcp_terminal_is_outcome_unknown(
    monkeypatch, tmp_path, case
):
    captured = {}
    first = _subject(server_id="first-server", tool_name="search")
    second = _subject(
        server_id="second-server",
        tool_name="lookup",
        endpoint="https://second.private.example/mcp",
    )
    call_id = "mcp-call-shared" if case == "owner_conflict" else "mcp-call-1"
    first_steps = _mcp_hook_steps(first, call_id=call_id)
    second_terminal = _mcp_hook_steps(
        second,
        call_id=call_id if case == "owner_conflict" else "mcp-call-unmatched",
    )[1]
    steps = (
        [first_steps[0], second_terminal]
        if case == "owner_conflict"
        else [*first_steps, second_terminal]
    )

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="done"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[first, second],
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert result.error == "mcp_execution_outcome_unknown"
    assert result.turn_diagnostics["retryable"] is False


@pytest.mark.asyncio
async def test_sdk_registers_only_exact_authorized_external_mcp_subjects(
    monkeypatch, tmp_path
):
    captured = {}
    valid = _subject(server_id="tenant__server", tool_name="search")
    denied = {
        **_subject(server_id="denied", tool_name="lookup"),
        "identity_authorized": False,
    }
    malformed = {
        **_subject(server_id="mismatch", tool_name="lookup"),
        "identity": "mcp__different__lookup",
    }
    monkeypatch.setitem(
        sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[])
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="question",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[valid, denied, malformed],
    )

    assert result.error is None
    assert set(captured["mcp_servers"]) == {"tenant__server"}
    assert valid["identity"] in captured["allowed_tools"]
    assert denied["identity"] not in captured["allowed_tools"]
    assert malformed["identity"] not in captured["allowed_tools"]


def _actual_mcp_steps(outcome, subjects, text, probe):
    first_pre, first_completed = _mcp_hook_steps(subjects[0], call_id="mcp-call-1")
    if outcome == "overflow":
        return [first_pre, first_completed, *_stream_steps(text), ("probe", probe)]
    if outcome == "stale":
        return [first_completed, *_stream_steps(text), ("probe", probe)]
    if outcome == "duplicate":
        return [
            first_pre,
            first_completed,
            first_completed,
            *_stream_steps(text),
            ("probe", probe),
        ]
    if outcome.startswith("multiple_"):
        second_pre, second_terminal = _mcp_hook_steps(
            subjects[1],
            call_id="mcp-call-2",
            terminal=outcome.removeprefix("multiple_"),
        )
        return [
            first_pre,
            second_pre,
            *_stream_steps(text),
            first_completed,
            ("probe", probe),
            second_terminal,
        ]
    steps = [first_pre, *_stream_steps(text), ("probe", probe)]
    if outcome != "incomplete":
        steps.append(
            _mcp_hook_steps(
                subjects[0], terminal="failed" if outcome == "failed" else "completed"
            )[1]
        )
    return steps


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "missing",
        "false",
        "exception",
        "failed",
        "incomplete",
        "overflow",
        "stale",
        "duplicate",
        "multiple_completed",
        "multiple_failed",
    ],
)
async def test_sdk_actual_mcp_publication_gate(monkeypatch, tmp_path, outcome):
    captured, acknowledged, deltas, sealed_probe = {}, [], [], []
    first = _subject()
    subjects = [
        first,
        _subject(
            server_id="other-server",
            tool_name="fetch",
            endpoint="https://other.private.example/mcp",
        ),
    ]
    private_text = f"Safe answer via {first['identity']} with mcp-call-1 at {first['mcp_server_config']['url']}."
    text = (
        "x " * 131_073
        if outcome == "overflow"
        else private_text
        if outcome == "success"
        else "must stay sealed"
    )
    steps = _actual_mcp_steps(
        outcome, subjects, text, lambda: sealed_probe.extend(deltas)
    )

    async def acknowledge(evidence):
        acknowledged.append(dict(evidence))
        if evidence["lifecycle_phase"] == "completed" and outcome == "false":
            return False
        if evidence["lifecycle_phase"] == "completed" and outcome == "exception":
            raise RuntimeError("private callback failure")
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )
    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects,
        on_text=deltas.append,
        on_capability_evidence=None if outcome == "missing" else acknowledge,
    )

    if outcome in {"stale", "duplicate"}:
        assert sealed_probe == []
    else:
        assert sealed_probe
    if outcome in {"success", "multiple_completed"}:
        assert result.error is None
        assert result.message
        assert "".join(deltas) == result.message
        if outcome == "success":
            assert result.capability_evidence == acknowledged
            assert [item["lifecycle_phase"] for item in acknowledged] == [
                "invocation_requested",
                "completed",
            ]
            assert not {
                "tool_input",
                "tool_response",
                "arguments",
                "error",
            } & set().union(*(item.keys() for item in acknowledged))
            for private_value in (
                first["identity"],
                "mcp-call-1",
                first["mcp_server_config"]["url"],
            ):
                assert private_value not in result.message
    else:
        expected = {
            "overflow": None,
            "false": "mcp_execution_succeeded_receipt_incomplete",
            "exception": "mcp_execution_succeeded_receipt_incomplete",
            "failed": "mcp_execution_outcome_unknown",
            "incomplete": "mcp_execution_outcome_unknown",
            "duplicate": "mcp_execution_succeeded_receipt_incomplete",
            "multiple_failed": "mcp_execution_outcome_unknown",
        }.get(outcome, "required_tool_completion_evidence_mismatch")
        if outcome == "overflow":
            assert result.error is None
            assert result.message == text
            assert "".join(deltas) == text
            assert "".join(sealed_probe) == text
        elif outcome in {"stale", "duplicate"}:
            assert (result.error, result.message, deltas) == (expected, "", [])
        else:
            assert result.error == expected
            assert result.message == text
            assert "mcp_execution_" not in result.message
            assert "private callback failure" not in result.message
            assert "retryable" in result.turn_diagnostics
            assert result.turn_diagnostics["retryable"] is False
            assert "".join(deltas) == text
        if outcome == "overflow":
            assert "projection_failure_reason" not in result.turn_diagnostics


@pytest.mark.asyncio
async def test_sdk_reconciles_complete_assistant_suffix_once(
    monkeypatch,
    tmp_path,
):
    captured, deltas, published_before_hook, published_before_terminal = {}, [], [], []
    subject = _subject()
    subject["write_capable"] = True
    call_id = "mcp-call-1"
    before = f"Before {subject['identity']}."
    after = f" After {call_id}."
    steps = [
        *_stream_steps(before),
        ("probe", lambda: published_before_hook.extend(deltas)),
        *_mcp_hook_steps(subject, call_id=call_id),
        *_stream_steps(" After ", index=1),
        ("assistant", before + after),
        ("probe", lambda: published_before_terminal.extend(deltas)),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=before + after),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert published_before_hook
    assert "".join(published_before_hook) == "Before \u2588"
    assert len(published_before_terminal) > len(published_before_hook)
    assert "".join(deltas) == "Before \u2588. After \u2588."
    assert "".join(deltas).count("\u2588.") == 2
    assert result.error is None
    assert result.message == "Before \u2588. After \u2588."
    for private_value in (
        subject["identity"],
        subject["mcp_server_config"]["url"],
        call_id,
        "safe-synthetic-value",
    ):
        assert private_value not in "".join(deltas)


@pytest.mark.asyncio
async def test_unmatched_capability_terminal_cannot_reopen_active_invocation(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    subject = _subject()
    subject["write_capable"] = True
    active_input = {
        "tool_name": subject["identity"],
        "tool_use_id": "mcp-call-a",
        "tool_input": {"private": "safe-synthetic-value"},
    }
    unrelated_terminal = {
        **active_input,
        "tool_use_id": "mcp-call-b",
    }
    steps = [
        ("hook", ("PreToolUse", active_input, "mcp-call-a")),
        ("hook", ("PostToolUse", unrelated_terminal, "mcp-call-b")),
        *_stream_steps("private tool output"),
        ("assistant", "private tool output"),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="private tool output"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert deltas == []
    assert result.error == "mcp_execution_outcome_unknown"
    assert result.turn_diagnostics["retryable"] is False
    assert result.message == ""


@pytest.mark.asyncio
async def test_sdk_converges_live_and_terminal_body_when_assistant_text_differs(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    steps = [
        *_stream_steps("Streamed answer. "),
        ("assistant", "Different complete answer."),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="Different complete answer."),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    expected_text = "Streamed answer. \n\nDifferent complete answer."
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text


@pytest.mark.asyncio
async def test_sdk_verified_effectful_mcp_keeps_only_published_text_on_failed_terminal(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    subject = _subject()
    subject["write_capable"] = True
    call_id = "mcp-call-1"
    steps = [
        *_mcp_hook_steps(subject, call_id=call_id),
        *_stream_steps("provisional answer must not escape"),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text="provisional answer must not escape",
            result_error="simulated terminal failure",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert result.error is not None
    assert result.message == ""
    published = "".join(deltas)
    assert published
    assert "provisional answer must not escape".startswith(published)


@pytest.mark.asyncio
async def test_sdk_preserves_pre_capability_terminal_text(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    observed_before_result = []
    subject = _subject()
    sealed_pre_capability_text = "raw MCP response and /private/path are sealed."
    verified_answer = "Verified MCP final answer streams safely."
    pre_hook, completed_hook = _mcp_hook_steps(subject, call_id="mcp-call-1")
    steps = [
        pre_hook,
        *_stream_steps(sealed_pre_capability_text),
        completed_hook,
        *_stream_steps(verified_answer, index=1),
        ("probe", lambda: observed_before_result.extend(deltas)),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text=f"{sealed_pre_capability_text}{verified_answer}",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[subject],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    expected_text = sealed_pre_capability_text + verified_answer
    assert observed_before_result
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
async def test_sdk_restarts_answer_disclosure_boundary_for_sequential_capabilities(
    monkeypatch,
    tmp_path,
):
    captured = {}
    deltas = []
    candidate_batches = []
    evidence_calls = []
    first = _subject(server_id="first-server", tool_name="search")
    second = _subject(server_id="second-server", tool_name="lookup")
    first["write_capable"] = True
    second["write_capable"] = True
    steps = [
        *_mcp_hook_steps(first, call_id="mcp-call-1"),
        ("assistant", "first verified answer"),
        (
            "hook",
            (
                "PreToolUse",
                {
                    "tool_name": second["identity"],
                    "tool_use_id": "mcp-call-2",
                    "tool_input": {"private": "safe-synthetic-value"},
                },
                "mcp-call-2",
            ),
        ),
        ("assistant", "second capability in-flight text"),
        *_mcp_hook_steps(second, call_id="mcp-call-2", terminal="failed")[1:],
    ]

    async def acknowledge(evidence):
        evidence_calls.append(dict(evidence))
        return (
            evidence["tool_call_id"] != "mcp-call-2"
            or evidence["lifecycle_phase"] != "completed"
        )

    async def acknowledge_candidates(candidates):
        candidate_batches.append(tuple(candidates))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text="first verified answer second capability in-flight text",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="search",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[first, second],
        on_text=deltas.append,
        on_agent_event=acknowledge_candidates,
        on_capability_evidence=acknowledge,
        run_id="run-1187",
        attempt_id="attempt-1",
    )

    candidate_events = [event for batch in candidate_batches for event in batch]
    assert [
        (item["tool_call_id"], item["lifecycle_phase"]) for item in evidence_calls
    ] == [
        ("mcp-call-1", "invocation_requested"),
        ("mcp-call-1", "completed"),
        ("mcp-call-2", "invocation_requested"),
        ("mcp-call-2", "failed"),
    ]
    assert result.error == "mcp_execution_outcome_unknown"
    assert result.turn_diagnostics["retryable"] is False
    assert result.message
    assert "first verified answer" in result.message
    assert "second capability in-flight text" in result.message
    assert "first verified answer" in "".join(deltas)
    assert "second capability in-flight text" in "".join(deltas)
    assert any("first verified " in repr(event.as_dict()) for event in candidate_events)
    assert any(
        "second capability in-flight text" in repr(event.as_dict())
        for event in candidate_events
    )


@pytest.mark.asyncio
async def test_sdk_selected_skill_is_optional_with_unused_available_mcp(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    skill_name = "qa-review"
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": skill_name},
    }
    steps = [
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _scripted_sdk(captured, steps))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id=skill_name,
        skills=[skill_name],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject(skill_name), _subject()],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    sdk_prompt = _captured_sdk_prompt(captured)
    assert result.error is None
    assert result.used_skills == [skill_name]
    assert [item["capability_kind"] for item in result.capability_evidence] == [
        "skill",
        "skill",
    ]
    assert (deltas, result.message) == (["done"], "done")
    assert "Authoritative platform Skill requirement" not in sdk_prompt
    assert "Authoritative platform MCP requirement" not in sdk_prompt
    assert _subject()["identity"] not in sdk_prompt
    assert _subject()["identity"] in captured["allowed_tools"]


@pytest.mark.asyncio
async def test_sdk_agent_skill_set_can_answer_without_invoking_a_skill(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured, _stream_steps("Direct answer."), result_text="Direct answer."
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer from your current context",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review", "reference-search"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[
            {
                **_skill_subject("qa-review"),
                "allowed_skill_names": ["qa-review", "reference-search"],
            },
        ],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert result.error is None
    assert result.used_skills == []
    assert result.capability_evidence == []
    assert "".join(deltas) == "Direct answer."
    assert "Authoritative platform Skill requirement" not in _captured_sdk_prompt(
        captured
    )
    assert {"Skill(qa-review)", "Skill(reference-search)"}.issubset(
        captured["allowed_tools"]
    )


@pytest.mark.asyncio
async def test_sdk_agent_skill_set_records_exact_evidence_for_second_skill(
    monkeypatch, tmp_path
):
    captured, acknowledged = {}, []
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-reference",
        "tool_input": {"skill": "reference-search"},
    }

    async def acknowledge(evidence):
        acknowledged.append(dict(evidence))
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                ("hook", ("PreToolUse", skill_input, "skill-call-reference")),
                ("hook", ("PostToolUse", skill_input, "skill-call-reference")),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )

    result = await run_claude_agent_sdk(
        prompt="find the relevant reference",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review", "reference-search"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[
            {
                **_skill_subject("qa-review"),
                "allowed_skill_names": ["qa-review", "reference-search"],
            },
        ],
        on_capability_evidence=acknowledge,
    )

    assert result.error is None
    assert result.used_skills == ["reference-search"]
    assert [item["canonical_identity"] for item in acknowledged] == [
        "reference-search",
        "reference-search",
    ]
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "optional_skill",
        "stream_parts",
        "shared_mcp",
        "call_id",
        "public_name",
        "expected_replacement",
    ),
    [
        (
            "reference-search",
            ("Using reference-", "search. "),
            False,
            "skill-call-reference",
            "Reference Search",
            "【技能：Ｒｅｆｅｒｅｎｃｅ　Ｓｅａｒｃｈ】",
        ),
        (
            "capability",
            ("Using cap", "ability. "),
            False,
            "capability",
            "Reference Search",
            "【技能：Ｒｅｆｅｒｅｎｃｅ　Ｓｅａｒｃｈ】",
        ),
        (
            "tool",
            ("Using to", "ol. "),
            False,
            "tool",
            "Reference Search",
            "【技能：Ｒｅｆｅｒｅｎｃｅ　Ｓｅａｒｃｈ】",
        ),
        (
            "mcp__tenant-server__search",
            ("Using mcp__tenant-", "server__search. "),
            True,
            "mcp__tenant-server__search",
            "Reference Search",
            "【技能：Ｒｅｆｅｒｅｎｃｅ　Ｓｅａｒｃｈ】",
        ),
        (
            "internal-reference-helper",
            ("Using internal-reference-", "helper. "),
            False,
            "skill-call-private",
            None,
            "【技能】",
        ),
    ],
)
async def test_sdk_redacts_optional_skill_identity_before_failed_receipt(
    monkeypatch,
    tmp_path,
    optional_skill,
    stream_parts,
    shared_mcp,
    call_id,
    public_name,
    expected_replacement,
):
    captured, deltas = {}, []
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": call_id,
        "tool_input": {"skill": optional_skill},
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                (
                    "stream",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    },
                ),
                (
                    "stream",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": stream_parts[0],
                        },
                    },
                ),
                (
                    "stream",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": stream_parts[1]},
                    },
                ),
                ("stream", {"type": "content_block_stop", "index": 0}),
                ("hook", ("PreToolUse", skill_input, call_id)),
                (
                    "hook",
                    ("PostToolUseFailure", skill_input, call_id),
                ),
            ],
            result_text="",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    skill_subject = {
        **_skill_subject("qa-review"),
        "allowed_skill_names": ["qa-review", optional_skill],
    }
    tool_policy_subjects = [skill_subject]
    if shared_mcp:
        tool_policy_subjects.append(_subject())

    result = await run_claude_agent_sdk(
        prompt="find the relevant reference",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review", optional_skill],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=tool_policy_subjects,
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
        public_skill_metadata=(
            {
                optional_skill: {
                    "name": public_name,
                    "version": "1.0.0",
                    "availability": "available",
                }
            }
            if public_name is not None
            else {}
        ),
    )

    public_text = "".join(deltas)
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.used_skills == []
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "failed",
    ]
    assert public_text.startswith("Using ")
    assert public_text.endswith(". ")
    assert result.message == public_text
    assert expected_replacement in public_text
    assert "\u2588" not in public_text
    assert optional_skill not in public_text


@pytest.mark.asyncio
async def test_sdk_selected_skill_streams_after_completed_evidence_before_terminal(
    monkeypatch,
    tmp_path,
):
    captured = {}
    deltas = []
    observed_before_result = []
    text = "Skill answer streams safely."
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    steps = [
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        *_stream_steps(text),
        ("probe", lambda: observed_before_result.extend(deltas)),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    assert "Authoritative platform MCP requirement:" not in _captured_sdk_prompt(
        captured
    )
    assert observed_before_result
    assert text.startswith("".join(observed_before_result))
    assert "".join(deltas) == text
    assert result.error is None
    assert result.message == text
    assert result.used_skills == ["qa-review"]
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
async def test_sdk_selected_skill_resumes_stream_after_incomplete_tool_block_boundary(
    monkeypatch,
    tmp_path,
):
    captured = {}
    deltas = []
    candidates = []
    observed_before_result = []
    text = "Answer after the Skill completes."
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    steps = [
        (
            "stream",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "skill-call-1",
                    "name": "Skill",
                },
            },
        ),
        (
            "stream",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"skill":"qa-review"}',
                },
            },
        ),
        (
            "assistant_tool",
            {
                "thinking": "Reviewing the Skill result.",
                "id": "skill-call-1",
                "name": "Skill",
                "input": {"skill": "qa-review"},
            },
        ),
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        *_stream_steps(text),
        ("probe", lambda: observed_before_result.extend(deltas)),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-post-tool-stream",
        attempt_id="attempt-post-tool-stream",
        thinking_effort="high",
    )

    assert observed_before_result
    assert text.startswith("".join(observed_before_result))
    assert result.error is None
    assert "".join(deltas) == text
    event_types = [
        candidate.event_type
        if hasattr(candidate, "event_type")
        else candidate.as_agent_event_fields()["type"]
        for candidate in candidates
    ]
    assert "claude_sdk_thinking_summary" not in event_types
    assert event_types.index("tool.completed") < event_types.index("message.delta")


@pytest.mark.asyncio
async def test_sdk_selected_skill_preserves_terminal_text_after_capability(
    monkeypatch,
    tmp_path,
):
    captured = {}
    deltas = []
    sealed_pre_capability_text = "raw tool output and /private/path are sealed."
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    steps = [
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        *_stream_steps(sealed_pre_capability_text),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text=f"{sealed_pre_capability_text} cumulative terminal answer",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    expected_text = sealed_pre_capability_text + " cumulative terminal answer"
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
async def test_sdk_selected_skill_preserves_pre_capability_terminal_text(
    monkeypatch,
    tmp_path,
):
    captured = {}
    deltas = []
    observed_before_result = []
    sealed_pre_capability_text = "raw tool output and /private/path are sealed."
    verified_answer = "Verified Skill final answer streams safely."
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    steps = [
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        *_stream_steps(sealed_pre_capability_text),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        *_stream_steps(verified_answer, index=1),
        ("probe", lambda: observed_before_result.extend(deltas)),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            steps,
            result_text=f"{sealed_pre_capability_text}{verified_answer}",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
    )

    expected_text = sealed_pre_capability_text + verified_answer
    assert observed_before_result
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_outcome", [False, "raise", "cancel", "missing"])
async def test_sdk_selected_skill_rejected_post_ack_preserves_pretool_narration(
    monkeypatch, tmp_path, callback_outcome
):
    captured, deltas, callback_phases = {}, [], []
    text = "I will run the selected Skill. "
    skill_input = {
        "tool_name": "Skill",
        "tool_use_id": "skill-call-1",
        "tool_input": {"skill": "qa-review"},
    }
    terminal_kind = "cancel_hook" if callback_outcome == "cancel" else "hook"
    steps = [
        *_stream_steps(text),
        ("hook", ("PreToolUse", skill_input, "skill-call-1")),
        (terminal_kind, ("PostToolUse", skill_input, "skill-call-1")),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        ("assistant", text),
    ]

    async def acknowledge(evidence):
        callback_phases.append(evidence["lifecycle_phase"])
        if evidence["lifecycle_phase"] == "invocation_requested":
            return True
        if callback_outcome == "raise":
            raise RuntimeError("private callback failure")
        if callback_outcome == "cancel":
            await asyncio.Event().wait()
        return False

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text=text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )
    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_skill_use=lambda *_: asyncio.sleep(0, result=deltas.append("skill_used")),
        on_capability_evidence=None if callback_outcome == "missing" else acknowledge,
    )

    assert callback_phases == (
        [] if callback_outcome == "missing" else ["invocation_requested", "completed"]
    )
    assert (
        result.error,
        result.message,
        result.used_skills,
        result.capability_evidence,
        deltas,
    ) == ("required_tool_completion_evidence_mismatch", text, [], [], [text])


@pytest.mark.asyncio
async def test_sdk_selected_skill_concurrent_rejection_prevents_inflight_commit(
    monkeypatch, tmp_path
):
    captured, deltas, callback_facts = {}, [], []
    success_started, rejection_started = asyncio.Event(), asyncio.Event()

    def skill_input(call_id):
        return {
            "tool_name": "Skill",
            "tool_use_id": call_id,
            "tool_input": {"skill": "qa-review"},
        }

    async def acknowledge(evidence):
        fact = (evidence["tool_call_id"], evidence["lifecycle_phase"])
        callback_facts.append(fact)
        if fact[1] == "invocation_requested":
            return True
        if fact[0] == "skill-call-success":
            success_started.set()
            await rejection_started.wait()
            return True
        await success_started.wait()
        rejection_started.set()
        return False

    success, rejected = (
        skill_input("skill-call-success"),
        skill_input("skill-call-rejected"),
    )
    steps = [
        ("hook", ("PreToolUse", success, "skill-call-success")),
        ("hook", ("PreToolUse", rejected, "skill-call-rejected")),
        (
            "concurrent_hooks",
            [
                ("PostToolUse", success, "skill-call-success"),
                ("PostToolUse", rejected, "skill-call-rejected"),
            ],
        ),
        ("hook", ("PostToolUse", success, "skill-call-success")),
        ("assistant", "sealed"),
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="sealed"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )
    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append,
        on_skill_use=lambda *_: asyncio.sleep(0, result=deltas.append("skill_used")),
        on_capability_evidence=acknowledge,
    )
    assert callback_facts == [
        ("skill-call-success", "invocation_requested"),
        ("skill-call-rejected", "invocation_requested"),
        ("skill-call-success", "completed"),
        ("skill-call-rejected", "completed"),
    ]
    assert (
        result.error,
        result.message,
        result.used_skills,
        result.capability_evidence,
        deltas,
    ) == ("required_tool_completion_evidence_mismatch", "sealed", [], [], ["sealed"])


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
    assert "mcp__tenant-server__search" not in _captured_sdk_prompt(captured)


@pytest.mark.parametrize(
    ("allowed", "outcome"), [(True, "ask"), (True, "defer"), (False, "allow")]
)
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
async def test_sdk_mcp_hook_omits_unknown_or_missing_tool_call_identity(
    monkeypatch, tmp_path
):
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
async def test_sdk_complete_assistant_body_publishes_before_terminal_suffix(
    monkeypatch, tmp_path
):
    captured, observed_before_result = {}, []

    class AssistantMessage:
        def __init__(self):
            self.content = [TextBlock("Complete Assistant body")]

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "Complete Assistant body with terminal suffix"
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
        observed_before_result.extend(deltas)
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _client_sdk(
            types.SimpleNamespace(
                AssistantMessage=AssistantMessage,
                ClaudeAgentOptions=ClaudeAgentOptions,
                ResultMessage=ResultMessage,
                StreamEvent=type("StreamEvent", (), {}),
                TextBlock=TextBlock,
                query=query,
            ),
            captured,
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

    assert observed_before_result
    assert "Complete Assistant body".startswith("".join(observed_before_result))
    assert "".join(deltas) == "Complete Assistant body with terminal suffix"
    assert result.error is None
    assert result.message == "Complete Assistant body with terminal suffix"


@pytest.mark.asyncio
async def test_sdk_attach_file_selects_ordered_final_deliverables(monkeypatch, tmp_path):
    captured, attach_results, deltas = {}, [], []
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "final.txt").write_text("final", encoding="utf-8")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "facts.json").write_text("{}", encoding="utf-8")
    skill_output = tmp_path / ".claude" / "skills" / "reporting" / "output"
    skill_output.mkdir(parents=True)
    (skill_output / "report.docx").write_bytes(b"report")

    class AssistantMessage:
        content = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ResultMessage:
        __annotations__ = {"structured_output": object}
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "Final user answer"
        structured_output = {"legacy": "ignored"}
        is_error = False
        errors = None
        stop_reason = "end_turn"
        terminal_reason = "completed"
        num_turns = 1
        permission_denials = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.__dict__.update(kwargs)

    def sdk_tool(name, description, input_schema):
        def decorate(handler):
            handler.name = name
            handler.description = description
            handler.input_schema = input_schema
            return handler

        return decorate

    def create_sdk_mcp_server(name, *, version, tools):
        return {
            "type": "sdk",
            "name": name,
            "version": version,
            "tools": tools,
        }

    async def query(*, prompt, options):
        del prompt
        attach_file = options.mcp_servers["ai-platform-response"]["tools"][0]
        attach_results.append(
            await attach_file(
                {
                    "path": "outputs/final.txt",
                    "role": [],
                }
            )
        )
        attach_results.append(
            await attach_file(
                {
                    "path": "outputs/final.txt",
                    "display_name": "draft-name.txt",
                }
            )
        )
        attach_results.append(
            await attach_file(
                {
                    "path": ".claude/skills/reporting/output/report.docx",
                    "role": "supporting",
                }
            )
        )
        attach_results.append(
            await attach_file(
                {
                    "path": "outputs/final.txt",
                    "display_name": "final-report.txt",
                    "role": "primary",
                    "description": "Final report",
                }
            )
        )
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _client_sdk(
            types.SimpleNamespace(
                AssistantMessage=AssistantMessage,
                ClaudeAgentOptions=ClaudeAgentOptions,
                ResultMessage=ResultMessage,
                StreamEvent=type("StreamEvent", (), {}),
                TextBlock=TextBlock,
                create_sdk_mcp_server=create_sdk_mcp_server,
                query=query,
                tool=sdk_tool,
            ),
            captured,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="reporting",
        skills=["reporting"],
        on_text=deltas.append,
    )

    assert "".join(deltas) == "Final user answer"
    assert result.error is None
    assert result.message == "Final user answer"
    assert result.response_files == [
        "outputs/final.txt",
        ".claude/skills/reporting/output/report.docx",
    ]
    assert result.response_file_descriptors[0] == {
        "source_path": "outputs/final.txt",
        "display_name": "final-report.txt",
        "role": "primary",
        "description": "Final report",
    }
    assert "tasks/facts.json" not in result.response_files
    assert attach_results[0]["is_error"] is True
    assert [json.loads(item["content"][0]["text"]) for item in attach_results[1:]] == [
        {"attached": True, "position": 0},
        {"attached": True, "position": 1},
        {"attached": True, "position": 0},
    ]
    assert "output_format" not in captured
    assert "ai-platform-response" in captured["mcp_servers"]


@pytest.mark.asyncio
async def test_sdk_empty_result_is_not_a_successful_terminal(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, [], result_text="   "),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
    )

    assert result.error == "claude_agent_sdk_missing_structured_terminal"
    assert result.received_structured_terminal is True
    assert not result.message.strip()


@pytest.mark.asyncio
async def test_sdk_tool_turn_publishes_safe_commentary_before_result(
    monkeypatch, tmp_path
):
    captured, candidates, observed_before_result, deltas = {}, [], [], []
    private_call_id = "call-private-1"

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ToolUseBlock:
        id = private_call_id
        name = "Read"
        input = {"file_path": "input.txt"}

    class AssistantMessage:
        content = [
            TextBlock(
                f"Checking {private_call_id} in {tmp_path} before the next step."
            ),
            ToolUseBlock(),
        ]

    class ResultMessage:
        __annotations__ = {"structured_output": object}
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "Final user answer"
        structured_output = {"legacy": "ignored"}
        is_error = False
        errors = None
        stop_reason = "end_turn"
        terminal_reason = "completed"
        num_turns = 1
        permission_denials = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    def acknowledge(batch):
        candidates.extend(batch)
        return True

    async def query(*, prompt, options):
        del prompt, options
        yield AssistantMessage()
        observed_before_result.extend(
            candidate.event_type for candidate in candidates
        )
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _client_sdk(
            types.SimpleNamespace(
                AssistantMessage=AssistantMessage,
                ClaudeAgentOptions=ClaudeAgentOptions,
                ResultMessage=ResultMessage,
                StreamEvent=type("StreamEvent", (), {}),
                TextBlock=TextBlock,
                query=query,
            ),
            captured,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
        on_agent_event=acknowledge,
        run_id="run-commentary",
        attempt_id="attempt-commentary",
    )

    commentary = [
        candidate
        for candidate in candidates
        if candidate.event_type == "commentary.delta"
    ]
    assert observed_before_result
    assert set(observed_before_result) == {"commentary.delta"}
    assert len(commentary) == len(observed_before_result)
    commentary_text = "".join(
        str(candidate.payload["delta"]) for candidate in commentary
    )
    assert private_call_id not in commentary_text
    assert str(tmp_path) not in commentary_text
    assert commentary_text == "Checking \u2588 in \u2588 before the next step."
    assert "Checking" not in "".join(deltas)
    assert "".join(deltas) == "Final user answer"
    assert result.error is None
    assert result.message == "Final user answer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "structured_output",
    [
        pytest.param(None, id="missing"),
        pytest.param({}, id="present-but-unused"),
    ],
)
async def test_sdk_structured_output_does_not_control_plain_text_terminal(
    monkeypatch, tmp_path, structured_output
):
    captured = {}

    class ResultMessage:
        __annotations__ = {"structured_output": object}
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "done"
        is_error = False
        errors = None
        stop_reason = "end_turn"
        terminal_reason = "completed"
        num_turns = 1
        permission_denials = None

    ResultMessage.structured_output = structured_output

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(*, prompt, options):
        del prompt, options
        yield ResultMessage()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _client_sdk(
            types.SimpleNamespace(
                AssistantMessage=type("AssistantMessage", (), {}),
                ClaudeAgentOptions=ClaudeAgentOptions,
                ResultMessage=ResultMessage,
                StreamEvent=type("StreamEvent", (), {}),
                TextBlock=type("TextBlock", (), {}),
                query=query,
            ),
            captured,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
    )

    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.message == "done"
    assert result.response_files == []
    assert result.response_file_descriptors == []
    assert "output_format" not in captured


@pytest.mark.asyncio
async def test_sdk_candidate_projection_failure_skips_batch_and_keeps_successful_terminal(
    monkeypatch, tmp_path
):
    captured, candidates, deltas = {}, [], []
    omitted = "omit this fragment "
    delivered = "Saved at /tmp/visible-result.txt."
    omitted_calls = 0

    def fail_one_answer_candidate(value):
        nonlocal omitted_calls
        if value == omitted:
            omitted_calls += 1
            if omitted_calls == 3:
                raise RuntimeError("synthetic candidate projection failure")
        return sanitize_public_answer_text(value)

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [
                *_stream_steps(omitted, index=0),
                *_stream_steps(delivered, index=1),
            ],
            result_text=omitted + delivered,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.sanitize_public_answer_text",
        fail_one_answer_candidate,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-1482",
        attempt_id="attempt-1482",
    )

    message_events = [
        candidate
        for candidate in candidates
        if candidate.event_type.startswith("message.")
    ]
    assert result.error is None
    assert result.message == ""
    assert "".join(deltas) == delivered
    assert [event.event_type for event in message_events] == [
        "message.started",
        "message.delta",
        "message.delta",
        "message.completed",
    ]
    assert "".join(
        event.payload["delta"]
        for event in message_events
        if event.event_type == "message.delta"
    ) == delivered
    assert message_events[-1].payload == {
        "delta_count": 2,
        "text_length": len(delivered),
    }
    assert result.answer_receipt == {
        "schema_version": "ai-platform.assistant-answer-receipt.v1",
        "message_id": message_events[0].message_id,
        "delta_count": 2,
        "text_length": len(delivered),
        "last_delta_event_id": message_events[-2].event_id,
    }
    assert result.turn_diagnostics["counters"]["public_projection_omissions"] == 1


@pytest.mark.asyncio
async def test_sdk_completion_projection_failure_keeps_delivered_text_without_receipt(
    monkeypatch, tmp_path
):
    captured, candidates, deltas = {}, [], []
    delivered = "Delivered answer"

    def fail_completion(value):
        if isinstance(value, dict) and value.get("event_type") == "message.completed":
            raise RuntimeError("synthetic completion projection failure")
        return sanitize_public_event_candidate(value)

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _stream_steps(delivered, index=0),
            result_text=delivered,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.sanitize_public_event_candidate",
        fail_completion,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-completion-projection",
        attempt_id="attempt-completion-projection",
    )

    assert result.error is None
    assert result.message == delivered
    assert result.answer_receipt is None
    assert "".join(deltas) == delivered
    event_types = [candidate.event_type for candidate in candidates]
    assert event_types[0] == "message.started"
    assert event_types[-1] == "model.completed"
    assert "message.completed" not in event_types
    assert "".join(
        candidate.payload["delta"]
        for candidate in candidates
        if candidate.event_type == "message.delta"
    ) == delivered
    assert result.turn_diagnostics["counters"]["public_projection_omissions"] == 1


@pytest.mark.asyncio
async def test_sdk_conflicting_result_keeps_terminal_body(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant", "Complete Assistant body")],
            result_text="Conflicting terminal result",
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    expected_text = "Complete Assistant body\n\nConflicting terminal result"
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text


@pytest.mark.asyncio
async def test_sdk_result_prefix_comparison_preserves_trailing_space(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant", "Answer ")],
            result_text="Answer suffix",
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    assert "".join(deltas) == "Answer suffix"
    assert result.error is None
    assert result.message == "Answer suffix"


@pytest.mark.asyncio
async def test_sdk_result_replaces_body_for_selected_empty_assistant(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant", "Earlier. "), ("assistant", "")],
            result_text="Current answer.",
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    expected_text = "Earlier. \n\nCurrent answer."
    assert "".join(deltas) == expected_text
    assert result.error is None
    assert result.message == expected_text


def _streaming_sdk(
    captured, events, *, on_before_result=None, result_text="terminal final"
):
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

    return _client_sdk(types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        query=query,
    ), captured)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_terminal_reason", "expected_error"),
    [
        ("max_turns", "claude_agent_sdk_turn_limit_exceeded"),
        ("aborted_streaming", "claude_agent_sdk_cancelled"),
        ("aborted_tools", "claude_agent_sdk_cancelled"),
    ],
)
async def test_sdk_target_terminal_reason_fails_closed_for_non_success_outcomes(
    monkeypatch,
    tmp_path,
    sdk_terminal_reason,
    expected_error,
):
    captured = {}
    sdk = _streaming_sdk(captured, [], result_text="must not be published")
    sdk.ResultMessage.terminal_reason = sdk_terminal_reason
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    deltas = []

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        on_text=deltas.append,
    )

    assert result.error == expected_error
    assert result.terminal_reason == sdk_terminal_reason
    assert result.received_structured_terminal is False
    assert result.message == ""
    assert deltas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostile_name", ["qa-review,Skill(other)", " qa-review", "/qa-review", "qa\nreview"]
)
async def test_sdk_rejects_hostile_skill_names_before_options_construction(
    monkeypatch,
    tmp_path,
    hostile_name,
):
    captured = {}
    monkeypatch.setitem(
        sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[])
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id=hostile_name,
        skills=[hostile_name],
    )

    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert captured == {}


def _sandbox_brokered_settings():
    return _settings()


@pytest.mark.asyncio
async def test_sandbox_streams_two_safe_raw_text_deltas_before_result_without_terminal_replay(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    result_gate = []
    streamed_chunks = ("Short safe ", "public answer.")
    streamed_text = "".join(streamed_chunks)
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": streamed_chunks[0]},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": streamed_chunks[1]},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _streaming_sdk(
            captured,
            events,
            on_before_result=lambda: result_gate.extend(deltas),
            result_text=streamed_text,
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result_gate
    assert streamed_text.startswith("".join(result_gate))
    assert "".join(deltas) == streamed_text
    assert result.message == streamed_text


@pytest.mark.asyncio
async def test_sandbox_stream_ignores_complete_tool_use_block_before_safe_text(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    raw_streamed_text = "Safe answer after tool-1 use."
    public_streamed_text = "Safe answer after \u2588 use."
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "tool-1", "name": "Skill"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"skill":"general-chat"}',
            },
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": raw_streamed_text},
        },
        {"type": "content_block_stop", "index": 1},
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _streaming_sdk(captured, events, result_text=raw_streamed_text),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result.error is None
    assert "".join(deltas) == public_streamed_text
    assert result.message == public_streamed_text


@pytest.mark.asyncio
async def test_sandbox_stream_duplicate_stop_never_replays_terminal_result(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "short answer"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_stop", "index": 0},
    ]
    monkeypatch.setitem(
        sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events)
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result.error is None
    assert result.message == "short answer\n\nterminal final"
    assert "".join(deltas) == result.message


@pytest.mark.asyncio
async def test_sdk_keeps_successful_terminal_body_after_stream_failure(
    monkeypatch, tmp_path
):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "safe partial must finish"},
        },
    ]
    monkeypatch.setitem(
        sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events)
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result.error is None
    assert result.message == "safe partial must finish\n\nterminal final"
    assert "".join(deltas) == result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events, expected",
    [
        (
            [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text"},
                }
            ],
            "terminal fallback",
        ),
        (
            [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text"},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "short"},
                },
            ],
            "short\n\nterminal fallback",
        ),
        (["malformed"], "terminal fallback"),
    ],
    ids=("start-only", "short-unfinished", "malformed-first-event"),
)
async def test_stream_failure_before_publication_recovers_terminal_body(
    monkeypatch,
    tmp_path,
    events,
    expected,
):
    captured, deltas = {}, []
    steps = [("stream", event) for event in events]
    steps.append(("assistant", "terminal fallback"))
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="terminal fallback"),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _sandbox_brokered_settings,
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert "".join(deltas) == expected
    assert result.error is None
    assert result.message == expected


@pytest.mark.asyncio
async def test_sdk_high_effort_does_not_publish_thinking_content(
    monkeypatch, tmp_path
):
    captured, candidates = {}, []
    subject = _subject()
    subject["mcp_server_config"]["headers"] = {
        "X-Static-Key": "static-header-secret",
        "Authorization": "Bearer static-jwt-token",
    }
    settings = _settings()
    settings.anthropic_auth_token = "anthropic-config-secret"
    settings.openai_api_key = "openai-config-secret"
    settings.anthropic_base_url = "https://private-provider.example/v1"
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_TOKEN", "native-tool-secret")
    private_values = (
        subject["identity"],
        subject["mcp_server_config"]["url"],
        *subject["mcp_server_config"]["headers"].values(),
        settings.anthropic_auth_token,
        settings.openai_api_key,
        settings.anthropic_base_url,
        "native-tool-secret",
    )
    thinking = (
        "Review C:/agent-workspaces/run-1/output/result.txt for "
        + " and ".join(private_values)
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[], thinking_text=thinking),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", lambda: settings
    )

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="worker_local_legacy",
        thinking_effort="high",
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-thinking-config",
        attempt_id="attempt-thinking-config",
        tool_policy_subjects=[subject],
    )

    assert result.error is None
    assert all(not hasattr(candidate, "summary") for candidate in candidates)
    assert thinking not in repr(candidates)
    for private_value in private_values:
        assert private_value not in repr(candidates)


@pytest.mark.asyncio
async def test_outer_cancellation_reaches_sdk_query_cleanup(monkeypatch, tmp_path):
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    class AssistantMessage:
        def __init__(self, *, content, model):
            self.content = content
            self.model = model

    class TextBlock:
        pass

    class ToolUseBlock:
        def __init__(self, *, id, name, input):
            self.id = id
            self.name = name
            self.input = input

    class StreamEvent:
        pass

    class ResultMessage:
        pass

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    class ClaudeAgentOptions:
        def __init__(self, **_kwargs):
            pass

    async def query(*, prompt, options):
        _ = [item async for item in prompt]
        yield AssistantMessage(
            content=[ToolUseBlock(id="late-tool", name="Read", input={})],
            model="model-a",
        )
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            try:
                pre = options.hooks["PreToolUse"][0].hooks[0]
                await pre(
                    {"tool_name": "Read", "tool_input": {}, "tool_use_id": "late-tool"},
                    "late-tool",
                    {},
                )
            finally:
                cleaned_up.set()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _client_sdk(
            types.SimpleNamespace(
                AssistantMessage=AssistantMessage,
                ClaudeAgentOptions=ClaudeAgentOptions,
                HookMatcher=HookMatcher,
                ResultMessage=ResultMessage,
                StreamEvent=StreamEvent,
                TextBlock=TextBlock,
                ToolUseBlock=ToolUseBlock,
                query=query,
            ),
            {},
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    events = []
    task = asyncio.create_task(
        run_claude_agent_sdk(
            prompt="cancel me",
            cwd=tmp_path,
            skill_id="general-chat",
            execution_policy="worker_local_legacy",
            on_agent_event=lambda batch: events.extend(batch) or True,
            run_id="run-cancel",
            attempt_id="attempt-cancel",
            tool_policy_subjects=[
                _subject(tool_name="Read", public_tool_label="Read file")
            ],
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned_up.is_set()
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_transcript", "expected_option"),
    [(None, "session_id"), ([{"uuid": "entry-1"}], "resume")],
)
async def test_sdk_provider_session_options_are_exclusive_and_eager(
    monkeypatch,
    tmp_path,
    stored_transcript,
    expected_option,
):
    captured = {}

    append_calls = []

    class Store:
        @property
        def accepted_final_sequence(self):
            return 1 if append_calls else None

        async def load(self, provider_session_id):
            assert provider_session_id == "stable-provider-id"
            return stored_transcript

        async def append(self, provider_session_id, entries):
            append_calls.append((provider_session_id, entries))

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[]))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="continue",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=expected_option == "resume",
    )

    assert result.error is None
    assert result.provider_final_sequence == 1
    assert captured["session_store_flush"] == "eager"
    assert captured["session_store"] is not None
    assert captured[expected_option] == "stable-provider-id"
    assert {"session_id", "resume"}.intersection(captured) == {expected_option}
    assert append_calls == [("stable-provider-id", [{"uuid": "entry-ack"}])]


@pytest.mark.asyncio
@pytest.mark.parametrize("append_subpath", [None, "child-agent"])
async def test_sdk_provider_session_requires_main_append_for_success(
    monkeypatch, tmp_path, append_subpath
):
    captured = {}

    class Store:
        async def load(self, _provider_session_id):
            return None

        async def append(self, _provider_session_id, _entries):
            return None

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[],
            append_provider_session=append_subpath is not None,
            append_provider_subpath=append_subpath,
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="continue",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=False,
    )

    assert result.error == "claude_agent_sdk_provider_session_failed"
    assert result.message == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_transcript", "resume_required"),
    [(None, True), ([{"uuid": "entry-1"}], False)],
)
async def test_sdk_provider_session_resume_state_mismatch_fails_closed(
    monkeypatch,
    tmp_path,
    stored_transcript,
    resume_required,
):
    captured = {}

    class Store:
        async def load(self, _provider_session_id):
            return stored_transcript

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[]))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="continue",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=resume_required,
    )

    assert result.error == "claude_agent_sdk_provider_session_failed"
    assert result.message == ""
    assert captured == {}


@pytest.mark.asyncio
async def test_sdk_provider_session_preflight_failure_is_private_and_fail_closed(monkeypatch, tmp_path):
    captured = {}

    class Store:
        async def load(self, _provider_session_id):
            raise RuntimeError("private callback details")

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[]))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="continue",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=False,
    )

    assert result.error == "claude_agent_sdk_provider_session_failed"
    assert result.message == ""
    assert "private callback details" not in repr(result)
    assert captured == {}


@pytest.mark.asyncio
async def test_sdk_mirror_error_is_a_private_fail_closed_provider_failure(monkeypatch, tmp_path):
    captured = {}

    class Store:
        async def load(self, _provider_session_id):
            return [{"uuid": "entry-1"}]

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[], mirror_error=True),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="continue",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=True,
    )

    assert result.error == "claude_agent_sdk_provider_session_failed"
    assert result.message == ""
    assert "MirrorError" not in repr(result)
    assert "entry-1" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_required", [False, True])
async def test_native_client_delegates_compaction_to_cli_without_session_open_query(
    monkeypatch, tmp_path, resume_required
):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    class Store:
        accepted_final_sequence = 1

        async def load(self, _key):
            return [{"uuid": "entry-1"}] if resume_required else None

        async def append(self, _key, _entries):
            return None

    class Client:
        def __init__(self, options):
            self.options = options
            self.responses = None

        async def connect(self):
            captured["connected"] = True

        async def query(self, prompt, session_id="default"):
            assert prompt != "/compact"
            captured["business_query"] = True
            captured["query_session_id"] = session_id
            self.responses = sdk.query(prompt=prompt, options=self.options)

        async def receive_response(self):
            async for message in self.responses:
                yield message

        async def disconnect(self):
            captured["disconnected"] = True

    result = await run_claude_agent_sdk(
        prompt="continue with recent user request",
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=resume_required,
        model_max_input_tokens=100_000,
        model_max_output_tokens=36_500,
        client_fn=Client,
    )

    assert result.error is None
    assert result.provider_final_sequence == 1
    assert (
        captured["connected"]
        and captured["business_query"]
        and captured["disconnected"]
    )
    assert captured["query_session_id"] == "stable-provider-id"
    assert captured["extra_args"] == {"autocompact": "100000"}
    assert captured["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "36500"
    assert captured["env"].get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") in {None, ""}

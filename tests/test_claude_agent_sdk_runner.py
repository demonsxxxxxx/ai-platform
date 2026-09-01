import asyncio
import sys
import types

import pytest

from app.executors.claude_agent_sdk_runner import (
    _sdk_run_timeout_seconds,
    project_sdk_turn_diagnostics,
    run_claude_agent_sdk,
)
from app.required_tool_contract import (
    parse_required_tool_declaration,
    with_sandbox_local_tool_capability_subjects,
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


def test_public_diagnostics_allow_only_fixed_projection_failure_reasons():
    common = {
        "error_code": "claude_agent_sdk_public_projection_failed",
        "selected_skill_id": None,
        "used_skill_ids": [],
        "public_skill_metadata": {},
    }

    allowed = project_sdk_turn_diagnostics(
        {"projection_failure_reason": "answer_too_large"},
        **common,
    )
    rejected = project_sdk_turn_diagnostics(
        {"projection_failure_reason": "C:/private/path?token=secret"},
        **common,
    )
    unrelated = [
        project_sdk_turn_diagnostics(
            {"projection_failure_reason": "answer_too_large"},
            error_code=error_code,
            selected_skill_id=None,
            used_skill_ids=[],
            public_skill_metadata={},
        )
        for error_code in (None, "claude_agent_sdk_tool_admission_failed")
    ]

    assert allowed["projection_failure_reason"] == "answer_too_large"
    assert "projection_failure_reason" not in rejected
    assert all("projection_failure_reason" not in item for item in unrelated)
    assert "private" not in str(rejected)
    assert "secret" not in str(rejected)


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


def _fake_sdk(captured, *, hook_invocations, thinking_text=None):
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
        del options
        captured["sdk_user_messages"] = [item async for item in prompt]
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
                matcher = next(item for item in matchers if item.matcher == matcher_name)
            hook_result = await matcher.hooks[0](hook_input, tool_call_id, {})
            captured.setdefault("hook_results", []).append((hook_name, hook_result))
        if thinking_text is not None:
            yield AssistantMessage([ThinkingBlock(thinking_text)])
        yield ResultMessage()

    return types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        ThinkingBlock=ThinkingBlock,
        query=query,
    )


def _scripted_sdk(captured, steps, *, result_text="done", result_error: str | None = None):
    class TextBlock:
        def __init__(self, text):
            self.text = text

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
        permission_denials = None

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
                matcher = next(item for item in matchers if item.matcher == matcher_name)
            await matcher.hooks[0](hook_input, tool_call_id, {})

        for step in steps:
            kind, value = step
            if kind == "assistant":
                yield AssistantMessage(value)
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

    return types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        query=query,
    )


def _stream_steps(text, *, index=0):
    return [
        ("stream", {"type": "content_block_start", "index": index, "content_block": {"type": "text"}}),
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


@pytest.mark.parametrize(
    ("thinking_effort", "expected_thinking", "expected_effort"),
    [
        ("off", None, None),
        *[
            (level, {"type": "adaptive", "display": "summarized"}, level)
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
async def test_sdk_off_does_not_publish_an_unexpected_thinking_block(
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
        thinking_effort="off",
        run_id="run-thinking-off",
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
        "tool_input": {"command": "python --version"},
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert result.error is None
    assert result.message == ""
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
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "grep-call-1",
        "tool_input": {"pattern": "TODO", "path": str(tmp_path), "glob": "*.md"},
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
        prompt="search the workspace",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    pretool_output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert result.error is None
    assert pretool_output["permissionDecision"] == "allow"
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=_acknowledge_capability_evidence,
    )

    assert captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"] == "deny"
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
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_required_keys", [None, "pattern", {"pattern": True}])
async def test_sandbox_grep_denies_invalid_required_parameter_configuration(
    monkeypatch,
    tmp_path,
    invalid_required_keys,
):
    captured = {}
    hook_input = {
        "tool_name": "Grep",
        "tool_use_id": "grep-call-1",
        "tool_input": {},
    }
    subjects = with_sandbox_local_tool_capability_subjects(
        [], sandbox_provider="opensandbox"
    )
    next(subject for subject in subjects if subject["identity"] == "Grep")[
        "required_parameter_keys"
    ] = invalid_required_keys
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
        prompt="search the workspace",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects,
        on_tool_lifecycle=_acknowledge_capability_evidence,
    )

    assert captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert result.turn_diagnostics["counters"]["tool_policy_denials"] == 1


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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
async def test_autonomous_sandbox_bash_keeps_answer_sealed_without_terminal_lifecycle(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    hook_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "python --version"},
    }
    steps = [
        *_stream_steps("must remain private before call ", index=0),
        ("hook", ("PreToolUse", hook_input, "bash-call-1")),
        *_stream_steps("must remain private after call", index=1),
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
        prompt="inspect the sandbox",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["Write", "Edit", "NotebookEdit"],
)
async def test_sandbox_effectful_tool_keeps_answer_sealed_without_terminal_lifecycle(
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
    steps = [
        ("hook", ("PreToolUse", hook_input, "local-call-1")),
        *_stream_steps("must remain private"),
        ("assistant", "must remain private"),
    ]

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
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
        prompt="use one sandbox-local tool",
        cwd=tmp_path,
        skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "callback_acknowledged"),
    [
        (tool_name, callback_acknowledged)
        for tool_name in ["Read", "Glob", "Grep", "LS"]
        for callback_acknowledged in [True, False]
    ],
)
async def test_sandbox_read_only_tool_records_incomplete_lifecycle_without_failing_run(
    monkeypatch,
    tmp_path,
    tool_name,
    callback_acknowledged,
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
        ("hook", ("PreToolUse", hook_input, "read-only-call-1")),
        *_stream_steps("safe answer"),
        ("assistant", "safe answer"),
    ]

    async def acknowledge(fact):
        lifecycle_facts.append(dict(fact))
        return callback_acknowledged

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_text="safe answer"),
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert lifecycle_facts == [
        {
            "fact_kind": "tool_invocation",
            "tool_name": tool_name,
            "invocation_id": "read-only-call-1",
            "lifecycle": "started",
        }
    ]
    assert result.error is None
    assert result.message == "safe answer"
    assert "".join(deltas) == "safe answer"
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 1


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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
async def test_sandbox_local_tool_denies_missing_or_conflicting_call_id(
    monkeypatch,
    tmp_path,
    hook_call_id,
    callback_call_id,
):
    captured = {}
    hook_input = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(tmp_path / "output" / "output.txt"),
            "content": "done",
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert captured["hook_results"][0][1]["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"
    assert result.error == "required_tool_completion_evidence_mismatch"
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
    assert result.message == ""
    assert "".join(deltas) == result.message
    assert call_id not in result.message


@pytest.mark.asyncio
async def test_sandbox_bash_availability_releases_terminal_answer_when_not_invoked(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    direct_answer = "No tool was needed. " + ("A" * 5_000)
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_text=deltas.append,
    )

    assert result.error is None
    assert result.message == direct_answer
    assert "".join(deltas) == direct_answer


@pytest.mark.asyncio
async def test_prior_mcp_completion_does_not_publish_before_bash_failure_terminal(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    mcp_subject = _subject()
    bash_subject = next(
        subject
        for subject in with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        )
        if subject["identity"] == "Bash"
    )
    bash_input = {
        "tool_name": "Bash",
        "tool_use_id": "bash-call-1",
        "tool_input": {"command": "false"},
    }
    steps = [
        *_mcp_hook_steps(mcp_subject),
        *_stream_steps("must remain private after MCP "),
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
            result_text="must remain private after MCP",
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
    assert result.message == ""
    assert deltas == []


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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
async def test_required_sandbox_bash_keeps_answer_sealed_without_terminal_lifecycle(
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
        on_text=deltas.append,
    )

    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""


@pytest.mark.asyncio
async def test_required_sandbox_bash_forwards_duplicate_started_lifecycle(
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [],
            sandbox_provider="opensandbox",
            required_declaration=declaration,
        ),
        on_tool_lifecycle=acknowledge,
    )

    assert len(lifecycle_facts) == 2
    assert captured["hook_results"][0][1]["hookSpecificOutput"][
        "permissionDecision"
    ] == "allow"
    assert captured["hook_results"][1][1]["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
    assert result.message == ""
    assert deltas == []


@pytest.mark.asyncio
async def test_required_sandbox_bash_failure_after_success_discards_answer(
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
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
    assert deltas == []
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == ""


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
@pytest.mark.parametrize("execution_policy", ["worker_local_legacy", "sandbox_brokered"])
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
        assert "mcp__tenant-server__search" in captured["allowed_tools"]


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
        matcher.matcher != "Skill"
        for matcher in captured["hooks"]["PostToolUse"]
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
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="opensandbox"
        ),
        on_tool_lifecycle=_acknowledge_capability_evidence,
    )

    assert captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"] == "deny"
    detail = result.turn_diagnostics["tool_policy_denials_detail"]
    assert len(detail) == 1
    assert detail[0]["tool_name"] == "Grep"
    assert detail[0]["reason"]


@pytest.mark.asyncio
async def test_sdk_available_external_mcp_streams_without_forced_prompt_or_hooks(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    subjects = [
        _subject(server_id="tenant__server", tool_name="search"),
        _subject(server_id="other-server", tool_name="fetch", endpoint="https://other.private.example/mcp"),
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
    assert "Authoritative platform MCP requirement" not in _captured_sdk_prompt(captured)
    assert set(captured["mcp_servers"]) == {"tenant__server", "other-server"}
    assert {subject["identity"] for subject in subjects}.issubset(captured["allowed_tools"])


@pytest.mark.asyncio
async def test_sdk_registers_only_exact_authorized_external_mcp_subjects(monkeypatch, tmp_path):
    captured = {}
    valid = _subject(server_id="tenant__server", tool_name="search")
    denied = {**_subject(server_id="denied", tool_name="lookup"), "identity_authorized": False}
    malformed = {
        **_subject(server_id="mismatch", tool_name="lookup"),
        "identity": "mcp__different__lookup",
    }
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[]))
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
    if outcome == "stale":
        return [first_completed, *_stream_steps(text), ("probe", probe)]
    if outcome == "duplicate":
        return [first_pre, first_completed, first_completed, *_stream_steps(text), ("probe", probe)]
    if outcome.startswith("multiple_"):
        second_pre, second_terminal = _mcp_hook_steps(
            subjects[1], call_id="mcp-call-2", terminal=outcome.removeprefix("multiple_")
        )
        return [
            first_pre, second_pre, *_stream_steps(text), first_completed,
            ("probe", probe), second_terminal,
        ]
    steps = [first_pre, *_stream_steps(text), ("probe", probe)]
    if outcome != "incomplete":
        steps.append(_mcp_hook_steps(subjects[0], terminal="failed" if outcome == "failed" else "completed")[1])
    return steps


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["success", "missing", "false", "exception", "failed", "incomplete", "overflow", "stale", "duplicate", "multiple_completed", "multiple_failed"],
)
async def test_sdk_actual_mcp_publication_gate(monkeypatch, tmp_path, outcome):
    captured, acknowledged, deltas, sealed_probe = {}, [], [], []
    first = _subject()
    subjects = [first, _subject(server_id="other-server", tool_name="fetch", endpoint="https://other.private.example/mcp")]
    private_text = f"Safe answer via {first['identity']} with mcp-call-1 at {first['mcp_server_config']['url']}."
    text = "x" * 4_097 if outcome == "overflow" else private_text if outcome == "success" else "must stay sealed"
    steps = _actual_mcp_steps(outcome, subjects, text, lambda: sealed_probe.extend(deltas))

    async def acknowledge(evidence):
        acknowledged.append(dict(evidence))
        if evidence["lifecycle_phase"] == "completed" and outcome == "false":
            return False
        if evidence["lifecycle_phase"] == "completed" and outcome == "exception":
            raise RuntimeError("private callback failure")
        return True

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _scripted_sdk(captured, steps, result_text=text))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)
    result = await run_claude_agent_sdk(
        prompt="search", cwd=tmp_path, skill_id="general-chat", execution_policy="sandbox_brokered",
        tool_policy_subjects=subjects, on_text=deltas.append,
        on_capability_evidence=None if outcome == "missing" else acknowledge,
    )

    assert sealed_probe == []
    if outcome in {"success", "multiple_completed"}:
        assert result.error is None
        assert (deltas, result.message) == ([], "")
        if outcome == "success":
            assert result.capability_evidence == acknowledged
            assert [item["lifecycle_phase"] for item in acknowledged] == ["invocation_requested", "completed"]
            assert not {"tool_input", "tool_response", "arguments", "error"} & set().union(*(item.keys() for item in acknowledged))
            for private_value in (first["identity"], "mcp-call-1", first["mcp_server_config"]["url"]):
                assert private_value not in result.message
    else:
        expected = (
            "claude_agent_sdk_public_projection_failed"
            if outcome == "overflow"
            else "required_tool_completion_evidence_mismatch"
        )
        assert (result.error, result.message, deltas) == (expected, "", [])
        if outcome == "overflow":
            assert result.turn_diagnostics["projection_failure_reason"] == (
                "upstream_projection_failed"
            )


@pytest.mark.asyncio
async def test_sdk_projects_known_mcp_identity_defers_suffix_until_terminal_and_releases_once(
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
        *_stream_steps(after, index=1),
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
    assert "Before ".startswith("".join(published_before_hook))
    assert len(published_before_terminal) > len(published_before_hook)
    terminal_chunks = deltas[len(published_before_terminal):]
    assert terminal_chunks
    assert "".join(deltas).endswith(" After tool invocation.")
    assert "".join(deltas).count("tool invocation.") == 1
    assert result.error is None
    assert result.message == " After tool invocation."
    for private_value in (
        subject["identity"],
        subject["mcp_server_config"]["url"],
        call_id,
        "safe-synthetic-value",
    ):
        assert private_value not in "".join(deltas)


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
async def test_sdk_mcp_discards_sealed_pre_capability_terminal_text(monkeypatch, tmp_path):
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
            result_text=f"{sealed_pre_capability_text} {verified_answer}",
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

    assert observed_before_result
    assert verified_answer.startswith("".join(observed_before_result))
    assert "".join(deltas) == verified_answer
    assert result.error is None
    assert result.message == verified_answer
    assert sealed_pre_capability_text not in result.message
    assert sealed_pre_capability_text not in "".join(deltas)
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
        ("hook", (
            "PreToolUse",
            {
                "tool_name": second["identity"],
                "tool_use_id": "mcp-call-2",
                "tool_input": {"private": "safe-synthetic-value"},
            },
            "mcp-call-2",
        )),
        ("assistant", "second capability in-flight text"),
        *_mcp_hook_steps(second, call_id="mcp-call-2", terminal="failed")[1:],
    ]

    async def acknowledge(evidence):
        evidence_calls.append(dict(evidence))
        return evidence["tool_call_id"] != "mcp-call-2" or evidence["lifecycle_phase"] != "completed"

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
        (item["tool_call_id"], item["lifecycle_phase"])
        for item in evidence_calls
    ] == [
        ("mcp-call-1", "invocation_requested"),
        ("mcp-call-1", "completed"),
        ("mcp-call-2", "invocation_requested"),
        ("mcp-call-2", "failed"),
    ]
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == ""
    assert deltas
    assert "first verified answer".startswith("".join(deltas))
    assert all(
        body not in repr(event.as_dict())
        for event in candidate_events
        for body in ("second capability in-flight text",)
    )
    assert any("first verified " in repr(event.as_dict()) for event in candidate_events)


@pytest.mark.asyncio
async def test_sdk_selected_skill_remains_required_with_unused_available_mcp(monkeypatch, tmp_path):
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
    assert (deltas, result.message) == ([], "")
    assert "Authoritative platform Skill requirement" in sdk_prompt
    assert "Authoritative platform MCP requirement" not in sdk_prompt
    assert _subject()["identity"] not in sdk_prompt
    assert _subject()["identity"] in captured["allowed_tools"]


@pytest.mark.asyncio
async def test_sdk_agent_skill_set_can_answer_without_invoking_a_skill(monkeypatch, tmp_path):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, _stream_steps("Direct answer."), result_text="Direct answer."),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)

    result = await run_claude_agent_sdk(
        prompt="answer from your current context",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review", "reference-search"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[
            {**_skill_subject("qa-review"), "allowed_skill_names": ["qa-review", "reference-search"]},
        ],
        on_text=deltas.append,
        on_capability_evidence=_acknowledge_capability_evidence,
        require_selected_skill_invocation=False,
    )

    assert result.error is None
    assert result.used_skills == []
    assert result.capability_evidence == []
    assert "".join(deltas) == "Direct answer."
    assert "Authoritative platform Skill requirement" not in _captured_sdk_prompt(captured)
    assert {"Skill(qa-review)", "Skill(reference-search)"}.issubset(
        captured["allowed_tools"]
    )


@pytest.mark.asyncio
async def test_sdk_agent_skill_set_records_exact_evidence_for_second_skill(monkeypatch, tmp_path):
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
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)

    result = await run_claude_agent_sdk(
        prompt="find the relevant reference",
        cwd=tmp_path,
        skill_id="qa-review",
        skills=["qa-review", "reference-search"],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[
            {**_skill_subject("qa-review"), "allowed_skill_names": ["qa-review", "reference-search"]},
        ],
        on_capability_evidence=acknowledge,
        require_selected_skill_invocation=False,
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

    assert "Authoritative platform MCP requirement:" not in _captured_sdk_prompt(captured)
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
async def test_sdk_selected_skill_omits_cumulative_terminal_text_without_post_capability_delta(
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

    assert deltas == []
    assert result.error is None
    assert result.message == ""
    assert sealed_pre_capability_text not in result.message
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
async def test_sdk_selected_skill_discards_sealed_pre_capability_terminal_text(
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
            result_text=f"{sealed_pre_capability_text} {verified_answer}",
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

    assert observed_before_result
    assert verified_answer.startswith("".join(observed_before_result))
    assert "".join(deltas) == verified_answer
    assert result.error is None
    assert result.message == verified_answer
    assert sealed_pre_capability_text not in result.message
    assert sealed_pre_capability_text not in "".join(deltas)
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "completed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_outcome", [False, "raise", "cancel", "missing"])
async def test_sdk_selected_skill_rejected_post_ack_seals_all_public_output(
    monkeypatch, tmp_path, callback_outcome
):
    captured, deltas, callback_phases = {}, [], []
    text = "sealed Skill answer"
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

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _scripted_sdk(captured, steps, result_text=text))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)
    result = await run_claude_agent_sdk(
        prompt="review", cwd=tmp_path, skill_id="qa-review", skills=["qa-review"],
        execution_policy="sandbox_brokered", tool_policy_subjects=[_skill_subject()],
        on_text=deltas.append, on_skill_use=lambda *_: asyncio.sleep(0, result=deltas.append("skill_used")),
        on_capability_evidence=None if callback_outcome == "missing" else acknowledge,
    )

    assert callback_phases == ([] if callback_outcome == "missing" else ["invocation_requested", "completed"])
    assert (result.error, result.message, result.used_skills, result.capability_evidence, deltas) == (
        "required_tool_completion_evidence_mismatch", "", [], [], [])


@pytest.mark.asyncio
async def test_sdk_selected_skill_concurrent_rejection_prevents_inflight_commit(monkeypatch, tmp_path):
    captured, deltas, callback_facts = {}, [], []
    success_started, rejection_started = asyncio.Event(), asyncio.Event()
    def skill_input(call_id):
        return {"tool_name": "Skill", "tool_use_id": call_id, "tool_input": {"skill": "qa-review"}}
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
    success, rejected = skill_input("skill-call-success"), skill_input("skill-call-rejected")
    steps = [("hook", ("PreToolUse", success, "skill-call-success")), ("concurrent_hooks", [
        ("PostToolUse", success, "skill-call-success"), ("PostToolUse", rejected, "skill-call-rejected"),
    ]), ("hook", ("PostToolUse", success, "skill-call-success")), ("assistant", "sealed")]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _scripted_sdk(captured, steps, result_text="sealed"))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)
    result = await run_claude_agent_sdk(
        prompt="review", cwd=tmp_path, skill_id="qa-review", skills=["qa-review"],
        execution_policy="sandbox_brokered", tool_policy_subjects=[_skill_subject()], on_text=deltas.append,
        on_skill_use=lambda *_: asyncio.sleep(0, result=deltas.append("skill_used")), on_capability_evidence=acknowledge,
    )
    assert callback_facts == [("skill-call-success", "invocation_requested"), ("skill-call-success", "completed"), ("skill-call-rejected", "completed")]
    assert (result.error, result.message, result.used_skills, result.capability_evidence, deltas) == ("required_tool_completion_evidence_mismatch", "", [], [], [])


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
@pytest.mark.parametrize("hostile_name", ["qa-review,Skill(other)", " qa-review", "/qa-review", "qa\nreview"])
async def test_sdk_rejects_hostile_skill_names_before_options_construction(
    monkeypatch,
    tmp_path,
    hostile_name,
):
    captured = {}
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured, hook_invocations=[]))
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
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": streamed_chunks[0]}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": streamed_chunks[1]}},
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
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)

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
async def test_sandbox_stream_ignores_complete_tool_use_block_before_safe_text(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    raw_streamed_text = "Safe answer after tool-1 use."
    public_streamed_text = "Safe answer after tool invocation use."
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "tool-1", "name": "Skill"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"skill":"general-chat"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": raw_streamed_text}},
        {"type": "content_block_stop", "index": 1},
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _streaming_sdk(captured, events, result_text=raw_streamed_text),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)

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
async def test_sandbox_stream_duplicate_stop_never_replays_terminal_result(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "short answer"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_stop", "index": 0},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert result.error == "claude_agent_sdk_public_projection_failed"
    assert result.turn_diagnostics["projection_failure_reason"] == "upstream_projection_failed"
    assert deltas
    assert "short answer".startswith("".join(deltas))


@pytest.mark.asyncio
async def test_governed_unfinished_stream_fails_closed_without_terminal_replay(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "safe partial must finish"}},
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _streaming_sdk(captured, events))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert captured["include_partial_messages"] is True
    assert result.error == "claude_agent_sdk_public_projection_failed"
    assert result.turn_diagnostics["projection_failure_reason"] == "upstream_projection_failed"
    assert deltas
    assert "safe partial must finish".startswith("".join(deltas))


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
                await pre({"tool_name": "Read", "tool_input": {}, "tool_use_id": "late-tool"}, "late-tool", {})
            finally:
                cleaned_up.set()

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
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
            tool_policy_subjects=[_subject(tool_name="Read", public_tool_label="Read file")],
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned_up.is_set()
    assert events == []

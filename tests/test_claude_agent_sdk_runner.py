import asyncio
import sys
import types

import pytest

from app.executors.claude_agent_sdk_runner import (
    run_claude_agent_sdk,
)
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
        del options
        captured["sdk_user_messages"] = [item async for item in prompt]
        for hook_name, hook_input, tool_call_id in hook_invocations:
            matchers = captured["hooks"][hook_name]
            if hook_name == "PreToolUse":
                matcher = matchers[0]
            else:
                tool_name = str(hook_input.get("tool_name") or "")
                matcher_name = "Skill" if tool_name.lower() == "skill" else "mcp__*"
                matcher = next(item for item in matchers if item.matcher == matcher_name)
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


def _scripted_sdk(captured, steps, *, result_text="done"):
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
        is_error = False
        errors = None
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
                matcher_name = "Skill" if tool_name.lower() == "skill" else "mcp__*"
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
        expected = "claude_agent_sdk_tool_admission_failed" if outcome == "overflow" else "required_tool_completion_evidence_mismatch"
        assert (result.error, result.message, deltas) == (expected, "", [])


@pytest.mark.asyncio
async def test_sdk_projects_known_mcp_identity_before_hook_and_releases_call_text_once(
    monkeypatch,
    tmp_path,
):
    captured, deltas, published_before_hook, published_after_hook = {}, [], [], []
    subject = _subject()
    call_id = "mcp-call-1"
    before = f"Before {subject['identity']}."
    after = f" After {call_id}."
    steps = [
        *_stream_steps(before),
        ("probe", lambda: published_before_hook.extend(deltas)),
        *_mcp_hook_steps(subject, call_id=call_id),
        *_stream_steps(after, index=1),
        ("probe", lambda: published_after_hook.extend(deltas)),
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

    joined = "".join(deltas)
    assert "".join(published_before_hook) == "Before external tool."
    assert published_after_hook[len(published_before_hook):] == [" After ", "tool invocation."]
    assert result.error is None
    assert joined == "Before external tool. After tool invocation."
    assert result.message == " After tool invocation."
    for private_value in (
        subject["identity"],
        subject["mcp_server_config"]["url"],
        call_id,
        "safe-synthetic-value",
    ):
        assert private_value not in joined


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

    assert observed_before_result == ["Verified MCP final answer streams ", "safely."]
    assert deltas == observed_before_result
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
    assert observed_before_result == ["Skill answer streams ", "safely."]
    assert deltas == observed_before_result
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

    assert observed_before_result == ["Verified Skill final answer streams ", "safely."]
    assert deltas == observed_before_result
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


def _sandbox_brokered_settings():
    return _settings()


@pytest.mark.asyncio
async def test_sandbox_streams_two_safe_raw_text_deltas_before_result_without_terminal_replay(
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
    assert result_gate == ["Short safe public ", "answer."]
    assert deltas == result_gate
    assert "".join(deltas) == streamed_text
    assert result.message == streamed_text


@pytest.mark.asyncio
async def test_sandbox_stream_ignores_complete_tool_use_block_before_safe_text(monkeypatch, tmp_path):
    captured = {}
    deltas = []
    streamed_text = "Safe answer after tool use."
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
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": streamed_text}},
        {"type": "content_block_stop", "index": 1},
    ]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _streaming_sdk(captured, events, result_text=streamed_text),
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
    assert "".join(deltas) == streamed_text
    assert result.message == streamed_text


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

    await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id="general-chat",
        execution_policy="sandbox_brokered",
        on_text=deltas.append,
    )

    assert deltas == ["short ", "answer"]


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
    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert deltas == ["safe partial must "]

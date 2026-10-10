import asyncio
import json
import sys
import types

import pytest

from tests.support.claude_mcp import install_mcp_sessions
from tests.support.claude_sdk import native_client_factory

from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    _canonical_sdk_error,
    _diagnostic_terminal_class,
    _public_skill_replacement,
    _sdk_autocompact_window,
    _sdk_run_timeout_seconds,
    run_claude_agent_sdk,
)
from app.executors.claude.capability_policy import (
    _canonical_tool_policy_subjects,
    _mcp_server_options,
    internal_context_tool_policy_subjects,
    internal_response_tool_policy_subjects,
)
from app.platform.public_payload import (
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
        (95_000, 100_000),
        (100_000, 100_000),
        (125_000, 125_000),
        (200_000, 200_000),
        (1_000_000, 1_000_000),
        (2_000_000, 1_000_000),
    ],
)
def test_sdk_autocompact_window_clamps_model_input_capacity_to_cli_bounds(
    model_max_input_tokens, expected
):
    assert _sdk_autocompact_window(model_max_input_tokens) == expected


def test_context_limit_error_outranks_prior_tool_denial():
    assert _canonical_sdk_error(
        ["prompt is too long: 100001 tokens > 100000 maximum"],
        terminal_reason="prompt_too_long",
        tool_admission_denials=1,
    ) == "claude_agent_sdk_input_context_too_large"
    assert _canonical_sdk_error(
        ["Request too large (max 32MB)"],
        terminal_reason="image_error",
        tool_admission_denials=1,
    ) == "claude_agent_sdk_input_image_invalid"


def _settings():
    return types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        claude_agent_sdk_max_turns=12,
        claude_agent_sdk_timeout_seconds=5,
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
        claude_agent_model="model-a",
        anthropic_model="",
        anthropic_base_url="",
        anthropic_auth_token="",
        openai_api_key="",
    )


def _assistant_text_part_roles(events):
    return {
        event.payload["part_id"]: event.payload["role"]
        for event in events
        if event.event_type == "message.part.classified"
    }


def _assistant_text_part_deltas(events, *, role=None):
    roles = _assistant_text_part_roles(events)
    return [
        event
        for event in events
        if event.event_type == "message.part.delta"
        and (role is None or roles.get(event.payload["part_id"]) == role)
    ]


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
    async def message_source(**kwargs):
        async for message in module.query(**kwargs):
            yield message

    class FakeClient(native_client_factory(message_source)):
        def __init__(self, options):
            super().__init__(options)
            self.options = options
            captured["client_mcp_server_types_at_construction"] = {
                name: config.get("type") if isinstance(config, dict) else None
                for name, config in getattr(options, "mcp_servers", {}).items()
            }

        async def connect(self, prompt):
            captured["client_connected"] = True
            await super().connect(prompt)

        async def disconnect(self):
            await super().disconnect()
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
    emit_answer=True,
):
    class ThinkingBlock:
        def __init__(self, thinking):
            self.thinking = thinking

    class AssistantMessage:
        def __init__(self, content, *, message_id="fake-message", uuid=None):
            self.content = content
            self.message_id = message_id
            self.uuid = uuid or message_id
            self.parent_tool_use_id = None
            self.stop_reason = None

    class TextBlock:
        def __init__(self, text=None):
            self.text = text

    raw_event_counter = 0

    class StreamEvent:
        def __init__(self, event):
            nonlocal raw_event_counter
            raw_event_counter += 1
            self.event = event
            self.uuid = f"fake-raw-{raw_event_counter}"
            self.parent_tool_use_id = None

    class MirrorErrorMessage:
        pass

    class ResultMessage:
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = "done"
        is_error = False
        errors = None
        stop_reason = "end_turn"
        num_turns = 1
        permission_denials = None
        uuid = "fake-result"
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
        captured["permission_results"] = [
            await captured["can_use_tool"](*probe)
            for probe in captured.get("permission_probes", [])
        ]
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
            if hook_name in {"PreToolUse", "SubagentStart", "SubagentStop"}:
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
        message_id = "fake-message"
        if not emit_answer:
            terminal = ResultMessage()
            if getattr(options, "session_store", None) is not None:
                terminal.session_id = getattr(options, "session_id", None) or getattr(options, "resume", None)
            yield terminal
            return
        yield StreamEvent(
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "role": "assistant",
                    "stop_reason": None,
                },
            }
        )
        if thinking_text is not None:
            yield StreamEvent(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking"},
                }
            )
            yield StreamEvent(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": thinking_text},
                }
            )
            yield StreamEvent({"type": "content_block_stop", "index": 0})
            text_index = 1
            content = [ThinkingBlock(thinking_text), TextBlock("done")]
        else:
            text_index = 0
            content = [TextBlock("done")]
        yield StreamEvent(
            {
                "type": "content_block_start",
                "index": text_index,
                "content_block": {"type": "text"},
            }
        )
        yield StreamEvent(
            {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": "done"},
            }
        )
        yield AssistantMessage(
            content,
            message_id=message_id,
            uuid="fake-assistant-observation",
        )
        yield StreamEvent({"type": "content_block_stop", "index": text_index})
        yield StreamEvent(
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
        )
        yield StreamEvent({"type": "message_stop"})
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
    result_stop_reason: str | None = "end_turn",
    permission_denials=None,
    result_uuid: str | None = "sdk-result",
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
        def __init__(
            self,
            text,
            *,
            content=None,
            message_id=None,
            uuid=None,
            stop_reason=None,
            parent_tool_use_id=None,
        ):
            self.content = [TextBlock(text)] if content is None else content
            self.message_id = message_id
            self.uuid = uuid
            self.stop_reason = stop_reason
            self.parent_tool_use_id = parent_tool_use_id

    stream_event_counter = 0

    class StreamEvent:
        def __init__(self, event):
            nonlocal stream_event_counter
            stream_event_counter += 1
            self.event = event
            self.uuid = f"sdk-raw-event-{stream_event_counter}"
            self.parent_tool_use_id = None

    class ResultMessage:
        def __init__(self, uuid):
            self.session_id = "sdk-session"
            self.usage = None
            self.model_usage = None
            self.result = result_text
            self.is_error = result_error is not None
            self.errors = [result_error] if result_error is not None else None
            self.stop_reason = result_stop_reason
            self.num_turns = 1
            self.permission_denials = denials
            self.uuid = uuid

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
        captured["permission_results"] = [
            await captured["can_use_tool"](*probe)
            for probe in captured.get("permission_probes", [])
        ]

        raw_text_by_index = {}
        raw_message_id = None
        last_assistant_id = None
        assistant_counter = 0
        assistant_observation_counter = 0

        def next_message_id():
            nonlocal assistant_counter
            assistant_counter += 1
            return f"typed-message-{assistant_counter}"

        def next_assistant_observation_id():
            nonlocal assistant_observation_counter
            assistant_observation_counter += 1
            return f"sdk-assistant-observation-{assistant_observation_counter}"

        async def invoke_hook(value):
            hook_name, hook_input, tool_call_id = value
            matchers = captured["hooks"][hook_name]
            if hook_name in {"PreToolUse", "SubagentStart", "SubagentStop"}:
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
            if kind == "assistant_error":
                error_code, text = value
                message = AssistantMessage(
                    text,
                    message_id=next_message_id(),
                    uuid=next_assistant_observation_id(),
                )
                message.error = error_code
                yield message
            elif kind == "assistant_typed":
                body = value["text"]
                last_assistant_id = value.get("message_id")
                yield AssistantMessage(
                    body,
                    content=value.get("content"),
                    message_id=last_assistant_id,
                    uuid=value.get("uuid"),
                    stop_reason=value.get("stop_reason"),
                    parent_tool_use_id=value.get("parent_tool_use_id"),
                )
            elif kind == "completed_text":
                last_assistant_id = "completed-provider"
                yield AssistantMessage(value, message_id=last_assistant_id,
                    uuid=next_assistant_observation_id())
            elif kind == "assistant":
                last_assistant_id = next_message_id()
                yield AssistantMessage(
                    value,
                    content=[TextBlock(value)],
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                )
            elif kind == "assistant_before_raw":
                body = value
                typed_stop_reason = None
                raw_stop_reason = "end_turn"
                if isinstance(value, tuple):
                    body, typed_stop_reason, raw_stop_reason = value
                last_assistant_id = raw_message_id or next_message_id()
                raw_message_id = last_assistant_id
                raw_text_by_index.clear()
                yield StreamEvent(
                    {
                        "type": "message_start",
                        "message": {
                            "id": last_assistant_id,
                            "role": "assistant",
                            "stop_reason": None,
                        },
                    }
                )
                yield StreamEvent(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    }
                )
                yield AssistantMessage(
                    body,
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                    stop_reason=typed_stop_reason,
                )
                raw_text_by_index[0] = body
                yield StreamEvent(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": body},
                    }
                )
                yield StreamEvent({"type": "content_block_stop", "index": 0})
                yield StreamEvent(
                    {"type": "message_delta", "delta": {"stop_reason": raw_stop_reason}}
                )
                yield StreamEvent({"type": "message_stop"})
            elif kind == "assistant_after_raw_prefix":
                prefix, body = value[:2]
                typed_body = value[2] if len(value) == 3 else body
                last_assistant_id = raw_message_id or next_message_id()
                raw_message_id = last_assistant_id
                raw_text_by_index.clear()
                yield StreamEvent(
                    {
                        "type": "message_start",
                        "message": {
                            "id": last_assistant_id,
                            "role": "assistant",
                            "stop_reason": None,
                        },
                    }
                )
                yield StreamEvent(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    }
                )
                raw_text_by_index[0] = prefix
                yield StreamEvent(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": prefix},
                    }
                )
                yield AssistantMessage(
                    typed_body,
                    content=[TextBlock(typed_body)],
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                )
                suffix = body[len(prefix) :]
                raw_text_by_index[0] = body
                if suffix:
                    yield StreamEvent(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": suffix},
                        }
                    )
                yield StreamEvent({"type": "content_block_stop", "index": 0})
                yield StreamEvent(
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
                )
                yield StreamEvent({"type": "message_stop"})
            elif kind == "assistant_parser_compressed":
                text = value
                last_assistant_id = raw_message_id or next_message_id()
                raw_message_id = last_assistant_id
                raw_text_by_index.clear()
                yield StreamEvent(
                    {
                        "type": "message_start",
                        "message": {
                            "id": last_assistant_id,
                            "role": "assistant",
                            "stop_reason": None,
                        },
                    }
                )
                yield StreamEvent(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "web_search_tool_result"},
                    }
                )
                yield StreamEvent({"type": "content_block_stop", "index": 0})
                yield StreamEvent(
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "text"},
                    }
                )
                raw_text_by_index[1] = text
                yield StreamEvent(
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": text},
                    }
                )
                yield AssistantMessage(
                    text,
                    content=[TextBlock(text)],
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                )
                yield StreamEvent({"type": "content_block_stop", "index": 1})
                yield StreamEvent(
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
                )
                yield StreamEvent({"type": "message_stop"})
            elif kind == "assistant_blocks":
                blocks = value
                raw_stop_reason = "tool_use"
                if isinstance(value, tuple):
                    blocks, raw_stop_reason = value
                last_assistant_id = raw_message_id or next_message_id()
                raw_message_id = last_assistant_id
                raw_text_by_index.clear()
                yield StreamEvent(
                    {
                        "type": "message_start",
                        "message": {
                            "id": last_assistant_id,
                            "role": "assistant",
                            "stop_reason": None,
                        },
                    }
                )
                for block_index, block in enumerate(blocks):
                    block_name = type(block).__name__
                    block_type = (
                        "server_tool_use"
                        if block_name == "ServerToolUseBlock"
                        else "tool_use"
                        if block_name == "ToolUseBlock"
                        else "thinking"
                        if block_name in {"ThinkingBlock", "RedactedThinkingBlock"}
                        else "text"
                    )
                    content_block = {"type": block_type}
                    if block_type in {"tool_use", "server_tool_use"}:
                        content_block.update(
                            {
                                "id": getattr(block, "id", None),
                                "name": getattr(block, "name", None),
                            }
                        )
                    yield StreamEvent(
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": content_block,
                        }
                    )
                    if block_type == "text":
                        text = getattr(block, "text", None)
                        if isinstance(text, str):
                            yield StreamEvent(
                                {
                                    "type": "content_block_delta",
                                    "index": block_index,
                                    "delta": {"type": "text_delta", "text": text},
                                }
                            )
                    elif block_type == "thinking":
                        thinking = getattr(block, "thinking", None)
                        if isinstance(thinking, str):
                            yield StreamEvent(
                                {
                                    "type": "content_block_delta",
                                    "index": block_index,
                                    "delta": {
                                        "type": "thinking_delta",
                                        "thinking": thinking,
                                    },
                                }
                            )
                    yield StreamEvent(
                        {"type": "content_block_stop", "index": block_index}
                    )
                message = AssistantMessage(
                    "",
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                )
                message.content = blocks
                yield message
                yield StreamEvent(
                    {"type": "message_delta", "delta": {"stop_reason": raw_stop_reason}}
                )
                yield StreamEvent({"type": "message_stop"})
            elif kind == "assistant_tool":
                last_assistant_id = (
                    value.get("message_id") or raw_message_id or next_message_id()
                )
                message = AssistantMessage(
                    "",
                    message_id=last_assistant_id,
                    uuid=value.get("uuid") or next_assistant_observation_id(),
                    stop_reason="tool_use",
                )
                message.content = [
                    ThinkingBlock(value["thinking"]),
                    ToolUseBlock(
                        id=value["id"],
                        name=value["name"],
                        input=value["input"],
                    ),
                ]
                yield message
            elif kind == "assistant_tool_turn":
                last_assistant_id = raw_message_id or next_message_id()
                raw_message_id = last_assistant_id
                raw_text_by_index.clear()
                yield StreamEvent(
                    {
                        "type": "message_start",
                        "message": {
                            "id": last_assistant_id,
                            "role": "assistant",
                            "stop_reason": None,
                        },
                    }
                )
                yield StreamEvent(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": value["id"],
                            "name": value["name"],
                        },
                    }
                )
                yield StreamEvent({"type": "content_block_stop", "index": 0})
                message = AssistantMessage(
                    "",
                    message_id=last_assistant_id,
                    uuid=next_assistant_observation_id(),
                    stop_reason="tool_use",
                )
                message.content = [
                    ThinkingBlock(value["thinking"]),
                    ToolUseBlock(
                        id=value["id"],
                        name=value["name"],
                        input=value["input"],
                    ),
                ]
                yield message
                yield StreamEvent(
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
                )
                yield StreamEvent({"type": "message_stop"})
            elif kind == "stream":
                event = value
                if isinstance(event, dict) and event.get("type") == "message_start":
                    message = event.get("message")
                    raw_message_id = (
                        message.get("id")
                        if isinstance(message, dict)
                        else None
                    )
                    last_assistant_id = raw_message_id
                    raw_text_by_index.clear()
                if (
                    isinstance(event, dict)
                    and event.get("type") == "content_block_delta"
                    and isinstance(event.get("delta"), dict)
                    and event["delta"].get("type") == "text_delta"
                    and isinstance(event["delta"].get("text"), str)
                    and isinstance(event.get("index"), int)
                ):
                    raw_text_by_index[event["index"]] = (
                        raw_text_by_index.get(event["index"], "")
                        + event["delta"]["text"]
                    )
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
        yield ResultMessage(result_uuid)

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


def _completed_text_steps(text, *, index=0):
    del index
    return [("completed_text", text)]


async def _acknowledge_capability_evidence(_evidence):
    return True





@pytest.mark.asyncio
async def test_sdk_structured_output_protocol_does_not_bypass_capability_admission(
    monkeypatch,
    tmp_path,
):
    captured = {}
    captured["permission_probes"] = [("StructuredOutput", {"answer": "done", "deliverables": []}, {"tool_use_id": "structured-output-call-1"})]
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
    permission = captured["permission_results"][0]
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
    captured["permission_probes"] = [("StructuredOutput", {"answer": "done", "deliverables": []}, {"tool_use_id": "structured-output-call-1"})]
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

    permission = captured["permission_results"][0]
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
async def test_sdk_registers_subagent_lifecycle_hooks(monkeypatch, tmp_path):
    captured, lifecycle_facts = {}, []
    identity = {
        "session_id": "sdk-parent-session",
        "agent_id": "sdk-child-agent",
        "agent_type": "reviewer",
    }

    async def record_lifecycle(fact):
        lifecycle_facts.append(dict(fact))

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(
            captured,
            hook_invocations=[
                (
                    "SubagentStart",
                    {
                        "session_id": identity["session_id"],
                        "agent_type": identity["agent_type"],
                        "hook_event_name": "SubagentStart",
                    },
                    None,
                ),
                (
                    "SubagentStart",
                    {**identity, "hook_event_name": "SubagentStart"},
                    None,
                ),
                (
                    "SubagentStop",
                    {**identity, "hook_event_name": "SubagentStop"},
                    None,
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="delegate",
        cwd=tmp_path,
        skill_id=None,
        on_subagent_lifecycle=record_lifecycle,
    )

    assert result.error is None
    assert captured["hooks"]["SubagentStart"][0].matcher is None
    assert captured["hooks"]["SubagentStop"][0].matcher is None
    assert lifecycle_facts == [
        {"lifecycle": "started", **identity},
        {"lifecycle": "stopped", **identity},
    ]





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
            emit_answer=False,
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
        "attachment_tool_registered": 0,
        "attachment_tool_calls": 0,
        "attachment_tool_failures": 0,
        "attachment_selected_files": 0,
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
        *_completed_text_steps("I will inspect the sandbox. ", index=0),
        ("hook", ("PreToolUse", hook_input, "bash-call-1")),
        *_completed_text_steps("The inspection started.", index=1),
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

    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""






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
        *_completed_text_steps(public_text),
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
    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""






@pytest.mark.asyncio
async def test_sandbox_effectful_tool_streams_safe_text_across_verified_lifecycle(
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
        *_completed_text_steps("I will update the file. ", index=0),
        ("hook", ("PreToolUse", hook_input, "local-call-1")),
        ("hook", ("PostToolUse", hook_input, "local-call-1")),
        *_completed_text_steps("The file was updated.", index=1),
        ("probe", lambda: observed_before_result.extend(deltas)),
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

    assert captured["include_partial_messages"] is False
    assert observed_before_result == []
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
        *_completed_text_steps("Before read. ", index=0),
        ("hook", ("PreToolUse", hook_input, "read-only-call-1")),
        *_completed_text_steps("private file content", index=1),
        ("hook", ("PostToolUse", hook_input, "read-only-call-1")),
        *_completed_text_steps("After read.", index=2),
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

    oversized_text = None
    steps = [
        ("assistant_typed", {"message_id": "invalid-text-source", "uuid": "invalid-text", "text": None}),
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
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.turn_diagnostics["counters"]["tool_lifecycle_denials"] == 0






@pytest.mark.asyncio
async def test_failed_answer_projection_keeps_skill_and_bash_receipts(
    monkeypatch,
    tmp_path,
):
    captured, deltas, lifecycle_facts, capability_facts = {}, [], [], []
    oversized_text = "x " * 131_072
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
        *_completed_text_steps(oversized_text),
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
        *_completed_text_steps("private file content"),
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

    assert deltas == []
    assert result.error == "claude_agent_sdk_tool_admission_failed"
    assert result.message == ""





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
        *_completed_text_steps("private file content"),
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
                ("assistant", f"Completed {call_id}."),
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
            _completed_text_steps(direct_answer),
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
        *_completed_text_steps(public_text),
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
        *_completed_text_steps("must remain private"),
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

    assert deltas == []
    assert result.error == "required_tool_completion_evidence_missing"
    assert result.message == ""






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
                ("assistant", "command completed"),
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
async def test_required_sandbox_bash_failure_rejects_unframed_typed_body(
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
    assert "".join(deltas) == ""
    assert result.error == "required_tool_completion_evidence_mismatch"
    assert result.message == ""
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
    captured["permission_probes"] = [("Bash", {"command": "pwd"})]
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
    denied = captured["permission_results"][0]

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

    def prepare(subjects, configs, callback_wrapper=None):
        return ClaudeMcpRegistration(
            subjects,
            configs,
            session_factory=session_factory,
            list_tools=list_tools,
            callback_wrapper=callback_wrapper,
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
@pytest.mark.parametrize("continue_after_denial", [False, True])
async def test_sdk_permission_denial_closes_started_internal_mcp_lifecycle(
    monkeypatch, tmp_path, continue_after_denial
):
    captured, lifecycle_facts = {}, []
    subject = internal_context_tool_policy_subjects(["read_run_artifact"])[0]
    call_id = "mcp-call-denied"
    hook_input = {
        "tool_name": subject["identity"],
        "tool_use_id": call_id,
        "tool_input": {"artifact_id": "artifact-a", "max_bytes": 10},
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

    interaction_kwargs = {}
    if continue_after_denial:
        from app.execution.application.run_interaction import RunInputCommand
        from tests.test_claude_run_interactions import Inputs
        from builtins import anext
        port = Inputs([RunInputCommand("continue", "text", text="try another approach")])
        queries = []

        class Client:
            def __init__(self, options):
                self.stream = None

            async def connect(self, stream):
                self.stream = stream
                await anext(stream)

            async def query(self, prompt, session_id):
                queries.append((prompt, session_id))

            async def receive_messages(self):
                for matcher in captured["hooks"]["PreToolUse"]:
                    for hook in matcher.hooks:
                        await hook(hook_input, call_id, {})
                yield sdk.ResultMessage("first-result")
                final = sdk.ResultMessage("final-result")
                final.permission_denials = []
                yield final
                with pytest.raises(StopAsyncIteration):
                    await anext(self.stream)

            async def disconnect(self):
                await self.stream.aclose()

        interaction_kwargs = {"interaction_client": port, "client_fn": Client,
                              "run_id": "run-a", "attempt_id": "attempt-a"}

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
        **interaction_kwargs,
    )

    assert lifecycle_facts == [
        ("MCP", call_id, "started"),
        ("MCP", call_id, "failed"),
    ]
    assert result.error is None
    assert result.turn_diagnostics["counters"]["tool_admission_denials"] == 2

    if continue_after_denial:
        assert queries == [("try another approach", "sdk-session")]
        assert port.acks == ["continue"]






@pytest.mark.asyncio
async def test_sdk_explicit_skillless_harness_registers_no_skill_tool(
    monkeypatch,
    tmp_path,
):
    captured = {}
    captured["permission_probes"] = [("Skill", {"skill": "untrusted-skill"})]
    reported = []

    async def on_skill_use(skill_name, metadata):
        reported.append((skill_name, metadata))

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, _completed_text_steps("done"), result_text="done"),
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
    denied = captured["permission_results"][0]
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
        _scripted_sdk(captured, _completed_text_steps("done"), result_text="done"),
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
    assert {"mcp__tenant_server__search", "mcp__other-server__fetch"}.issubset(captured["allowed_tools"])





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
    subject["write_capable"] = True

    async def acknowledge_candidates(candidates):
        event_types = {event.event_type for event in candidates}
        if rejected_terminal_stage == "answer" and "message.part.delta" in event_types:
            return False
        if rejected_terminal_stage == "result" and "model.completed" in event_types:
            return False
        return True

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [*_mcp_hook_steps(subject), ("assistant", "done")],
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
@pytest.mark.parametrize(
    ("terminal_hook", "expected_error"),
    [
        ("PostToolUse", "mcp_execution_succeeded_receipt_incomplete"),
        (None, "mcp_execution_outcome_unknown"),
    ],
)
async def test_sdk_error_after_mcp_admission_requires_reconciliation(
    monkeypatch, tmp_path, terminal_hook, expected_error
):
    captured = {}
    subject = _subject()
    subject["write_capable"] = True
    steps = _mcp_hook_steps(subject)
    if terminal_hook is None:
        steps = steps[:1]
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, steps, result_error="synthetic upstream error"),
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
    )

    assert result.error == expected_error
    assert result.turn_diagnostics["action"] == "reconcile_before_retry"
    assert result.turn_diagnostics["retryable"] is False
    assert result.runtime_diagnostics["error_code"] == expected_error





@pytest.mark.asyncio
async def test_sdk_first_mcp_terminal_without_admission_is_outcome_unknown(
    monkeypatch, tmp_path
):
    captured = {}
    subject = _subject()
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(captured, _mcp_hook_steps(subject)[1:]),
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
    )

    assert result.error == "mcp_execution_outcome_unknown"
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
    assert "mcp__tenant_server__search" in captured["allowed_tools"]
    assert denied["identity"] not in captured["allowed_tools"]
    assert malformed["identity"] not in captured["allowed_tools"]





def _actual_mcp_steps(outcome, subjects, text, probe):
    first_pre, first_completed = _mcp_hook_steps(subjects[0], call_id="mcp-call-1")
    if outcome == "overflow":
        return [first_pre, first_completed, *_completed_text_steps(text), ("probe", probe)]
    if outcome == "stale":
        return [first_completed, *_completed_text_steps(text), ("probe", probe)]
    if outcome == "duplicate":
        return [
            first_pre,
            first_completed,
            first_completed,
            *_completed_text_steps(text),
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
            *_completed_text_steps(text),
            first_completed,
            ("probe", probe),
            second_terminal,
        ]
    steps = [first_pre, *_completed_text_steps(text), ("probe", probe)]
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
@pytest.mark.parametrize("write_capable", [False, True])
async def test_sdk_actual_mcp_streams_public_text_without_waiting_for_receipt(
    monkeypatch, tmp_path, outcome, write_capable
):
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
    for subject in subjects:
        subject["write_capable"] = write_capable
    private_text = f"Safe answer via {first['identity']} with mcp-call-1 at {first['mcp_server_config']['url']}."
    text = (
        "x " * 131_072
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

    assert sealed_probe == []  # Legacy on_text converges only after SDK terminal controls.
    if outcome in {"success", "multiple_completed"} or (not write_capable and outcome in {"failed", "multiple_failed"}):
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
            "missing": "mcp_execution_outcome_unknown",
            "stale": "mcp_execution_outcome_unknown",
            "duplicate": "mcp_execution_succeeded_receipt_incomplete",
            "multiple_failed": "mcp_execution_outcome_unknown",
        }.get(outcome, "required_tool_completion_evidence_mismatch")
        if not write_capable and outcome in {"false", "exception", "incomplete", "duplicate"}:
            expected = "required_tool_completion_evidence_mismatch"
        if outcome == "overflow":
            assert result.error is None
            assert result.message == text
            assert "".join(deltas) == text
            assert text.startswith("".join(sealed_probe))
        elif outcome in {"stale", "duplicate"}:
            assert (result.error, result.message, deltas) == (expected, "", [])
        else:
            assert result.error == expected
            assert result.message == ""
            assert result.answer_receipt is None
            assert "mcp_execution_" not in result.message
            assert "private callback failure" not in result.message
            assert "retryable" in result.turn_diagnostics
            assert result.turn_diagnostics["retryable"] is False
            assert deltas == []
            if outcome == "missing":
                assert deltas == []
        if outcome == "overflow":
            assert "projection_failure_reason" not in result.turn_diagnostics








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
        *_completed_text_steps("private tool output"),
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
async def test_sdk_keeps_already_published_safe_prefix_on_failed_terminal(
    monkeypatch,
    tmp_path,
):
    captured, deltas = {}, []
    subject = _subject()
    subject["write_capable"] = True
    call_id = "mcp-call-1"
    steps = [
        *_mcp_hook_steps(subject, call_id=call_id),
        *_completed_text_steps("provisional answer must not escape"),
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
    assert deltas == []
    assert "provisional answer must not escape".startswith("".join(deltas))






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
        *_completed_text_steps(sealed_pre_capability_text),
        completed_hook,
        *_completed_text_steps(verified_answer, index=1),
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
    assert observed_before_result == []
    assert expected_text.startswith("".join(observed_before_result))
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
    assert result.message == ""
    assert deltas == []
    assert result.answer_receipt is None
    assert any(event.event_type == "message.part.delta" for event in candidate_events)
    assert not any(event.event_type == "message.completed" for event in candidate_events)






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
        ("assistant", "done"),
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
            captured, _completed_text_steps("Direct answer."), result_text="Direct answer."
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





@pytest.mark.parametrize(
    "public_name",
    ["Reference Search", "input-tax-reconciliation", "进项税核对 V8"],
)
def test_public_skill_replacement_preserves_name_characters(public_name):
    assert _public_skill_replacement(
        "qa-review", {"qa-review": {"name": public_name}}
    ) == f"【技能：{public_name}】"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "optional_skill",
        "stream_parts",
        "shared_mcp",
        "call_id",
        "public_name",
        "expected_replacement",
        "private_token",
    ),
    [
        (
            "reference-search",
            ("Using reference-", "search. "),
            False,
            "skill-call-reference",
            "Reference Search",
            "【技能：Reference Search】",
            "",
        ),
        (
            "capability",
            ("Using cap", "ability. "),
            False,
            "capability",
            "Reference Search",
            "【技能：Reference Search】",
            "",
        ),
        (
            "tool",
            ("Using to", "ol. "),
            False,
            "tool",
            "Reference Search",
            "【技能：Reference Search】",
            "",
        ),
        (
            "mcp__tenant-server__search",
            ("Using mcp__tenant-", "server__search. "),
            True,
            "mcp__tenant-server__search",
            "Reference Search",
            "【技能：Reference Search】",
            "",
        ),
        (
            "internal-reference-helper",
            ("Using internal-reference-", "helper. "),
            False,
            "skill-call-private",
            None,
            "【技能】",
            "",
        ),
        (
            "input-tax-reconciliation",
            ("Using input-tax-", "reconciliation. "),
            False,
            "skill-call-reference",
            "input-tax-reconciliation",
            "【技能：input-tax-reconciliation】",
            "",
        ),
        (
            "reference-search",
            ("Using reference-", "search. "),
            False,
            "skill-call-reference",
            "reference-search V8",
            "【技能：reference-search V8】",
            "",
        ),
        (
            "reference-search",
            ("Using reference-", "search. "),
            False,
            "skill-call-reference",
            "进项税核对 V8",
            "【技能：进项税核对 V8】",
            "",
        ),
        (
            "input-tax-reconciliation",
            ("Using input-tax-", "reconciliation. "),
            False,
            "skill-call-reference",
            "input-tax-reconciliation",
            "█",
            "input-tax-reconciliation",
        ),
        (
            "input-tax-reconciliation",
            ("Using input-tax-", "reconciliation. "),
            False,
            "skill-call-reference",
            "input-tax-reconciliation",
            "█",
            "tax",
        ),
    ],
)
async def test_sdk_preserves_public_optional_skill_text_before_failed_receipt(
    monkeypatch,
    tmp_path,
    optional_skill,
    stream_parts,
    shared_mcp,
    call_id,
    public_name,
    expected_replacement,
    private_token,
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
                *_completed_text_steps("".join(stream_parts)),
                ("hook", ("PreToolUse", skill_input, call_id)),
                (
                    "hook",
                    ("PostToolUseFailure", skill_input, call_id),
                ),
            ],
            result_text="",
        ),
    )
    settings = _sandbox_brokered_settings()
    settings.anthropic_auth_token = private_token
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", lambda: settings
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
    assert result.error is None
    assert result.used_skills == []
    assert [item["lifecycle_phase"] for item in result.capability_evidence] == [
        "invocation_requested",
        "failed",
    ]
    assert public_text == f"Using {expected_replacement}. "
    assert result.message == public_text





@pytest.mark.asyncio
async def test_sdk_hook_only_call_id_cannot_reuse_an_authorized_public_skill_identity(
    monkeypatch, tmp_path
):
    identity = "reference-search"
    hook_input = {
        "tool_name": "Skill",
        "tool_use_id": identity,
        "tool_input": {"skill": identity},
    }
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            {},
            [
                *_completed_text_steps(f"Using {identity}. "),
                ("hook", ("PreToolUse", hook_input, identity)),
                ("hook", ("PostToolUseFailure", hook_input, identity)),
            ],
            result_text="",
        ),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )
    result = await run_claude_agent_sdk(
        prompt="find the relevant reference",
        cwd=tmp_path,
        skill_id=identity,
        skills=[identity],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject(identity)],
        on_capability_evidence=_acknowledge_capability_evidence,
        public_skill_metadata={
            identity: {"name": f"{identity} V8", "version": "1.0.0", "availability": "available"}
        },
    )
    assert result.error == "claude_agent_sdk_output_validation_failed"
    assert result.answer_receipt is None


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
        *_completed_text_steps(text),
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
    assert observed_before_result == []
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
        *_completed_text_steps(text),
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

    assert result.error is None
    assert observed_before_result == []
    assert "".join(deltas) == text
    assert result.message == ""
    assert result.answer_receipt is not None
    assert result.answer_receipt["text_length"] == len(text)
    event_types = [
        candidate.event_type
        if hasattr(candidate, "event_type")
        else candidate.as_agent_event_fields()["type"]
        for candidate in candidates
    ]
    assert "claude_sdk_thinking_summary" not in event_types
    assert "message.delta" not in event_types
    assert "message.part.delta" in event_types






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
        *_completed_text_steps(sealed_pre_capability_text),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        *_completed_text_steps(" cumulative terminal answer"),
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
        *_completed_text_steps(sealed_pre_capability_text),
        ("hook", ("PostToolUse", skill_input, "skill-call-1")),
        *_completed_text_steps(verified_answer, index=1),
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
    assert observed_before_result == []
    assert expected_text.startswith("".join(observed_before_result))
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
        *_completed_text_steps(text),
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
    ) == ("required_tool_completion_evidence_mismatch", "", [], [], [])






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
    ) == ("required_tool_completion_evidence_mismatch", "", [], [], [])





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
@pytest.mark.parametrize("answer", ["Final user answer", ""])
async def test_sdk_attach_file_selects_ordered_final_deliverables(monkeypatch, tmp_path, answer):
    captured, attach_results, deltas = {}, [], []
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "final.txt").write_text("final", encoding="utf-8")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "facts.json").write_text("{}", encoding="utf-8")
    skill_output = tmp_path / ".claude" / "skills" / "reporting" / "output"
    skill_output.mkdir(parents=True)
    (skill_output / "report.docx").write_bytes(b"report")

    class AssistantMessage:
        message_id = "attach-message"
        uuid = "attach-message"
        parent_tool_use_id = None
        stop_reason = None

        def __init__(self):
            self.content = [TextBlock(answer)]

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ResultMessage:
        __annotations__ = {"structured_output": object}
        session_id = "sdk-session"
        usage = None
        model_usage = None
        result = answer
        structured_output = {"legacy": "ignored"}
        is_error = False
        errors = None
        stop_reason = "end_turn"
        terminal_reason = "completed"
        num_turns = 1
        permission_denials = None
        uuid = "attach-message"

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
        yield AssistantMessage()
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

    assert "".join(deltas) == answer
    assert result.error is None
    assert result.message == answer
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
    counters = result.turn_diagnostics["counters"]
    assert {name: counters[name] for name in (
        "attachment_tool_registered", "attachment_tool_calls",
        "attachment_tool_failures", "attachment_selected_files",
    )} == {
        "attachment_tool_registered": 1,
        "attachment_tool_calls": 4,
        "attachment_tool_failures": 1,
        "attachment_selected_files": 2,
    }
    assert attach_results[0]["is_error"] is True
    assert [json.loads(item["content"][0]["text"]) for item in attach_results[1:]] == [
        {"selected": True, "position": 0},
        {"selected": True, "position": 1},
        {"selected": True, "position": 0},
    ]
    assert "output_format" not in captured
    assert "ai-platform-response" in captured["mcp_servers"]





@pytest.mark.asyncio
@pytest.mark.parametrize("select_file", [False, True])
@pytest.mark.parametrize("publish_events", [False, True])
async def test_response_delivery_observes_real_sdk_mcp_dispatch(
    monkeypatch, tmp_path, select_file, publish_events
):
    import claude_agent_sdk as installed_sdk
    from mcp import types as mcp_types

    captured, lifecycle, public_events = {}, [], []
    (tmp_path / "final.txt").write_text("synthetic deliverable", encoding="utf-8")
    sdk = _scripted_sdk(captured, [], result_text="任务已完成")
    sdk.tool = installed_sdk.tool
    sdk.create_sdk_mcp_server = installed_sdk.create_sdk_mcp_server
    sdk.ToolPermissionContext = installed_sdk.ToolPermissionContext
    sdk.PermissionResultAllow = installed_sdk.PermissionResultAllow
    sdk.PermissionResultDeny = installed_sdk.PermissionResultDeny
    scripted_query = sdk.query

    async def query(*, prompt, options):
        server = captured["mcp_servers"]["ai-platform-response"]["instance"]
        listed = await server.request_handlers[mcp_types.ListToolsRequest](
            mcp_types.ListToolsRequest()
        )
        assert [tool.name for tool in listed.root.tools] == ["attach_file"]
        identity = "mcp__ai-platform-response__attach_file"
        assert identity in captured["allowed_tools"]
        if select_file:
            args = {"path": "final.txt"}
            allowed = await captured["can_use_tool"](
                identity, args, installed_sdk.ToolPermissionContext(tool_use_id="file-call")
            )
            assert allowed.behavior == "allow"
            hook_input = {
                "tool_name": identity, "tool_input": args, "tool_use_id": "file-call"
            }
            before = captured["hooks"]["PreToolUse"][0]
            admitted = await before.hooks[0](hook_input, "file-call", {})
            assert admitted["hookSpecificOutput"]["permissionDecision"] == "allow"
            called = await server.request_handlers[mcp_types.CallToolRequest](
                mcp_types.CallToolRequest(
                    params=mcp_types.CallToolRequestParams(name="attach_file", arguments=args)
                )
            )
            assert called.root.isError is False
            assert json.loads(called.root.content[0].text) == {"selected": True, "position": 0}
            after = next(
                item for item in captured["hooks"]["PostToolUse"] if item.matcher == "mcp__*"
            )
            await after.hooks[0](hook_input, "file-call", {})
        async for message in scripted_query(prompt=prompt, options=options):
            yield message

    async def acknowledge(fact):
        lifecycle.append(fact)
        return True

    async def acknowledge_events(events):
        public_events.extend(events)
        return True

    sdk.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(
        prompt="请提供文件", cwd=tmp_path, skill_id=None,
        execution_policy="sandbox_brokered",
        tool_policy_subjects=internal_response_tool_policy_subjects(),
        on_tool_lifecycle=acknowledge,
        on_agent_event=acknowledge_events if publish_events else None,
        run_id="run-response-delivery", attempt_id="attempt-response-delivery",
    )

    assert result.error is None
    assert result.response_files == (["final.txt"] if select_file else [])
    counters = result.turn_diagnostics["counters"]
    assert counters["attachment_tool_registered"] == 1
    assert counters["attachment_tool_calls"] == int(select_file)
    assert counters["attachment_tool_failures"] == 0
    assert counters["attachment_selected_files"] == int(select_file)
    assert [fact["lifecycle"] for fact in lifecycle] == (
        ["started", "completed"] if select_file else []
    )
    tool_events = [event for event in public_events if event.event_type.startswith("tool.")]
    assert [event.event_type for event in tool_events] == (
        ["tool.started", "tool.completed"] if select_file and publish_events else []
    )
    assert all(event.payload["display_name"] == "交付文件" for event in tool_events)
    serialized = json.dumps([event.payload for event in public_events], ensure_ascii=False)
    assert all(value not in serialized for value in (
        "mcp__", "ai-platform-response", "attach_file", "file-call", "final.txt",
    ))





@pytest.mark.asyncio
async def test_sdk_empty_success_is_separate_from_public_answer_absence(monkeypatch, tmp_path):
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

    assert result.error is None
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.received_structured_terminal is True
    assert not result.message.strip()





















@pytest.mark.asyncio
async def test_sdk_sandbox_typed_body_before_raw_delta_is_published_once(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    body = "typed before raw."
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant_before_raw", body)],
            result_text=body,
            result_uuid="typed-before-raw-result",
        ),
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

    assert result.error is None
    assert result.message == body
    assert "".join(deltas) == body





@pytest.mark.asyncio
async def test_sdk_sandbox_raw_prefix_typed_extension_does_not_replay_suffix(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    body = "Hello world"
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant_after_raw_prefix", ("Hello", body))],
            result_text=body,
            result_uuid="raw-prefix-typed-extension-result",
        ),
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

    assert result.error is None
    assert result.message == body
    assert "".join(deltas) == body













@pytest.mark.asyncio
async def test_sdk_streamed_answer_can_complete_when_result_field_is_empty(
    monkeypatch, tmp_path
):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant", "streamed final answer")],
            result_text="   ",
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
    assert result.message == "streamed final answer"





@pytest.mark.asyncio
@pytest.mark.parametrize("result_text", ["terminal answer", "   "])
async def test_sdk_open_stop_reason_string_does_not_invent_body_or_execution_failure(
    monkeypatch, tmp_path, result_text
):
    captured, deltas = {}, []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [],
            result_text=result_text,
            result_stop_reason="future_stop",
        ),
    )
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        on_text=deltas.append,
    )

    assert result.error is None
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.received_structured_terminal is True
    assert deltas == []
    assert result.message == ""







@pytest.mark.asyncio
async def test_sdk_tool_narration_uses_work_trace_without_answer_receipt(
    monkeypatch, tmp_path
):
    captured, candidates, deltas = {}, [], []
    steps = []
    sdk = _scripted_sdk(captured, steps, result_text="Final user answer")
    steps.append(
        (
            "assistant_blocks",
            [
                sdk.TextBlock("Checking tool-private before answering."),
                sdk.ToolUseBlock(id="tool-private", name="Read", input={}),
            ],
        )
    )
    steps.append(("assistant", "Final user answer"))
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="answer", cwd=tmp_path, skill_id="general-chat",
        on_text=deltas.append,
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-work-trace", attempt_id="attempt-work-trace",
    )

    work = _assistant_text_part_deltas(candidates, role="work")
    answer = _assistant_text_part_deltas(candidates, role="answer")
    assert result.error is None
    assert "".join(event.payload["delta"] for event in work) == "Checking █ before answering."
    assert "".join(event.payload["delta"] for event in answer) == "Final user answer"
    assert "".join(deltas) == "Final user answer"
    assert result.answer_receipt is None
    assert result.message == "Final user answer"










@pytest.mark.asyncio
@pytest.mark.parametrize("reject_second_batch", [False, True])
async def test_sdk_tool_narration_batches_at_callback_limit(
    monkeypatch, tmp_path, reject_second_batch
):
    captured, batches, deltas = {}, [], []
    steps = []
    sdk = _scripted_sdk(captured, steps, result_text="Done.")
    narration = "x " * (8_192 * 50) + "x"
    steps.append(("assistant_blocks", [
        sdk.TextBlock(narration),
        sdk.ToolUseBlock(id="tool-1", name="Read", input={}),
    ]))
    steps.append(("assistant", "Done."))
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    def acknowledge(batch):
        batches.append(batch)
        return not (reject_second_batch and len(batches) == 2)

    result = await run_claude_agent_sdk(
        prompt="answer", cwd=tmp_path, skill_id="general-chat",
        on_text=deltas.append, on_agent_event=acknowledge,
        run_id="run-long-work-trace", attempt_id="attempt-long-work-trace",
    )
    assert all(len(batch) <= 100 for batch in batches)
    if reject_second_batch:
        assert result.error == "agent_event_callback_not_acknowledged"
        assert deltas == []
    else:
        assert result.error is None
        work = _assistant_text_part_deltas(
            [event for batch in batches for event in batch], role="work",
        )
        assert "".join(event.payload["delta"] for event in work) == narration
        assert deltas == ["Done."]






@pytest.mark.asyncio
async def test_sdk_tool_turn_omits_unsafe_work_trace_before_result(
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
        message_id = "tool-turn-message"
        uuid = "tool-turn-message"
        parent_tool_use_id = None
        stop_reason = "tool_use"
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
        uuid = "final-tool-turn-message"

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
        final_message = AssistantMessage()
        final_message.message_id = "final-tool-turn-message"
        final_message.uuid = "final-tool-turn-message"
        final_message.stop_reason = "end_turn"
        final_message.content = [TextBlock("Final user answer")]
        yield final_message
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

    work = _assistant_text_part_deltas(candidates, role="work")
    assert "message.part.delta" in observed_before_result
    assert not any(candidate.event_type == "commentary.delta" for candidate in candidates)
    assert "Checking █ in █ before the next step." == "".join(
        candidate.payload["delta"] for candidate in work
    )
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

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        message_id = "structured-message"
        uuid = "structured-observation"
        parent_tool_use_id = None
        stop_reason = None
        content = [TextBlock("done")]

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
        uuid = "structured-result"

    ResultMessage.structured_output = structured_output

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
        skill_id=None,
    )

    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.message == "done"
    assert result.response_files == []
    assert result.response_file_descriptors == []
    assert "output_format" not in captured







@pytest.mark.asyncio
async def test_sdk_preserves_gate_output_across_8192_candidate_boundary(
    monkeypatch, tmp_path
):
    answer = "a" * 8_182 + " token-count "
    captured, candidates, deltas = {}, [], []
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _completed_text_steps(answer),
            result_text=answer,
        ),
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
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-8192-boundary",
        attempt_id="attempt-8192-boundary",
    )

    answer_deltas = [
        event.payload["delta"]
        for event in _assistant_text_part_deltas(candidates, role="answer")
    ]
    assert len(answer) == 8_195
    assert result.error is None
    assert "".join(deltas) == answer
    assert "".join(answer_deltas) == answer
    assert answer_deltas and all(len(chunk) <= 8_192 for chunk in answer_deltas)
    assert result.answer_receipt is not None
    assert result.answer_receipt["text_length"] == len(answer)
    assert result.turn_diagnostics["counters"]["public_projection_omissions"] == 0





@pytest.mark.asyncio
async def test_sdk_rejected_part_candidate_cannot_be_reported_as_success(
    monkeypatch, tmp_path
):
    captured, candidates, deltas = {}, [], []

    def reject_part_batch(batch):
        candidates.extend(batch)
        return not any(event.event_type == "message.part.delta" for event in batch)

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _completed_text_steps("Delivered body"),
            result_text="Delivered body",
        ),
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
        on_agent_event=reject_part_batch,
        run_id="run-answer-candidate-failure",
        attempt_id="attempt-answer-candidate-failure",
    )

    assert result.error is not None
    assert result.answer_receipt is None
    assert any(event.event_type == "message.part.delta" for event in candidates)
    assert not any(event.event_type == "message.completed" for event in candidates)
    assert deltas == []





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
            _completed_text_steps(delivered, index=0),
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
    assert result.message == ""
    assert result.answer_receipt is None
    assert "".join(deltas) == delivered
    event_types = [candidate.event_type for candidate in candidates]
    assert event_types[0] == "message.started"
    assert event_types[-1] == "model.completed"
    assert "message.completed" not in event_types
    assert "".join(
        candidate.payload["delta"]
        for candidate in candidates
        if candidate.event_type == "message.part.delta"
    ) == delivered
    assert result.turn_diagnostics["counters"]["public_projection_omissions"] == 1









@pytest.mark.asyncio
async def test_sdk_first_projection_failure_survives_later_skill_hook(
    monkeypatch, tmp_path
):
    captured = {}
    skill_name = "qa-review"
    hook_input = {
        "tool_name": "Skill",
        "tool_use_id": "late-skill",
        "tool_input": {"skill": skill_name},
    }
    steps = []
    sdk = _scripted_sdk(captured, steps)
    steps.extend(
        [
            (
                "assistant_typed",
                {
                    "text": "",
                    "message_id": "typed-message",
                    "uuid": "typed-observation",
                    "content": [sdk.TextBlock(None)],
                },
            ),
            ("hook", ("PreToolUse", hook_input, "late-skill")),
        ]
    )
    original_receive = sdk.ClaudeSDKClient.receive_messages

    async def receive_messages(client):
        async for message in original_receive(client):
            yield message
        matcher = next(
            item
            for item in captured["hooks"]["PostToolUse"]
            if item.matcher == "Skill"
        )
        await matcher.hooks[0](hook_input, "late-skill", {})

    sdk.ClaudeSDKClient.receive_messages = receive_messages
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id=skill_name,
        skills=[skill_name],
        execution_policy="sandbox_brokered",
        tool_policy_subjects=[_skill_subject(skill_name)],
        on_capability_evidence=_acknowledge_capability_evidence,
        public_skill_metadata={
            skill_name: {"name": "QA", "version": "v1", "availability": "available"}
        },
    )

    assert result.error is None
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.turn_diagnostics["last_public_stage"] == "skills"
    assert result.runtime_diagnostics["failure_stage"] == "message"
    assert result.runtime_diagnostics["projection_failure"] == {
        "reason": "typed_text_block_invalid",
        "stage": "message",
        "location": "assistant_observation",
    }
















def _streaming_sdk(
    captured, events, *, on_before_result=None, result_text="terminal final"
):
    class AssistantMessage:
        message_id = "stream-message"
        uuid = "stream-message"
        parent_tool_use_id = None
        stop_reason = None

    class TextBlock:
        def __init__(self, text=None):
            self.text = text

    raw_event_counter = 0

    class StreamEvent:
        def __init__(self, event):
            nonlocal raw_event_counter
            raw_event_counter += 1
            event_uuid = event.get("__uuid") if isinstance(event, dict) else None
            self.event = (
                {key: value for key, value in event.items() if key != "__uuid"}
                if isinstance(event, dict)
                else event
            )
            self.uuid = event_uuid or f"stream-raw-event-{raw_event_counter}"
            self.parent_tool_use_id = None

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
        uuid = "stream-result"

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
    (
        "terminal_kind",
        "expected_error",
        "expected_reason",
        "expected_received_terminal",
        "expected_failure_source",
        "expected_public_text",
    ),
    [
        (
            "result_error",
            "claude_agent_sdk_upstream_error",
            None,
            False,
            "sdk_result_error",
            "",
        ),
        (
            "abnormal_terminal",
            "claude_agent_sdk_turn_limit_exceeded",
            "max_turns",
            False,
            "sdk_abnormal_terminal",
            "",
        ),
        ("success", None, "end_turn", True, None, "terminal final"),
    ],
)
async def test_sdk_terminal_snapshots_preserve_shared_fields_and_release_semantics(
    monkeypatch,
    tmp_path,
    terminal_kind,
    expected_error,
    expected_reason,
    expected_received_terminal,
    expected_failure_source,
    expected_public_text,
):
    captured = {}
    sdk = _scripted_sdk(captured, [("assistant", "terminal final")], result_text="PRIVATE_RESULT")
    original_result_init = sdk.ResultMessage.__init__
    def result_init(message, uuid):
        original_result_init(message, uuid)
        message.usage = {"input_tokens": 7, "output_tokens": 3}
    sdk.ResultMessage.__init__ = result_init
    if terminal_kind == "result_error":
        def failed_init(message, uuid):
            result_init(message, uuid)
            message.is_error, message.errors = True, ["upstream unavailable"]
        sdk.ResultMessage.__init__ = failed_init
    elif terminal_kind == "abnormal_terminal":
        sdk.ResultMessage.terminal_reason = "max_turns"
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    public_chunks = []

    result = await run_claude_agent_sdk(
        prompt="answer",
        cwd=tmp_path,
        skill_id=None,
        on_text=public_chunks.append,
    )

    assert result.used_sdk is True
    assert result.session_id == "sdk-session"
    assert result.usage == {"input_tokens": 7, "output_tokens": 3}
    assert result.used_skills == []
    assert result.used_skills_source == ""
    assert result.capability_evidence == []
    assert result.response_files == []
    assert result.response_file_descriptors == []
    assert result.provider_final_sequence is None
    assert result.error == expected_error
    assert result.terminal_reason == expected_reason
    assert result.received_structured_terminal is expected_received_terminal
    assert result.message == expected_public_text
    assert "".join(public_chunks) == expected_public_text
    assert result.turn_diagnostics["error_code"] == expected_error
    assert (
        result.runtime_diagnostics.get("failure_source")
        if result.runtime_diagnostics
        else None
    ) == expected_failure_source






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
async def test_sandbox_server_tool_use_does_not_imply_tool_stop_reason(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    public_text = "public after server tool"
    steps = []

    class ServerToolUseBlock:
        id = "server-tool-use-1"
        name = "web_search"
        input = {}

    sdk = _scripted_sdk(
        captured,
        steps,
        result_text=public_text,
        result_uuid="server-tool-result",
    )
    steps.append(
        (
            "assistant_blocks",
            ([ServerToolUseBlock(), sdk.TextBlock(public_text)], "end_turn"),
        )
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
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

    assert result.error is None
    assert result.message == public_text
    assert "".join(deltas) == public_text





@pytest.mark.asyncio
async def test_sandbox_stream_binds_parser_compressed_text_to_raw_text_source(
    monkeypatch, tmp_path
):
    captured, deltas = {}, []
    public_text = "public after compressed search"
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            [("assistant_parser_compressed", public_text)],
            result_text=public_text,
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

    assert result.error is None
    assert "".join(deltas) == public_text
    assert result.message == public_text

















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
@pytest.mark.parametrize(
    "current_message",
    [
        "continue",
        '格式化 JSON：{"path":"/tmp/example.json"}',
        "Review /home/example/report.txt",
        "Review output/report.csv",
        r"Review C:\examples\report.json",
    ],
)
async def test_sdk_provider_session_options_are_exclusive_and_eager(
    monkeypatch,
    tmp_path,
    stored_transcript,
    expected_option,
    current_message,
):
    from app.runs.infrastructure.capability_admission_postgres import normalize_run_input_for_enqueue

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

    admitted_input = normalize_run_input_for_enqueue(
        {"message": current_message, "prompt": "stale alias"}, redact_public=True
    )
    result = await run_claude_agent_sdk(
        prompt=str(admitted_input.get("message") or admitted_input.get("prompt") or ""),
        cwd=tmp_path,
        skill_id=None,
        session_id="stable-provider-id",
        session_store=Store(),
        provider_session_resume_required=expected_option == "resume",
    )

    assert result.error is None
    assert captured["sdk_user_messages"][0]["message"]["content"] == current_message
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
    assert result.turn_diagnostics["action"] == "start_new_conversation"
    assert result.turn_diagnostics["retryable"] is False





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

    class Client(sdk.ClaudeSDKClient):
        async def connect(self, prompt):
            captured["connected"] = True
            captured["business_query"] = True
            captured["query_session_id"] = (
                getattr(self.options, "session_id", None) or getattr(self.options, "resume", None)
            )
            await super().connect(prompt)

        async def disconnect(self):
            await super().disconnect()
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





@pytest.mark.asyncio
async def test_sdk_cli_stripped_result_completes_wrapped_stream_with_answer_receipt(
    monkeypatch, tmp_path
):
    captured, candidates, deltas = {}, [], []
    body = '<cc-memory filenames="preferences.md">Use UTF-8.</cc-memory>'

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _scripted_sdk(
            captured,
            _completed_text_steps(body),
            result_text="Use UTF-8.",
            result_uuid="wrapped-sdk-result",
        ),
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
        on_agent_event=lambda batch: candidates.extend(batch) or True,
        run_id="run-wrapped-result",
        attempt_id="attempt-wrapped-result",
    )

    message_events = [
        candidate
        for candidate in candidates
        if candidate.event_type.startswith("message.")
    ]
    delta_events = [
        candidate
        for candidate in message_events
        if candidate.event_type == "message.part.delta"
    ]
    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.message == ""
    assert "".join(deltas) == body
    assert message_events[0].event_type == "message.started"
    assert message_events[-1].event_type == "message.completed"
    assert "".join(event.payload["delta"] for event in delta_events) == body
    assert result.answer_receipt == {
        "schema_version": "ai-platform.assistant-answer-receipt.v2",
        "message_id": message_events[0].message_id,
        "delta_count": len(delta_events),
        "text_length": len(body),
        "last_delta_event_id": delta_events[-1].event_id,
    }





@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["PostToolUse", "PostToolUseFailure"])
async def test_protocol_close_includes_late_skill_outcome_without_sticky_failure(monkeypatch, tmp_path, terminal):
    captured = {}
    skill_name = "qa-review"
    hook_input = {"tool_name": "Skill", "tool_use_id": "late-skill", "tool_input": {"skill": skill_name}}
    sdk = _scripted_sdk(captured, [("hook", ("PreToolUse", hook_input, "late-skill"))])
    original_receive = sdk.ClaudeSDKClient.receive_messages

    async def receive_messages(client):
        async for message in original_receive(client):
            yield message
        matcher = next(item for item in captured["hooks"][terminal] if item.matcher == "Skill")
        await matcher.hooks[0](hook_input, "late-skill", {})

    sdk.ClaudeSDKClient.receive_messages = receive_messages
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(
        prompt="review", cwd=tmp_path, skill_id=skill_name, skills=[skill_name],
        execution_policy="sandbox_brokered", tool_policy_subjects=[_skill_subject(skill_name)],
        on_capability_evidence=_acknowledge_capability_evidence,
        public_skill_metadata={skill_name: {"name": "QA", "version": "v1", "availability": "available"}},
    )
    assert result.error is None
    expected_skills = [skill_name] if terminal == "PostToolUse" else []
    assert result.used_skills == expected_skills
    assert result.used_skills_source == ("executor_hook" if expected_skills else "")
    assert result.turn_diagnostics["used_skills"] == (
        [{"name": "QA", "version": "v1", "availability": "available"}] if expected_skills else []
    )
    assert result.capability_evidence[-1]["lifecycle_phase"] == ("completed" if expected_skills else "failed")





@pytest.mark.asyncio
@pytest.mark.parametrize('first_outcome', ['completed', 'failed', 'pending', 'unacknowledged'])
async def test_external_write_admission_waits_for_confirmed_prior_outcome(monkeypatch, tmp_path, first_outcome):
    captured = {}
    write = {**_subject(tool_name='write'), 'write_capable': True}
    read = _subject(tool_name='read')
    first = _mcp_hook_steps(write, call_id='write-a', terminal=first_outcome)
    if first_outcome == 'pending':
        first = first[:1]
    elif first_outcome == 'unacknowledged':
        first = _mcp_hook_steps(write, call_id='write-a')
    second = _mcp_hook_steps(write, call_id='write-b')
    steps = first + second[:1]
    if first_outcome == 'completed':
        steps += second[1:]
    if first_outcome == 'failed':
        steps += _mcp_hook_steps(read, call_id='read-reconcile')

    async def acknowledge(evidence):
        return not (first_outcome == 'unacknowledged' and evidence['lifecycle_phase'] == 'completed')

    monkeypatch.setitem(sys.modules, 'claude_agent_sdk', _scripted_sdk(captured, steps))
    monkeypatch.setattr('app.executors.claude_agent_sdk_runner.get_settings', _sandbox_brokered_settings)
    result = await run_claude_agent_sdk(
        prompt='perform task', cwd=tmp_path, skill_id='general-chat',
        execution_policy='sandbox_brokered', tool_policy_subjects=[write, read],
        on_capability_evidence=acknowledge,
    )
    admissions = [value['hookSpecificOutput']['permissionDecision']
                  for kind, value in captured['hook_results'] if kind == 'PreToolUse']
    assert admissions[:2] == ['allow', 'allow' if first_outcome in {'completed', 'pending'} else 'deny']
    if first_outcome == 'failed':
        assert admissions[2] == 'allow'
    if first_outcome in {'failed', 'pending'}:
        assert result.error == 'mcp_execution_outcome_unknown'
        assert result.turn_diagnostics['retryable'] is False
    elif first_outcome == 'unacknowledged':
        assert result.error == 'mcp_execution_succeeded_receipt_incomplete'
    else:
        assert result.error is None





@pytest.mark.asyncio
async def test_concurrent_external_writes_remain_allowed_before_any_failure(monkeypatch, tmp_path):
    captured = {}
    subject = {**_subject(), 'write_capable': True}
    async def acknowledge(_evidence):
        await asyncio.sleep(0)
        return True
    hooks = [_mcp_hook_steps(subject, call_id=call_id)[0][1] for call_id in ('write-a', 'write-b')]
    monkeypatch.setitem(sys.modules, 'claude_agent_sdk', _scripted_sdk(captured, [('concurrent_hooks', hooks)]))
    monkeypatch.setattr('app.executors.claude_agent_sdk_runner.get_settings', _sandbox_brokered_settings)
    await run_claude_agent_sdk(
        prompt='perform task', cwd=tmp_path, skill_id='general-chat',
        execution_policy='sandbox_brokered', tool_policy_subjects=[subject],
        on_capability_evidence=acknowledge,
    )
    admissions = [value['hookSpecificOutput']['permissionDecision']
                  for kind, value in captured['hook_results'] if kind == 'PreToolUse']
    assert admissions == ['allow', 'allow']





@pytest.mark.asyncio
async def test_external_write_rechecks_uncertainty_after_awaiting_admission_receipt(monkeypatch, tmp_path):
    captured = {}
    write = {**_subject(), 'write_capable': True}
    first = _mcp_hook_steps(write, call_id='write-a', terminal='failed')
    second = _mcp_hook_steps(write, call_id='write-b')
    async def acknowledge(_evidence):
        await asyncio.sleep(0)
        return True
    steps = [first[0], ('concurrent_hooks', [second[0][1], first[1][1]])]
    monkeypatch.setitem(sys.modules, 'claude_agent_sdk', _scripted_sdk(captured, steps))
    monkeypatch.setattr('app.executors.claude_agent_sdk_runner.get_settings', _sandbox_brokered_settings)
    result = await run_claude_agent_sdk(
        prompt='perform task', cwd=tmp_path, skill_id='general-chat',
        execution_policy='sandbox_brokered', tool_policy_subjects=[write],
        on_capability_evidence=acknowledge,
    )
    admissions = [value['hookSpecificOutput']['permissionDecision']
                  for kind, value in captured['hook_results'] if kind == 'PreToolUse']
    assert admissions == ['allow', 'deny']
    assert result.error == 'mcp_execution_outcome_unknown'





@pytest.mark.parametrize(
    ("raw_error", "reason", "expected", "terminal_class", "retryable"),
    [
        ("prompt is too long: private-count", "", "input_context_too_large", "input_limit_exceeded", False),
        ("request too large (max 32MB)", "", "input_context_too_large", "input_limit_exceeded", False),
        ("private-error", "prompt_too_long", "input_context_too_large", "input_limit_exceeded", False),
        ("private-error", "image_error", "input_image_invalid", "input_image_invalid", False),
        ("private-error", "server_error", "upstream_error", "upstream_error", True),
        ("API Error: 429", "", "upstream_error", "upstream_error", True),
        ("upstream unavailable", "", "upstream_error", "upstream_error", True),
        ("private internal exception", "", "execution_failed", "execution_failure", True),
        ("sdk_rejected", "unknown", "execution_failed", "execution_failure", True),
    ],
)
def test_sdk_error_classification_requires_source_evidence(raw_error, reason, expected, terminal_class, retryable):
    code = _canonical_sdk_error(raw_error, terminal_reason=reason)
    assert code == f"claude_agent_sdk_{expected}"
    classification = _diagnostic_terminal_class(code)
    assert classification[0] == terminal_class
    assert classification[3] is retryable
    assert "private" not in str(classification)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_error", "expected_code"),
    [
        ("prompt is too long: private diagnostic", "claude_agent_sdk_input_context_too_large"),
        ("image_error", "claude_agent_sdk_input_image_invalid"),
        ("server_error: private diagnostic", "claude_agent_sdk_upstream_error"),
        ("private diagnostic without attribution", "claude_agent_sdk_execution_failed"),
    ],
)
async def test_sdk_error_result_keeps_uncommitted_text_private_and_error_evidence_safe(monkeypatch, tmp_path, sdk_error, expected_code):
    captured, deltas = {}, []
    sdk = _scripted_sdk(captured, [("assistant", "Already accepted public text.")], result_error=sdk_error)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(prompt="answer", cwd=tmp_path, skill_id="general-chat", on_text=deltas.append)
    assert result.error == expected_code
    assert deltas == []
    assert result.message == ""
    assert "private diagnostic" not in "".join(deltas)
    assert "private diagnostic" not in str(result.turn_diagnostics)
    assert result.runtime_diagnostics["failure_source"] == "sdk_result_error"
    assert sdk_error in str(result.runtime_diagnostics)





@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assistant_error", "error_text", "expected_code"),
    [
        ("rate_limit", "private provider details", "claude_agent_sdk_upstream_error"),
        ("server_error", "private provider details", "claude_agent_sdk_upstream_error"),
        ("invalid_request", "prompt is too long: private provider details", "claude_agent_sdk_input_context_too_large"),
        ("unknown", "private provider details", "claude_agent_sdk_execution_failed"),
    ],
)
async def test_sdk_assistant_error_envelope_is_private_classification_evidence(monkeypatch, tmp_path, assistant_error, error_text, expected_code):
    captured, deltas = {}, []
    sdk = _scripted_sdk(
        captured,
        [("assistant", "Already accepted public text."), ("assistant_error", (assistant_error, error_text))],
        result_error="unclassified SDK terminal",
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(prompt="answer", cwd=tmp_path, skill_id="general-chat", on_text=deltas.append)
    assert result.error == expected_code
    assert deltas == []
    assert "private provider" not in "".join(deltas)
    assert "private provider" not in str(result.turn_diagnostics)
    assert error_text in str(result.runtime_diagnostics)
    assert assistant_error in str(result.runtime_diagnostics)





@pytest.mark.asyncio
async def test_sdk_can_recover_after_a_private_assistant_error_envelope(monkeypatch, tmp_path):
    captured, deltas = {}, []
    sdk = _scripted_sdk(
        captured,
        [("assistant_error", ("server_error", "private provider details")), ("assistant", "Recovered answer")],
        result_text="Recovered answer",
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(prompt="answer", cwd=tmp_path, skill_id="general-chat", on_text=deltas.append)
    assert result.error is None
    assert "Recovered answer" in "".join(deltas)
    assert "private provider" not in "".join(deltas)





@pytest.mark.asyncio
@pytest.mark.parametrize("exception_text", ["API Error: 500 private diagnostic", "server_error", "prompt is too long", "local execution failure"])
async def test_sdk_unknown_local_exceptions_do_not_claim_provider_or_input_failure(monkeypatch, tmp_path, exception_text):
    def fail_locally():
        raise ValueError(exception_text)

    sdk = _scripted_sdk({}, [("probe", fail_locally)])
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(prompt="answer", cwd=tmp_path, skill_id="general-chat")
    assert result.error == "claude_agent_sdk_execution_failed"
    assert result.turn_diagnostics["terminal_class"] == "execution_failure"
    assert result.runtime_diagnostics["failure_source"] == "sdk_exception"
    assert exception_text in str(result.runtime_diagnostics)

import json
import sys
import types

import pytest

from app.context_retrieval import ContextRetrieval, InMemoryContextRetrievalRepository
from app.executors.claude_agent_sdk_runner import build_skill_prompt, run_claude_agent_sdk
from app.executors.claude_agent_sdk_runner import ScopedContextRetrievalIdentity


def test_skill_prompt_lists_context_manifest_and_requires_retrieval_tools_without_private_payload():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue",
        file_names=["input.docx"],
        context_pack={
            "schema_version": "ai-platform.executor-context-pack.v1",
            "context_pack_version": "v-context-manifest",
            "context_pack_generated_at": "2026-07-02T01:02:03Z",
            "prompt_summary": "Context manifest: 2 message refs, 1 file ref.",
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "current_message": "continue",
                "files": [{"file_id": "file-a", "name": "input.docx", "storage_key": "secret"}],
                "private_payload": {"storage_key": "tenants/private/input.docx"},
            },
        },
    )

    assert "Context manifest: 2 message refs, 1 file ref." in prompt
    assert "Use context retrieval tools before assuming full prior message, file, artifact, or memory content is available" in prompt
    assert "storage_key" not in prompt
    assert "tenants/private" not in prompt
    assert "private_payload" not in prompt


@pytest.mark.asyncio
async def test_sdk_runner_uses_authorized_session_id_in_stream_instead_of_global_default(monkeypatch, tmp_path):
    captured_messages = []
    captured_options = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session-returned"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured_options.update(kwargs)

    async def query(prompt, options):
        async for item in prompt:
            captured_messages.append(item)
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
            "claude_agent_permission_mode": "dontAsk",
            "claude_agent_allowed_tools": "Read,Glob,LS",
            "claude_agent_disallowed_tools": "",
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        session_id="sdk-session-authorized",
    )

    assert result.session_id == "sdk-session-returned"
    assert captured_options["session_id"] == "sdk-session-authorized"
    assert captured_options.get("resume") is None
    assert captured_messages[0]["session_id"] == "sdk-session-authorized"
    assert captured_messages[0]["session_id"] != "default"


@pytest.mark.asyncio
async def test_sdk_runner_wires_scoped_context_retrieval_mcp_server(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session-returned"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    class SdkTool:
        def __init__(self, name, handler):
            self.name = name
            self.handler = handler

    def tool(name, description, input_schema, annotations=None):
        def decorator(handler):
            return SdkTool(name, handler)

        return decorator

    def create_sdk_mcp_server(name, version="1.0.0", tools=None):
        return {"name": name, "version": version, "tools": tools or []}

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
            "claude_agent_permission_mode": "dontAsk",
            "claude_agent_allowed_tools": "Read,Glob,LS",
            "claude_agent_disallowed_tools": "",
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        create_sdk_mcp_server=create_sdk_mcp_server,
        query=query,
        tool=tool,
    )
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            messages=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "message_id": "msg-a",
                    "role": "user",
                    "content": "scoped private message",
                }
            ],
            files=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-a",
                    "original_name": "source.txt",
                    "content": "workspace staged content",
                    "storage_key": "tenants/tenant-a/private/source.txt",
                }
            ],
        )
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        context_retrieval=retrieval,
        context_retrieval_identity=ScopedContextRetrievalIdentity(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            agent_id="general-agent",
        ),
    )

    assert result.message == "ok"
    server = captured["mcp_servers"]["ai-platform-context"]
    assert server["name"] == "ai-platform-context"
    assert [tool.name for tool in server["tools"]] == [
        "read_session_messages",
        "read_context_file",
        "read_run_artifact",
        "stage_context_file_to_workspace",
        "search_memory",
    ]
    assert "read_session_messages" in captured["allowed_tools"]
    assert "read_context_file" in captured["allowed_tools"]
    assert "stage_context_file_to_workspace" in captured["allowed_tools"]
    message_tool = server["tools"][0]
    tool_result = await message_tool.handler({"tenant_id": "tenant-b", "limit": 5, "offset": 0, "max_tokens": 20})
    assert "scoped private message" in tool_result["content"][0]["text"]
    assert "tenant-b" not in tool_result["content"][0]["text"]
    stage_tool = server["tools"][3]
    stage_result = await stage_tool.handler({"file_id": "file-a"})
    assert "context/file-a/source.txt" in stage_result["content"][0]["text"]
    assert "workspace staged content" not in stage_result["content"][0]["text"]
    assert "storage_key" not in stage_result["content"][0]["text"]
    assert (tmp_path / "context" / "file-a" / "source.txt").read_text(encoding="utf-8") == "workspace staged content"
    too_large_result = await stage_tool.handler({"file_id": "file-a", "max_bytes": 8})
    assert too_large_result["is_error"] is True
    assert "context_file_too_large" in too_large_result["content"][0]["text"]
    assert "workspace staged content" not in too_large_result["content"][0]["text"]
    too_large_payload = json.loads(too_large_result["content"][0]["text"])
    assert too_large_payload["audit"] == {
        "action": "context_retrieval.stage_context_file_to_workspace",
        "result": "denied",
        "reason": "context_file_too_large",
    }
    assert too_large_payload["redaction"] == {"object_locator_refs_removed": True}

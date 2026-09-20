import asyncio
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration
from app.mcp.infrastructure import client as mcp_client
from app.mcp.infrastructure.catalog import _ValidatedDiscoveryTarget
from tests.support.mcp_protocol_peers import RAW_TOOL, SCHEMA, SDK_TOOL, local_mcp_peers


def subject(server="gateway", tool=RAW_TOOL, url="https://mcp.example/mcp"):
    return {
        "identity": f"mcp__{server}__{tool}", "mcp_server": server, "mcp_tool": tool,
        "registered": True, "declared": True, "active": True, "distributed": True,
        "identity_authorized": True, "object_authorized": True, "parameters_authorized": True,
        "risk_level": "low", "write_capable": False, "parameter_delegation": "external_mcp",
        "public_tool_label": "Sequence lookup",
        "mcp_server_config": {"type": "http", "url": url,
            "headers": {"JWT-Authorization": "Bearer synthetic.jwt", "X-Static-Key": "synthetic"}},
    }


def allow_local_peer(monkeypatch, url):
    async def target(endpoint):
        assert endpoint in {url + "/mcp", url + "/sse"}
        return _ValidatedDiscoveryTarget(endpoint, endpoint, urlsplit(url).netloc, "127.0.0.1")
    monkeypatch.setattr(mcp_client, "_validated_discovery_target", target)
    from app.mcp.infrastructure import catalog
    monkeypatch.setattr(catalog, "_validated_discovery_target", target)


@pytest.mark.parametrize("pairs", [
    [("gateway", "a.b"), ("gateway", "a_b")],
    [("gateway", "a:b"), ("gateway", "a_b")],
    [("server.name", "one"), ("server_name", "two")],
])
def test_ambiguous_sdk_names_fail_before_connections(pairs):
    subjects = [subject(server, tool) for server, tool in pairs]
    with pytest.raises(ValueError, match="mcp_sdk_name_collision"):
        ClaudeMcpRegistration(
            {item["identity"]: item for item in subjects},
            {item["mcp_server"]: item["mcp_server_config"] for item in subjects},
            session_factory=None, list_tools=None,
        )


def test_ambiguous_canonical_names_fail_before_registration():
    from app.executors.claude.capability_policy import _canonical_tool_policy_subjects
    with pytest.raises(ValueError, match="mcp_identity_collision"):
        _canonical_tool_policy_subjects([subject("a__b", "c"), subject("a", "b__c")])


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_real_mcp_transport_preserves_auth_names_results_and_cleanup(monkeypatch, transport):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        config = subject(url=peer.url + ("/mcp" if transport == "http" else "/sse"))["mcp_server_config"]
        config["type"] = transport
        async with asyncio.timeout(10):
            async with mcp_client.open_mcp_session(config, timeout_seconds=3) as session:
                tools = await mcp_client.list_mcp_tools(session)
                assert tools[0].inputSchema == SCHEMA
                result = await session.call_tool(RAW_TOOL, {"query": "synthetic"})
                assert result.structuredContent == {"sequence": 7}
                assert result.isError is False
                peer.is_error = True
                failed = await session.call_tool(RAW_TOOL, {"query": "synthetic"})
                assert failed.isError is True
        assert [call["name"] for call in peer.calls] == [RAW_TOOL, RAW_TOOL]
        assert all({key.lower(): value for key, value in headers.items()}.get("jwt-authorization") == "Bearer synthetic.jwt" for _, headers in peer.requests)
        assert all({key.lower(): value for key, value in headers.items()}.get("x-static-key") == "synthetic" for _, headers in peer.requests)
        if transport == "sse":
            assert await asyncio.to_thread(peer.sse_closed.wait, 2)


@pytest.mark.asyncio
async def test_registration_filters_catalog_preserves_results_and_closes_on_cancel():
    item = subject()
    closed = []
    calls = []
    ready = asyncio.Event()

    @asynccontextmanager
    async def session_factory(_config):
        async def call_tool(name, arguments):
            calls.append((name, arguments))
            return CallToolResult(content=[TextContent(type="text", text="result")], structuredContent={"n": 1}, isError=True)
        try:
            yield SimpleNamespace(call_tool=call_tool)
        finally:
            closed.append(True)

    async def list_tools(_session):
        return [Tool(name=RAW_TOOL, inputSchema=SCHEMA), Tool(name="unselected", inputSchema={"type": "object"})]

    config = {"gateway": item["mcp_server_config"]}
    registration = ClaudeMcpRegistration({item["identity"]: item}, config, session_factory=session_factory, list_tools=list_tools)
    assert registration.sdk_names[item["identity"]] == SDK_TOOL
    assert registration.canonical_identity(SDK_TOOL) == item["identity"]

    async def consume():
        async with registration.activate(SimpleNamespace()):
            from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
            server = config["gateway"]["instance"]
            listed = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
            assert [tool.name for tool in listed.root.tools] == [RAW_TOOL]
            for name in (RAW_TOOL, "unselected"):
                result = await server.request_handlers[CallToolRequest](CallToolRequest(
                    method="tools/call", params=CallToolRequestParams(name=name, arguments={"query": "synthetic"}),
                ))
                assert result.root.isError is True
                if name == RAW_TOOL:
                    assert result.root.structuredContent == {"n": 1}
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=3)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed == [True]
    assert calls == [(RAW_TOOL, {"query": "synthetic"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_error", [False, True])
@pytest.mark.parametrize("server_name", ["gateway", "server.name"])
async def test_installed_claude_cli_selected_mcp_end_to_end(monkeypatch, tmp_path, remote_error, server_name):
    from app.executors import claude_agent_sdk_runner as runner


    with local_mcp_peers() as peer:
        peer.is_error = remote_error
        peer.sdk_tool = f"mcp__{server_name.replace('.', '_')}__{RAW_TOOL.replace('.', '_')}"
        allow_local_peer(monkeypatch, peer.url)
        (tmp_path / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"unconfigured": subject(server=server_name, url=peer.url + "/mcp")["mcp_server_config"]},
        }), encoding="utf-8")
        settings = SimpleNamespace(
            claude_agent_sdk_enabled=True, claude_agent_sdk_max_turns=4,
            claude_agent_sdk_timeout_seconds=40, claude_agent_sdk_skills="",
            claude_agent_permission_mode="dontAsk", claude_agent_model="claude-sonnet-4-6",
            anthropic_model="", anthropic_base_url=peer.url, anthropic_auth_token="synthetic-model-token",
            openai_api_key="",
        )
        monkeypatch.setattr(runner, "get_settings", lambda: settings)
        real_env = runner.build_sdk_env

        def synthetic_env(*, cwd, model_max_output_tokens=None):
            env = real_env(
                cwd=cwd,
                model_max_output_tokens=model_max_output_tokens,
            )
            env.update({"ANTHROPIC_BASE_URL": peer.url, "ANTHROPIC_AUTH_TOKEN": "synthetic-model-token",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "NO_PROXY": "127.0.0.1,localhost"})
            for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA"):
                if name in os.environ:
                    env[name] = os.environ[name]
            return env

        monkeypatch.setattr(runner, "build_sdk_env", synthetic_env)
        receipts = []
        events = []

        async def receipt(value):
            receipts.append(value)
            return True

        async def event(values):
            events.extend(values)
            return True

        result = await runner.run_claude_agent_sdk(
            prompt="Use the selected lookup once for synthetic, then report its result.",
            cwd=tmp_path, skill_id=None, skills=[], execution_policy="sandbox_brokered",
            tool_policy_subjects=[subject(server=server_name, url=peer.url + "/mcp")],
            on_capability_evidence=receipt, on_agent_event=event,
            run_id="run_mcp_check", attempt_id="attempt_mcp_check",
        )
        expected_error = "required_tool_completion_evidence_mismatch" if remote_error else None
        assert result.error == expected_error, result.runtime_diagnostics
        assert peer.models
        assert sum(method == "initialize" for method, _ in peer.requests) == 1
        assert all([tool["name"] for tool in request.get("tools", [])] == [peer.sdk_tool] for request in peer.models)
        assert peer.models[0]["tools"][0]["input_schema"] == SCHEMA
        tool_result_texts = [
            block.get("content", "")
            for request in peer.models
            for message in request.get("messages", [])
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        if not remote_error:
            assert any(
                part.get("text", "").startswith("[structuredContent]")
                and '"sequence":7' in part.get("text", "")
                for content in tool_result_texts
                for part in content
                if isinstance(part, dict)
            )
        assert peer.calls == [{"name": RAW_TOOL, "arguments": {"query": "synthetic"}}], (
            result.turn_diagnostics, result.runtime_path_diagnostics,
            [block for request in peer.models for message in request.get("messages", [])
             for block in message.get("content", []) if isinstance(block, dict) and block.get("type") == "tool_result"],
        )
        assert [(value["lifecycle_phase"], value["lifecycle_status"]) for value in receipts] == [
            ("invocation_requested", "invoking"), ("failed", "failed") if remote_error else ("completed", "succeeded"),
        ]
        assert all(value["canonical_identity"] == subject(server=server_name)["identity"] for value in receipts)

        assert {value.event_type for value in events} >= {"policy.allowed", "tool.started", "tool.failed" if remote_error else "tool.completed"}
        assert peer.sdk_tool not in str([value.payload for value in events])

        assert "synthetic.jwt" not in str([value.payload for value in events])


@pytest.mark.asyncio
async def test_registration_rejects_missing_selected_tool_before_activation():
    item = subject()
    closed = []
    activated = []

    @asynccontextmanager
    async def session_factory(_config):
        try:
            yield SimpleNamespace()
        finally:
            closed.append(True)

    async def list_tools(_session):
        return []

    registration = ClaudeMcpRegistration(
        {item["identity"]: item}, {"gateway": item["mcp_server_config"]},
        session_factory=session_factory, list_tools=list_tools,
    )
    with pytest.raises(ValueError, match="mcp_selected_tool_unavailable"):
        async with registration.activate(SimpleNamespace()):
            activated.append(True)
    assert activated == []
    assert closed == [True]

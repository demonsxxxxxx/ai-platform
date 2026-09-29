import asyncio
import json
import os
import re
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
def test_sdk_name_collisions_get_stable_unique_aliases(pairs):
    subjects = [subject(server, tool) for server, tool in pairs]
    keyed = {item["identity"]: item for item in subjects}
    configs = {item["mcp_server"]: item["mcp_server_config"] for item in subjects}
    registration = ClaudeMcpRegistration(
        keyed, configs, session_factory=None, list_tools=None
    )
    reversed_registration = ClaudeMcpRegistration(
        dict(reversed(list(keyed.items()))), configs, session_factory=None, list_tools=None
    )

    aliases = list(registration.sdk_names.values())
    assert len(set(alias.casefold() for alias in aliases)) == len(pairs)
    assert all(re.fullmatch(r"mcp__[A-Za-z0-9_-]{1,57}", alias) for alias in aliases)
    assert registration.sdk_names == reversed_registration.sdk_names
    assert all(
        registration.canonical_identity(alias) == identity
        for identity, alias in registration.sdk_names.items()
    )
    for identity, (server, remote_tool) in registration._identity_components.items():
        assert registration.sdk_names[identity] == (
            f"mcp__{registration.server_aliases[server]}__"
            f"{registration._tool_aliases[(server, remote_tool)]}"
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
        options = SimpleNamespace()
        async with registration.activate(options):
            from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
            server = options.mcp_servers[registration.server_aliases["gateway"]]["instance"]
            listed = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
            assert [tool.name for tool in listed.root.tools] == [registration._tool_aliases[("gateway", RAW_TOOL)]]
            for name in (registration._tool_aliases[("gateway", RAW_TOOL)], "unselected"):
                result = await server.request_handlers[CallToolRequest](CallToolRequest(
                    method="tools/call", params=CallToolRequestParams(name=name, arguments={"query": "synthetic"}),
                ))
                assert result.root.isError is True
                if name != "unselected":
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
@pytest.mark.parametrize("discovery_phase", ["connect", "list"])
async def test_cancel_during_discovery_closes_owner_without_waiting_for_timeout(discovery_phase):
    entered = asyncio.Event()
    closed = asyncio.Event()
    owner = None

    @asynccontextmanager
    async def session_factory(_config):
        nonlocal owner
        owner = asyncio.current_task()
        try:
            if discovery_phase == "connect":
                entered.set()
                await asyncio.Event().wait()
            yield object()
        finally:
            assert asyncio.current_task() is owner
            closed.set()

    async def list_tools(_session):
        entered.set()
        await asyncio.Event().wait()

    item = subject()
    registration = ClaudeMcpRegistration(
        {item["identity"]: item}, {"gateway": item["mcp_server_config"]},
        session_factory=session_factory, list_tools=list_tools,
    )

    async def activate():
        async with registration.activate(SimpleNamespace()):
            pytest.fail("cancelled discovery must not activate")

    task = asyncio.create_task(activate())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    try:
        # No second cancellation: wait_for would mask slow cleanup by cancelling
        # the owner itself. The normal discovery timeout is ten seconds.
        done, _pending = await asyncio.wait({task}, timeout=1)
        assert done == {task}
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_error", [False, True])
@pytest.mark.parametrize("server_name", ["gateway", "server.name"])
async def test_installed_claude_cli_selected_mcp_end_to_end(monkeypatch, tmp_path, remote_error, server_name):
    from app.executors import claude_agent_sdk_runner as runner


    with local_mcp_peers() as peer:
        peer.is_error = remote_error
        selected_subject = subject(server=server_name)
        registration = ClaudeMcpRegistration(
            {selected_subject["identity"]: selected_subject}, {}, session_factory=None, list_tools=None
        )
        peer.sdk_tool = registration.sdk_names[selected_subject["identity"]]
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
        assert result.error is None, result.runtime_diagnostics
        assert peer.models
        assert sum(method == "initialize" for method, _ in peer.requests) == 1
        assert all(
            {tool["name"] for tool in request.get("tools", [])}
            == {peer.sdk_tool, "mcp__ai-platform-response__attach_file"}
            for request in peer.models
        )
        selected_tool = next(
            tool for tool in peer.models[0]["tools"] if tool["name"] == peer.sdk_tool
        )
        assert selected_tool["input_schema"] == SCHEMA
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
            result.turn_diagnostics, result.runtime_diagnostics,
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
async def test_registration_keeps_optional_server_absent_when_no_selected_tool_is_available():
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
    options = SimpleNamespace()
    async with registration.activate(options):
        activated.append(True)
        assert "gateway" not in options.mcp_servers
    assert activated == [True]
    assert closed == [True]
    assert registration.unavailable == {"gateway": "selected_tool_unavailable"}


@pytest.mark.asyncio
async def test_registration_keeps_available_selected_tools_when_one_is_missing():
    available = subject(tool="lookup")
    missing = subject(tool="missing")
    closed = []

    @asynccontextmanager
    async def session_factory(_config):
        try:
            yield SimpleNamespace(
                call_tool=lambda name, arguments: asyncio.sleep(0, result=CallToolResult(
                    content=[TextContent(type="text", text=f"{name}:{arguments['query']}")]
                ))
            )
        finally:
            closed.append(True)

    async def list_tools(_session):
        return [Tool(name="lookup", inputSchema={"type": "object"})]

    registration = ClaudeMcpRegistration(
        {available["identity"]: available, missing["identity"]: missing},
        {"gateway": available["mcp_server_config"]},
        session_factory=session_factory,
        list_tools=list_tools,
    )
    options = SimpleNamespace()
    async with registration.activate(options):
        assert registration.unavailable == {"gateway": "selected_tool_unavailable"}
        assert registration.server_aliases["gateway"] in options.mcp_servers
        server = options.mcp_servers[registration.server_aliases["gateway"]]["instance"]
        from mcp.types import ListToolsRequest

        listed = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        assert [tool.name for tool in listed.root.tools] == [
            registration._tool_aliases[("gateway", "lookup")]
        ]
    assert closed == [True]


@pytest.mark.asyncio
async def test_registration_keeps_healthy_server_when_another_is_offline_and_cleans_up_in_owner():
    healthy = subject(server="healthy", tool="lookup")
    offline = subject(server="offline", tool="lookup")
    configs = {"healthy": {"server": "healthy"}, "offline": {"server": "offline"}}
    events = []
    call_names = []
    task_owners = {}
    discovered = set()
    both_discovered = asyncio.Event()

    @asynccontextmanager
    async def session_factory(config):
        server = config["server"]
        task = asyncio.current_task()
        task_owners[server] = [task, task]
        events.append((server, "open"))
        try:
            async def call_tool(name, arguments):
                call_names.append((server, name, arguments))
                return CallToolResult(content=[TextContent(type="text", text="ok")])

            yield SimpleNamespace(server=server, call_tool=call_tool)
        finally:
            task_owners[server][1] = asyncio.current_task()
            events.append((server, "close"))

    async def list_tools(session):
        discovered.add(session.server)
        if discovered == {"healthy", "offline"}:
            both_discovered.set()
        await asyncio.wait_for(both_discovered.wait(), timeout=0.5)
        if session.server == "offline":
            raise RuntimeError("offline")
        return [Tool(name="lookup", inputSchema={"type": "object"})]

    registration = ClaudeMcpRegistration(
        {healthy["identity"]: healthy, offline["identity"]: offline},
        configs,
        session_factory=session_factory,
        list_tools=list_tools,
    )
    options = SimpleNamespace()
    async with registration.activate(options):
        assert registration.unavailable == {"offline": "connection_failed"}
        assert "offline" not in options.mcp_servers
        assert options.mcp_servers[registration.server_aliases["healthy"]]["type"] == "sdk"
        assert options.mcp_servers[registration.server_aliases["healthy"]]["type"] == "sdk"
        assert ("offline", "close") in events
        assert ("healthy", "close") not in events

        from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

        server = options.mcp_servers[registration.server_aliases["healthy"]]["instance"]
        listed = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        alias = registration._tool_aliases[("healthy", "lookup")]
        assert [tool.name for tool in listed.root.tools] == [alias]
        result = await server.request_handlers[CallToolRequest](CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=alias, arguments={"query": "synthetic"}),
        ))
        assert result.root.isError is False

    assert events.index(("offline", "close")) < events.index(("healthy", "close"))
    assert task_owners["healthy"][0] is task_owners["healthy"][1]
    assert task_owners["offline"][0] is task_owners["offline"][1]
    assert call_names == [("healthy", "lookup", {"query": "synthetic"})]


@pytest.mark.asyncio
async def test_registration_timeout_does_not_limit_healthy_session_lifetime(monkeypatch):
    item = subject(tool="lookup")
    closed = []

    @asynccontextmanager
    async def session_factory(_config):
        try:
            yield SimpleNamespace(call_tool=lambda *_args: None)
        finally:
            closed.append(True)

    async def list_tools(_session):
        return [Tool(name="lookup", inputSchema={"type": "object"})]

    from app.execution.infrastructure import claude_mcp

    monkeypatch.setattr(claude_mcp, "_MCP_DISCOVERY_TIMEOUT_SECONDS", 0.05)
    registration = ClaudeMcpRegistration(
        {item["identity"]: item}, {"gateway": item["mcp_server_config"]},
        session_factory=session_factory, list_tools=list_tools,
    )
    options = SimpleNamespace()
    async with registration.activate(options):
        await asyncio.sleep(0.1)
        assert registration.server_aliases["gateway"] in options.mcp_servers
        assert closed == []
    assert closed == [True]

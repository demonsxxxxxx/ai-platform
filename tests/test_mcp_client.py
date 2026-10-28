import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
import gzip
from dataclasses import dataclass
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from mcp_types import ListToolsResult, Tool

from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.domain.errors import McpToolDiscoveryError
from app.mcp.infrastructure import client, catalog
from app.mcp.infrastructure import runtime as mcp_runtime
from app.mcp.infrastructure.runtime import McpRuntimeContextError
from app.runs.api import (
    WorkerDispatchBindingService, WorkerDispatchContextPorts, WorkerDispatchExecutionPorts,
)
from tests.support.mcp_protocol_peers import RAW_TOOL, local_mcp_peers
from tests.test_claude_mcp_registration import allow_local_peer, subject


def test_sse_event_limit_clones_the_mcp_23_global_helper_per_session():
    sdk_generator = client.sse_client.__wrapped__
    sdk_globals = sdk_generator.__globals__
    original_helper = sdk_globals["sse_within_origin"]

    discovery = client._sse_client_with_event_limit(1024 * 1024).__wrapped__
    invocation = client._sse_client_with_event_limit(16 * 1024 * 1024).__wrapped__

    assert discovery.__code__ is invocation.__code__ is sdk_generator.__code__
    assert discovery.__defaults__ is invocation.__defaults__ is sdk_generator.__defaults__
    assert discovery.__closure__ is invocation.__closure__ is sdk_generator.__closure__
    assert discovery.__kwdefaults__ is invocation.__kwdefaults__ is sdk_generator.__kwdefaults__
    assert discovery.__globals__ is not sdk_globals
    assert invocation.__globals__ is not sdk_globals
    assert discovery.__globals__["sse_within_origin"].func is original_helper
    assert invocation.__globals__["sse_within_origin"].func is original_helper
    assert discovery.__globals__["sse_within_origin"].keywords == {"max_event_size": 1024 * 1024}
    assert invocation.__globals__["sse_within_origin"].keywords == {"max_event_size": 16 * 1024 * 1024}
    assert sdk_globals["sse_within_origin"] is original_helper


@pytest.mark.asyncio
async def test_client_auto_negotiates_stateless_2026_protocol_and_stamps_tool_headers(monkeypatch):
    with local_mcp_peers(protocol="modern") as peer:
        output_schema = {
            "type": "object",
            "properties": {"sequence": {"type": "integer"}},
            "required": ["sequence"],
            "additionalProperties": False,
        }
        peer.tools[0]["outputSchema"] = output_schema
        allow_local_peer(monkeypatch, peer.url)
        config = subject(url=peer.url + "/mcp")["mcp_server_config"]
        async with client.open_mcp_session(config, timeout_seconds=3) as session:
            assert session.protocol_version == "2026-07-28"
            tools = await client.list_mcp_tools(session)
            result = await session.call_tool(RAW_TOOL, {"query": "synthetic"})

        assert [tool.name for tool in tools] == [RAW_TOOL, "unselected_sibling"]
        assert tools[0].output_schema == output_schema
        assert tools[0].model_dump(by_alias=True, exclude_none=True)["outputSchema"] == output_schema
        assert result.structured_content == {"sequence": 7}
        assert peer.deleted == 0
        assert [method for method, _headers in peer.requests] == [
            "server/discover", "tools/list", "tools/call",
        ]
        headers_by_method = {
            method: {key.casefold(): value for key, value in headers.items()}
            for method, headers in peer.requests
        }
        assert all(
            headers["mcp-protocol-version"] == "2026-07-28"
            for headers in headers_by_method.values()
        )
        assert headers_by_method["tools/call"]["mcp-name"] == RAW_TOOL
        assert all("mcp-session-id" not in headers for headers in headers_by_method.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["header_mismatch", "input_required"])
async def test_v2_tool_call_does_not_replay_one_authorized_dispatch(monkeypatch, response):
    from mcp import MCPError

    with local_mcp_peers(protocol="modern") as peer:
        allow_local_peer(monkeypatch, peer.url)
        peer.call_error = {"code": -32020, "message": "synthetic header mismatch"} if response == "header_mismatch" else None
        peer.input_required = response == "input_required"
        config = subject(url=peer.url + "/mcp")["mcp_server_config"]
        async with client.open_mcp_session(config, timeout_seconds=3) as session:
            await client.list_mcp_tools(session)
            with pytest.raises(MCPError if response == "header_mismatch" else RuntimeError):
                await session.call_tool(RAW_TOOL, {"query": "synthetic"})
        assert len(peer.calls) == 1
        assert [method for method, _headers in peer.requests] == [
            "server/discover", "tools/list", "tools/call",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable_http", "sse", "sandbox"])
async def test_catalog_dispatches_the_configured_transport(monkeypatch, transport):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)

        async def target(*_args):
            return SimpleNamespace(
                endpoint=peer.url + ("/sse" if transport == "sse" else "/mcp"),
                static_headers={"X-Static-Key": "synthetic"}, transport=transport,
            )

        async def current_credential(principal):
            assert principal.tenant_id == "tenant" and principal.user_id == "user"
            return "synthetic.jwt"

        monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=current_credential))
        service = LiveMcpCatalogService(
            target_resolver=target, discovery=client.McpToolDiscoveryAdapter(),
            sse_discovery=client.McpToolDiscoveryAdapter(transport="sse"),
        )
        result = await service.list_server_tools(tenant_id="tenant", user_id="user", server_id="gateway", jwt="synthetic.jwt")
        if transport == "sandbox":
            assert result.unavailable_reason == "unsupported_transport"
            assert peer.requests == []
        else:
            assert result.unavailable_reason is None
            assert result.tools[0].tool_id == "gateway::" + RAW_TOOL
            assert (peer.requests[0][0] == "GET") == (transport == "sse")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_cancellation_closes_real_mcp_sessions(monkeypatch, transport):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        config = subject(url=peer.url + ("/sse" if transport == "sse" else "/mcp"))["mcp_server_config"]
        config["type"] = transport
        ready = asyncio.Event()

        async def operation():
            async with client.open_mcp_session(config, timeout_seconds=3) as session:
                await client.list_mcp_tools(session)
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(ready.wait(), timeout=3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if transport == "sse":
            assert await asyncio.to_thread(peer.sse_closed.wait, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [" tool", "tool ", "tool\n"])
async def test_discovery_rejects_names_instead_of_silently_renaming(bad_name):
    async def list_tools(**_kwargs):
        return ListToolsResult(tools=[Tool(name="tool", inputSchema={}), Tool(name=bad_name, inputSchema={})])
    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await client.list_mcp_tools(SimpleNamespace(list_tools=list_tools))
    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        catalog._canonical_live_definition({"name": bad_name})


@pytest.mark.asyncio
async def test_long_unselected_tool_description_is_truncated_without_rejecting_catalog():
    long_description = "x" * (catalog.MCP_TOOL_DESCRIPTION_MAX_LENGTH + 500)

    async def list_tools(**_kwargs):
        return ListToolsResult(tools=[
            Tool(name="selected", description="selected description", inputSchema={}),
            Tool(name="unselected", description=long_description, inputSchema={}),
        ])

    tools = await client.list_mcp_tools(SimpleNamespace(list_tools=list_tools))
    assert [tool.name for tool in tools] == ["selected", "unselected"]
    assert tools[1].description == long_description[:catalog.MCP_TOOL_DESCRIPTION_MAX_LENGTH]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["compressed", "redirect", "oversize"])
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_transport_rejects_unsafe_responses_before_followup(monkeypatch, case, transport):
    requests = []

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: " + b"x" * 100 + b"\n\n"

    async def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "mcp.example"
        assert request.headers["Accept-Encoding"] == "identity"
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example/secret"})
        if case == "compressed":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"x" * 100000))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ResponseStream())

    async def target(_endpoint):
        return catalog._ValidatedDiscoveryTarget("https://mcp.example/mcp", "https://8.8.8.8/mcp", "mcp.example", "mcp.example")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(client, "_validated_discovery_target", target)
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = subject()["mcp_server_config"]
    config["type"] = transport
    with pytest.raises(McpToolDiscoveryError):
        async with asyncio.timeout(3):
            async with client.open_mcp_session(config, timeout_seconds=1, max_response_bytes=64):
                pytest.fail("unsafe response admitted")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_limit,expected_failure",
    [(2 * 1024 * 1024, False), (1024 * 1024, True), (16 * 1024 * 1024, True)],
)
async def test_http_event_stream_response_uses_configured_event_budget_without_replay(
    monkeypatch, response_limit, expected_failure,
):
    payload_size = response_limit + 64 * 1024 if expected_failure else 1024 * 1024 + 64 * 1024
    text = "x" * payload_size
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        peer.tool_response_content_type = "text/event-stream"
        peer.tool_result = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"length": len(text)},
            "isError": False,
        }
        config = subject(url=peer.url + "/mcp")["mcp_server_config"]
        config["max_response_bytes"] = response_limit
        # The full 16 MiB event exercises real streaming and decoder work.
        async with asyncio.timeout(30):
            if expected_failure:
                with pytest.raises(McpToolDiscoveryError, match="response_too_large"):
                    async with client.open_mcp_session(config, timeout_seconds=20) as session:
                        await client.list_mcp_tools(session)
                        await session.call_tool(RAW_TOOL, {"query": "synthetic"})
            else:
                async with client.open_mcp_session(config, timeout_seconds=20) as session:
                    await client.list_mcp_tools(session)
                    result = await session.call_tool(RAW_TOOL, {"query": "synthetic"})
                assert result.content[0].text == text
                assert result.structured_content == {"length": len(text)}
        assert len(peer.calls) == 1
        assert sum(method == "tools/call" for method, _headers in peer.requests) == 1


@pytest.mark.asyncio
async def test_runtime_drops_unsupported_server_before_credentials(monkeypatch):
    async def runtime_target(*_args, **_kwargs):
        return {"transport": "sandbox", "credential_envelope": "sealed"}

    monkeypatch.setattr(mcp_runtime.mcp_postgres, "get_mcp_server_runtime_target", runtime_target)
    monkeypatch.setattr(
        mcp_runtime,
        "get_mcp_principal_jwt_store",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must not be loaded")),
    )
    @dataclass
    class Payload:
        input: dict

    payload = Payload(input={
        "_runtime_tool_policy_subjects": [
            {"identity": "mcp__gateway__tool", "mcp_server": "gateway"},
            {"identity": "mcp__ai-platform-context__lookup", "mcp_server": "ai-platform-context"},
        ]
    })
    principal = SimpleNamespace(tenant_id="tenant", user_id="user")
    result = await mcp_runtime.attach_mcp_server_configs(
        object(), principal=principal, run_payload=payload
    )
    assert result.input["_runtime_tool_policy_subjects"] == [
        {"identity": "mcp__ai-platform-context__lookup", "mcp_server": "ai-platform-context"}
    ]


@pytest.mark.asyncio
async def test_runtime_missing_target_or_jwt_only_removes_optional_external_subjects(monkeypatch):
    @dataclass
    class Payload:
        input: dict

    builtin = {"identity": "mcp__ai-platform-context__lookup", "mcp_server": "ai-platform-context"}
    external = {"identity": "mcp__gateway__tool", "mcp_server": "gateway"}
    principal = SimpleNamespace(tenant_id="tenant", user_id="user")

    async def missing_target(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_runtime.mcp_postgres, "get_mcp_server_runtime_target", missing_target)
    monkeypatch.setattr(
        mcp_runtime,
        "get_mcp_principal_jwt_store",
        lambda: (_ for _ in ()).throw(AssertionError("JWT should not be loaded without targets")),
    )
    payload = Payload(input={"_runtime_tool_policy_subjects": [external, builtin]})
    result = await mcp_runtime.attach_mcp_server_configs(object(), principal=principal, run_payload=payload)
    assert result.input["_runtime_tool_policy_subjects"] == [builtin]

    async def runtime_target(*_args, **_kwargs):
        return {"transport": "streamable_http", "credential_envelope": "sealed"}

    monkeypatch.setattr(mcp_runtime.mcp_postgres, "get_mcp_server_runtime_target", runtime_target)
    monkeypatch.setattr(
        mcp_runtime,
        "open_mcp_server_credentials",
        lambda **_kwargs: ("https://mcp.example/mcp", {"X-Static-Key": "synthetic"}),
    )

    class MissingJwt:
        async def get_with_subject(self, _principal):
            raise McpRuntimeContextError("mcp_principal_jwt_missing", status_code=401)

    monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: MissingJwt())
    payload = Payload(input={"_runtime_tool_policy_subjects": [external, builtin]})
    result = await mcp_runtime.attach_mcp_server_configs(object(), principal=principal, run_payload=payload)
    assert result.input["_runtime_tool_policy_subjects"] == [builtin]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "credential_corruption", "jwt_corruption"])
async def test_runtime_does_not_hide_database_or_corrupt_credential_failures(monkeypatch, failure):
    @dataclass
    class Payload:
        input: dict

    async def runtime_target(*_args, **_kwargs):
        if failure == "database":
            raise RuntimeError("database failure")
        return {"transport": "streamable_http", "credential_envelope": "sealed"}

    monkeypatch.setattr(mcp_runtime.mcp_postgres, "get_mcp_server_runtime_target", runtime_target)
    if failure == "credential_corruption":
        def invalid_credentials(**_kwargs):
            raise McpRuntimeContextError("mcp_server_credentials_invalid", status_code=503)

        monkeypatch.setattr(mcp_runtime, "open_mcp_server_credentials", invalid_credentials)
    else:
        monkeypatch.setattr(
            mcp_runtime,
            "open_mcp_server_credentials",
            lambda **_kwargs: ("https://mcp.example/mcp", {}),
        )

    class CorruptJwt:
        async def get_with_subject(self, _principal):
            raise McpRuntimeContextError("mcp_principal_jwt_corrupt", status_code=503)

    monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: CorruptJwt())
    error = RuntimeError if failure == "database" else McpRuntimeContextError
    principal = SimpleNamespace(tenant_id="tenant", user_id="user")
    payload = Payload(input={
        "_runtime_tool_policy_subjects": [{"identity": "mcp__gateway__tool", "mcp_server": "gateway"}]
    })
    with pytest.raises(error):
        await mcp_runtime.attach_mcp_server_configs(object(), principal=principal, run_payload=payload)

@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize("integration_kind", ["mcp", "enterprise_gateway"])
async def test_open_mcp_session_updates_jwt_on_each_dispatch_without_replaying_tools(monkeypatch, transport, integration_kind):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        token = "first.synthetic.jwt"
        bound = {"tenant_id": "tenant", "user_id": "user", "grant_id": "grant-a", "subject_epoch": 1}

        async def current_credential(principal):
            assert vars(principal) == bound
            return token

        monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=current_credential))
        config = subject(url=peer.url + ("/sse" if transport == "sse" else "/mcp"))["mcp_server_config"]
        config.update(type=transport, credential_subject=bound, integration_kind=integration_kind)
        jwt_header = "authorization" if integration_kind == "enterprise_gateway" else "jwt-authorization"
        if integration_kind == "enterprise_gateway":
            config["headers"] = {"Authorization": "Bearer first.synthetic.jwt"}
        async with asyncio.timeout(5):
            async with client.open_mcp_session(config, timeout_seconds=2) as session:
                await client.list_mcp_tools(session)
                token = "rotated.synthetic.jwt"
                await session.call_tool(RAW_TOOL, {"query": "synthetic"})
        tool_requests = [headers for method, headers in peer.requests if method == "tools/call"]
        assert len(peer.calls) == len(tool_requests) == 1
        assert {key.lower(): value for key, value in tool_requests[0].items()}[jwt_header] == "Bearer rotated.synthetic.jwt"
        assert {key.lower(): value for key, value in peer.requests[0][1].items()}[jwt_header] == "Bearer first.synthetic.jwt"
        if integration_kind == "enterprise_gateway":
            assert all("jwt-authorization" not in {key.lower() for key in headers}
                       for _, headers in peer.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("integration_kind", ["mcp", "enterprise_gateway"])
async def test_mcp_revoked_execution_credential_stops_before_tool_dispatch(monkeypatch, integration_kind):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        revoked = False

        async def current_credential(_principal):
            if revoked:
                raise McpRuntimeContextError("mcp_principal_jwt_missing", status_code=401)
            return "synthetic.jwt"

        monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=current_credential))
        config = subject(url=peer.url + "/mcp")["mcp_server_config"]
        config["credential_subject"] = {"tenant_id": "tenant", "user_id": "user"}
        config["integration_kind"] = integration_kind
        if integration_kind == "enterprise_gateway":
            config["headers"] = {"Authorization": "Bearer synthetic.jwt"}
        with pytest.raises(McpToolDiscoveryError, match="authorization_required"):
            async with asyncio.timeout(4):
                async with client.open_mcp_session(config, timeout_seconds=1) as session:
                    revoked = True
                    await session.call_tool(RAW_TOOL, {"query": "synthetic"})
        assert peer.calls == []


# Credential attachment snapshot and Worker pre-dispatch regressions.
@dataclass(frozen=True)
class Payload:
    input: dict
    owner_generation: int = 0


@pytest.fixture
def credential_case(monkeypatch):
    settings = SimpleNamespace(mcp_encryption_keys_json=json.dumps({'current': '01' * 32}),
                               mcp_encryption_current_key_id='current')
    monkeypatch.setattr(mcp_runtime, '_settings_provider', lambda: settings)
    principal = SimpleNamespace(tenant_id='tenant', user_id='user')
    store = mcp_runtime.McpPrincipalJwtStore(clock=lambda: 1000)
    monkeypatch.setattr(mcp_runtime, '_DEFAULT_PRINCIPAL_JWT_STORE', store)

    def token(exp, tag='synthetic'):
        return 'e30.' + base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=') + '.' + tag

    jwt = token(2000)
    _, envelope = store.prepare(principal, jwt)
    builtin = {'identity': 'mcp__ai-platform-context__lookup', 'mcp_server': 'ai-platform-context'}
    external = {'identity': 'mcp__gateway__tool', 'mcp_server': 'gateway'}
    payload = Payload({'_runtime_tool_policy_subjects': [external, builtin, {'identity': 'builtin'}]})
    server = mcp_runtime.seal_mcp_server_credentials(tenant_id='tenant', server_id='gateway',
                                               endpoint='https://example.invalid/mcp', static_headers={})

    async def target(*args, **kwargs):
        return {'transport': 'streamable_http', 'credential_envelope': server}

    monkeypatch.setattr(mcp_runtime.mcp_postgres, 'get_mcp_server_runtime_target', target)

    class Redis:
        def __init__(self, values):
            self.values = iter(values)
            self.reads = self.closed = 0
        async def get(self, key):
            self.reads += 1
            value = next(self.values)
            if isinstance(value, Exception):
                raise value
            return value
        async def aclose(self):
            self.closed += 1

    def reads(*values):
        redis = Redis(values)
        monkeypatch.setattr(mcp_runtime, '_redis_provider', lambda: redis)
        return redis

    def record(**updates):
        values = dict(tenant_id='tenant', user_id='user', jwt=jwt, jwt_exp=2000)
        values.update(updates)
        return mcp_runtime._seal_principal_jwt(mcp_runtime._PrincipalJwtRecord(**values))

    return SimpleNamespace(principal=principal, store=store, jwt=jwt, envelope=envelope,
                           builtin=builtin, payload=payload, reads=reads, record=record, token=token)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'expired', 'unavailable'])
async def test_optional_credential_initial_failure_drops_only_external(credential_case, failure):
    c = credential_case
    value = {'missing': None, 'expired': c.record(jwt=c.token(900), jwt_exp=900),
             'unavailable': OSError('synthetic unavailable')}[failure]
    redis = c.reads(value)
    result = await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)
    assert result.input['_runtime_tool_policy_subjects'] == [c.builtin, {'identity': 'builtin'}]
    assert redis.reads == redis.closed == 1
    assert len(c.payload.input['_runtime_tool_policy_subjects']) == 3
    # The required, per-dispatch credential API still raises rather than degrading.
    c.reads(value)
    with pytest.raises(mcp_runtime.McpRuntimeContextError, match='mcp_principal_jwt_' + failure):
        await c.store.get(c.principal)


@pytest.mark.asyncio
@pytest.mark.parametrize('next_value', ['missing', 'expired', 'unavailable', 'corrupt', 'different_login'])
async def test_attachment_never_rereads_for_subject(credential_case, next_value):
    c = credential_case
    poison = {'missing': None, 'expired': c.record(jwt=c.token(900), jwt_exp=900),
              'unavailable': OSError('synthetic unavailable'), 'corrupt': 'not-json',
              'different_login': c.record(jwt=c.token(2000, 'other'))}[next_value]
    redis = c.reads(c.envelope, poison)
    result = await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)
    config = result.input['_runtime_tool_policy_subjects'][0]['mcp_server_config']
    assert config['credential_subject'] == {'tenant_id': 'tenant', 'user_id': 'user',
        'legacy_jwt_id': hashlib.sha256(c.jwt.encode()).hexdigest()}
    assert config['headers'][mcp_runtime.mcp_user_jwt_header('mcp')] == 'Bearer ' + c.jwt
    assert redis.reads == redis.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'expired', 'unavailable', 'corrupt', 'configuration'])
async def test_refresh_reread_preserves_optional_and_fail_closed_policy(credential_case, monkeypatch, failure):
    c = credential_case
    expired = c.record(jwt=c.token(900), jwt_exp=900, refresh_cookie='v2.synthetic',
                       subject_epoch=1, refresh_expires_at=3000)
    async def grant(cookie):
        return {'principal': {'tenant_id': 'tenant', 'user_id': 'user'}, 'subject_epoch': 1}
    async def refresh(*args, **kwargs):
        if failure == 'configuration':
            # Configuration failure at the actual read, not translated refresh adapter error.
            monkeypatch.setattr(mcp_runtime, '_settings_provider', lambda: SimpleNamespace(
                mcp_encryption_keys_json='', mcp_encryption_current_key_id='current'))
    monkeypatch.setattr(mcp_runtime, '_grant_reader', grant)
    monkeypatch.setattr(mcp_runtime, '_credential_refresher', refresh)
    second = {'missing': None, 'expired': c.record(jwt=c.token(900), jwt_exp=900),
              'unavailable': OSError('synthetic unavailable'), 'corrupt': 'not-json',
              'configuration': c.envelope}[failure]
    redis = c.reads(expired, second)
    # Existing store contract classifies an invalid post-refresh JWT as corrupt.
    if failure in {'expired', 'corrupt', 'configuration'}:
        code = 'mcp_context_key_not_configured' if failure == 'configuration' else 'mcp_principal_jwt_corrupt'
        with pytest.raises(mcp_runtime.McpRuntimeContextError, match=code):
            await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)
    else:
        result = await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)
        assert result.input['_runtime_tool_policy_subjects'] == [c.builtin, {'identity': 'builtin'}]
    assert redis.reads == redis.closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['corrupt', 'configuration', 'tenant_mismatch'])
async def test_initial_corruption_configuration_and_tenant_mismatch_fail(credential_case, monkeypatch, failure):
    c = credential_case
    value = c.envelope
    if failure == 'corrupt':
        value = 'not-json'
    elif failure == 'tenant_mismatch':
        value = c.record(tenant_id='other-tenant')
    else:
        monkeypatch.setattr(mcp_runtime, '_settings_provider', lambda: SimpleNamespace(
            mcp_encryption_keys_json='', mcp_encryption_current_key_id='current'))
    c.reads(value)
    with pytest.raises(mcp_runtime.McpRuntimeContextError):
        await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)


@pytest.mark.asyncio
async def test_refresh_subject_and_legacy_api_use_validated_record(credential_case, monkeypatch):
    c = credential_case
    refreshed = c.record(refresh_cookie='v2.synthetic', subject_epoch=2, refresh_expires_at=3000)
    async def grant(cookie):
        return {'principal': {'tenant_id': 'tenant', 'user_id': 'user'}, 'subject_epoch': 2}
    monkeypatch.setattr(mcp_runtime, '_grant_reader', grant)
    c.reads(refreshed)
    jwt, subject = await c.store.get_with_subject(c.principal)
    assert jwt == c.jwt
    assert subject == {'tenant_id': 'tenant', 'user_id': 'user',
                       'grant_id': hashlib.sha256(b'v2.synthetic').hexdigest(), 'subject_epoch': 2}
    c.reads(refreshed)
    assert await c.store.credential_subject(c.principal) == subject
    c.reads(refreshed)
    assert await c.store.get(SimpleNamespace(**subject)) == c.jwt
    c.reads(refreshed)
    with pytest.raises(mcp_runtime.McpRuntimeContextError, match='mcp_principal_jwt_missing'):
        await c.store.get(SimpleNamespace(**{**subject, 'subject_epoch': 1}))


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'unavailable', 'corrupt', 'configuration', 'required'])
async def test_worker_binding_optional_loss_vs_required_failure(credential_case, monkeypatch, failure):
    c = credential_case
    c.reads(None if failure == 'missing' else OSError('synthetic') if failure == 'unavailable'
            else 'not-json' if failure == 'corrupt' else c.envelope)
    if failure == 'configuration':
        monkeypatch.setattr(mcp_runtime, '_settings_provider', lambda: SimpleNamespace(
            mcp_encryption_keys_json='', mcp_encryption_current_key_id='current'))
    events = []
    @asynccontextmanager
    async def transaction():
        yield object()
    async def fence(*args, **kwargs):
        return 'ready'
    async def materialize(*args, **kwargs):
        return {'file_ids': [], 'context_snapshot_id': 'snapshot', 'context_snapshot': {},
                'conversation_context': {}}, None
    def compile_spec(**kwargs):
        if failure == 'required':
            raise ValueError('required capability denied')
        return object()
    async def event(*args, **kwargs):
        events.append(kwargs['event_type'])
    async def fail(*args, **kwargs):
        events.append('fail')
        return SimpleNamespace(payload=kwargs['payload'], outcome=SimpleNamespace(
            status='failed', error_code=kwargs['error_code'], error_message='failed'))
    class Attempt:
        async def bind_execution_spec(self, *args):
            events.append('bound')
            return {'owner_generation': 7}
    class Queue(SimpleNamespace):
        def model_copy(self, *, update):
            return Queue(**{**vars(self), **update})
    service = WorkerDispatchBindingService(transaction_factory=transaction, dispatch_fence=fence,
        compile_spec=compile_spec, context=WorkerDispatchContextPorts(materialize, lambda x: x, lambda x: {}),
        execution=WorkerDispatchExecutionPorts(project_spec=lambda *a, **kw: c.payload,
            attach_mcp=mcp_runtime.attach_mcp_server_configs, mcp_runtime_error=mcp_runtime.McpRuntimeContextError,
            append_user_event=event, runtime_evidence=lambda **kw: {}, uses_runtime_sandbox=lambda *a, **kw: True,
            create_runtime_lease=event, missing_attempt_error=RuntimeError), fail_pre_dispatch=fail)
    result = await service.bind(payload=Queue(context_snapshot_id='snapshot', executor_type='synthetic'),
        run_identity={'tenant_id': 'tenant', 'run_id': 'run'}, locked_run={}, trace_id='trace',
        attempt_id='attempt', attempt_authority=Attempt(), capabilities=object(), principal=c.principal,
        worker_id='worker', reconciliation=False)
    if failure in {'missing', 'unavailable'}:
        assert result.outcome is None
        assert result.run_payload.input['_runtime_tool_policy_subjects'] == [c.builtin, {'identity': 'builtin'}]
        assert events == ['bound', 'worker_started']
    else:
        assert result.outcome.status == 'failed'
        assert events == ['fail']


@pytest.mark.asyncio
async def test_builtin_only_never_loads_credentials(credential_case):
    c = credential_case
    redis = c.reads(AssertionError('must not read'))
    payload = Payload({'_runtime_tool_policy_subjects': [c.builtin]})
    assert await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=payload) is payload
    assert redis.reads == redis.closed == 0


@pytest.mark.asyncio
async def test_refresh_pairs_new_token_and_subject_without_third_read(credential_case, monkeypatch):
    c = credential_case
    expired = c.record(jwt=c.token(900), jwt_exp=900, refresh_cookie='v2.synthetic',
                       subject_epoch=1, refresh_expires_at=3000)
    fresh_jwt = c.token(2000, 'refreshed')
    fresh = c.record(jwt=fresh_jwt, refresh_cookie='v2.synthetic', subject_epoch=1, refresh_expires_at=3000)
    async def grant(cookie):
        return {'principal': {'tenant_id': 'tenant', 'user_id': 'user'}, 'subject_epoch': 1}
    async def refresh(*args, **kwargs):
        assert kwargs == {'expected_user': ('tenant', 'user'), 'expected_subject_epoch': 1}
    monkeypatch.setattr(mcp_runtime, '_grant_reader', grant)
    monkeypatch.setattr(mcp_runtime, '_credential_refresher', refresh)
    redis = c.reads(expired, fresh, AssertionError('no third read'))
    result = await mcp_runtime.attach_mcp_server_configs(None, principal=c.principal, run_payload=c.payload)
    config = result.input['_runtime_tool_policy_subjects'][0]['mcp_server_config']
    assert config['headers'][mcp_runtime.mcp_user_jwt_header('mcp')] == 'Bearer ' + fresh_jwt
    assert config['credential_subject'] == {'tenant_id': 'tenant', 'user_id': 'user',
        'grant_id': hashlib.sha256(b'v2.synthetic').hexdigest(), 'subject_epoch': 1}
    assert redis.reads == redis.closed == 2

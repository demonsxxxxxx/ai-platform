import ipaddress
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest

from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.domain.errors import McpRuntimeContextError, McpToolDiscoveryError
from app.mcp.infrastructure import catalog, runtime
from app.mcp.infrastructure.gateway_catalog import GatewayCatalogAdapter
from app.executors.claude.capability_policy import _mcp_server_options


def payload():
    return {"servers": [{"id": 1, "projectName": "demo", "catalogGeneration": 2, "tools": [
        {"id": index, "name": f"demo.{name}", "description": "Synthetic tool", "inputSchema": {"type": "object"},
         "definitionRevision": 1, "catalogGeneration": 2, "available": available}
        for index, (name, available) in enumerate((("lookup", True), ("missing", False)), start=1)
    ]}]}


@pytest.fixture
def wire(monkeypatch):
    original = httpx.AsyncClient
    requests = []
    clients = []
    async def addresses(*_args):
        return (ipaddress.ip_address("8.8.8.8"),)
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", addresses)
    def install(response):
        def handler(request):
            requests.append(request)
            return response(request)
        def factory(**kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            client = original(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return requests, clients
    return install


@pytest.mark.asyncio
async def test_gateway_catalog_uses_no_credentials_and_preserves_unavailability(wire):
    requests, clients = wire(lambda request: httpx.Response(200, json=payload()))
    async def target(*_args):
        return SimpleNamespace(endpoint="https://gateway.example/", integration_kind="enterprise_gateway",
                               static_headers={"Authorization": "Bearer must-not-send", "Cookie": "must-not-send"})
    service = LiveMcpCatalogService(target_resolver=target, discovery=None, gateway_catalog=GatewayCatalogAdapter())
    result = await service.list_server_catalog(tenant_id="tenant", server_id="gateway")
    assert result.unavailable_reason is None
    assert [tool.available for tool in result.tools] == [True, False]
    assert result.tools[0].tool_id == "gateway::demo.lookup"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://8.8.8.8/api/mcp/catalog"
    assert request.headers["host"] == "gateway.example"
    assert request.extensions["sni_hostname"] == "gateway.example"
    assert not {"authorization", "jwt-authorization", "cookie", "x-api-key"}.intersection(request.headers)
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [
    ("redirect", "transport_failure"), ("oversize", "response_too_large"),
    ("compression", "unsupported_content_encoding"), ("invalid", "protocol_error"),
    ("duplicate", "protocol_error"), ("unavailable_type", "protocol_error"),
])
async def test_gateway_catalog_rejects_unsafe_or_partial_results(wire, failure, reason):
    data = payload()
    if failure == "duplicate":
        data["servers"][0]["tools"].append(data["servers"][0]["tools"][0])
    if failure == "unavailable_type":
        data["servers"][0]["tools"][0]["available"] = "true"
    responses = {
        "redirect": lambda: httpx.Response(302, headers={"location": "https://other.example/"}),
        "oversize": lambda: httpx.Response(200, content=b"x" * 1100),
        "compression": lambda: httpx.Response(200, headers={"content-encoding": "gzip"}),
        "invalid": lambda: httpx.Response(200, json={"tools": []}),
    }
    requests, clients = wire(lambda _request: responses.get(failure, lambda: httpx.Response(200, json=data))())
    with pytest.raises(McpToolDiscoveryError, match=reason):
        await GatewayCatalogAdapter(max_response_bytes=1024).discover_definitions("https://gateway.example/")
    assert len(requests) == 1 and all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_enterprise_runtime_and_sdk_options_preserve_user_header_mode(monkeypatch):
    @dataclass
    class Payload:
        input: dict
    async def target(*_args, **_kwargs):
        return {"transport": "streamable_http", "integration_kind": "enterprise_gateway", "credential_envelope": "sealed"}
    class Store:
        async def get_with_subject(self, principal):
            return f"{principal.user_id}.synthetic.jwt", {"tenant_id": principal.tenant_id, "user_id": principal.user_id}
    monkeypatch.setattr(runtime.mcp_postgres, "get_mcp_server_runtime_target", target)
    monkeypatch.setattr(runtime, "open_mcp_server_credentials", lambda **_kwargs: ("https://gateway.example/", {}))
    monkeypatch.setattr(runtime, "get_mcp_principal_jwt_store", lambda: Store())
    for user in ("user-a", "user-b"):
        result = await runtime.attach_mcp_server_configs(object(), principal=SimpleNamespace(tenant_id="tenant", user_id=user),
            run_payload=Payload({"_runtime_tool_policy_subjects": [{"identity": "mcp__gateway__demo.lookup", "mcp_server": "gateway"}]}))
        subject = result.input["_runtime_tool_policy_subjects"][0]
        config = _mcp_server_options({subject["identity"]: subject})["gateway"]
        assert config["integration_kind"] == "enterprise_gateway"
        assert config["headers"] == {"Authorization": f"Bearer {user}.synthetic.jwt"}
        assert config["credential_subject"]["user_id"] == user
    monkeypatch.setattr(runtime, "open_mcp_server_credentials", lambda **_kwargs: ("https://gateway.example/", {"Authorization": "Bearer colleague"}))
    with pytest.raises(McpRuntimeContextError, match="mcp_header_conflict"):
        await runtime.attach_mcp_server_configs(object(), principal=SimpleNamespace(tenant_id="tenant", user_id="user-a"),
            run_payload=Payload({"_runtime_tool_policy_subjects": [{"identity": "mcp__gateway__demo.lookup", "mcp_server": "gateway"}]}))

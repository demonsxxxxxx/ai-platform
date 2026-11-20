from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.executors.claude.capability_policy import _mcp_server_options
from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.domain.errors import McpRuntimeContextError
from app.mcp.infrastructure import client, runtime
from tests.support.mcp_protocol_peers import local_mcp_peers
from tests.test_claude_mcp_registration import allow_local_peer


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["legacy", "modern"])
async def test_gateway_connection_test_negotiates_without_listing_tools(monkeypatch, protocol):
    with local_mcp_peers(protocol=protocol) as peer:
        allow_local_peer(monkeypatch, peer.url)
        peer.tools[0]["description"] = "x" * (2 * 1024 * 1024)
        endpoint = peer.url + "/mcp"
        subject = {"tenant_id": "tenant", "user_id": "admin"}

        async def jwt(principal):
            assert vars(principal) == subject
            return "admin.synthetic.jwt"

        async def target(tenant_id, server_id):
            assert (tenant_id, server_id) == ("tenant", "gateway")
            return SimpleNamespace(endpoint=endpoint, transport="streamable_http", static_headers={},
                                   integration_kind="enterprise_gateway")

        monkeypatch.setattr(runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=jwt))
        service = LiveMcpCatalogService(target_resolver=target, discovery=client.McpToolDiscoveryAdapter())
        identity = {**subject, "server_id": "gateway", "jwt": "admin.synthetic.jwt"}
        result = await service.list_server_tools(**identity)
        assert result.unavailable_reason == "gateway_managed" and not result.tools
        assert peer.requests == []
        result = await service.test_connection(**identity, endpoint=endpoint, static_headers={},
                                               transport="streamable_http", integration_kind="enterprise_gateway")
        assert result.unavailable_reason is None and not result.tools
        methods = [method for method, _ in peer.requests]
        if protocol == "modern":
            assert methods == ["server/discover"]
        else:
            assert methods == ["server/discover", "initialize", "notifications/initialized"]
        assert not peer.calls
        for _, headers in peer.requests:
            normalized = {name.lower(): value for name, value in headers.items()}
            assert normalized["authorization"] == "Bearer admin.synthetic.jwt"
            assert "jwt-authorization" not in normalized


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

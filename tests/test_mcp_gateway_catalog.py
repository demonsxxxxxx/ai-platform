from dataclasses import dataclass
from unittest.mock import AsyncMock
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
@pytest.mark.parametrize("case", ["enabled", "off", "wrong-role", "wrong-department", "revoked", "turned-off", "target-changed"])
async def test_new_task_gateway_selection_uses_principal_and_rechecks_authority(monkeypatch, case):
    from app.auth import AuthPrincipal
    from app.bootstrap import mcp

    principal = AuthPrincipal("user-a", "Synthetic", "tenant-a", roles=["member"], department_ids=("110",))
    services = mcp._McpRuntimeServices()
    row = {"capability_id": "vendor", "status": "active", "visible_to_user": True,
           "allowed_roles": ["guest" if case == "wrong-role" else "member"],
           "department_ids": ["210" if case == "wrong-department" else "110"]}
    preference = ["vendor"] if case == "off" else []

    async def get_distributions(_conn, *, tenant_id, capability_kind, include_disabled, capability_ids):
        assert (tenant_id, capability_kind, include_disabled, capability_ids) == ("tenant-a", "mcp_server", False, ["vendor"])
        return [dict(row)]

    async def get_metadata(_conn, *, tenant_id, user_id):
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        return {"mcp_gateway_disabled_servers": list(preference)}

    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        endpoint = peer.url + "/mcp"

        async def target(_conn, tenant_id, name):
            assert (tenant_id, name) == ("tenant-a", "vendor")
            return mcp._LiveMcpTarget(endpoint, {}, "streamable_http", "enterprise_gateway")

        discovery = services.live_catalog.discover_gateway_task_tools

        async def discover(*args, **kwargs):
            nonlocal endpoint
            result = await discovery(*args, **kwargs)
            if case == "revoked":
                row["allowed_roles"] = ["guest"]
            if case == "turned-off":
                preference.append("vendor")
            if case == "target-changed":
                endpoint = "https://different.example/mcp"
            return result

        monkeypatch.setattr(mcp.mcp_postgres, "lock_task_mcp_servers", AsyncMock(return_value=[]))
        monkeypatch.setattr(mcp.mcp_postgres, "list_task_gateway_servers", AsyncMock(return_value=[
            {"name": "vendor", "status": "active"}, {"name": "deleted-gateway", "status": "deleted"},
        ]))
        monkeypatch.setattr(mcp.identity_postgres, "get_user_profile_metadata", get_metadata)
        monkeypatch.setattr(mcp.identity_capability_distributions_postgres, "list_capability_distribution_rows", get_distributions)
        monkeypatch.setattr(services, "_read_live_target", target)
        monkeypatch.setattr(services.live_catalog, "discover_gateway_task_tools", discover)
        jwt = AsyncMock(return_value="synthetic.user.jwt")
        monkeypatch.setattr(services, "principal_jwt_store", SimpleNamespace(get=jwt))

        async def current_credential(subject):
            assert vars(subject) == {"tenant_id": "tenant-a", "user_id": "user-a"}
            return "synthetic.user.jwt"

        monkeypatch.setattr(runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=current_credential))
        result = await services.repository_call("get_task_gateway_mcp_selection", object(), principal=principal)
        assert result.without_gateway(["vendor::old", "deleted-gateway::old", "other::keep"]) == ["other::keep"]
        assert result.tool_ids == (tuple(f"vendor::{tool['name']}" for tool in peer.tools) if case == "enabled" else ())
        if case in {"off", "wrong-role", "wrong-department"}:
            jwt.assert_not_awaited()
            assert not peer.requests
        else:
            jwt.assert_awaited_once_with(principal)
            assert [method for method, _ in peer.requests].count("tools/list") == 1
            assert all({key.lower(): value for key, value in h.items()}.get("authorization") == "Bearer synthetic.user.jwt"
                       for _, h in peer.requests)
        assert not peer.calls


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

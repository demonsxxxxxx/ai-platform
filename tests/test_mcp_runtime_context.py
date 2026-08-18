import asyncio
import base64
import ipaddress
import json
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, Request, Response

from app.auth import AuthPrincipal
from app.mcp.domain.targets import mcp_targets_from_policy_subjects
from app.mcp.infrastructure import runtime as mcp_runtime
from app.mcp.infrastructure.runtime import (
    MCP_RELAY_AUTH_FAILURE_CAPABILITY_LIMIT,
    MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT,
    HostMcpRelay,
    InMemoryRuntimeContextStore,
    McpRelayAuthFailureLimiter,
    McpRelayAuthFailureCounts,
    McpRelayError,
    McpRelayTarget,
    McpRuntimeContextError,
    McpRuntimeContextManager,
    McpToolSelectionRequired,
    McpValidatedTarget,
    bounded_tool_view,
    normalize_static_mcp_headers,
    open_mcp_server_credentials,
    preflight_mcp_admission,
    resolve_registered_mcp_target,
    seal_mcp_server_credentials,
    validate_registered_mcp_target,
)
from app.models import ChatStreamRequest, CreateRunRequest
from app.routes import mcp as mcp_routes
from app.runtime.sandbox.contracts import MCP_RELAY_CALLBACK_PATH, build_trusted_callback_target
from app.settings import Settings


def _jwt(*, exp: int) -> str:
    def part(value: dict[str, object]) -> str:
        encoded = base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return encoded.rstrip("=")

    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part({'sub': 'company-user', 'exp': exp})}.signature"


def _principal(*, user_id: str = "user-a", tenant_id: str = "tenant-a") -> AuthPrincipal:
    return AuthPrincipal(
        user_id=user_id,
        display_name=user_id,
        tenant_id=tenant_id,
        source="company-login",
    )


def _settings(
    monkeypatch,
    *,
    now: int = 1_700_000_000,
    max_response_bytes: int = 1024 * 1024,
) -> int:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
    monkeypatch.setattr(
        "app.mcp.infrastructure.runtime.get_settings",
        lambda: Settings(
            mcp_context_encryption_keys_json=json.dumps({"current": key}),
            mcp_context_current_key_id="current",
            mcp_context_ttl_seconds=300,
            mcp_context_lease_seconds=1800,
            mcp_relay_max_response_bytes=max_response_bytes,
        ),
    )
    return now


class _Client:
    def __init__(self, handler, **kwargs):
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def post(self, *args, **kwargs):
        return await self.client.post(*args, **kwargs)

    def stream(self, *args, **kwargs):
        return self.client.stream(*args, **kwargs)


async def _accept_test_target(endpoint: str) -> str:
    return endpoint


async def _manager_with_capability(
    monkeypatch,
    *,
    targets=None,
    max_response_bytes: int = 1024 * 1024,
):
    now = _settings(monkeypatch, max_response_bytes=max_response_bytes)
    store = InMemoryRuntimeContextStore(clock=lambda: now)
    manager = McpRuntimeContextManager(store=store, clock=lambda: now)
    token = _jwt(exp=now + 900)
    context = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {token}"
    )
    await manager.bind_to_run(
        context_id=context["mcp_context_id"], principal=_principal(), run_id="run-a"
    )
    capability = await manager.claim_attempt_lease(
        context_id=context["mcp_context_id"],
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        attempt_id="attempt-a",
        targets=targets or {"inventory-mcp": ("search",)},
    )
    return now, token, store, manager, context, capability


@pytest.mark.asyncio
async def test_runtime_context_is_sealed_and_capability_contains_only_server_tool_targets(monkeypatch):
    now, token, store, manager, context, capability = await _manager_with_capability(
        monkeypatch,
        targets={"inventory-mcp": ("search",), "project-mcp": ("get-project",)},
    )

    raw = await store.get(f"ai-platform:mcp:runtime-context:v1:{context['mcp_context_id']}")
    assert raw is not None
    assert token not in raw
    assert "inventory-mcp" not in capability.token
    assert capability.targets == {
        "inventory-mcp": ("search",),
        "project-mcp": ("get-project",),
    }
    assert capability.expires_at == now + 900
    resolved = await manager.resolve_capability(capability.token)
    assert resolved.jwt == token
    assert resolved.capability.targets == capability.targets


@pytest.mark.asyncio
async def test_discard_unbound_context_preserves_bound_and_other_principal_contexts(monkeypatch):
    now = _settings(monkeypatch)
    store = InMemoryRuntimeContextStore(clock=lambda: now)
    manager = McpRuntimeContextManager(store=store, clock=lambda: now)
    token = _jwt(exp=now + 900)
    unbound = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {token}"
    )
    bound = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {token}"
    )
    other = await manager.create_context(
        principal=_principal(user_id="user-b"), bearer_jwt=f"Bearer {token}"
    )
    await manager.bind_to_run(
        context_id=bound["mcp_context_id"],
        principal=_principal(),
        run_id="run-bound",
    )

    assert await manager.discard_unbound_context(
        unbound["mcp_context_id"], _principal()
    ) is True
    assert await manager.discard_unbound_context(
        bound["mcp_context_id"], _principal()
    ) is False
    assert await manager.discard_unbound_context(
        other["mcp_context_id"], _principal()
    ) is False
    assert await store.get(
        f"ai-platform:mcp:runtime-context:v1:{unbound['mcp_context_id']}"
    ) is None
    assert await store.get(
        f"ai-platform:mcp:runtime-context:v1:{bound['mcp_context_id']}"
    ) is not None
    assert await store.get(
        f"ai-platform:mcp:runtime-context:v1:{other['mcp_context_id']}"
    ) is not None


@pytest.mark.asyncio
async def test_runtime_context_ciphertext_cannot_be_relocated_to_another_context_key(
    monkeypatch,
):
    now, _, store, manager, context, _ = await _manager_with_capability(monkeypatch)
    source_key = f"ai-platform:mcp:runtime-context:v1:{context['mcp_context_id']}"
    raw = await store.get(source_key)
    assert raw is not None
    relocated_context_id = "mcpctx_relocated"
    await store.set(
        f"ai-platform:mcp:runtime-context:v1:{relocated_context_id}",
        raw,
        ttl_seconds=300,
    )

    with pytest.raises(McpRuntimeContextError, match="mcp_context_corrupt"):
        await manager._read(relocated_context_id)


@pytest.mark.asyncio
async def test_attempt_capability_is_bound_to_one_attempt_and_old_token_cannot_replay(monkeypatch):
    _, _, _, manager, context, first = await _manager_with_capability(monkeypatch)

    with pytest.raises(McpRuntimeContextError, match="mcp_attempt_lease_conflict"):
        await manager.claim_attempt_lease(
            context_id=context["mcp_context_id"],
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            attempt_id="attempt-b",
            targets={"inventory-mcp": ("search",)},
        )

    await manager.release_attempt_lease(token=first.token)
    second = await manager.claim_attempt_lease(
        context_id=context["mcp_context_id"],
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        attempt_id="attempt-b",
        targets={"inventory-mcp": ("search",)},
    )
    assert second.token != first.token
    with pytest.raises(McpRelayError, match="mcp_capability_invalid"):
        await manager.resolve_capability(first.token)


@pytest.mark.asyncio
async def test_same_attempt_cannot_reuse_a_capability_with_changed_targets(monkeypatch):
    _, _, _, manager, context, _ = await _manager_with_capability(monkeypatch)

    with pytest.raises(McpRuntimeContextError, match="mcp_attempt_lease_conflict"):
        await manager.claim_attempt_lease(
            context_id=context["mcp_context_id"],
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            attempt_id="attempt-a",
            targets={"inventory-mcp": ("search", "delete")},
        )


def test_static_headers_reject_dynamic_name_and_round_trip_only_in_envelope(monkeypatch):
    _settings(monkeypatch)
    for name in ("JWT-Authorization", "jwt-authorization", " Jwt-Authorization "):
        with pytest.raises(McpRuntimeContextError, match="mcp_header_conflict"):
            normalize_static_mcp_headers({name: "static-secret"})

    with pytest.raises(McpRuntimeContextError, match="mcp_header_duplicate"):
        normalize_static_mcp_headers({"X-API-Key": "a", "x-api-key": "b"})

    envelope = seal_mcp_server_credentials(
        tenant_id="tenant-a",
        server_id="inventory-mcp",
        endpoint="https://inventory.example/mcp",
        static_headers={"X-API-Key": "static-secret", "Authorization": "Basic service"},
    )
    assert "static-secret" not in envelope
    endpoint, headers = open_mcp_server_credentials(
        tenant_id="tenant-a", server_id="inventory-mcp", envelope=envelope
    )
    assert endpoint == "https://inventory.example/mcp"
    assert headers == {"X-API-Key": "static-secret", "Authorization": "Basic service"}


def test_open_server_credentials_maps_aad_mismatch_to_safe_503(monkeypatch):
    _settings(monkeypatch)
    secret = "static-secret"
    envelope = seal_mcp_server_credentials(
        tenant_id="tenant-a",
        server_id="inventory-mcp",
        endpoint="https://inventory.example/mcp",
        static_headers={"X-API-Key": secret},
    )

    with pytest.raises(McpRelayError) as exc_info:
        open_mcp_server_credentials(
            tenant_id="tenant-a",
            server_id="wrong-server",
            envelope=envelope,
        )

    assert exc_info.value.code == "mcp_server_credentials_invalid"
    assert exc_info.value.status_code == 503
    assert str(exc_info.value) == "mcp_server_credentials_invalid"
    assert secret not in repr(exc_info.value)


def test_open_server_credentials_preserves_header_conflict_409(monkeypatch):
    _settings(monkeypatch)
    normalizer = mcp_runtime.normalize_static_mcp_headers
    monkeypatch.setattr(
        mcp_runtime,
        "normalize_static_mcp_headers",
        lambda headers: dict(headers or {}),
    )
    envelope = seal_mcp_server_credentials(
        tenant_id="tenant-a",
        server_id="inventory-mcp",
        endpoint="https://inventory.example/mcp",
        static_headers={"JWT-Authorization": "static-secret"},
    )
    monkeypatch.setattr(mcp_runtime, "normalize_static_mcp_headers", normalizer)

    with pytest.raises(McpRelayError) as exc_info:
        open_mcp_server_credentials(
            tenant_id="tenant-a",
            server_id="inventory-mcp",
            envelope=envelope,
        )

    assert exc_info.value.code == "mcp_header_conflict"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_runtime_target_rechecks_dns_policy_before_dispatch(monkeypatch):
    async def loopback_dns(_hostname: str, _port: int):
        return (ipaddress.ip_address("127.0.0.1"),)

    monkeypatch.setattr(
        "app.mcp.catalog._resolve_discovery_addresses",
        loopback_dns,
    )
    with pytest.raises(McpRelayError, match="mcp_server_target_invalid"):
        await validate_registered_mcp_target("https://mcp.example/tools")
    with pytest.raises(McpRelayError, match="mcp_server_target_invalid"):
        await validate_registered_mcp_target("https://mcp.example/tools?jwt=secret")


@pytest.mark.asyncio
async def test_registered_target_never_falls_back_to_plaintext_catalog_endpoint(
    monkeypatch,
):
    async def target_reader(_tenant_id, _server_id):
        return {
            "credential_envelope": "",
            "registered_endpoint": "https://plaintext.example/mcp",
            "active_tool_names": ["search"],
        }

    monkeypatch.setattr(
        "app.mcp.infrastructure.runtime._relay_target_reader",
        target_reader,
    )

    with pytest.raises(McpRelayError, match="mcp_server_not_available"):
        await resolve_registered_mcp_target("tenant-a", "inventory-mcp")


@pytest.mark.asyncio
async def test_runtime_target_pins_the_validated_address_and_preserves_tls_identity(
    monkeypatch,
):
    async def public_dns(_hostname: str, _port: int):
        return (ipaddress.ip_address("8.8.8.8"),)

    monkeypatch.setattr(
        "app.mcp.catalog._resolve_discovery_addresses",
        public_dns,
    )

    target = await validate_registered_mcp_target(
        "https://mcp.example:8443/tools"
    )

    assert target == McpValidatedTarget(
        endpoint="https://mcp.example:8443/tools",
        connect_url="https://8.8.8.8:8443/tools",
        host_header="mcp.example:8443",
        sni_hostname="mcp.example",
    )


@pytest.mark.asyncio
async def test_relay_uses_original_hostname_for_real_tls_handshake(tmp_path, monkeypatch):
    _settings(monkeypatch)
    hostname = "mcp.example"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "mcp-cert.pem"
    key_path = tmp_path / "mcp-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    client_context = ssl.create_default_context(cafile=str(certificate_path))
    observed_sni: list[str | None] = []
    server_context.set_servername_callback(
        lambda _socket, server_name, _context: observed_sni.append(server_name)
    )

    async def handle_request(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            body = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_server(
        handle_request,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    port = int(server.sockets[0].getsockname()[1])
    endpoint = f"https://{hostname}:{port}/mcp"

    async def pin_target(_endpoint: str) -> McpValidatedTarget:
        return McpValidatedTarget(
            endpoint=endpoint,
            connect_url=f"https://127.0.0.1:{port}/mcp",
            host_header=f"{hostname}:{port}",
            sni_hostname=hostname,
        )

    relay = HostMcpRelay(
        context_manager=object(),  # type: ignore[arg-type]
        target_validator=pin_target,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            verify=client_context,
            trust_env=False,
            **kwargs,
        ),
    )
    try:
        response = await relay._post(
            target=McpRelayTarget(
                endpoint=endpoint,
                static_headers={},
                active_tool_names=("search",),
            ),
            jwt="company.jwt",
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    finally:
        server.close()
        await server.wait_closed()

    assert response.status_code == 200
    assert response.json()["result"]["tools"] == []
    assert observed_sni == [hostname]


@pytest.mark.asyncio
async def test_relay_merges_static_headers_with_fixed_jwt_and_isolates_servers(monkeypatch):
    now, token, _, manager, _, capability = await _manager_with_capability(
        monkeypatch,
        targets={"inventory-mcp": ("search",), "project-mcp": ("get-project",)},
    )
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Mcp-Session-Id": "downstream-session"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": [{"name": "search", "inputSchema": {"type": "object"}}]},
            },
        )

    async def resolve(tenant_id: str, server_id: str) -> McpRelayTarget:
        assert tenant_id == "tenant-a"
        assert server_id == "inventory-mcp"
        return McpRelayTarget(
            endpoint="https://inventory.example/mcp",
            static_headers={"X-API-Key": "static-secret", "Authorization": "Basic service"},
            active_tool_names=("search",),
        )

    async def pin_target(endpoint: str) -> McpValidatedTarget:
        assert endpoint == "https://inventory.example/mcp"
        return McpValidatedTarget(
            endpoint=endpoint,
            connect_url="https://10.20.30.40/mcp",
            host_header="inventory.example",
            sni_hostname="inventory.example",
        )

    relay = HostMcpRelay(
        context_manager=manager,
        target_resolver=resolve,
        target_validator=pin_target,
        client_factory=lambda **kwargs: _Client(handler, **kwargs),
    )
    response_headers = {}
    result = await relay.forward(
        capability_token=capability.token,
        server_id="inventory-mcp",
        payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        incoming_headers={
            "JWT-Authorization": "Bearer attacker",
            "Authorization": "Bearer attacker",
            "Cookie": "session=secret",
            "Mcp-Session-Id": "session-1",
        },
        response_headers=response_headers,
    )

    assert result["result"]["tools"][0]["name"] == "search"
    assert seen[0].headers["JWT-Authorization"] == f"Bearer {token}"
    assert seen[0].headers["Authorization"] == "Basic service"
    assert seen[0].headers["X-API-Key"] == "static-secret"
    assert seen[0].headers["Mcp-Session-Id"] == "session-1"
    assert response_headers == {"Mcp-Session-Id": "downstream-session"}
    assert "Cookie" not in seen[0].headers
    assert seen[0].url == httpx.URL("https://10.20.30.40/mcp")
    assert seen[0].headers["Host"] == "inventory.example"
    assert seen[0].extensions["sni_hostname"] == "inventory.example"

    with pytest.raises(McpRelayError, match="mcp_server_not_selected"):
        await relay.forward(
            capability_token=capability.token,
            server_id="unselected-mcp",
            payload={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
        )
    with pytest.raises(McpRelayError, match="mcp_tool_not_selected"):
        await relay.forward(
            capability_token=capability.token,
            server_id="inventory-mcp",
            payload={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "delete-all", "arguments": {}},
            },
        )
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_relay_blocks_redirect_and_401_invalidates_context(monkeypatch):
    _, _, _, manager, _, capability = await _manager_with_capability(monkeypatch)

    async def resolve(_tenant_id: str, _server_id: str) -> McpRelayTarget:
        return McpRelayTarget(
            endpoint="https://inventory.example/mcp",
            static_headers={},
            active_tool_names=("search",),
        )

    async def redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/metadata"})

    relay = HostMcpRelay(
        context_manager=manager,
        target_resolver=resolve,
        target_validator=_accept_test_target,
        client_factory=lambda **kwargs: _Client(redirect_handler, **kwargs),
    )
    with pytest.raises(McpRelayError, match="mcp_server_redirect_blocked"):
        await relay.forward(
            capability_token=capability.token,
            server_id="inventory-mcp",
            payload={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    async def unauthorized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    relay = HostMcpRelay(
        context_manager=manager,
        target_resolver=resolve,
        target_validator=_accept_test_target,
        client_factory=lambda **kwargs: _Client(unauthorized_handler, **kwargs),
    )
    with pytest.raises(McpRelayError, match="mcp_server_unauthorized"):
        await relay.forward(
            capability_token=capability.token,
            server_id="inventory-mcp",
            payload={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    with pytest.raises(McpRelayError, match="mcp_capability_invalid"):
        await manager.resolve_capability(capability.token)


@pytest.mark.asyncio
async def test_relay_stops_reading_when_streamed_response_exceeds_limit(monkeypatch):
    _, _, _, manager, _, capability = await _manager_with_capability(
        monkeypatch,
        max_response_bytes=1024,
    )

    async def resolve(_tenant_id: str, _server_id: str) -> McpRelayTarget:
        return McpRelayTarget(
            endpoint="https://inventory.example/mcp",
            static_headers={},
            active_tool_names=("search",),
        )

    async def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1025)

    relay = HostMcpRelay(
        context_manager=manager,
        target_resolver=resolve,
        target_validator=_accept_test_target,
        client_factory=lambda **kwargs: _Client(oversized_handler, **kwargs),
    )

    with pytest.raises(McpRelayError, match="mcp_server_response_too_large"):
        await relay.forward(
            capability_token=capability.token,
            server_id="inventory-mcp",
            payload={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )


def test_tool_view_requires_explicit_selection_without_truncating_schema():
    tools = [
        {"name": f"tool-{index}", "inputSchema": {"type": "object", "index": index}}
        for index in range(41)
    ]
    with pytest.raises(McpToolSelectionRequired, match="mcp_tool_selection_required"):
        bounded_tool_view(tools)
    assert bounded_tool_view(tools, selected_tool_names=("tool-40",)) == [tools[-1]]


def test_tool_view_rejects_duplicate_catalog_names_and_incomplete_selection():
    duplicate = [
        {"name": "search", "inputSchema": {}},
        {"name": "search", "inputSchema": {}},
    ]
    with pytest.raises(McpRelayError, match="mcp_tools_schema_invalid"):
        bounded_tool_view(duplicate)
    with pytest.raises(McpRelayError, match="mcp_tool_selection_invalid"):
        bounded_tool_view(
            [{"name": "search", "inputSchema": {}}],
            selected_tool_names=("search", "missing"),
        )
    with pytest.raises(McpRelayError, match="mcp_tool_selection_invalid"):
        bounded_tool_view(
            [{"name": "search", "inputSchema": {}}],
            selected_tool_names=("search", "search"),
        )


def test_targets_are_derived_only_from_authorized_policy_subjects():
    assert mcp_targets_from_policy_subjects(
        [
            {"identity": "Read"},
            {
                "identity": "mcp__inventory-mcp__search",
                "mcp_server": "inventory-mcp",
                "mcp_tool": "search",
            },
            {
                "identity": "mcp__project-mcp__get-project",
                "mcp_server": "project-mcp",
                "mcp_tool": "get-project",
            },
        ]
    ) == {"inventory-mcp": ("search",), "project-mcp": ("get-project",)}


def test_platform_request_contract_has_no_gateway_specific_selector():
    request = ChatStreamRequest(
        message="use MCP",
        mcp_context_id="mcpctx-test",
        selected_mcp_tool_ids=["mcpt-inventory-query"],
    )
    assert request.selected_mcp_tool_ids == ["mcpt-inventory-query"]
    with pytest.raises(ValueError):
        ChatStreamRequest(message="use MCP", mcp_context_id="../invalid")
    with pytest.raises(ValueError):
        CreateRunRequest(agent_id="general-agent", mcp_context_id="invalid context")
    with pytest.raises(ValueError):
        CreateRunRequest.model_validate(
            {
                "agent_id": "general-agent",
                "mcp_context_id": "mcpctx-test",
                "mcp_gateway_tool_names": ["inventory.erase"],
            }
        )


def test_runtime_config_and_callback_have_no_fixed_gateway_dependency():
    target = build_trusted_callback_target("http://localhost:8000")
    assert target.mcp_relay_url == f"http://localhost:8000{MCP_RELAY_CALLBACK_PATH}"
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "ai-platform"
        / "docker-compose.yml"
    )
    compose = compose_path.read_text(encoding="utf-8")
    compose_lines = compose.splitlines()
    assert "MCP_GATEWAY_URL" not in compose
    required = (
        "MCP_CONTEXT_ENCRYPTION_KEYS_JSON",
        "MCP_CONTEXT_CURRENT_KEY_ID",
        "MCP_CONTEXT_TTL_SECONDS",
        "MCP_CONTEXT_LEASE_SECONDS",
    )
    assert all(
        sum(
            1
            for line in compose_lines
            if line.lstrip().startswith(f"{key}:")
        )
        == 2
        for key in required
    )


@pytest.mark.asyncio
async def test_preflight_requires_and_binds_context_before_mcp_admission(monkeypatch):
    now = _settings(monkeypatch)
    manager = McpRuntimeContextManager(
        store=InMemoryRuntimeContextStore(clock=lambda: now), clock=lambda: now
    )
    with pytest.raises(McpRuntimeContextError, match="mcp_context_required"):
        await preflight_mcp_admission(
            context_id=None,
            principal=_principal(),
            run_id="run-a",
            selected_tool_names=("mcpt-inventory-query",),
            mcp_required=True,
            context_manager=manager,
        )

    context = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {_jwt(exp=now + 900)}"
    )
    result = await preflight_mcp_admission(
        context_id=context["mcp_context_id"],
        principal=_principal(),
        run_id="run-a",
        selected_tool_names=("mcpt-inventory-query",),
        mcp_required=True,
        context_manager=manager,
    )
    assert result is not None and result.run_id == "run-a"


@pytest.mark.asyncio
async def test_preflight_ignores_context_when_resolved_run_has_no_mcp(monkeypatch):
    now = _settings(monkeypatch)
    manager = McpRuntimeContextManager(
        store=InMemoryRuntimeContextStore(clock=lambda: now), clock=lambda: now
    )
    context = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {_jwt(exp=now + 900)}"
    )

    result = await preflight_mcp_admission(
        context_id=context["mcp_context_id"],
        principal=_principal(),
        run_id="run-without-mcp",
        selected_tool_names=(),
        mcp_required=False,
        context_manager=manager,
    )

    assert result is None
    record = await manager._read(context["mcp_context_id"])
    assert record.bound_run_id is None


@pytest.mark.asyncio
async def test_context_binding_and_attempt_leases_are_race_safe(monkeypatch):
    now = _settings(monkeypatch)
    manager = McpRuntimeContextManager(
        store=InMemoryRuntimeContextStore(clock=lambda: now), clock=lambda: now
    )
    context = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {_jwt(exp=now + 3600)}"
    )
    bind_results = await asyncio.gather(
        manager.bind_to_run(
            context_id=context["mcp_context_id"], principal=_principal(), run_id="run-a"
        ),
        manager.bind_to_run(
            context_id=context["mcp_context_id"], principal=_principal(), run_id="run-b"
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in bind_results) == 1
    bound_run_id = next(item.bound_run_id for item in bind_results if not isinstance(item, Exception))
    lease_results = await asyncio.gather(
        manager.claim_attempt_lease(
            context_id=context["mcp_context_id"],
            tenant_id="tenant-a",
            user_id="user-a",
            run_id=bound_run_id,
            attempt_id="attempt-a",
            targets={"inventory-mcp": ("search",)},
        ),
        manager.claim_attempt_lease(
            context_id=context["mcp_context_id"],
            tenant_id="tenant-a",
            user_id="user-a",
            run_id=bound_run_id,
            attempt_id="attempt-b",
            targets={"inventory-mcp": ("search",)},
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in lease_results) == 1


@pytest.mark.asyncio
async def test_runtime_context_route_reads_only_jwt_authorization_and_sets_no_store(monkeypatch):
    now = _settings(monkeypatch)
    manager = McpRuntimeContextManager(
        store=InMemoryRuntimeContextStore(clock=lambda: now), clock=lambda: now
    )
    monkeypatch.setattr(mcp_routes, "MCP_RUNTIME_CONTEXT_MANAGER", manager)
    response = Response()
    result = await mcp_routes.create_mcp_runtime_context(
        response=response,
        jwt_authorization=f"Bearer {_jwt(exp=now + 900)}",
        principal=_principal(),
    )
    assert result["mcp_context_id"].startswith("mcpctx_")
    assert response.headers["cache-control"] == "no-store"
    assert datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")).tzinfo


@pytest.mark.asyncio
async def test_runtime_context_discard_route_is_principal_scoped_and_opaque(monkeypatch):
    now = _settings(monkeypatch)
    store = InMemoryRuntimeContextStore(clock=lambda: now)
    manager = McpRuntimeContextManager(store=store, clock=lambda: now)
    token = _jwt(exp=now + 900)
    owned = await manager.create_context(
        principal=_principal(), bearer_jwt=f"Bearer {token}"
    )
    other = await manager.create_context(
        principal=_principal(user_id="user-b"), bearer_jwt=f"Bearer {token}"
    )

    async def discard(context_id, principal):
        await manager.discard_unbound_context(context_id, principal)

    monkeypatch.setattr(mcp_routes, "discard_unbound_mcp_runtime_context", discard)

    responses = [
        await mcp_routes.discard_mcp_runtime_context(
            context_id=owned["mcp_context_id"], principal=_principal()
        ),
        await mcp_routes.discard_mcp_runtime_context(
            context_id=other["mcp_context_id"], principal=_principal()
        ),
        await mcp_routes.discard_mcp_runtime_context(
            context_id="mcpctx_missing", principal=_principal()
        ),
    ]

    assert [response.status_code for response in responses] == [204, 204, 204]
    assert await store.get(
        f"ai-platform:mcp:runtime-context:v1:{owned['mcp_context_id']}"
    ) is None
    assert await store.get(
        f"ai-platform:mcp:runtime-context:v1:{other['mcp_context_id']}"
    ) is not None


@pytest.mark.asyncio
async def test_relay_auth_limiter_does_not_block_capabilities_on_shared_egress():
    class Redis:
        def __init__(self, *, source_count: int, capability_count: int):
            self.source_count = source_count
            self.capability_count = capability_count

        async def mget(self, *keys: str):
            values = []
            for key in keys:
                if ":source:" in key:
                    value = self.source_count
                elif ":capability:" in key:
                    value = self.capability_count
                else:
                    value = None
                values.append(None if value is None else str(value).encode("ascii"))
            return values

        async def eval(self, _script: str, key_count: int, *_args: object):
            assert key_count == 2
            return [self.source_count + 1, self.capability_count + 1]

    shared_source = Redis(
        source_count=MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT - 1,
        capability_count=0,
    )
    limiter = McpRelayAuthFailureLimiter(redis=shared_source)
    await limiter.ensure_allowed(
        source_fingerprint="shared-egress",
        capability_fingerprint="valid-caller",
    )
    counts = await limiter.record_failure(
        source_fingerprint="shared-egress",
        capability_fingerprint="valid-caller",
    )
    assert counts == McpRelayAuthFailureCounts(
        source=MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT,
        capability=1,
    )

    exhausted_capability = Redis(
        source_count=MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT - 1,
        capability_count=MCP_RELAY_AUTH_FAILURE_CAPABILITY_LIMIT,
    )
    with pytest.raises(McpRuntimeContextError, match="mcp_relay_rate_limited"):
        await McpRelayAuthFailureLimiter(redis=exhausted_capability).ensure_allowed(
            source_fingerprint="shared-egress",
            capability_fingerprint="failing-caller",
        )

    abusive_source = Redis(
        source_count=MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT,
        capability_count=0,
    )
    with pytest.raises(McpRuntimeContextError, match="mcp_relay_rate_limited"):
        await McpRelayAuthFailureLimiter(redis=abusive_source).ensure_allowed(
            source_fingerprint="abusive-egress",
            capability_fingerprint="rotated-token",
        )


@pytest.mark.asyncio
async def test_relay_route_throttles_and_audits_safe_authorization_fingerprints(
    monkeypatch,
    caplog,
):
    calls: list[tuple[str, str, str | None]] = []

    class Limiter:
        async def ensure_allowed(self, *, source_fingerprint, capability_fingerprint):
            calls.append(("check", source_fingerprint, capability_fingerprint))

        async def record_failure(self, *, source_fingerprint, capability_fingerprint):
            calls.append(("failure", source_fingerprint, capability_fingerprint))
            return McpRelayAuthFailureCounts(source=23, capability=2)

    class Relay:
        def __init__(self, **_kwargs):
            pass

        async def forward(self, **_kwargs):
            raise McpRelayError("mcp_capability_invalid", status_code=401)

    monkeypatch.setattr(mcp_routes, "MCP_RELAY_AUTH_FAILURE_LIMITER", Limiter())
    monkeypatch.setattr(mcp_routes, "HostMcpRelay", Relay)
    caplog.set_level("WARNING", logger=mcp_routes.__name__)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ai/mcp/relay/inventory",
            "headers": [],
            "client": ("10.0.0.7", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await mcp_routes.relay_mcp_jsonrpc(
            server_id="inventory",
            request=request,
            response=Response(),
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            capability="mcpbrk:private-token",
        )

    assert getattr(exc_info.value, "status_code", None) == 401
    assert [item[0] for item in calls] == ["check", "failure"]
    assert all(len(item[1]) == 64 and len(item[2] or "") == 64 for item in calls)
    assert "10.0.0.7" not in repr(calls)
    assert "private-token" not in repr(calls)
    audit_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "mcp_relay_auth_failure"
    )
    assert audit_record.mcp_source_failure_count == 23
    assert audit_record.mcp_capability_failure_count == 2
    assert not hasattr(audit_record, "mcp_failure_count")


@pytest.mark.asyncio
async def test_relay_route_maps_non_relay_runtime_errors_to_safe_http_errors(monkeypatch):
    class Limiter:
        async def ensure_allowed(self, **_kwargs):
            return None

        async def record_failure(self, **_kwargs):
            raise AssertionError("503 runtime failures are not auth failures")

    class Relay:
        def __init__(self, **_kwargs):
            pass

        async def forward(self, **_kwargs):
            raise McpRuntimeContextError("mcp_context_corrupt", status_code=503)

    monkeypatch.setattr(mcp_routes, "MCP_RELAY_AUTH_FAILURE_LIMITER", Limiter())
    monkeypatch.setattr(mcp_routes, "HostMcpRelay", Relay)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ai/mcp/relay/inventory",
            "headers": [],
            "client": ("10.0.0.7", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await mcp_routes.relay_mcp_jsonrpc(
            server_id="inventory",
            request=request,
            response=Response(),
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            capability="mcpbrk:private-token",
        )

    assert getattr(exc_info.value, "status_code", None) == 503
    assert getattr(exc_info.value, "detail", None) == "mcp_context_corrupt"


@pytest.mark.asyncio
async def test_relay_route_preserves_downstream_session_header_for_json_and_no_content(
    monkeypatch,
):
    class Limiter:
        async def ensure_allowed(self, **_kwargs):
            return None

    class Relay:
        def __init__(self, **_kwargs):
            pass

        async def forward(self, **kwargs):
            kwargs["response_headers"]["Mcp-Session-Id"] = "session-next"
            if kwargs["payload"]["method"] == "notifications/initialized":
                return None
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

    monkeypatch.setattr(mcp_routes, "MCP_RELAY_AUTH_FAILURE_LIMITER", Limiter())
    monkeypatch.setattr(mcp_routes, "HostMcpRelay", Relay)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ai/mcp/relay/inventory",
            "headers": [],
            "client": ("10.0.0.7", 12345),
        }
    )
    json_response = Response()

    result = await mcp_routes.relay_mcp_jsonrpc(
        server_id="inventory",
        request=request,
        response=json_response,
        payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        capability="mcpbrk:private-token",
    )
    no_content = await mcp_routes.relay_mcp_jsonrpc(
        server_id="inventory",
        request=request,
        response=Response(),
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        capability="mcpbrk:private-token",
    )

    assert result == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert json_response.headers["Mcp-Session-Id"] == "session-next"
    assert no_content.status_code == 204
    assert no_content.headers["Mcp-Session-Id"] == "session-next"


@pytest.mark.asyncio
async def test_relay_route_stops_before_dispatch_when_auth_failures_are_limited(
    monkeypatch,
):
    class Limiter:
        async def ensure_allowed(self, **_kwargs):
            raise McpRuntimeContextError(
                "mcp_relay_rate_limited",
                status_code=429,
            )

    class ForbiddenRelay:
        def __init__(self, **_kwargs):
            raise AssertionError("rate-limited requests must not reach dispatch")

    monkeypatch.setattr(mcp_routes, "MCP_RELAY_AUTH_FAILURE_LIMITER", Limiter())
    monkeypatch.setattr(mcp_routes, "HostMcpRelay", ForbiddenRelay)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ai/mcp/relay/inventory",
            "headers": [],
            "client": ("10.0.0.7", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await mcp_routes.relay_mcp_jsonrpc(
            server_id="inventory",
            request=request,
            response=Response(),
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            capability="mcpbrk:private-token",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "mcp_relay_rate_limited"

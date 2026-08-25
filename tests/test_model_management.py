from __future__ import annotations

import base64
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.model_management import client, routes as model_routes
from app.model_management.client import (
    ModelUpstreamError,
    UpstreamResponse,
    open_upstream_stream,
    parse_model_ids,
)
from app.model_management.repository import (
    activate_connection_and_sync,
    get_run_connection,
    platform_model_id,
    resolve_run_model,
)
from app.model_management.security import (
    ModelConnectionSecurityError,
    decrypt_api_key,
    encrypt_api_key,
    validate_endpoint,
)


def _key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def test_model_api_key_encryption_is_revision_bound_and_never_plaintext() -> None:
    ciphertext = encrypt_api_key("secret-upstream-key", revision=7, encoded_key=_key())

    assert b"secret-upstream-key" not in ciphertext
    assert decrypt_api_key(ciphertext, revision=7, encoded_key=_key()) == "secret-upstream-key"
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_secret_invalid"):
        decrypt_api_key(ciphertext, revision=8, encoded_key=_key())


def test_model_endpoint_rejects_private_resolution_and_accepts_only_origin_or_v1(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))],
    )
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_endpoint_forbidden"):
        validate_endpoint("https://gateway.example/v1")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    endpoint = validate_endpoint("https://gateway.example/v1/")
    assert endpoint.base_url == "https://gateway.example"
    assert endpoint.ips == ("8.8.8.8",)
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_endpoint_must_be_origin"):
        validate_endpoint("https://gateway.example/openai/v1")


def test_model_endpoint_allows_explicit_internal_host_but_not_plain_http_public(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 8080))],
    )
    endpoint = validate_endpoint(
        "http://newapi.internal:8080/v1",
        allowed_internal_hosts="newapi.internal",
    )
    assert endpoint.base_url == "http://newapi.internal:8080"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))],
    )
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_https_required"):
        validate_endpoint("http://gateway.example")


def test_model_endpoint_preserves_non_default_ports_and_brackets_ipv6(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))],
    )
    assert validate_endpoint("https://gateway.example:80").base_url == "https://gateway.example:80"
    assert validate_endpoint(
        "http://gateway.example:443",
        allowed_internal_hosts="gateway.example",
    ).base_url == "http://gateway.example:443"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0))
        ],
    )
    assert validate_endpoint("https://[2001:4860:4860::8888]:8443").base_url == (
        "https://[2001:4860:4860::8888]:8443"
    )


def test_slash_bearing_upstream_model_gets_stable_platform_identity() -> None:
    first = platform_model_id("openai/gpt-5")
    assert first == platform_model_id("openai/gpt-5")
    assert first.startswith("mdl_")
    assert "/" not in first
    assert platform_model_id("gpt-5.1") == "gpt-5.1"
    assert platform_model_id("mdl_public") != "mdl_public"


def test_upstream_model_discovery_preserves_raw_names_and_rejects_invalid_payloads() -> None:
    response = UpstreamResponse(
        status=200,
        content_type="application/json",
        body=b'{"data":[{"id":"openai/gpt-5"},{"id":"claude-4"},{"id":"openai/gpt-5"}]}',
    )
    assert parse_model_ids(response) == ["openai/gpt-5", "claude-4"]

    with pytest.raises(ModelUpstreamError, match="model_connection_catalog_invalid"):
        parse_model_ids(UpstreamResponse(status=200, content_type="text/html", body=b"no"))


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _ActivationConnection:
    def __init__(self, *, existing_rows=None):
        self.calls = []
        self.existing_rows = existing_rows or []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "coalesce(max(revision), 0) + 1" in normalized:
            return _Cursor(row={"revision": 3})
        if normalized.startswith("select model_id, upstream_model_id from model_catalog_entries"):
            return _Cursor(rows=self.existing_rows)
        if normalized.startswith("select model_id, upstream_model_id, display_name, provider"):
            now = datetime.now(timezone.utc)
            return _Cursor(
                rows=[
                    {
                        "model_id": "mdl_public",
                        "upstream_model_id": "openai/gpt-5",
                        "display_name": "openai/gpt-5",
                        "provider": "compatible",
                        "enabled": False,
                        "upstream_available": True,
                        "is_default": False,
                        "display_order": 1,
                        "last_seen_revision": 3,
                        "last_seen_at": now,
                    }
                ]
            )
        return _Cursor()


@pytest.mark.asyncio
async def test_sync_rejects_platform_identity_collision_before_mutating_enabled_default_entry() -> None:
    platform_id = platform_model_id("legacy/provider-model")
    conn = _ActivationConnection(
        existing_rows=[
            {
                "model_id": platform_id,
                "upstream_model_id": "different/provider-model",
                "enabled": True,
                "is_default": True,
            }
        ]
    )

    with pytest.raises(ValueError, match="model_catalog_identity_collision"):
        await activate_connection_and_sync(
            conn,
            base_url="https://gateway.example",
            api_key="activation-secret",
            key_fingerprint="0123456789abcdef",
            encryption_key=_key(),
            actor_user_id="admin-user",
            upstream_model_ids=["legacy/provider-model"],
        )

    assert not any(
        "update model_gateway_revisions" in sql
        or "insert into model_gateway_revisions" in sql
        or "update model_catalog_entries" in sql
        or "insert into model_catalog_entries" in sql
        for sql, _params in conn.calls
    )


    conn = _ActivationConnection()

    revision, models = await activate_connection_and_sync(
        conn,
        base_url="https://gateway.example",
        api_key="activation-secret",
        key_fingerprint="0123456789abcdef",
        encryption_key=_key(),
        actor_user_id="admin-user",
        upstream_model_ids=["openai/gpt-5"],
    )

    assert revision == 3
    assert models[0]["value"] == "openai/gpt-5"
    assert models[0]["enabled"] is False
    assert "activation-secret" not in repr(conn.calls)
    insert_revision = next(
        params for sql, params in conn.calls if "insert into model_gateway_revisions" in sql
    )
    assert isinstance(insert_revision[2], bytes)
    upsert_model = next(
        params for sql, params in conn.calls if "insert into model_catalog_entries" in sql
    )
    assert upsert_model[:4] == (
        platform_model_id("openai/gpt-5"),
        "openai/gpt-5",
        "openai/gpt-5",
        "compatible",
    )


class _ResolveConnection:
    def __init__(self, *, row):
        self.row = row
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor(row=self.row)


class _RunConnection:
    def __init__(
        self,
        *,
        lease_attempt_id: str,
        lease_status: str = "active",
        released: bool = False,
        unexpired: bool = True,
    ):
        self.lease_attempt_id = lease_attempt_id
        self.lease_status = lease_status
        self.released = released
        self.unexpired = unexpired
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        run_id, attempt_id, model_value = params
        matches_current_lease = (
            run_id == "run_123"
            and attempt_id == self.lease_attempt_id
            and model_value == "openai/gpt-5"
            and self.lease_status == "active"
            and not self.released
            and self.unexpired
        )
        row = None
        if matches_current_lease:
            row = {
                "revision": 4,
                "base_url": "https://gateway.example",
                "api_key_ciphertext": encrypt_api_key(
                    "revision-secret", revision=4, encoded_key=_key()
                ),
                "key_fingerprint": "sha256:example",
            }
        return _Cursor(row=row)


@pytest.mark.asyncio
async def test_run_model_resolution_uses_enabled_available_catalog_and_pins_revision() -> None:
    conn = _ResolveConnection(
        row={
            "connection_revision": 9,
            "model_id": "mdl_public",
            "upstream_model_id": "openai/gpt-5",
        }
    )

    selection = await resolve_run_model(
        conn,
        model_id="mdl_public",
        model_value="openai/gpt-5",
    )

    assert selection is not None
    assert selection.model_id == "mdl_public"
    assert selection.model_value == "openai/gpt-5"
    assert selection.connection_revision == 9
    assert len(conn.calls) == 1
    model_query, params = conn.calls[0]
    assert "catalog.enabled = true" in model_query
    assert "catalog.upstream_available = true" in model_query
    assert "catalog.last_seen_revision = active_gateway.revision" in model_query
    assert params == ("mdl_public", "mdl_public", "openai/gpt-5", "openai/gpt-5")


@pytest.mark.asyncio
async def test_run_model_resolution_preserves_legacy_path_until_control_plane_is_active() -> None:
    conn = _ResolveConnection(row=None)
    assert await resolve_run_model(conn, model_id="legacy", model_value="legacy") is None
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_run_model_resolution_rejects_active_but_unavailable_catalog_entry() -> None:
    conn = _ResolveConnection(row={"connection_revision": 9, "model_id": None, "upstream_model_id": None})

    with pytest.raises(ValueError, match="model_id_not_available"):
        await resolve_run_model(conn, model_id="missing", model_value="missing")

    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_run_connection_lookup_requires_active_status_and_exact_model_value() -> None:
    conn = _RunConnection(lease_attempt_id="attempt_123")

    connection = await get_run_connection(
        conn,
        run_id="run_123",
        attempt_id="attempt_123",
        model_value="openai/gpt-5",
        encryption_key=_key(),
    )

    assert connection is not None and connection.api_key == "revision-secret"
    sql, params = conn.calls[0]
    assert "runs.status in ('queued', 'running')" in sql
    assert "runs.model_gateway_revision" in sql
    assert "sandbox_leases.attempt_id = %s" in sql
    assert "sandbox_leases.status = 'active'" in sql
    assert "sandbox_leases.released_at is null" in sql
    assert "sandbox_leases.expires_at > now()" in sql
    assert params == ("run_123", "attempt_123", "openai/gpt-5")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempt_id", "lease_attempt_id", "lease_status", "released", "unexpired", "label"),
    [
        ("", "attempt_123", "active", False, True, "missing attempt header"),
        ("attempt_wrong", "attempt_123", "active", False, True, "wrong attempt"),
        ("attempt_123", "attempt_123", "released", True, True, "stale released lease"),
        ("attempt_123", "attempt_123", "active", False, False, "expired lease"),
    ],
)
async def test_run_connection_lookup_fails_closed_without_current_exact_attempt(
    attempt_id: str,
    lease_attempt_id: str,
    lease_status: str,
    released: bool,
    unexpired: bool,
    label: str,
) -> None:
    conn = _RunConnection(
        lease_attempt_id=lease_attempt_id,
        lease_status=lease_status,
        released=released,
        unexpired=unexpired,
    )

    assert (
        await get_run_connection(
            conn,
            run_id="run_123",
            attempt_id=attempt_id,
            model_value="openai/gpt-5",
            encryption_key=_key(),
        )
        is None
    ), label
    sql, params = conn.calls[0]
    assert "join sandbox_leases" in sql
    assert "sandbox_leases.run_id = runs.id" in sql
    assert params == ("run_123", attempt_id, "openai/gpt-5")


def test_runtime_proxy_streams_incrementally_and_replaces_untrusted_credentials(monkeypatch) -> None:
    endpoint = type(
        "Endpoint",
        (),
        {
            "base_url": "https://gateway.example",
            "scheme": "https",
            "hostname": "gateway.example",
            "port": 443,
            "ips": ("8.8.8.8",),
        },
    )()
    observed = {}

    class Response:
        status = 200

        def __init__(self):
            self.chunks = iter((b"first", b"second", b""))
            self.closed = False

        def getheader(self, _name):
            return "text/event-stream"

        def read(self, _size):
            return next(self.chunks)

        def close(self):
            self.closed = True

    response = Response()

    class Connection:
        def request(self, method, path, *, body, headers):
            observed.update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            return response

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr(client, "validate_endpoint", lambda *_args, **_kwargs: endpoint)
    monkeypatch.setattr(client, "_connection", lambda *_args, **_kwargs: Connection())

    stream = open_upstream_stream(
        base_url=endpoint.base_url,
        allowed_internal_hosts="",
        api_key="real-secret",
        method="POST",
        path="/v1/chat/completions",
        provider="openai",
        body=b'{}',
        headers={"authorization": "Bearer sandbox-secret", "content-type": "application/json"},
    )

    body = stream.body()
    assert next(body) == b"first"
    assert observed["headers"]["authorization"] == "Bearer real-secret"
    assert "sandbox-secret" not in repr(observed)
    assert next(body) == b"second"
    with pytest.raises(StopIteration):
        next(body)
    assert response.closed and observed["closed"]


def test_runtime_proxy_rejects_redirect_without_following(monkeypatch) -> None:
    endpoint = type(
        "Endpoint",
        (),
        {
            "base_url": "https://gateway.example",
            "scheme": "https",
            "hostname": "gateway.example",
            "port": 443,
            "ips": ("8.8.8.8",),
        },
    )()

    class Response:
        status = 307

        @staticmethod
        def close():
            return None

    class Connection:
        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            return Response()

        def close(self):
            return None

    monkeypatch.setattr(client, "validate_endpoint", lambda *_args, **_kwargs: endpoint)
    monkeypatch.setattr(client, "_connection", lambda *_args, **_kwargs: Connection())

    with pytest.raises(ModelUpstreamError, match="model_upstream_redirect_rejected"):
        open_upstream_stream(
            base_url=endpoint.base_url,
            allowed_internal_hosts="",
            api_key="secret",
            method="POST",
            path="/v1/chat/completions",
            provider="openai",
        )


@pytest.mark.asyncio
async def test_internal_runtime_proxy_resolves_run_revision_and_streams_response(monkeypatch) -> None:
    captured = {}

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_run_connection(_conn, *, run_id, attempt_id, model_value, encryption_key):
        assert (run_id, attempt_id, model_value, encryption_key) == (
            "run-123",
            "attempt-123",
            "openai/gpt-5",
            _key(),
        )
        return SimpleNamespace(
            base_url="https://gateway.example",
            api_key="run-pinned-secret",
        )

    class FakeUpstream:
        status = 200
        content_type = "text/event-stream; charset=utf-8"

        @staticmethod
        def body():
            yield b"data: first\n\n"
            yield b"data: second\n\n"

    def fake_open_stream(**kwargs):
        captured.update(kwargs)
        return FakeUpstream()

    payload = json.dumps({"model": "openai/gpt-5", "messages": []}).encode()
    events = iter([{"type": "http.request", "body": payload, "more_body": False}])

    async def receive():
        return next(events)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ai/internal/model-proxy/openai/v1/chat/completions",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-ai-platform-internal-token", b"internal-token"),
                (b"x-ai-platform-attempt-id", b"attempt-123"),
                (b"authorization", b"Bearer model-proxy-internal"),
            ],
            "server": ("api", 8020),
        },
        receive,
    )
    monkeypatch.setattr(model_routes, "transaction", fake_transaction)
    monkeypatch.setattr(model_routes, "get_run_connection", fake_run_connection)
    monkeypatch.setattr(
        model_routes,
        "get_settings",
        lambda: SimpleNamespace(
            model_proxy_internal_token="internal-token",
            model_connection_encryption_key=_key(),
            model_connection_allowed_internal_hosts="",
        ),
    )
    monkeypatch.setattr(model_routes, "open_upstream_stream", fake_open_stream)

    response = await model_routes.proxy_model_request(
        "openai",
        "v1/chat/completions",
        request,
        x_ai_platform_run_id="run-123",
        x_ai_platform_attempt_id="attempt-123",
        x_ai_platform_internal_token="internal-token",
    )
    streamed = b"".join([chunk async for chunk in response.body_iterator])

    assert streamed == b"data: first\n\ndata: second\n\n"
    assert captured["api_key"] == "run-pinned-secret"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["body"] == payload
    outbound = client._outbound_headers(
        endpoint=type(
            "Endpoint",
            (),
            {"hostname": "gateway.example", "port": 443},
        )(),
        provider="openai",
        api_key=captured["api_key"],
        headers=captured["headers"],
    )
    assert outbound["authorization"] == "Bearer run-pinned-secret"
    assert "x-ai-platform-internal-token" not in outbound


@pytest.mark.asyncio
async def test_internal_runtime_proxy_bounds_streamed_request_before_database(monkeypatch) -> None:
    database_accessed = False

    @asynccontextmanager
    async def fake_transaction():
        nonlocal database_accessed
        database_accessed = True
        yield object()

    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/proxy",
            "query_string": b"",
            "headers": [],
            "server": ("api", 8020),
        },
        receive,
    )
    monkeypatch.setattr(model_routes, "transaction", fake_transaction)
    monkeypatch.setattr(
        model_routes,
        "get_settings",
        lambda: SimpleNamespace(model_proxy_internal_token="internal-token"),
    )

    with pytest.raises(HTTPException) as raised:
        await model_routes.proxy_model_request(
            "openai",
            "v1/chat/completions",
            request,
            x_ai_platform_run_id="run-123",
            x_ai_platform_attempt_id="attempt-123",
            x_ai_platform_internal_token="internal-token",
        )

    assert raised.value.status_code == 413
    assert database_accessed is False

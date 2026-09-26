from __future__ import annotations

import base64
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.execution.application import model_selection
from app.execution.application.model_control_plane import (
    ModelControlPlaneService,
    _runtime_proxy_headers,
)
from app.execution.domain.model_catalog import normalize_model_token_limits, platform_model_id
from app.execution.infrastructure import model_upstream as client
from app.execution.infrastructure.model_management import (
    activate_connection_and_sync,
    get_run_connection,
    publish_models,
    resolve_run_model,
)
from app.execution.infrastructure.model_security import (
    ModelConnectionSecurityError,
    decrypt_api_key,
    encrypt_api_key,
    validate_endpoint,
)
from app.execution.infrastructure.model_upstream import (
    ModelUpstreamError,
    UpstreamResponse,
    open_upstream_stream,
    parse_model_ids,
)
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    callback_token_matches,
    derive_callback_token,
)
from app.execution.transport import model_management as model_routes
from app.runs.infrastructure.postgres import (
    bind_run_model,
    inherit_run_model,
    load_run_model_snapshot,
)


def _key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def _attempt_capability_verifier(secret: str):
    def verify(*, run_id: str, attempt_id: str, provided_capability: str) -> bool:
        return callback_token_matches(
            secret=secret,
            token_id=callback_token_id_for_binding(
                CallbackTokenBinding(run_id=run_id, attempt_id=attempt_id)
            ),
            provided_token=provided_capability,
        )

    return verify


def test_catalog_capacity_pair_and_anthropic_proxy_contract():
    assert normalize_model_token_limits(32000, 2048) == (32000, 2048)
    for pair in ((32000, None), (True, 2048), (0, 2048), (10_000_001, 2048)):
        with pytest.raises(ValueError):
            normalize_model_token_limits(*pair)

    headers = {
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "interleaved-thinking-2025-05-14,claude-code-20250219,claude-code-20250219",
    }
    normalized = _runtime_proxy_headers("anthropic", "v1/messages", headers)
    assert normalized["anthropic-beta"] == "claude-code-20250219,interleaved-thinking-2025-05-14"
    for invalid in (
        {"anthropic-version": "2024-01-01"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "unknown-beta"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219,"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "effort-2025-11-24"},
    ):
        with pytest.raises(PermissionError):
            _runtime_proxy_headers("anthropic", "v1/messages/count_tokens", invalid)


def test_model_transport_maps_missing_write_only_key_to_validation_error() -> None:
    error = model_routes._translate_control_plane_error(
        ValueError("model_connection_api_key_required")
    )

    assert error.status_code == 422
    assert error.detail == "model_connection_api_key_required"
    conflict = model_routes._translate_control_plane_error(
        ValueError("model_catalog_revision_conflict")
    )
    assert (conflict.status_code, conflict.detail) == (
        409, "model_catalog_revision_conflict"
    )
    for code in ("model_capacity_pair_required", "max_input_tokens_invalid", "max_output_tokens_invalid"):
        mapped = model_routes._translate_control_plane_error(ValueError(code))
        assert (mapped.status_code, mapped.detail) == (422, code)
    for budget in (True, 0, 10_000_001, "32000"):
        with pytest.raises(ValidationError):
            model_routes.ModelPublicationEntry(
                id="mdl_gpt", value="openai/gpt-5", display_name="GPT-5",
                enabled=True, is_default=True, order=1,
                max_input_tokens=budget, max_output_tokens=2048,
            )


@pytest.mark.asyncio
async def test_model_discovery_is_read_only_and_publication_rechecks_upstream_identity() -> None:
    upstream_ids = ["openai/gpt-5"]
    publications = []

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    class Repository:
        async def active_connection(self, _conn, **_kwargs):
            return None

        async def connection_projection(self, _conn):
            return {"revision": None}

        async def admin_models(self, _conn):
            return [{
                "id": platform_model_id("openai/gpt-5"), "value": "openai/gpt-5",
                "label": "Existing GPT", "provider": "compatible", "enabled": True,
                "available": False, "is_default": True, "order": 9,
                "max_input_tokens": 32000, "max_output_tokens": 2048,
            }]

        async def publish_models(self, _conn, **kwargs):
            publications.append(kwargs)
            return 1, kwargs["models"]

    class Security:
        def validate(self, base_url, **_kwargs):
            return SimpleNamespace(base_url=base_url)

        def fingerprint(self, _api_key):
            return "synthetic-fingerprint"

    class Upstream:
        def request(self, **_kwargs):
            return b"synthetic-catalog"

        def parse_model_ids(self, _body):
            return list(upstream_ids)

    service = ModelControlPlaneService(
        transaction_factory=fake_transaction,
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key=_key(),
            model_connection_allowed_internal_hosts="",
        ),
        repository=Repository(), security=Security(), upstream=Upstream(),
        attempt_capability_verifier=lambda **_kwargs: True,
    )
    candidate = await service.discover(
        base_url="https://gateway.example", api_key="synthetic-secret"
    )
    assert publications == []
    assert candidate["models"][0]["label"] == "Existing GPT"
    assert candidate["models"][0]["order"] == 1
    entry = {
        "id": candidate["models"][0]["id"], "value": "openai/gpt-5",
        "display_name": "GPT-5", "enabled": True, "is_default": True,
        "order": 1, "max_input_tokens": 32000, "max_output_tokens": 2048,
    }
    upstream_ids[:] = ["different-model"]
    with pytest.raises(ValueError, match="model_catalog_discovery_changed"):
        await service.publish(
            base_url="https://gateway.example", api_key="synthetic-secret",
            expected_revision=None, models=[entry], actor_user_id="admin-user",
        )
    assert publications == []
    upstream_ids[:] = ["openai/gpt-5"]
    published = await service.publish(
        base_url="https://gateway.example", api_key="synthetic-secret",
        expected_revision=None, models=[entry], actor_user_id="admin-user",
    )
    assert published["revision"] == 1
    assert publications[0]["models"] == [entry]


def test_model_transport_router_uses_bootstrap_auth_dependencies(monkeypatch) -> None:
    principal = SimpleNamespace(user_id="admin-user")
    authorized = {"value": True}
    calls: list[tuple[str, dict[str, object]]] = []

    async def require_principal() -> SimpleNamespace:
        return principal

    class _Service:
        async def admin_projection(self) -> dict[str, object]:
            return {"connection": None, "models": []}

        async def discover(self, **kwargs) -> dict[str, object]:
            calls.append(("discover", kwargs))
            return {"connection": None, "base_url": kwargs["base_url"], "models": []}

        async def publish(self, **kwargs) -> dict[str, object]:
            calls.append(("publish", kwargs))
            return {"connection": {"revision": 2}, "models": []}

    monkeypatch.setattr(model_routes, "configured_model_control_plane", lambda: _Service())
    app = FastAPI()
    app.include_router(
        model_routes.build_model_management_router(
            principal_dependency=require_principal,
            is_admin=lambda candidate: authorized["value"] and candidate is principal,
        ),
        prefix="/api/ai",
    )
    entry = {
        "id": "mdl_gpt", "value": "openai/gpt-5", "display_name": "GPT-5",
        "enabled": True, "is_default": True, "order": 1,
        "max_input_tokens": 32000, "max_output_tokens": 2048,
    }
    with TestClient(app) as client:
        accepted = client.get("/api/ai/admin/models")
        found = client.post("/api/ai/admin/models/discover", json={
            "base_url": "https://gateway.example", "credential": "synthetic-secret",
        })
        published = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "credential": "synthetic-secret",
            "expected_revision": 1, "models": [entry],
        })
        invalid = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "models": [{**entry, "max_input_tokens": True}],
        })
        forbidden_field = client.post("/api/ai/admin/models/discover", json={
            "base_url": "https://gateway.example", "api_key": "synthetic-secret",
        })
        unknown_key = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "credentail": "synthetic-secret",
            "models": [entry],
        })
        retired = client.put("/api/ai/admin/models/connection", json={
            "base_url": "https://gateway.example", "credential": "synthetic-secret",
        })
        authorized["value"] = False
        denied = client.post("/api/ai/admin/models/discover", json={
            "base_url": "https://gateway.example", "credential": "synthetic-secret",
        })

    assert accepted.status_code == 200
    assert found.status_code == 200
    assert published.status_code == 200
    assert invalid.status_code == 422
    assert forbidden_field.status_code == 422
    assert unknown_key.json() == {"detail": "model_publication_request_invalid"}
    assert retired.status_code == 404
    assert denied.status_code == 403
    assert all("synthetic-secret" not in response.text for response in (
        found, published, invalid, forbidden_field, unknown_key, retired, denied
    ))
    assert calls == [
        ("discover", {"base_url": "https://gateway.example", "api_key": "synthetic-secret"}),
        ("publish", {
            "base_url": "https://gateway.example", "api_key": "synthetic-secret",
            "expected_revision": 1, "models": [entry], "actor_user_id": "admin-user",
        }),
    ]


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


class _RunModelMutationConnection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor(row=next(self.rows))


@pytest.mark.asyncio
async def test_load_run_model_snapshot_locks_exact_run_for_dispatch() -> None:
    conn = _RunModelMutationConnection(
        [
            {
                "model_id": "model-public",
                "model_value": "openai/gpt-5",
                "model_gateway_revision": 7,
                "max_input_tokens": 32000,
                "max_output_tokens": 2048,
            }
        ]
    )

    snapshot = await load_run_model_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    sql, params = conn.calls[0]
    assert "for update" in sql.lower()
    assert params == ("tenant-a", "run-a")
    assert snapshot == {
        "model_id": "model-public",
        "model_value": "openai/gpt-5",
        "model_gateway_revision": 7,
        "max_input_tokens": 32000,
        "max_output_tokens": 2048,
    }


@pytest.mark.asyncio
async def test_bind_run_model_persists_execution_admitted_snapshot_on_exact_run() -> None:
    conn = _RunModelMutationConnection([{"id": "run-child"}])

    await bind_run_model(
        conn,
        tenant_id="tenant-a",
        run_id="run-child",
        model_id="model-public",
        model_value="openai/gpt-5",
        connection_revision=7,
        max_input_tokens=32000,
        max_output_tokens=2048,
    )

    sql, params = conn.calls[0]
    assert "status = 'queued'" in sql
    assert "model_id is null" in sql
    assert "returning id" in sql
    assert params == (
        "model-public",
        "openai/gpt-5",
        7,
        32000,
        2048,
        "tenant-a",
        "run-child",
    )


@pytest.mark.asyncio
async def test_bind_run_model_rejects_missing_capacity_before_write() -> None:
    conn = _RunModelMutationConnection([{"id": "run-child"}])
    with pytest.raises(ValueError, match="run_model_capacity_invalid"):
        await bind_run_model(
            conn, tenant_id="tenant-a", run_id="run-child", model_id="model-public",
            model_value="openai/gpt-5", connection_revision=7,
            max_input_tokens=None, max_output_tokens=2048,
        )
    assert not conn.calls


@pytest.mark.asyncio
async def test_bind_run_model_rejects_invalid_revision_before_write() -> None:
    for revision in (True, 0, -1, "7"):
        conn = _RunModelMutationConnection([{"id": "run-child"}])
        with pytest.raises(ValueError, match="run_model_binding_invalid"):
            await bind_run_model(
                conn,
                tenant_id="tenant-a",
                run_id="run-child",
                model_id="model-public",
                model_value="openai/gpt-5",
                connection_revision=revision,
                max_input_tokens=32000,
                max_output_tokens=2048,
            )
        assert not conn.calls


@pytest.mark.asyncio
async def test_bind_run_model_fails_when_run_snapshot_update_does_not_match() -> None:
    conn = _RunModelMutationConnection([None])

    with pytest.raises(ValueError, match="run_model_binding_invalid"):
        await bind_run_model(
            conn,
            tenant_id="tenant-a",
            run_id="run-child",
            model_id="model-public",
            model_value="openai/gpt-5",
            connection_revision=7,
            max_input_tokens=32000,
            max_output_tokens=2048,
        )


@pytest.mark.asyncio
async def test_inherit_run_model_requires_exact_copy_relation_and_updates_child() -> None:
    conn = _RunModelMutationConnection(
        [
            {
                "model_id": "model-public",
                "model_value": "openai/gpt-5",
                "model_gateway_revision": 7,
                "max_input_tokens": 32000,
                "max_output_tokens": 2048,
            },
            {
                "status": "queued",
                "copied_from_run_id": "run-source",
                "model_id": None,
                "model_value": None,
                "model_gateway_revision": None,
            },
            {"id": "run-child"},
        ]
    )

    await inherit_run_model(
        conn,
        tenant_id="tenant-a",
        source_run_id="run-source",
        child_run_id="run-child",
    )

    assert "for update" in conn.calls[0][0].lower()
    assert "copied_from_run_id" in conn.calls[1][0]
    update_sql, update_params = conn.calls[2]
    assert "returning id" in update_sql
    assert update_params == (
        "model-public",
        "openai/gpt-5",
        7,
        32000,
        2048,
        "tenant-a",
        "run-child",
    )


@pytest.mark.parametrize("operation", ["copy", "retry", "resume"])
@pytest.mark.asyncio
async def test_inherit_run_model_rejects_legacy_source_without_capacity(operation: str) -> None:
    conn = _RunModelMutationConnection(
        [
            {"model_id": "model-public", "model_value": "openai/gpt-5", "model_gateway_revision": 7,
             "max_input_tokens": None, "max_output_tokens": None},
            {"status": "queued", "copied_from_run_id": "run-source", "model_id": None,
             "model_value": None, "model_gateway_revision": None},
        ]
    )
    with pytest.raises(ValueError, match="run_model_capacity_missing"):
        await inherit_run_model(conn, tenant_id="tenant-a", source_run_id="run-source", child_run_id=f"run-{operation}")
    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_inherit_run_model_rejects_child_from_a_different_source() -> None:
    conn = _RunModelMutationConnection(
        [
            {
                "model_id": "model-public",
                "model_value": "openai/gpt-5",
                "model_gateway_revision": 7,
            },
            {
                "status": "queued",
                "copied_from_run_id": "run-other",
                "model_id": None,
                "model_value": None,
                "model_gateway_revision": None,
            },
        ]
    )

    with pytest.raises(ValueError, match="run_model_child_source_mismatch"):
        await inherit_run_model(
            conn,
            tenant_id="tenant-a",
            source_run_id="run-source",
            child_run_id="run-child",
        )

    assert len(conn.calls) == 2


class _ActivationConnection:
    def __init__(self, *, existing_rows=None, active_revision=2):
        self.calls = []
        self.existing_rows = existing_rows or []
        self.active_revision = active_revision

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("select revision from model_gateway_revisions where active"):
            return _Cursor(row={"revision": self.active_revision} if self.active_revision else None)
        if "coalesce(max(revision), 0) + 1" in normalized:
            return _Cursor(row={"revision": 3})
        if normalized.startswith("select model_id, upstream_model_id from model_catalog_entries"):
            return _Cursor(rows=self.existing_rows)
        if normalized.startswith("update model_catalog_entries set display_name"):
            return _Cursor(row={"model_id": params[6]})
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
async def test_connection_activation_rejects_platform_identity_collision_before_mutating_enabled_default_entry() -> None:
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


@pytest.mark.asyncio
async def test_publication_checks_revision_before_changing_gateway_and_writes_one_catalog() -> None:
    entry = {
        "id": platform_model_id("openai/gpt-5"), "value": "openai/gpt-5",
        "display_name": "GPT-5", "enabled": True, "is_default": True,
        "order": 1, "max_input_tokens": 32000, "max_output_tokens": 2048,
    }
    kwargs = {
        "models": [entry], "base_url": "https://gateway.example",
        "api_key": "activation-secret", "key_fingerprint": "0123456789abcdef",
        "encryption_key": _key(), "actor_user_id": "admin-user",
        "upstream_model_ids": ["openai/gpt-5"],
    }
    conn = _ActivationConnection()
    with pytest.raises(ValueError, match="model_catalog_revision_conflict"):
        await publish_models(conn, expected_revision=1, **kwargs)
    assert not any("insert into model_gateway_revisions" in sql for sql, _ in conn.calls)

    conn = _ActivationConnection()
    revision, _ = await publish_models(conn, expected_revision=2, **kwargs)
    assert revision == 3
    applied = [params for sql, params in conn.calls
               if "update model_catalog_entries" in sql and "set display_name" in sql]
    assert applied == [("GPT-5", True, True, 1, 32000, 2048,
                        platform_model_id("openai/gpt-5"), "openai/gpt-5", 3)]


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
        run_id, attempt_id, lease_attempt_id, model_value = params
        matches_current_lease = (
            run_id == "run_123"
            and attempt_id == self.lease_attempt_id
            and lease_attempt_id == self.lease_attempt_id
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
                "max_input_tokens": 32000,
                "max_output_tokens": 2048,
                "conversation_mode": "native_resume",
            }
        return _Cursor(row=row)


@pytest.mark.asyncio
async def test_run_model_resolution_uses_enabled_available_catalog_and_pins_revision() -> None:
    conn = _ResolveConnection(
        row={
            "connection_revision": 9,
            "model_id": "mdl_public",
            "upstream_model_id": "openai/gpt-5",
            "max_input_tokens": 32000,
            "max_output_tokens": 2048,
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
    assert (selection.max_input_tokens, selection.max_output_tokens) == (32000, 2048)
    assert len(conn.calls) == 2
    assert "pg_advisory_xact_lock_shared" in conn.calls[0][0]
    model_query, params = conn.calls[1]
    assert "catalog.enabled = true" in model_query
    assert "catalog.upstream_available = true" in model_query
    assert "catalog.last_seen_revision = active_gateway.revision" in model_query
    assert params == ("mdl_public", "mdl_public", "openai/gpt-5", "openai/gpt-5")


@pytest.mark.asyncio
async def test_run_model_resolution_without_gateway_returns_none() -> None:
    conn = _ResolveConnection(row=None)
    assert await resolve_run_model(conn, model_id="mdl_public", model_value="openai/gpt-5") is None
    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_chat_model_resolution_fails_without_governed_connection() -> None:
    conn = _ResolveConnection(row=None)
    with pytest.raises(ValueError, match="model_connection_not_configured"):
        await model_selection.resolve_chat_model_selection(
            conn, selection=None, resolve_governed_model=resolve_run_model,
        )
    assert "pg_advisory_xact_lock_shared" in conn.calls[0][0]


@pytest.mark.asyncio
async def test_run_model_resolution_rejects_missing_capacity() -> None:
    conn = _ResolveConnection(row={
        "connection_revision": 9,
        "model_id": "mdl_public",
        "upstream_model_id": "openai/gpt-5",
        "max_input_tokens": None,
        "max_output_tokens": None,
    })
    with pytest.raises(ValueError, match="model_capacity_missing"):
        await resolve_run_model(conn, model_id="mdl_public", model_value="openai/gpt-5")


@pytest.mark.asyncio
async def test_run_model_resolution_rejects_active_but_unavailable_catalog_entry() -> None:
    conn = _ResolveConnection(row={"connection_revision": 9, "model_id": None, "upstream_model_id": None})

    with pytest.raises(ValueError, match="model_id_not_available"):
        await resolve_run_model(conn, model_id="missing", model_value="missing")

    assert len(conn.calls) == 2


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
    assert "sandbox_leases.run_id = runs.id" in sql
    assert "sandbox_leases.tenant_id = runs.tenant_id" in sql
    assert "sandbox_leases.attempt_id = %s" in sql
    assert "sandbox_leases.lease_payload_json ->> 'owner_generation' = run_attempts.owner_generation::text" in sql
    assert "sandbox_leases.status = 'active'" in sql
    assert "sandbox_leases.released_at is null" in sql
    assert "sandbox_leases.expires_at > now()" in sql
    assert "run_attempts.execution_spec_schema_version = 'ai-platform.execution-spec.v2'" in sql
    assert "run_attempts.execution_spec_json->>'model_max_input_tokens' = runs.max_input_tokens::text" in sql
    assert connection.conversation_mode == "native_resume"
    assert params == ("run_123", "attempt_123", "attempt_123", "openai/gpt-5")


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
    assert "sandbox_leases.tenant_id = runs.tenant_id" in sql
    assert params == ("run_123", attempt_id, attempt_id, "openai/gpt-5")


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
async def test_model_proxy_transport_rejects_duplicate_anthropic_headers_before_service(monkeypatch) -> None:
    class ForbiddenService:
        async def proxy(self, **_kwargs):
            raise AssertionError("duplicate header reached model service")

    monkeypatch.setattr(model_routes, "configured_model_control_plane", lambda: ForbiddenService())
    for duplicate in (b"anthropic-version", b"anthropic-beta"):
        async def receive():
            return {"type": "http.request", "body": b'{}', "more_body": False}

        request = Request(
            {
                "type": "http", "method": "POST", "path": "/api/ai/internal/model-proxy/anthropic/v1/messages",
                "query_string": b"beta=true", "headers": [(duplicate, b"value"), (duplicate, b"value")],
                "server": ("api", 8020),
            }, receive,
        )
        with pytest.raises(HTTPException) as rejected:
            await model_routes.proxy_model_request(
                "anthropic", "v1/messages", request,
                x_ai_platform_run_id="run-a", x_ai_platform_attempt_id="attempt-a",
                x_ai_platform_internal_token="synthetic-internal",
                x_ai_platform_model_authorization="", x_ai_platform_model_api_key="",
            )
        assert (rejected.value.status_code, rejected.value.detail) == (403, "model_proxy_header_duplicate")


@pytest.mark.asyncio
async def test_anthropic_beta_query_is_forwarded_only_on_fixed_allowed_paths():
    captured = {}

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def connection(_conn, **_kwargs):
        return SimpleNamespace(
            base_url="https://gateway.example", api_key="synthetic-key",
            max_input_tokens=32000, max_output_tokens=2048, conversation_mode="empty_start",
        )

    class FakeStream:
        status = 200
        content_type = "application/json"

        def body(self):
            return iter([b"{}"])

    def open_stream(**kwargs):
        captured.update(kwargs)
        return FakeStream()

    def request(**kwargs):
        captured["count"] = kwargs
        return SimpleNamespace(status=200, body=b'{"input_tokens": 12}')

    service = ModelControlPlaneService(
        transaction_factory=fake_transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="synthetic-internal",
            model_connection_encryption_key="synthetic-encryption",
            model_connection_allowed_internal_hosts="",
        ),
        repository=SimpleNamespace(run_connection=connection),
        security=SimpleNamespace(),
        upstream=SimpleNamespace(open_stream=open_stream, request=request),
        attempt_capability_verifier=lambda **_kwargs: True,
    )
    fields = {
        "body": b'{"model":"model-a","max_tokens":512,"messages":[]}', "run_id": "run-a", "attempt_id": "attempt-a",
        "internal_token": "synthetic-internal", "model_proxy_capability": "synthetic-capability",
    }
    await service.proxy(
        provider="anthropic", upstream_path="v1/messages", query="beta=true",
        headers={"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219"},
        **fields,
    )
    assert captured["query"] == "beta=true"
    assert captured["headers"]["anthropic-beta"] == "claude-code-20250219"
    assert captured["path"] == "/v1/messages"
    for invalid_query in ("Beta=true", "beta=true&beta=true", "beta%3Dtrue"):
        captured.clear()
        with pytest.raises(PermissionError, match="model_proxy_query_not_allowed"):
            await service.proxy(
                provider="anthropic", upstream_path="v1/messages", query=invalid_query,
                headers={"anthropic-version": "2023-06-01"}, **fields,
            )
        assert captured == {}

    captured.clear()
    count_response = await service.proxy(
        provider="anthropic", upstream_path="v1/messages/count_tokens", query="beta=true",
        headers={"anthropic-version": "2023-06-01", "anthropic-beta": "token-counting-2024-11-01"},
        **{**fields, "body": b'{"model":"model-a","messages":[]}'},
    )
    assert captured["count"]["path"] == "/v1/messages/count_tokens"
    assert count_response.status == 200
    assert json.loads(b"".join(count_response.body)) == {"input_tokens": 12}


@pytest.mark.asyncio
async def test_anthropic_messages_delegate_input_capacity_to_claude():
    forwarded = []
    mode = "empty_start"

    @asynccontextmanager
    async def transaction():
        yield object()

    async def connection(_conn, **_kwargs):
        return SimpleNamespace(
            base_url="https://gateway.example", api_key="synthetic-key",
            max_input_tokens=32_000, max_output_tokens=2048, conversation_mode=mode,
        )

    def forbidden_count(**_kwargs):
        raise AssertionError("platform must not count Claude input tokens")

    def forward(**kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(
            status=200,
            content_type="application/json",
            body=lambda: iter([b"{}"]),
        )

    service = ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="synthetic-internal",
            model_connection_encryption_key="synthetic-encryption",
            model_connection_allowed_internal_hosts="",
        ),
        repository=SimpleNamespace(run_connection=connection),
        security=SimpleNamespace(),
        upstream=SimpleNamespace(request=forbidden_count, open_stream=forward),
        attempt_capability_verifier=lambda **_kwargs: True,
    )
    fields = dict(
        provider="anthropic", upstream_path="v1/messages", query="beta=true",
        headers={"anthropic-version": "2023-06-01"}, run_id="run-a",
        attempt_id="attempt-a", internal_token="synthetic-internal",
        model_proxy_capability="synthetic-capability",
    )
    payload = {
        "model": "model-a", "system": "current policy",
        "messages": [{"role": "user", "content": "x" * 40_000}],
        "tools": [{"name": "Read"}], "thinking": {"type": "disabled"},
        "stream": True, "max_tokens": 512,
    }
    for mode in ("empty_start", "native_resume"):
        response = await service.proxy(body=json.dumps(payload).encode(), **fields)
        assert response.status == 200
    assert len(forwarded) == 2
    assert all(call["path"] == "/v1/messages" for call in forwarded)

    mode = "platform_bootstrap"
    with pytest.raises(PermissionError, match="model_proxy_conversation_mode_invalid"):
        await service.proxy(body=json.dumps(payload).encode(), **fields)

    mode = "empty_start"
    for output in (True, 2049, 0):
        with pytest.raises(ValueError, match="model_proxy_max_tokens_invalid"):
            await service.proxy(
                body=json.dumps({**payload, "max_tokens": output}).encode(),
                **fields,
            )
    assert len(forwarded) == 2


@pytest.mark.asyncio
async def test_anthropic_count_tokens_404_uses_bounded_local_fallback_only():
    forwarded = []

    @asynccontextmanager
    async def transaction():
        yield object()

    async def connection(_conn, **_kwargs):
        return SimpleNamespace(
            base_url="https://gateway.example", api_key="synthetic-key",
            max_input_tokens=5000, max_output_tokens=2048, conversation_mode="empty_start",
        )

    def missing_count(**_kwargs):
        return SimpleNamespace(status=404, content_type="application/json", body=b'{}')

    def forward(**kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(
            status=200, content_type="application/json", body=lambda: iter([b"{}"]),
        )

    service = ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="synthetic-internal",
            model_connection_encryption_key="synthetic-encryption",
            model_connection_allowed_internal_hosts="",
        ),
        repository=SimpleNamespace(run_connection=connection), security=SimpleNamespace(),
        upstream=SimpleNamespace(request=missing_count, open_stream=forward),
        attempt_capability_verifier=lambda **_kwargs: True,
    )
    fields = dict(
        query="beta=true", headers={"anthropic-version": "2023-06-01"},
        run_id="run-a", attempt_id="attempt-a", internal_token="synthetic-internal",
        model_proxy_capability="synthetic-capability",
    )
    message_body = b'{"model":"model-a","max_tokens":512,"messages":[]}'
    response = await service.proxy(
        provider="anthropic", upstream_path="v1/messages", body=message_body, **fields,
    )
    assert response.status == 200 and len(forwarded) == 1

    count_body = b'{"model":"model-a","messages":[]}'
    response = await service.proxy(
        provider="anthropic", upstream_path="v1/messages/count_tokens",
        body=count_body, **fields,
    )
    assert response.status == 200
    assert json.loads(b"".join(response.body)) == {"input_tokens": len(count_body) + 4096}

    service._upstream = SimpleNamespace(
        request=lambda **_kwargs: SimpleNamespace(status=200, body=b'{"input_tokens":true}'),
        open_stream=forward,
    )
    with pytest.raises(RuntimeError, match="model_proxy_count_tokens_invalid"):
        await service.proxy(
            provider="anthropic", upstream_path="v1/messages/count_tokens",
            body=count_body, **fields,
        )

    for status, error in (
        (401, "model_proxy_count_tokens_failed"),
        (429, "model_proxy_count_tokens_unavailable"),
        (500, "model_proxy_count_tokens_failed"),
    ):
        service._upstream = SimpleNamespace(
            request=lambda status=status, **_kwargs: SimpleNamespace(
                status=status, body=b'{}',
            ),
            open_stream=forward,
        )
        with pytest.raises(RuntimeError, match=error):
            await service.proxy(
                provider="anthropic", upstream_path="v1/messages/count_tokens",
                body=count_body, **fields,
            )

    service._upstream = SimpleNamespace(request=missing_count, open_stream=forward)

    async def low_capacity_connection(_conn, **_kwargs):
        value = await connection(_conn, **_kwargs)
        value.max_input_tokens = 4096
        return value

    service._repository = SimpleNamespace(run_connection=low_capacity_connection)
    response = await service.proxy(
        provider="anthropic", upstream_path="v1/messages", body=message_body, **fields,
    )
    assert response.status == 200
    assert len(forwarded) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("binding_available", [True, False])
async def test_internal_runtime_proxy_resolves_run_revision_and_streams_response(
    monkeypatch, binding_available: bool,
) -> None:
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
        if not binding_available:
            return None
        return SimpleNamespace(
            base_url="https://gateway.example",
            api_key="run-pinned-secret",
            max_input_tokens=32000,
            max_output_tokens=2048,
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

    payload = json.dumps({"model": "openai/gpt-5", "max_tokens": 512, "messages": []}).encode()
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
    callback_secret = "model-proxy-callback-secret"
    callback_token_id = callback_token_id_for_binding(
        CallbackTokenBinding(run_id="run-123", attempt_id="attempt-123")
    )
    model_capability = derive_callback_token(callback_secret, callback_token_id)
    service = ModelControlPlaneService(
        transaction_factory=fake_transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="internal-token",
            sandbox_callback_token=callback_secret,
            model_connection_encryption_key=_key(),
            model_connection_allowed_internal_hosts="",
            openai_base_url="https://legacy.example",
            openai_api_key="legacy-env-secret",
        ),
        repository=SimpleNamespace(run_connection=fake_run_connection),
        security=SimpleNamespace(),
        upstream=SimpleNamespace(open_stream=fake_open_stream),
        attempt_capability_verifier=_attempt_capability_verifier(callback_secret),
    )
    monkeypatch.setattr(model_routes, "configured_model_control_plane", lambda: service)

    response_call = model_routes.proxy_model_request(
        "openai",
        "v1/chat/completions",
        request,
        x_ai_platform_run_id="run-123",
        x_ai_platform_attempt_id="attempt-123",
        x_ai_platform_internal_token="internal-token",
        x_ai_platform_model_authorization=f"Bearer {model_capability}",
        x_ai_platform_model_api_key="",
    )
    if not binding_available:
        with pytest.raises(HTTPException) as caught:
            await response_call
        assert caught.value.status_code == 403
        assert caught.value.detail == "model_proxy_run_binding_invalid"
        assert captured == {}
        return
    response = await response_call
    streamed = b"".join([chunk async for chunk in response.body_iterator])

    assert streamed == b"data: first\n\ndata: second\n\n"
    assert captured["base_url"] == "https://gateway.example"
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
@pytest.mark.parametrize("invalid_capability", ["another-attempt", "令牌"])
async def test_internal_runtime_proxy_rejects_invalid_capability_before_database(
    invalid_capability: str,
) -> None:
    @asynccontextmanager
    async def forbidden_transaction():
        raise AssertionError("cross-attempt capability reached database")
        yield

    callback_secret = "model-proxy-callback-secret"
    if invalid_capability == "another-attempt":
        invalid_capability = derive_callback_token(
            callback_secret,
            callback_token_id_for_binding(
                CallbackTokenBinding(run_id="run-123", attempt_id="attempt-other")
            ),
        )
    service = ModelControlPlaneService(
        transaction_factory=forbidden_transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="internal-token",
            sandbox_callback_token=callback_secret,
        ),
        repository=SimpleNamespace(),
        security=SimpleNamespace(),
        upstream=SimpleNamespace(),
        attempt_capability_verifier=_attempt_capability_verifier(callback_secret),
    )

    with pytest.raises(PermissionError, match="model_proxy_capability_invalid"):
        await service.proxy(
            provider="openai",
            upstream_path="v1/chat/completions",
            query="",
            body=b'{"model":"openai/gpt-5"}',
            headers={},
            run_id="run-123",
            attempt_id="attempt-123",
            internal_token="internal-token",
            model_proxy_capability=invalid_capability,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("internal_token", "model_authorization", "model_api_key", "expected_detail"),
    [
        ("令牌", "", "", "model_proxy_forbidden"),
        ("internal-token", "Bearer 令牌", "密钥", "model_proxy_capability_invalid"),
    ],
)
async def test_internal_runtime_proxy_rejects_malformed_tokens_before_database(
    monkeypatch,
    caplog,
    internal_token: str,
    model_authorization: str,
    model_api_key: str,
    expected_detail: str,
) -> None:
    @asynccontextmanager
    async def forbidden_transaction():
        raise AssertionError("invalid token reached database")
        yield

    service = ModelControlPlaneService(
        transaction_factory=forbidden_transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="internal-token",
        ),
        repository=SimpleNamespace(),
        security=SimpleNamespace(),
        upstream=SimpleNamespace(),
        attempt_capability_verifier=lambda **_kwargs: False,
    )

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"model":"openai/gpt-5"}',
            "more_body": False,
        }

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
    monkeypatch.setattr(
        model_routes,
        "configured_model_control_plane",
        lambda: service,
    )

    with pytest.raises(HTTPException) as captured:
        await model_routes.proxy_model_request(
            "openai",
            "v1/chat/completions",
            request,
            x_ai_platform_run_id="run-123",
            x_ai_platform_attempt_id="attempt-123",
            x_ai_platform_internal_token=internal_token,
            x_ai_platform_model_authorization=model_authorization,
            x_ai_platform_model_api_key=model_api_key,
        )

    assert captured.value.status_code == 403
    assert captured.value.detail == expected_detail
    denial = next(
        record
        for record in reversed(caplog.records)
        if record.name == model_routes.__name__
        and record.getMessage() == "model_proxy_denied"
    )
    assert denial.reason_code == expected_detail
    assert denial.provider == "openai"
    assert denial.upstream_path == "v1/chat/completions"
    assert denial.run_id == "run-123"
    assert denial.attempt_id == "attempt-123"
    serialized_record = repr(denial.__dict__)
    for secret in (
        internal_token,
        model_authorization,
        model_api_key,
        '"model":"openai/gpt-5"',
    ):
        if secret:
            assert secret not in serialized_record


@pytest.mark.asyncio
async def test_internal_runtime_proxy_bounds_streamed_request_before_database(monkeypatch) -> None:
    database_accessed = False

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

    class ForbiddenService:
        async def proxy(self, **_kwargs):
            nonlocal database_accessed
            database_accessed = True
            raise AssertionError("oversized request reached application service")

    monkeypatch.setattr(
        model_routes,
        "configured_model_control_plane",
        lambda: ForbiddenService(),
    )

    with pytest.raises(HTTPException) as raised:
        await model_routes.proxy_model_request(
            "openai",
            "v1/chat/completions",
            request,
            x_ai_platform_run_id="run-123",
            x_ai_platform_attempt_id="attempt-123",
            x_ai_platform_internal_token="internal-token",
            x_ai_platform_model_authorization="",
            x_ai_platform_model_api_key="",
        )

    assert raised.value.status_code == 413
    assert database_accessed is False

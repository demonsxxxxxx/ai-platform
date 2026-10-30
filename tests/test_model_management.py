from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
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
from app.execution.application.model_response_evidence import observe_anthropic_text
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
    load_run_model_snapshot,
)
from app.runs.application.model_reauthorization import trusted_usage_scene


def _key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def test_model_services_share_repository_and_inject_company_directory(monkeypatch):
    from app.bootstrap import model_services
    from app.identity.api import CompanyEmployeeDirectoryService

    captured = {}
    monkeypatch.setattr(model_services, "configure_model_control_plane", lambda service: captured.update(models=service))
    monkeypatch.setattr(model_services, "configure_follow_up_suggestions", lambda service: captured.update(follow_up=service))
    monkeypatch.setattr(model_services, "configure_run_model_snapshots", lambda _service: None)
    monkeypatch.setattr(model_services, "configure_worker_dispatch_run_facts_loader", lambda _loader: None)
    model_services.configure_model_services()

    assert captured["models"]._repository is captured["follow_up"]._repository
    assert isinstance(captured["models"]._employee_directory, CompanyEmployeeDirectoryService)
    assert not hasattr(captured["models"]._repository, "existing_user_ids")
    assert captured["models"]._settings_provider is captured["follow_up"]._settings_provider


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
    assert normalize_model_token_limits(200_000, 2048) == (200_000, 2048)
    # An omitted window takes the platform default; the output cap stays optional.
    assert normalize_model_token_limits(None, 2048) == (100_000, 2048)
    assert normalize_model_token_limits(None, None) == (100_000, None)
    for pair in ((99_999, 2048), (1_000_001, 2048), (True, 2048), ("200000", 2048)):
        with pytest.raises(ValueError):
            normalize_model_token_limits(*pair)
    for output in (0, 10_000_001, True):
        with pytest.raises(ValueError):
            normalize_model_token_limits(100_000, output)

    headers = {
        "anthropic-version": "2023-06-01",
        "anthropic-beta": (
            "claude-code-20250219,interleaved-thinking-2025-05-14,"
            "thinking-token-count-2026-05-13,context-management-2025-06-27,"
            "prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,"
            "mid-conversation-tool-changes-2026-07-01,effort-2025-11-24,"
            "thinking-display-updates-2026-08-18"
        ),
    }
    normalized = _runtime_proxy_headers("anthropic", "v1/messages", headers)
    assert normalized["anthropic-beta"] == (
        "claude-code-20250219,context-management-2025-06-27,"
        "effort-2025-11-24,interleaved-thinking-2025-05-14,"
        "mid-conversation-system-2026-04-07,mid-conversation-tool-changes-2026-07-01,"
        "prompt-caching-scope-2026-01-05,thinking-display-updates-2026-08-18,"
        "thinking-token-count-2026-05-13"
    )
    for invalid in (
        {"anthropic-version": "2024-01-01"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "unknown-beta"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219,"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "effort-2025-11-24"},
    ):
        with pytest.raises(PermissionError):
            _runtime_proxy_headers("anthropic", "v1/messages/count_tokens", invalid)


def test_installed_cli_structured_subtask_beta_is_messages_only():
    headers = {
        "anthropic-version": "2023-06-01",
        "anthropic-beta": (
            "claude-code-20250219,interleaved-thinking-2025-05-14,"
            "thinking-token-count-2026-05-13,context-management-2025-06-27,"
            "mid-conversation-tool-changes-2026-07-01,effort-2025-11-24,"
            "structured-outputs-2025-12-15"
        ),
    }
    normalized = _runtime_proxy_headers("anthropic", "v1/messages", headers)
    assert set(normalized["anthropic-beta"].split(",")) == set(headers["anthropic-beta"].split(","))
    with pytest.raises(PermissionError, match="model_proxy_anthropic_beta_not_allowed"):
        _runtime_proxy_headers("anthropic", "v1/messages/count_tokens", headers)
    for value in ("unknown-beta", "structured-outputs-2025-12-15,unknown-beta", "structured-outputs-2025-12-15,"):
        with pytest.raises(PermissionError, match="model_proxy_anthropic_beta_not_allowed"):
            _runtime_proxy_headers("anthropic", "v1/messages", {**headers, "anthropic-beta": value})


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
    for code in ("model_capacity_pair_required", "auto_compact_window_invalid", "max_output_tokens_invalid"):
        mapped = model_routes._translate_control_plane_error(ValueError(code))
        assert (mapped.status_code, mapped.detail) == (422, code)
    for budget in (True, 0, 99_999, 1_000_001, "200000"):
        with pytest.raises(ValidationError):
            model_routes.ModelPublicationEntry(
                id="mdl_gpt", value="openai/gpt-5", display_name="GPT-5",
                enabled=True, order=1,
                auto_compact_window=budget, max_output_tokens=2048,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["legacy", "supplier-a"])
async def test_model_discovery_is_read_only_and_publication_rechecks_upstream_identity(provider_id) -> None:
    upstream_ids = ["openai/gpt-5"]
    publications = []

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    class Repository:
        async def active_connection(self, _conn, **_kwargs):
            return None

        async def require_provider(self, _conn, **_kwargs):
            return {"id": provider_id, "name": "Team provider"}

        async def connection_projection(self, _conn, **_kwargs):
            return {"revision": None}

        async def admin_models(self, _conn, **_kwargs):
            return [{
                "id": platform_model_id("openai/gpt-5", provider_id), "value": "openai/gpt-5",
                "label": "Existing GPT", "provider": "compatible", "enabled": True,
                "available": False, "is_default": True, "order": 9,
                "auto_compact_window": 200_000, "max_output_tokens": 2048,
            }]

        async def publish_models(self, _conn, **kwargs):
            publications.append(kwargs)
            return 1, kwargs["models"]

        async def catalog_access(self, _conn):
            return {}

    class Security:
        def normalize(self, base_url):
            return base_url

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
        base_url="https://gateway.example", api_key="synthetic-secret", provider_id=provider_id
    )
    assert publications == []
    assert candidate["models"][0]["id"] == platform_model_id("openai/gpt-5", provider_id)
    assert candidate["models"][0]["label"] == "Existing GPT"
    assert candidate["models"][0]["order"] == 1
    assert candidate["models"][0]["provider_id"] == provider_id
    assert candidate["models"][0]["provider_name"] == "Team provider"
    upstream_ids.append("new-model")
    discovered = await service.discover(
        base_url="https://gateway.example", api_key="synthetic-secret", provider_id=provider_id
    )
    assert discovered["models"][1]["provider_id"] == provider_id
    assert discovered["models"][1]["provider_name"] == "Team provider"
    assert publications == []
    entry = {
        "id": candidate["models"][0]["id"], "value": "openai/gpt-5",
        "display_name": "GPT-5", "enabled": True,
        "order": 1, "auto_compact_window": 200_000, "max_output_tokens": 2048,
    }
    upstream_ids[:] = ["different-model"]
    with pytest.raises(ValueError, match="model_catalog_discovery_changed"):
        await service.publish(
            base_url="https://gateway.example", api_key="synthetic-secret",
            expected_revision=None, models=[entry], actor_user_id="admin-user", provider_id=provider_id,
        )
    assert publications == []
    upstream_ids[:] = ["openai/gpt-5"]
    published = await service.publish(
        base_url="https://gateway.example", api_key="synthetic-secret",
        expected_revision=None, models=[entry], actor_user_id="admin-user", provider_id=provider_id,
    )
    assert published["revision"] == 1
    assert publications[0]["provider_id"] == provider_id
    assert publications[0]["models"] == [entry]


def test_model_group_batch_endpoint_is_atomic_contract(monkeypatch) -> None:
    from app.execution.transport import model_management as transport

    captured = {}

    class Service:
        async def batch_group_models(self, **kwargs):
            captured.update(kwargs)
            return {"preview": True, "groups": []}

    principal = SimpleNamespace(user_id="admin-a", tenant_id="tenant-a")
    app = FastAPI()
    monkeypatch.setattr(transport, "configured_model_control_plane", lambda: Service())
    app.include_router(
        transport.build_model_management_router(
            principal_dependency=lambda: principal,
            is_admin=lambda _principal: True,
        ),
        prefix="/api/ai",
    )
    response = TestClient(app).post("/api/ai/admin/model-groups/models/batch", json={
        "group_ids": ["group-a", "group-b"],
        "model_ids": ["model-a", "model-b"],
        "operation": "replace",
        "expected_group_revisions": {"group-a": 3, "group-b": 8},
        "preview": True,
    })
    assert response.status_code == 200
    assert captured == {
        "tenant_id": "tenant-a",
        "group_ids": ["group-a", "group-b"],
        "model_ids": ["model-a", "model-b"],
        "operation": "replace",
        "expected_group_revisions": {"group-a": 3, "group-b": 8},
        "preview": True,
    }


def test_group_retire_projects_active_count_and_keeps_admin_tenant_boundary(monkeypatch):
    from app.execution.domain.model_groups import ModelGroupPolicyError
    principal = SimpleNamespace(tenant_id="tenant-a")
    authorized = True
    calls = []

    class Service:
        async def archive_or_delete_model_group(self, **kwargs):
            calls.append(kwargs)
            raise ModelGroupPolicyError("model_group_active_runs", active_run_count=3, private_payload="never-project")

    monkeypatch.setattr(model_routes, "configured_model_control_plane", lambda: Service())
    app = FastAPI()
    app.include_router(model_routes.build_model_management_router(principal_dependency=lambda: principal, is_admin=lambda _: authorized))
    with TestClient(app) as client:
        payload = {"revision": 4, "delete": True, "target_group_id": "target", "target_group_revision": 2}
        response = client.post("/admin/model-groups/source/retire", json=payload)
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "model_group_active_runs", "active_run_count": 3}}
        assert calls[0] == {"tenant_id": "tenant-a", "group_id": "source", "revision": 4, "delete": True, "confirm_impact": False, "target_group_id": "target", "target_group_revision": 2, "confirm_default_fallback": False}
        authorized = False
        assert client.post("/admin/model-groups/source/retire", json=payload).status_code == 403
        assert len(calls) == 1
        authorized = True
        assert client.post("/admin/model-groups/source/retire", json={**payload, "target_group_revision": True}).status_code == 422


def test_model_transport_router_uses_bootstrap_auth_dependencies(monkeypatch) -> None:
    principal = SimpleNamespace(user_id="admin-user", tenant_id="tenant-a")
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
        "enabled": True, "order": 1,
        "auto_compact_window": 200_000, "max_output_tokens": 2048,
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
            "base_url": "https://gateway.example", "models": [{**entry, "auto_compact_window": True}],
        })
        forbidden_field = client.post("/api/ai/admin/models/discover", json={
            "base_url": "https://gateway.example", "api_key": "synthetic-secret",
        })
        unknown_key = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "credentail": "synthetic-secret",
            "models": [entry],
        })
        legacy_field = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "credential": "synthetic-secret",
            "models": [{**entry, "is_default": True}],
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
    assert legacy_field.status_code == 422
    assert retired.status_code == 404
    assert denied.status_code == 403
    assert all("synthetic-secret" not in response.text for response in (
        found, published, invalid, forbidden_field, unknown_key, legacy_field, retired, denied
    ))
    assert calls == [
        ("discover", {"base_url": "https://gateway.example", "api_key": "synthetic-secret", "provider_id": "legacy"}),
        ("publish", {
            "base_url": "https://gateway.example", "api_key": "synthetic-secret",
            "expected_revision": 1, "models": [entry], "provider_id": "legacy",
            "actor_user_id": "admin-user", "tenant_id": "tenant-a",
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
                "auto_compact_window": 200_000,
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
        "auto_compact_window": 200_000,
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
        auto_compact_window=200_000,
        max_output_tokens=2048,
        group_id="group-a", group_code="alpha", group_type="business", group_revision=1,
        usage_scene="chat", policy_version="model-groups.v1",
    )

    sql, params = conn.calls[0]
    assert "status = 'queued'" in sql
    assert "model_id is null" in sql
    assert "returning id" in sql
    assert params == (
        "model-public",
        "openai/gpt-5",
        7,
        "group-a", "alpha", "business", 1, "explicit", "chat", "model-groups.v1",
        200_000,
        2048,
        "tenant-a",
        "run-child",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"group_id": "group-a"},
        {"group_code": "sales"},
        {"group_id": "group-a", "group_code": "default", "group_type": "ordinary_default", "group_revision": 2, "policy_version": "model-groups.v1", "usage_scene": "chat"},
    ],
)
async def test_bind_run_model_rejects_partial_or_forbidden_group_snapshot(kwargs) -> None:
    conn = _RunModelMutationConnection([{"id": "run-child"}])
    with pytest.raises(ValueError, match="run_model_group_snapshot_invalid"):
        await bind_run_model(
            conn, tenant_id="tenant-a", run_id="run-child", model_id="model-public",
            model_value="openai/gpt-5", connection_revision=7,
            auto_compact_window=200_000, max_output_tokens=2048, **kwargs,
        )
    assert not conn.calls


@pytest.mark.parametrize(
    ("execution_kind", "session", "requested_scene", "has_session", "expected"),
    [
        ("skill", {"status": "active", "purpose": "conversation"}, "skill", True, "expert"),
        ("skill", {"status": "active", "purpose": "builder_test"}, "expert", True, "skill"),
        ("skill", None, "independent", False, "skill"),
        ("harness_chat", None, "independent", True, "chat"),
    ],
)
def test_trusted_usage_scene_uses_server_session_not_skill_claim(
    execution_kind, session, requested_scene, has_session, expected,
):
    assert trusted_usage_scene(
        execution_kind=execution_kind, session=session,
        requested_scene=requested_scene, has_session=has_session,
    ) == expected


def test_trusted_usage_scene_rejects_missing_bound_session():
    with pytest.raises(ValueError, match="model_not_available_to_user"):
        trusted_usage_scene(
            execution_kind="skill", session=None, requested_scene="expert", has_session=True,
        )


@pytest.mark.asyncio
async def test_bind_run_model_rejects_missing_capacity_before_write() -> None:
    conn = _RunModelMutationConnection([{"id": "run-child"}])
    with pytest.raises(ValueError, match="run_model_capacity_invalid"):
        await bind_run_model(
            conn, tenant_id="tenant-a", run_id="run-child", model_id="model-public",
            model_value="openai/gpt-5", connection_revision=7,
            auto_compact_window=None, max_output_tokens=2048,
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
                auto_compact_window=200_000,
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
            auto_compact_window=200_000,
            max_output_tokens=2048,
            group_id="group-a", group_code="alpha", group_type="business", group_revision=1,
            selection_mode="explicit", usage_scene="chat", policy_version="model-groups.v1",
        )


@pytest.mark.asyncio
async def test_reauthorized_model_snapshot_requires_unattempted_exact_copy_and_rolls_back():
    from app.runs.infrastructure.postgres import replace_run_model_snapshot
    kwargs = dict(tenant_id="tenant-a", run_id="child", model_id="m", model_value="provider/m",
                  connection_revision=2, auto_compact_window=200000, max_output_tokens=2048,
                  group_id="g", group_code="sales", group_type="business", group_revision=1,
                  selection_mode="explicit", usage_scene="chat", policy_version="model-groups.v1")
    conn = _RunModelMutationConnection([{"id":"child"}])
    await replace_run_model_snapshot(conn, **kwargs)
    statement, params = conn.calls[0]
    assert "source.id = runs.copied_from_run_id" in statement
    assert "not exists (select 1 from run_attempts" in statement
    assert params[-4:] == ("m", "provider/m", "m", "provider/m")
    with pytest.raises(ValueError, match="run_model_reauthorization_failed"):
        await replace_run_model_snapshot(_RunModelMutationConnection([None]), **kwargs)


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
        if "select id, name from model_providers" in normalized:
            return _Cursor(row={"id": "legacy", "name": "默认供应商"})
        if "coalesce(max(revision), 0) + 1" in normalized:
            return _Cursor(row={"revision": 3})
        if normalized.startswith("select model_id, upstream_model_id from model_catalog_entries"):
            return _Cursor(rows=self.existing_rows)
        if normalized.startswith("update model_catalog_entries set display_name"):
            return _Cursor(row={"model_id": params[6]})
        if normalized.startswith("select c.*, p.name as provider_name"):
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
        "display_name": "GPT-5", "enabled": True,
        "order": 1, "auto_compact_window": 200_000, "max_output_tokens": 2048,
    }
    kwargs = {
        "models": [entry], "base_url": "https://gateway.example",
        "api_key": "activation-secret", "key_fingerprint": "0123456789abcdef",
        "encryption_key": _key(), "actor_user_id": "admin-user",
        "upstream_model_ids": ["openai/gpt-5"],
    }
    for stale_revision in (1, None):
        conn = _ActivationConnection()
        with pytest.raises(ValueError, match="model_catalog_revision_conflict"):
            await publish_models(conn, expected_revision=stale_revision, **kwargs)
        assert not any("insert into model_gateway_revisions" in sql for sql, _ in conn.calls)

    conn = _ActivationConnection()
    revision, _ = await publish_models(conn, expected_revision=2, **kwargs)
    assert revision == 3
    applied = [params for sql, params in conn.calls
               if "update model_catalog_entries" in sql and "set display_name" in sql]
    assert applied == [("GPT-5", True, 1, 200_000, 2048,
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
                "auto_compact_window": 200_000,
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
            "auto_compact_window": 200_000,
            "max_output_tokens": 2048,
        }
    )

    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await resolve_run_model(
            conn,
            model_id="mdl_public",
            model_value="openai/gpt-5",
            user_id="user-a",
        )


@pytest.mark.asyncio
async def test_run_model_resolution_without_tenant_scope_fails_closed() -> None:
    conn = _ResolveConnection(row=None)
    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await resolve_run_model(
            conn, model_id="mdl_public", model_value="openai/gpt-5", user_id="user-a",
        )
    assert conn.calls == []


@pytest.mark.asyncio
async def test_chat_model_resolution_fails_without_governed_connection(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_ID", "retired-env-model")
    monkeypatch.setenv("MODEL_CATALOG_JSON", '[{"id":"retired-env-model"}]')
    conn = _ResolveConnection(row=None)
    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await model_selection.resolve_chat_model_selection(
            conn, selection=None, user_id="user-a", resolve_governed_model=resolve_run_model,
        )
    assert conn.calls == []


@pytest.mark.asyncio
async def test_run_model_resolution_rejects_missing_capacity() -> None:
    conn = _ResolveConnection(row={
        "connection_revision": 9,
        "model_id": "mdl_public",
        "upstream_model_id": "openai/gpt-5",
        "auto_compact_window": None,
        "max_output_tokens": None,
    })
    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await resolve_run_model(
            conn, model_id="mdl_public", model_value="openai/gpt-5", user_id="user-a",
        )


@pytest.mark.asyncio
async def test_run_model_resolution_rejects_active_but_unavailable_catalog_entry() -> None:
    conn = _ResolveConnection(row={"connection_revision": 9, "model_id": None, "upstream_model_id": None})

    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await resolve_run_model(
            conn, model_id="missing", model_value="missing", user_id="user-a",
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_settled_run_completion_connection_reads_the_run_window_column() -> None:
    """A settled Run's follow-up completion reads the renamed window column."""

    from app.execution.infrastructure.model_management import (
        get_run_model_completion_connection,
    )

    conn = _ResolveConnection(row={
        "revision": 3, "base_url": "https://gateway.example",
        "api_key_ciphertext": encrypt_api_key(
            "completion-secret", revision=3, encoded_key=_key()
        ),
        "key_fingerprint": "fingerprint",
        "model_value": "openai/gpt-5", "auto_compact_window": 200_000,
        "max_output_tokens": 2048,
    })

    connection = await get_run_model_completion_connection(
        conn, tenant_id="tenant-a", user_id="user-a", session_id="session-a",
        run_id="run_123", encryption_key=_key(),
    )

    sql, _params = conn.calls[0]
    assert "runs.auto_compact_window" in sql
    assert "runs.max_input_tokens" not in sql
    assert connection is not None and connection.auto_compact_window == 200_000


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
    assert (
        "run_attempts.execution_spec_schema_version in (\n"
        "            'ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3'\n          )"
    ) in sql
    assert "run_attempts.execution_spec_json->>'model_auto_compact_window'" in sql
    # Historical v2 attempts bind through the window their input capacity clamped to.
    assert "(run_attempts.execution_spec_json->>'model_max_input_tokens')::bigint" in sql
    assert "= runs.auto_compact_window::text" in sql
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

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"first"
            yield b"second"

        def close(self):
            observed["closed"] = True

    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())

    def send(request):
        observed.update(
            method=request.method, path=request.url.raw_path,
            body=request.content, headers=request.headers,
        )
        return response

    monkeypatch.setattr(client, "validate_endpoint", lambda *_args, **_kwargs: endpoint)
    monkeypatch.setattr(client, "_new_client", lambda *_args: httpx.Client(
        transport=httpx.MockTransport(send), trust_env=False,
    ))

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
    assert response.is_closed and stream.client.is_closed and observed["closed"]


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

    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://forbidden.example/"})

    monkeypatch.setattr(client, "validate_endpoint", lambda *_args, **_kwargs: endpoint)
    monkeypatch.setattr(client, "_new_client", lambda *_args: httpx.Client(
        transport=httpx.MockTransport(send), trust_env=False, follow_redirects=False,
    ))

    with pytest.raises(ModelUpstreamError, match="model_upstream_redirect_rejected"):
        open_upstream_stream(
            base_url=endpoint.base_url,
            allowed_internal_hosts="",
            api_key="secret",
            method="POST",
            path="/v1/chat/completions",
            provider="openai",
        )

    assert len(requests) == 1


def test_model_proxy_observes_text_without_rewriting_or_logging_content(caplog) -> None:
    answer = "请处理文件" * 130
    text_event = (
        b"data: "
        + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": answer}}, ensure_ascii=False).encode()
        + b"\r\n\r\n"
    )
    thinking_event = b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"private-thought"}}\n\n'
    start = b'data: {"type":"message_start","message":{"id":"private-model-id"}}\n\n'
    stop = b'data: {"type":"message_stop"}\n\n'
    wire = b": heartbeat\n\n" + start + thinking_event + text_event + stop
    fragments = [wire[:47], wire[47:102], wire[102:115], wire[115:]]
    with caplog.at_level(logging.WARNING):
        assert b"".join(observe_anthropic_text(fragments, run_id="run-test", attempt_id="attempt-test")) == wire
    final = [record.message for record in caplog.records if "model_wire_text" in record.message][-1]
    assert "events=1" in final and f"chars={len(answer)}" in final
    assert hashlib.sha256(answer.encode()).hexdigest() in final
    assert "final=True complete=True coverage=text_delta" in final
    assert "private-model-id" not in caplog.text
    assert answer not in caplog.text and "private-thought" not in caplog.text

    caplog.clear()
    interrupted = observe_anthropic_text([start + text_event, text_event], run_id="run-test", attempt_id="attempt-test")
    with caplog.at_level(logging.WARNING):
        assert next(interrupted) == start + text_event
        interrupted.close()
    assert "coverage=partial_stream_end" in caplog.records[-1].message
    assert "events=1" in caplog.records[-1].message
    assert "final=True complete=False" in caplog.records[-1].message

    caplog.clear()
    oversized = b"data: " + b"x" * (64 * 1024 + 1) + b"\n\n" + text_event
    with caplog.at_level(logging.WARNING):
        assert b"".join(observe_anthropic_text([oversized], run_id="run-test", attempt_id="attempt-test")) == oversized
    assert "coverage=partial_oversized_event" in caplog.records[-1].message
    assert "events=0" in caplog.records[-1].message


def test_model_proxy_invalid_unicode_evidence_never_interrupts_stream(caplog) -> None:
    malformed = b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"\\ud800"}}\n\n'
    later = b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"after"}}\n\n'
    with caplog.at_level(logging.WARNING):
        assert b"".join(observe_anthropic_text([malformed + later], run_id="run-test", attempt_id="attempt-test")) == malformed + later
    assert "coverage=partial_invalid_text" in caplog.records[-1].message
    assert "events=0" in caplog.records[-1].message


def test_model_proxy_deep_invalid_json_cannot_interrupt_forwarding(caplog):
    # Below the SSE byte limit; Python 3.12's JSON parser raises RecursionError.
    wire = b"data: " + b"[" * 15_000 + b"0" + b"]" * 15_000 + b"\n\n"
    with caplog.at_level(logging.WARNING):
        assert b"".join(observe_anthropic_text([wire], run_id="run-test", attempt_id="attempt-test")) == wire
    assert "coverage=partial_invalid_event" in caplog.records[-1].message
    assert "final=True complete=False" in caplog.records[-1].message


def test_model_proxy_evidence_logger_failure_does_not_interrupt_stream(monkeypatch) -> None:
    from app.execution.application import model_response_evidence

    def broken_logger(*_args):
        raise RuntimeError("log_sink_failed")

    monkeypatch.setattr(model_response_evidence._logger, "warning", broken_logger)
    wire = b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\n\n'
    assert b"".join(observe_anthropic_text([wire], run_id="run-test", attempt_id="attempt-test")) == wire


def test_model_proxy_call_correlation_is_scoped_private_and_requires_identity(caplog):
    from tests.support.model_text import proxy_checkpoints, response_events

    caplog.set_level(logging.WARNING)
    events = response_events("synthetic-provider-message", ["hello"])
    first = proxy_checkpoints(events, caplog)
    same = proxy_checkpoints(events, caplog)
    other_attempt = proxy_checkpoints(events, caplog, attempt_id="other-attempt")
    other_run = proxy_checkpoints(events, caplog, run_id="other-run")
    other_call = proxy_checkpoints(response_events("another-provider-message", ["hello"]), caplog)
    assert first == same
    assert len({records[0]["call_ref"] for records in (first, other_attempt, other_run, other_call)}) == 4
    assert "synthetic-provider-message" not in caplog.text
    assert "another-provider-message" not in caplog.text
    missing = proxy_checkpoints(response_events("", ["hello"]), caplog)
    assert all(record["call_ref"] is None and not record["complete"] for record in missing)
    assert missing[-1]["final"] is True
    assert missing[-1]["coverage"] == "partial_missing_call_identity"
    empty = proxy_checkpoints(response_events("tools-only-response", []), caplog)
    assert len(empty) == 1
    assert empty[0]["events"] == empty[0]["chars"] == 0
    assert empty[0]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert empty[0]["final"] is True and empty[0]["complete"] is True


def test_model_proxy_eof_without_message_stop_is_not_complete(caplog):
    from tests.support.model_text import assert_response_checkpoints, proxy_checkpoints, response_events

    caplog.set_level(logging.WARNING)
    chunks = ["分片"] * 256
    records = proxy_checkpoints(response_events("synthetic-provider-message", chunks, complete=False), caplog)
    assert_response_checkpoints(records, records, chunks, complete=False)
    assert [record["events"] for record in records] == [1, 128, 256, 256]


@pytest.mark.asyncio
async def test_model_proxy_evidence_only_wraps_authorized_anthropic_sse(caplog) -> None:
    @asynccontextmanager
    async def transaction():
        yield object()

    async def connection(_conn, **_kwargs):
        return SimpleNamespace(
            base_url="https://gateway.example", api_key="synthetic-key",
            auto_compact_window=200_000, max_output_tokens=2048, conversation_mode="empty_start",
        )

    wire = b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\n\n'
    upstream = SimpleNamespace(open_stream=lambda **_kwargs: SimpleNamespace(
        status=200, content_type="text/event-stream; charset=utf-8", body=lambda: iter([wire]), close=lambda: None
    ))
    service = ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(
            model_proxy_internal_token="synthetic-internal",
            model_connection_encryption_key="synthetic-encryption",
            model_connection_allowed_internal_hosts="",
        ),
        repository=SimpleNamespace(run_connection=connection), security=SimpleNamespace(),
        upstream=upstream, attempt_capability_verifier=lambda **_kwargs: True,
    )
    fields = dict(
        body=b'{"model":"model-a","max_tokens":512,"messages":[]}',
        run_id="run-a", attempt_id="attempt-a", internal_token="synthetic-internal",
        model_proxy_capability="synthetic-capability", query="beta=true",
        headers={"anthropic-version": "2023-06-01"},
    )
    with caplog.at_level(logging.WARNING):
        response = await service.proxy(provider="anthropic", upstream_path="v1/messages", **fields)
        assert b"".join(response.body) == wire
    assert "model_wire_text" in caplog.text
    caplog.clear()
    upstream.open_stream = lambda **_kwargs: SimpleNamespace(
        status=200, content_type="application/json", body=lambda: iter([wire]), close=lambda: None
    )
    with caplog.at_level(logging.WARNING):
        response = await service.proxy(provider="anthropic", upstream_path="v1/messages", **fields)
        assert b"".join(response.body) == wire
    assert "model_wire_text" not in caplog.text


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
            auto_compact_window=200_000, max_output_tokens=2048, conversation_mode="empty_start",
        )

    class FakeStream:
        close = staticmethod(lambda: None)
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
            auto_compact_window=200_000, max_output_tokens=2048, conversation_mode=mode,
        )

    def forbidden_count(**_kwargs):
        raise AssertionError("platform must not count Claude input tokens")

    def forward(**kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(
            status=200,
            content_type="application/json",
            body=lambda: iter([b"{}"]), close=lambda: None,
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
            auto_compact_window=100_000, max_output_tokens=2048, conversation_mode="empty_start",
        )

    def missing_count(**_kwargs):
        return SimpleNamespace(status=404, content_type="application/json", body=b'{}')

    def forward(**kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(
            status=200, content_type="application/json", body=lambda: iter([b"{}"]), close=lambda: None,
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
        (503, "model_proxy_count_tokens_unavailable"),
        (502, "model_proxy_count_tokens_failed"),
        (500, "model_proxy_count_tokens_failed"),
    ):
        service._upstream = SimpleNamespace(
            request=lambda status=status, **_kwargs: SimpleNamespace(
                status=status, body=b'{}', debug={"attempt_count": 2, "body": "PRIVATE"},
            ),
            open_stream=forward,
        )
        with pytest.raises(RuntimeError, match=error) as caught:
            await service.proxy(
                provider="anthropic", upstream_path="v1/messages/count_tokens",
                body=count_body, **fields,
            )
        assert caught.value.debug == {
            "component": "model_proxy", "operation": "count_tokens", "phase": "response",
            "failure_class": "http_error", "http_status": status, "attempt_count": 2,
        }

    service._upstream = SimpleNamespace(request=missing_count, open_stream=forward)

    async def low_capacity_connection(_conn, **_kwargs):
        value = await connection(_conn, **_kwargs)
        value.auto_compact_window = 100_000
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
            auto_compact_window=200_000,
            max_output_tokens=2048,
        )

    class FakeUpstream:
        close = staticmethod(lambda: None)
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
            {"base_url": "https://gateway.example", "hostname": "gateway.example", "port": 443},
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


def test_model_provider_routes_require_admin_and_keep_provider_scope(monkeypatch):
    captured = []
    class Service:
        async def create_provider(self, **kwargs):
            captured.append(kwargs)
            return {"id": "mp-a", "name": kwargs["name"]}
        async def admin_projection(self, **kwargs):
            captured.append(kwargs)
            return {"models": [], "providers": []}
        async def provider_impact(self, **kwargs):
            captured.append(kwargs)
            return {"id": kwargs["provider_id"], "group_count": 0}
        async def retire_provider(self, **kwargs):
            captured.append(kwargs)
            return {"deleted": True}
    authorized = True
    monkeypatch.setattr(model_routes, "configured_model_control_plane", lambda: Service())
    app = FastAPI()
    app.include_router(model_routes.build_model_management_router(
        principal_dependency=lambda: SimpleNamespace(user_id="admin", tenant_id="tenant-a"),
        is_admin=lambda _: authorized), prefix="/api/ai")
    client = TestClient(app)
    requests = [("POST", "/api/ai/admin/model-providers", {"name": "Supplier A"}),
                ("GET", "/api/ai/admin/model-providers/mp-a", None),
                ("GET", "/api/ai/admin/model-providers/mp-a/impact?missing_only=true", None),
                ("POST", "/api/ai/admin/model-providers/mp-a/retire", {"expected_revision": 2, "confirm": True})]
    for method, path, payload in requests:
        assert client.request(method, path, **({"json": payload} if payload else {})).status_code == 200
    assert captured == [{"name": "Supplier A"}, {"provider_id": "mp-a"},
        {"provider_id": "mp-a", "missing_only": True, "tenant_id": "tenant-a"},
        {"provider_id": "mp-a", "expected_revision": 2, "confirm": True, "missing_only": False}]
    captured.clear()
    authorized = False
    for method, path, payload in requests:
        assert client.request(method, path, **({"json": payload} if payload else {})).status_code == 403
    assert captured == []

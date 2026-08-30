from __future__ import annotations

import base64
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.knowledge.application.control_plane import KnowledgeControlPlane
from app.knowledge.domain import KnowledgeError, ProviderSourceRecord, canonical_origin
from app.knowledge.infrastructure.providers.ragflow import RagFlowCatalogProvider
from app.knowledge.transport import admin as knowledge_routes
from app.platform.credentials.vault import (
    PlatformCredentialError,
    decrypt_credential,
    encrypt_credential,
)


def _key() -> str:
    return base64.b64encode(b"knowledge-platform-key-32-bytes!").decode("ascii")


def test_platform_credential_ciphertext_is_reference_and_purpose_bound() -> None:
    secret_ref = "sec_a"
    ciphertext = encrypt_credential(
        "write-only-ragflow-key",
        secret_ref=secret_ref,
        purpose="knowledge_provider",
        encoded_key=_key(),
    )

    assert b"write-only-ragflow-key" not in ciphertext
    assert (
        decrypt_credential(
            ciphertext,
            secret_ref=secret_ref,
            purpose="knowledge_provider",
            encoded_key=_key(),
        )
        == "write-only-ragflow-key"
    )
    with pytest.raises(PlatformCredentialError, match="platform_credential_invalid"):
        decrypt_credential(
            ciphertext,
            secret_ref="sec_b",
            purpose="knowledge_provider",
            encoded_key=_key(),
        )


def test_knowledge_origin_is_canonical_and_rejects_paths_or_userinfo() -> None:
    assert canonical_origin("HTTPS://RAGFLOW.EXAMPLE:443/") == "https://ragflow.example"
    assert canonical_origin("http://ragflow.internal:9380") == "http://ragflow.internal:9380"
    for invalid in (
        "https://user:secret@ragflow.example",
        "https://ragflow.example/api/v1",
        "https://ragflow.example?token=secret",
    ):
        with pytest.raises(KnowledgeError, match="knowledge_connection_endpoint_invalid"):
            canonical_origin(invalid)


@pytest.mark.asyncio
async def test_ragflow_provider_uses_bearer_auth_and_complete_bounded_pagination(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        if page == 1:
            data = [
                {
                    "id": f"dataset-{index:03d}",
                    "name": f"制度库 {index:03d}",
                    "document_count": index,
                    "private_provider_field": "ignored",
                }
                for index in range(100)
            ]
        else:
            data = [{"id": "dataset-final", "name": "最终目录", "document_count": 4}]
        return httpx.Response(
            200,
            json={"code": 0, "data": data},
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 9380))
        ],
    )
    provider = RagFlowCatalogProvider(
        settings_provider=lambda: SimpleNamespace(
            knowledge_connection_allowed_hosts="ragflow.internal",
            knowledge_provider_timeout_seconds=3,
        ),
        transport=httpx.MockTransport(handler),
    )

    snapshot = await provider.list_sources(
        base_url="http://ragflow.internal:9380",
        credential="provider-key",
    )

    assert snapshot.page_count == 2
    assert len(snapshot.records) == 101
    assert snapshot.records[-1] == ProviderSourceRecord(
        provider_resource_id="dataset-final",
        provider_name="最终目录",
        provider_metadata={"document_count": 4},
    )
    assert "private_provider_field" not in snapshot.records[0].provider_metadata
    assert requests[0].url.path == "/api/v1/datasets"
    assert requests[0].url.host == "10.20.30.40"
    assert requests[0].headers["host"] == "ragflow.internal:9380"
    assert requests[0].extensions["sni_hostname"] == "ragflow.internal"
    assert requests[0].headers["authorization"] == "Bearer provider-key"
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


@pytest.mark.asyncio
async def test_ragflow_provider_rejects_an_oversized_catalog_without_echoing_body(
    monkeypatch,
) -> None:
    marker = "provider-private-body-marker"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 9380))
        ],
    )
    provider = RagFlowCatalogProvider(
        settings_provider=lambda: SimpleNamespace(
            knowledge_connection_allowed_hosts="ragflow.internal",
            knowledge_provider_timeout_seconds=3,
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=(marker + ("x" * (2 * 1024 * 1024))).encode(),
                headers={"content-type": "application/json"},
            )
        ),
    )

    with pytest.raises(
        KnowledgeError,
        match="knowledge_provider_catalog_limit_exceeded",
    ) as caught:
        await provider.list_sources(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
        )

    assert marker not in str(caught.value)


@pytest.mark.asyncio
async def test_ragflow_provider_rejects_private_target_without_explicit_allowlist(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 9380))
        ],
    )
    provider = RagFlowCatalogProvider(
        settings_provider=lambda: SimpleNamespace(
            knowledge_connection_allowed_hosts="",
            knowledge_provider_timeout_seconds=3,
        ),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )

    with pytest.raises(KnowledgeError, match="knowledge_connection_endpoint_forbidden"):
        await provider.check(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
        )


@pytest.mark.asyncio
async def test_ragflow_provider_rejects_public_target_without_explicit_allowlist(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))
        ],
    )
    provider = RagFlowCatalogProvider(
        settings_provider=lambda: SimpleNamespace(
            knowledge_connection_allowed_hosts="",
            knowledge_provider_timeout_seconds=3,
        ),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )

    with pytest.raises(KnowledgeError, match="knowledge_connection_endpoint_forbidden"):
        await provider.check(
            base_url="https://ragflow.example",
            credential="provider-key",
        )


class _TransportService:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.builder_catalog_requests: list[dict[str, object]] = []

    async def create_connection(self, **kwargs):
        self.created.append(kwargs)
        return {
            "id": "knc_a",
            "name": kwargs["name"],
            "credential_state": "configured",
            "credential_fingerprint": "0123456789abcdef",
        }

    async def list_connections(self, **_kwargs):
        return {"items": [], "next_cursor": None, "limit": 20}

    async def list_builder_catalog(self, **kwargs):
        self.builder_catalog_requests.append(kwargs)
        return {
            "sources": [],
            "next_cursor": "next-a",
            "limit": kwargs["limit"],
            "retrieval_profiles": [],
        }


def test_knowledge_transport_is_admin_only_and_never_echoes_credentials(monkeypatch) -> None:
    service = _TransportService()
    principal = SimpleNamespace(user_id="admin-a", tenant_id="default")
    allowed = {"value": True}

    async def require_principal():
        return principal

    monkeypatch.setattr(
        knowledge_routes,
        "configured_knowledge_control_plane",
        lambda: service,
    )
    app = FastAPI()
    app.include_router(
        knowledge_routes.build_knowledge_admin_router(
            principal_dependency=require_principal,
            is_admin=lambda candidate: allowed["value"] and candidate is principal,
        ),
        prefix="/api/ai",
    )
    marker = "write-only-provider-secret"
    with TestClient(app) as client:
        created = client.post(
            "/api/ai/admin/knowledge/connections",
            json={
                "operation_id": str(uuid4()),
                "name": "企业制度库",
                "base_url": "https://ragflow.example",
                "credential": marker,
            },
        )
        invalid = client.post(
            "/api/ai/admin/knowledge/connections",
            json={
                "operation_id": str(uuid4()),
                "name": "企业制度库",
                "base_url": "https://ragflow.example",
                "credential": {"value": marker},
            },
        )
        builder_catalog = client.get(
            "/api/ai/admin/knowledge/builder-catalog",
            params=[
                ("limit", "25"),
                ("q", "制度"),
                ("selected_source_id", "ksrc_a"),
                ("selected_source_id", "ksrc_b"),
            ],
        )
        allowed["value"] = False
        denied = client.get("/api/ai/admin/knowledge/connections")

    assert created.status_code == 201
    assert marker not in created.text
    assert service.created[0]["credential"] == marker
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "knowledge_connection_credential_invalid"}
    assert marker not in invalid.text
    assert builder_catalog.status_code == 200
    assert service.builder_catalog_requests == [
        {
            "tenant_id": "default",
            "limit": 25,
            "cursor": None,
            "query": "制度",
            "selected_source_ids": ["ksrc_a", "ksrc_b"],
        }
    ]
    assert denied.status_code == 403
    assert denied.json() == {"detail": "knowledge_admin_required"}


class _FakeVault:
    async def store(self, _conn, **_kwargs):
        return SimpleNamespace(secret_ref="sec_a", fingerprint="0123456789abcdef")

    async def resolve(self, _conn, **_kwargs):
        return "provider-key"


class _FakeAuditWriter:
    def __init__(self) -> None:
        self.facts = []

    async def append(self, _conn, **kwargs):
        self.facts.append(kwargs)
        return "aud_a"


class _FakeProvider:
    provider_key = "ragflow"

    def __init__(self) -> None:
        self.checks = 0

    async def check(self, **_kwargs):
        self.checks += 1

    async def list_sources(self, **_kwargs):
        from app.knowledge.domain import ProviderCatalogSnapshot

        return ProviderCatalogSnapshot(
            records=(
                ProviderSourceRecord(
                    provider_resource_id="dataset-a",
                    provider_name="制度库",
                    provider_metadata={},
                ),
            ),
            page_count=1,
        )


class _FakeRepository:
    def __init__(self) -> None:
        self.connection = None
        self.commits = []

    async def get_connection_by_create_operation(self, _conn, **_kwargs):
        return None

    async def create_connection(self, _conn, **kwargs):
        self.connection = {
            "id": "knc_a",
            "provider_key": "ragflow",
            "base_url": kwargs["definition"].base_url,
            "status": "draft",
        }
        return self.connection

    async def load_revision_for_operation(self, _conn, **_kwargs):
        return {
            "revision_id": "knr_a",
            "provider_key": "ragflow",
            "base_url": "https://ragflow.example",
            "secret_ref": "sec_a",
        }

    async def record_check(self, _conn, **_kwargs):
        return self.connection

    async def claim_catalog_sync(self, _conn, **kwargs):
        return {
            "claimed": True,
            "sync": {"id": "kns_a", "status": "enumerating"},
            "revision": {
                "revision_id": "knr_a",
                "provider_key": "ragflow",
                "base_url": "https://ragflow.example",
                "secret_ref": "sec_a",
            },
            "lease_owner": kwargs["lease_owner"],
            "lease_generation": 1,
        }

    async def commit_catalog(self, _conn, **kwargs):
        self.commits.append(kwargs)
        self.connection["status"] = "active"
        return {"id": "kns_a", "status": "succeeded", "observed_count": 1}

    async def get_connection(self, _conn, **_kwargs):
        return self.connection


@pytest.mark.asyncio
async def test_control_plane_activates_only_after_provider_catalog_succeeds() -> None:
    repository = _FakeRepository()
    provider = _FakeProvider()

    @asynccontextmanager
    async def transaction():
        yield object()

    service = KnowledgeControlPlane(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(knowledge_provider_timeout_seconds=3),
        repository=repository,
        credential_vault=_FakeVault(),
        audit_writer=_FakeAuditWriter(),
        providers=(provider,),
    )
    await service.create_connection(
        tenant_id="default",
        actor_id="admin-a",
        operation_id=str(uuid4()),
        name="企业制度库",
        base_url="https://ragflow.example",
        credential="provider-key",
    )
    result = await service.activate_candidate(
        tenant_id="default",
        actor_id="admin-a",
        connection_id="knc_a",
        operation_id=str(uuid4()),
    )

    assert result["sync"] == {
        "id": "kns_a",
        "status": "succeeded",
        "observed_count": 1,
    }
    assert repository.commits[0]["purpose"] == "candidate_activation"
    assert repository.commits[0]["records"][0].provider_resource_id == "dataset-a"
    assert repository.commits[0]["page_count"] == 1

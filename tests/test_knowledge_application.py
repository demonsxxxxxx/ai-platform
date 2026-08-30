from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.department_directory import normalize_department_directory
from app.knowledge.application.control_plane import KnowledgeControlPlane
from app.knowledge.domain import (
    KnowledgeError,
    ProviderCatalogSnapshot,
    ProviderSourceRecord,
)


@asynccontextmanager
async def _transaction():
    yield object()


class _Vault:
    def __init__(self) -> None:
        self.store_calls = 0

    async def store(self, _conn, **_kwargs):
        self.store_calls += 1
        return SimpleNamespace(secret_ref="sec_a", fingerprint="0123456789abcdef")

    async def resolve(self, _conn, **_kwargs):
        return "provider-key"


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, _conn, **kwargs):
        self.events.append(kwargs)
        return "aud_a"


class _Provider:
    provider_key = "ragflow"

    async def check(self, **_kwargs):
        return None

    async def list_sources(self, **_kwargs):
        return ProviderCatalogSnapshot(records=(), page_count=1)


def _service(*, repository, provider=None, vault=None, audit=None, directory=None):
    return KnowledgeControlPlane(
        transaction_factory=_transaction,
        settings_provider=lambda: SimpleNamespace(knowledge_provider_timeout_seconds=3),
        repository=repository,
        credential_vault=vault or _Vault(),
        audit_writer=audit or _Audit(),
        providers=(provider or _Provider(),),
        department_directory_provider=directory,
    )


def test_catalog_snapshot_rejects_duplicate_provider_identities() -> None:
    duplicate = ProviderSourceRecord("dataset-a", "制度库", {})

    with pytest.raises(KnowledgeError, match="knowledge_provider_catalog_invalid"):
        ProviderCatalogSnapshot(records=(duplicate, duplicate), page_count=1)


@pytest.mark.asyncio
async def test_create_operation_replay_rejects_different_request() -> None:
    class Repository:
        async def get_connection_by_create_operation(self, _conn, **_kwargs):
            return {
                "id": "knc_a",
                "status": "draft",
                "_create_request_hash": KnowledgeControlPlane._request_hash(
                    {
                        "base_url": "https://ragflow.example",
                        "credential_fingerprint": (
                            "6f55b1cd876107f7"
                        ),
                        "name": "企业制度库",
                        "provider_key": "ragflow",
                    }
                ),
            }

    vault = _Vault()
    service = _service(repository=Repository(), vault=vault)

    with pytest.raises(KnowledgeError, match="knowledge_operation_identity_reused"):
        await service.create_connection(
            tenant_id="default",
            actor_id="admin-a",
            operation_id="op-a",
            name="另一个名称",
            base_url="https://ragflow.example",
            credential="different-key",
        )

    assert vault.store_calls == 0


@pytest.mark.asyncio
async def test_unexpected_provider_failure_closes_catalog_lease_with_safe_code() -> None:
    class Provider(_Provider):
        async def check(self, **_kwargs):
            raise RuntimeError("private upstream detail")

    class Repository:
        def __init__(self) -> None:
            self.failed: list[dict[str, object]] = []

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

        async def record_check(self, _conn, **_kwargs):
            return None

        async def fail_catalog_sync(self, _conn, **kwargs):
            self.failed.append(kwargs)
            return {"id": kwargs["sync_id"]}

    repository = Repository()
    audit = _Audit()
    service = _service(repository=repository, provider=Provider(), audit=audit)

    with pytest.raises(KnowledgeError, match="knowledge_provider_unavailable") as caught:
        await service.activate_candidate(
            tenant_id="default",
            actor_id="admin-a",
            connection_id="knc_a",
            operation_id="op-a",
        )

    assert "private upstream detail" not in str(caught.value)
    assert repository.failed[0]["failure_code"] == "knowledge_provider_unavailable"
    assert audit.events[0]["payload"] == {
        "failure_code": "knowledge_provider_unavailable",
        "purpose": "candidate_activation",
        "sync_id": "kns_a",
    }


@pytest.mark.asyncio
async def test_department_acl_update_preserves_existing_role_and_user_scopes() -> None:
    source = {
        "id": "ksrc_a",
        "status": "pending_review",
        "allowed_roles": ["finance_reviewer"],
        "allowed_user_ids": ["employee-a"],
    }

    class Repository:
        def __init__(self) -> None:
            self.replacement = None

        async def get_source(self, _conn, **_kwargs):
            return source

        async def get_source_acl_by_operation(self, _conn, **_kwargs):
            return None

        async def replace_source_acl(self, _conn, **kwargs):
            self.replacement = kwargs
            return {**source, "authorization_version": 2, "visibility": "restricted"}

    async def directory():
        return normalize_department_directory(
            [
                {
                    "value": "100",
                    "parentId": "1",
                    "label": "财务部",
                    "children": [],
                }
            ]
        )

    repository = Repository()
    service = _service(repository=repository, directory=directory)

    await service.replace_source_acl(
        tenant_id="default",
        actor_id="admin-a",
        source_id="ksrc_a",
        operation_id="op-a",
        expected_version=1,
        visibility="restricted",
        department_ids=["财务部"],
    )

    assert repository.replacement["department_ids"] == ("财务部",)
    assert repository.replacement["roles"] == ("finance_reviewer",)
    assert repository.replacement["user_ids"] == ("employee-a",)

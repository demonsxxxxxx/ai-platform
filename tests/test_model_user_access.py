"""Per-model user access and platform display name ownership."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.execution.application.model_control_plane import ModelControlPlaneService
from app.execution.application import model_selection
from app.execution.domain.model_catalog import (
    normalize_model_display_name,
    platform_model_id,
    public_model_projection,
)
from app.execution.infrastructure.model_management import resolve_run_model


def _service(repository, directory=None):
    from app.identity.api import CompanyEmployee, CompanyEmployeeDirectoryService

    in_transaction = False

    @asynccontextmanager
    async def transaction():
        nonlocal in_transaction
        in_transaction = True
        try:
            yield object()
        finally:
            in_transaction = False

    async def read_employees():
        assert not in_transaction, "directory I/O must not hold a database transaction"
        return [CompanyEmployee(work_id, "Employee") for work_id in repository.existing]


    class Security:
        def normalize(self, base_url):
            return base_url

        def fingerprint(self, _api_key):
            return "synthetic-fingerprint"

    class Upstream:
        def request(self, **_kwargs):
            return b"synthetic-catalog"

        def parse_model_ids(self, _body):
            return ["openai/gpt-5"]

    return ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key="synthetic-key",
            model_connection_allowed_internal_hosts="",
        ),
        repository=repository,
        employee_directory=directory or CompanyEmployeeDirectoryService(read_employees, tenant_id="tenant-a"),
        security=Security(),
        upstream=Upstream(),
        attempt_capability_verifier=lambda **_kwargs: True,
    )


def _entry(**overrides):
    entry = {
        "id": platform_model_id("openai/gpt-5"),
        "value": "openai/gpt-5",
        "display_name": "GPT-5",
        "enabled": True,
        "order": 1,
        "auto_compact_window": 200_000,
        "max_output_tokens": 2048,
    }
    entry.update(overrides)
    return entry


class _PublishRepository:
    def __init__(self, *, saved=None, existing=()):
        self.saved = saved or {}
        self.existing = set(existing)
        self.published = None

    async def active_connection(self, _conn, **_kwargs):
        return SimpleNamespace(base_url="https://gateway.example", api_key="synthetic")

    async def connection_projection(self, _conn):
        return {"revision": 1}

    async def admin_models(self, _conn):
        return []

    async def catalog_access(self, _conn):
        return self.saved

    async def publish_models(self, _conn, **kwargs):
        self.published = kwargs
        return 2, kwargs["models"]


def test_display_name_clears_to_upstream_value_only_when_explicitly_empty():
    assert normalize_model_display_name(None, fallback="openai/gpt-5") == "openai/gpt-5"
    assert normalize_model_display_name("", fallback="openai/gpt-5") == "openai/gpt-5"
    assert normalize_model_display_name("   ", fallback="openai/gpt-5") == "openai/gpt-5"
    assert normalize_model_display_name("GPT Five", fallback="x") == "GPT Five"
    # An older client omitting the field must not erase the saved name.
    assert normalize_model_display_name(
        None, fallback="openai/gpt-5", current="Saved Name"
    ) == "Saved Name"
    with pytest.raises(ValueError, match="model_display_name_invalid"):
        normalize_model_display_name("x" * 161, fallback="y")


@pytest.mark.asyncio
async def test_publication_rejects_legacy_acl_fields():
    repository = _PublishRepository(existing={"user-a"})
    with pytest.raises(ValueError, match="model_user_access_retired"):
        await _service(repository).publish(
            base_url="https://gateway.example", api_key="synthetic",
            expected_revision=1,
            models=[_entry(access_mode="selected", allowed_user_ids=["user-a"])],
            actor_user_id="admin-a", tenant_id="tenant-a",
        )
    assert repository.published is None


@pytest.mark.asyncio
async def test_publication_keeps_saved_restriction_when_update_omits_access_fields():
    repository = _PublishRepository(
        saved={
            platform_model_id("openai/gpt-5"): {
                "display_name": "Saved Name",
                "access_mode": "selected",
                "allowed_user_ids": ["user-a"],
            }
        },
    )
    await _service(repository).publish(
        base_url="https://gateway.example", api_key="synthetic",
        expected_revision=1, models=[_entry()],  # no access_mode / allowed_user_ids
        actor_user_id="admin-a", tenant_id="tenant-a",
    )
    assert "access_mode" not in repository.published["models"][0]
    assert "allowed_user_ids" not in repository.published["models"][0]
    assert repository.published["models"][0]["display_name"] == "GPT-5"

    # An omitted display name keeps the saved one.
    repository = _PublishRepository(
        saved={
            platform_model_id("openai/gpt-5"): {
                "display_name": "Saved Name",
                "access_mode": "all",
                "allowed_user_ids": [],
            }
        },
    )
    await _service(repository).publish(
        base_url="https://gateway.example", api_key="synthetic",
        expected_revision=1, models=[_entry(display_name=None)],
        actor_user_id="admin-a", tenant_id="tenant-a",
    )
    assert repository.published["models"][0]["display_name"] == "Saved Name"


def test_public_projection_default_follows_the_filtered_rows():
    rows = [
        {"model_id": "mdl_b", "upstream_model_id": "b", "display_name": "B",
         "provider": "compatible", "is_default": False},
    ]
    # The global default is not in this user's filtered rows.
    assert public_model_projection(rows)["default_model_id"] == "mdl_b"
    # No usable model means no default, not a dangling global default.
    assert public_model_projection([])["default_model_id"] is None


class _ResolveCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _ResolveConnection:
    def __init__(self, row):
        self.calls = []
        self._row = row

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _ResolveCursor(self._row)


@pytest.mark.asyncio
async def test_run_resolution_requires_tenant_group_policy():
    conn = _ResolveConnection({
        "connection_revision": 3, "model_id": "mdl", "upstream_model_id": "openai/gpt-5",
        "auto_compact_window": 200_000, "max_output_tokens": 2048,
    })
    with pytest.raises(ValueError, match="model_policy_unavailable"):
        await resolve_run_model(
            conn, model_id="mdl", model_value=None, user_id="user-b",
        )
    assert conn.calls == []


def test_default_model_selection_carries_the_calling_user():
    captured = {}

    async def resolver(_conn, *, model_id, model_value, user_id):
        captured.update(model_id=model_id, model_value=model_value, user_id=user_id)
        return SimpleNamespace(
            model_id="mdl", model_value="openai/gpt-5",
            connection_revision=1, auto_compact_window=200_000, max_output_tokens=2048,
        )

    import asyncio

    asyncio.run(
        model_selection.resolve_chat_model_selection(
            object(), selection=None, user_id="user-b",
            resolve_governed_model=resolver,
        )
    )
    assert captured == {"model_id": None, "model_value": None, "user_id": "user-b"}


def test_publication_transport_carries_tenant_scope(monkeypatch):
    from app.execution.transport import model_management as transport

    captured = {}

    class Service:
        async def publish(self, **kwargs):
            captured.update(kwargs)
            return {"connection": {"revision": 2}, "models": []}

    monkeypatch.setattr(transport, "configured_model_control_plane", lambda: Service())
    app = FastAPI()
    app.include_router(
        transport.build_model_management_router(
            principal_dependency=lambda: SimpleNamespace(
                user_id="admin-a", tenant_id="tenant-a",
            ),
            is_admin=lambda _principal: True,
        ),
        prefix="/api/ai",
    )
    response = TestClient(app).post("/api/ai/admin/models/publish", json={
        "base_url": "https://gateway.example",
        "models": [{
            "id": platform_model_id("openai/gpt-5"), "value": "openai/gpt-5",
            "display_name": "GPT-5",
            "enabled": True, "order": 1,
            "auto_compact_window": 200_000, "max_output_tokens": 2048,
        }],
    })
    assert response.status_code == 200
    assert captured["tenant_id"] == "tenant-a"
    assert "access_mode" not in captured["models"][0]
    assert "allowed_user_ids" not in captured["models"][0]


@pytest.mark.asyncio
async def test_publication_does_not_depend_on_legacy_directory_acl():
    from app.identity.api import CompanyEmployeeDirectoryService, CompanyEmployeeDirectoryUnavailable

    async def unavailable():
        raise CompanyEmployeeDirectoryUnavailable()

    directory = CompanyEmployeeDirectoryService(unavailable, tenant_id="tenant-a")
    repository = _PublishRepository(saved={platform_model_id("openai/gpt-5"): {
        "display_name": "Saved", "access_mode": "selected", "allowed_user_ids": ["E001", "E002"],
    }})
    await _service(repository, directory).publish(
        base_url="https://gateway.example", api_key="synthetic", expected_revision=1,
        models=[_entry()], actor_user_id="admin-a", tenant_id="tenant-a",
    )
    assert repository.published is not None


def test_publication_transport_rejects_foreign_tenant_before_directory_read_or_publish(monkeypatch):
    from app.execution.transport import model_management as transport
    from app.identity.api import CompanyEmployeeDirectoryService

    reader_calls = []

    async def read_employees():
        reader_calls.append(True)
        raise AssertionError("foreign tenant must not read the company directory")

    repository = _PublishRepository()
    service = _service(repository, CompanyEmployeeDirectoryService(read_employees, tenant_id="default"))
    monkeypatch.setattr(transport, "configured_model_control_plane", lambda: service)
    app = FastAPI()
    app.include_router(
        transport.build_model_management_router(
            principal_dependency=lambda: SimpleNamespace(user_id="admin-a", tenant_id="foreign"),
            is_admin=lambda _principal: True,
        ),
        prefix="/api/ai",
    )
    with TestClient(app) as client:
        response = client.post("/api/ai/admin/models/publish", json={
            "base_url": "https://gateway.example", "credential": "synthetic",
            "expected_revision": 1,
            "models": [_entry()],
        })
    assert response.status_code == 200
    assert reader_calls == []
    assert repository.published is not None

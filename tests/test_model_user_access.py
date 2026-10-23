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
    normalize_model_user_access,
    platform_model_id,
    public_model_projection,
)
from app.execution.infrastructure.model_management import (
    model_access_excludes_user,
    resolve_run_model,
)
from app.execution.transport.model_management import _translate_control_plane_error


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
        "is_default": True,
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


def test_access_normalization_never_silently_widens():
    # Omitted mode with a user list is an explicit restriction, not "everyone".
    assert normalize_model_user_access(None, ["user-a", "user-a", "user-b"]) == (
        "selected", ["user-a", "user-b"],
    )
    # A model that never configured access stays fully available.
    assert normalize_model_user_access(None, None) == ("all", [])
    # Omitted fields on a restricted model keep the saved restriction.
    assert normalize_model_user_access(None, None, current=("selected", ["user-a"])) == (
        "selected", ["user-a"],
    )
    assert normalize_model_user_access(None, [], current=("selected", ["user-a"])) == (
        "selected", ["user-a"],
    )
    # An explicit empty selection is rejected rather than meaning "everyone".
    for mode, users in (("selected", []), ("selected", ()), ("selected", None)):
        with pytest.raises(ValueError, match="model_user_access_(requires_users|invalid)"):
            normalize_model_user_access(mode, users)
    for invalid in (("everyone", []), ("selected", "user-a"), ("selected", [""]),
                    ("selected", ["user a"]), ("selected", ["../etc"]), ("selected", [3])):
        with pytest.raises(ValueError, match="model_user_access_invalid"):
            normalize_model_user_access(*invalid)


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
async def test_publication_persists_company_work_id_without_local_login_and_rejects_unknown_users():
    repository = _PublishRepository(existing={"user-a"})
    published = await _service(repository).publish(
        base_url="https://gateway.example", api_key="synthetic",
        expected_revision=1,
        models=[_entry(access_mode="selected", allowed_user_ids=["user-a"])],
        actor_user_id="admin-a", tenant_id="tenant-a",
    )
    assert published["revision"] == 2
    assert repository.published["models"][0]["access_mode"] == "selected"
    assert repository.published["models"][0]["allowed_user_ids"] == ["user-a"]

    # A user that is not an active member of this tenant is refused.
    for users in (["user-b"], ["user-a", "user-b"]):
        repository = _PublishRepository(existing={"user-a"})
        with pytest.raises(ValueError, match="model_user_access_unknown_user"):
            await _service(repository).publish(
                base_url="https://gateway.example", api_key="synthetic",
                expected_revision=1,
                models=[_entry(access_mode="selected", allowed_user_ids=users)],
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
    assert repository.published["models"][0]["access_mode"] == "selected"
    assert repository.published["models"][0]["allowed_user_ids"] == ["user-a"]
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


class _AccessCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _AccessConnection:
    def __init__(self, row):
        self._row = row

    async def execute(self, _sql, _params=None):
        return _AccessCursor(self._row)


@pytest.mark.asyncio
async def test_new_run_admission_fails_closed_for_excluded_or_missing_models():
    # A new Run may not use a model whose current list excludes this user.
    assert await model_access_excludes_user(
        _AccessConnection({"access_mode": "selected", "allowed_user_ids": ["user-a"]}),
        model_id="mdl", user_id="user-b",
    )
    # A current user list that includes the caller admits the new Run.
    assert not await model_access_excludes_user(
        _AccessConnection({"access_mode": "selected", "allowed_user_ids": ["user-b"]}),
        model_id="mdl", user_id="user-b",
    )
    assert not await model_access_excludes_user(
        _AccessConnection({"access_mode": "all", "allowed_user_ids": []}),
        model_id="mdl", user_id="user-b",
    )
    # A retired catalog row must not become a new-Run bypass.
    assert await model_access_excludes_user(
        _AccessConnection(None), model_id="mdl", user_id="user-b",
    )


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
async def test_run_resolution_filters_by_the_calling_user_in_one_predicate():
    conn = _ResolveConnection({
        "connection_revision": 3, "model_id": "mdl", "upstream_model_id": "openai/gpt-5",
        "auto_compact_window": 200_000, "max_output_tokens": 2048,
    })
    for selection in ({"id": "mdl"}, {"value": "openai/gpt-5"}, {}):
        await resolve_run_model(
            conn,
            model_id=selection.get("id"),
            model_value=selection.get("value"),
            user_id="user-b",
        )
        sql, params = conn.calls[-1]
        assert "allowed_user_ids ? %s" in sql
        assert params[0] == "user-b"


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


def test_access_errors_map_to_validation_responses():
    for code in ("model_user_access_invalid", "model_user_access_requires_users",
                 "model_user_access_unknown_user"):
        error = _translate_control_plane_error(ValueError(code))
        assert (error.status_code, error.detail) == (422, code)


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
            "enabled": True, "is_default": True, "order": 1,
            "access_mode": "selected", "allowed_user_ids": ["user-a"],
            "auto_compact_window": 200_000, "max_output_tokens": 2048,
        }],
    })
    assert response.status_code == 200
    assert captured["tenant_id"] == "tenant-a"
    assert captured["models"][0]["access_mode"] == "selected"
    assert captured["models"][0]["allowed_user_ids"] == ["user-a"]


@pytest.mark.asyncio
async def test_directory_outage_blocks_new_grants_but_allows_carryover_and_removal():
    from app.identity.api import CompanyEmployeeDirectoryService, CompanyEmployeeDirectoryUnavailable

    async def unavailable():
        raise CompanyEmployeeDirectoryUnavailable()

    directory = CompanyEmployeeDirectoryService(unavailable, tenant_id="tenant-a")
    repository = _PublishRepository(saved={platform_model_id("openai/gpt-5"): {
        "display_name": "Saved", "access_mode": "selected", "allowed_user_ids": ["E001", "E002"],
    }})
    for fields in ({}, {"access_mode": "selected", "allowed_user_ids": ["E001"]}):
        await _service(repository, directory).publish(
            base_url="https://gateway.example", api_key="synthetic", expected_revision=1,
            models=[_entry(**fields)], actor_user_id="admin-a", tenant_id="tenant-a",
        )
        assert repository.published is not None
    repository.published = None
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        await _service(repository, directory).publish(
            base_url="https://gateway.example", api_key="synthetic", expected_revision=1,
            models=[_entry(access_mode="selected", allowed_user_ids=["E001", "E003"])],
            actor_user_id="admin-a", tenant_id="tenant-a",
        )
    assert repository.published is None
    assert _translate_control_plane_error(CompanyEmployeeDirectoryUnavailable()).status_code == 503


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
            "models": [_entry(access_mode="selected", allowed_user_ids=["E001"])],
        })
    assert response.status_code == 403
    assert response.json() == {"detail": "company_employee_directory_forbidden"}
    assert reader_calls == []
    assert repository.published is None

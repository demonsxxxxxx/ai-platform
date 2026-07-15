from contextlib import asynccontextmanager
import importlib
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def admin_headers():
    return {
        "X-AI-User-ID": "distribution-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "tenant-a",
        "X-AI-Department-ID": "platform",
    }


def user_headers():
    return {
        "X-AI-User-ID": "ordinary-user",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
        "X-AI-Department-ID": "qa",
    }


def distribution_row(**overrides):
    row = {
        "id": "capdist-qa-file-reviewer",
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ["qa"],
        "allowed_roles": ["qa_reviewer"],
        "metadata_json": {"source": "admin"},
        "updated_by": "distribution-admin",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
    }
    row.update(overrides)
    return row


def configure_admin_route(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    if importlib.util.find_spec("app.routes.capability_distributions") is None:
        return None
    route_module = importlib.import_module("app.routes.capability_distributions")

    async def fake_ensure_user(conn, **kwargs):
        return None

    monkeypatch.setattr(route_module, "transaction", fake_transaction)
    monkeypatch.setattr(route_module.repositories, "ensure_user", fake_ensure_user)
    return route_module


def patch_repository(monkeypatch, route_module, name, replacement):
    if route_module is not None:
        monkeypatch.setattr(route_module.repositories, name, replacement, raising=False)


def test_admin_lists_and_filters_capability_distributions(monkeypatch):
    calls = []

    async def fake_list_rows(conn, *, tenant_id, capability_kind, include_disabled):
        calls.append((tenant_id, capability_kind, include_disabled))
        return [
            distribution_row(),
            distribution_row(
                id="capdist-mcp-qa-search",
                capability_kind="mcp_server",
                capability_id="qa-search",
                status="disabled",
            ),
        ]

    route_module = configure_admin_route(monkeypatch)
    patch_repository(
        monkeypatch,
        route_module,
        "list_capability_distribution_rows",
        fake_list_rows,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/admin/capability-distributions?capability_kind=mcp_server&status=disabled",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": "tenant-a",
        "capability_distributions": [
            {
                **distribution_row(
                    id="capdist-mcp-qa-search",
                    capability_kind="mcp_server",
                    capability_id="qa-search",
                    status="disabled",
                )
            }
        ],
        "total": 1,
    }
    assert calls == [("tenant-a", "mcp_server", True)]


def test_admin_reads_capability_distribution_detail(monkeypatch):
    async def fake_get_row(conn, **kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
        }
        return distribution_row()

    route_module = configure_admin_route(monkeypatch)
    patch_repository(
        monkeypatch,
        route_module,
        "get_capability_distribution_row",
        fake_get_row,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/admin/capability-distributions/skill/qa-file-reviewer",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == distribution_row()


def test_admin_updates_skill_distribution_normalizes_scopes_and_audits(monkeypatch):
    calls = []
    metadata = {
        "ticket": "CAP-2",
        "api_key": "plain-secret",
        "credentials": {"token": "nested-secret"},
        "environment": {"OPENAI_API_KEY": "env-secret"},
    }

    async def fake_get_skill(conn, *, skill_id):
        assert skill_id == "qa-file-reviewer"
        return {"skill_id": skill_id, "status": "active"}

    async def fake_upsert(conn, **kwargs):
        calls.append(("upsert", kwargs))
        return distribution_row(
            status=kwargs["status"],
            visible_to_user=kwargs["visible_to_user"],
            department_ids=kwargs["department_ids"],
            allowed_roles=kwargs["allowed_roles"],
            metadata_json=kwargs["metadata_json"],
        )

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-capdist-updated"

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", fake_get_skill)
    patch_repository(
        monkeypatch,
        route_module,
        "upsert_capability_distribution_row",
        fake_upsert,
    )
    patch_repository(monkeypatch, route_module, "append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/admin/capability-distributions/skill/qa-file-reviewer",
        headers=admin_headers(),
        json={
            "status": "active",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": [" QA ", "qa", "RD"],
            "allowed_roles": [" QA_REVIEWER ", "qa_reviewer", "RD-Lead"],
            "metadata": metadata,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["audit_id"] == "aud-capdist-updated"
    assert body["audit_action"] == "capability_distribution.updated"
    assert body["capability_distribution"]["department_ids"] == ["QA", "qa", "RD"]
    assert body["capability_distribution"]["allowed_roles"] == ["qa_reviewer", "rd-lead"]
    assert body["capability_distribution"]["metadata_json"] == metadata
    assert calls[0][1]["department_ids"] == ["QA", "qa", "RD"]
    assert calls[0][1]["metadata_json"] == metadata
    assert calls[1][1]["action"] == "capability_distribution.updated"
    assert calls[1][1]["payload_json"] == {
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
        "actor_department_id": "platform",
        "actor_roles": ["admin"],
        "department_scope_ids": ["QA", "qa", "RD"],
        "role_scope_ids": ["qa_reviewer", "rd-lead"],
        "scope_mode": "allowlist",
        "decision_reason": "admin_bypass",
        "admin_bypass": True,
        "status": "active",
        "visible_to_user": False,
    }
    assert "plain-secret" not in str(calls[1][1]["payload_json"])
    assert "nested-secret" not in str(calls[1][1]["payload_json"])
    assert "env-secret" not in str(calls[1][1]["payload_json"])


@pytest.mark.parametrize("reserved_key", ["archived_at", "archived_by"])
def test_admin_update_rejects_reserved_archive_metadata_before_repository_write(monkeypatch, reserved_key):
    async def fail(*args, **kwargs):
        raise AssertionError("reserved archive metadata must not reach repositories")

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", fail)
    patch_repository(monkeypatch, route_module, "upsert_capability_distribution_row", fail)
    client = TestClient(create_app())

    response = client.put(
        "/api/admin/capability-distributions/skill/qa-file-reviewer",
        headers=admin_headers(),
        json={"metadata": {reserved_key: "forged"}},
    )

    assert (response.status_code, response.json()["detail"]) == (400, "capability_distribution_metadata_reserved")


def test_admin_updates_mcp_distribution_only_for_tenant_registry_server(monkeypatch):
    async def fake_list_names(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return ["qa-search"]

    async def fake_upsert(conn, **kwargs):
        return distribution_row(
            id="capdist-mcp-qa-search",
            capability_kind="mcp_server",
            capability_id="qa-search",
            status=kwargs["status"],
        )

    async def fake_append_audit_log(conn, **kwargs):
        return "aud-capdist-mcp"

    route_module = configure_admin_route(monkeypatch)
    patch_repository(
        monkeypatch,
        route_module,
        "list_mcp_server_registry_names",
        fake_list_names,
    )
    patch_repository(
        monkeypatch,
        route_module,
        "upsert_capability_distribution_row",
        fake_upsert,
    )
    patch_repository(monkeypatch, route_module, "append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/admin/capability-distributions/mcp_server/qa-search",
        headers=admin_headers(),
        json={},
    )

    assert response.status_code == 200
    assert response.json()["capability_distribution"]["capability_kind"] == "mcp_server"


def test_unknown_capability_and_invalid_kind_or_id_are_controlled_errors(monkeypatch):
    async def missing_skill(conn, *, skill_id):
        return None

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", missing_skill)
    client = TestClient(create_app())

    unknown = client.put(
        "/api/admin/capability-distributions/skill/missing-skill",
        headers=admin_headers(),
        json={},
    )
    invalid_kind = client.get(
        "/api/admin/capability-distributions/mcp_tool/qa-search",
        headers=admin_headers(),
    )
    invalid_id = client.get(
        "/api/admin/capability-distributions/skill/bad%20id",
        headers=admin_headers(),
    )

    assert (unknown.status_code, unknown.json()["detail"]) == (404, "skill_not_found")
    assert (invalid_kind.status_code, invalid_kind.json()["detail"]) == (400, "capability_kind_invalid")
    assert (invalid_id.status_code, invalid_id.json()["detail"]) == (400, "capability_id contains unsupported characters")


def test_missing_toggle_distribution_is_controlled_404(monkeypatch):
    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id}

    async def missing_toggle(conn, **kwargs):
        raise RepositoryNotFoundError("capability_distribution_not_found")

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", fake_get_skill)
    patch_repository(
        monkeypatch,
        route_module,
        "toggle_capability_distribution_row",
        missing_toggle,
    )
    client = TestClient(create_app())

    response = client.patch(
        "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle",
        headers=admin_headers(),
        json={"enabled": True},
    )

    assert (response.status_code, response.json()["detail"]) == (404, "capability_distribution_not_found")


@pytest.mark.parametrize(
    ("method", "path", "replacement"),
    [
        ("put", "/api/admin/capability-distributions/skill/qa-file-reviewer", "upsert_capability_distribution_row"),
        (
            "patch",
            "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle",
            "toggle_capability_distribution_row",
        ),
    ],
    ids=["update", "toggle"],
)
def test_archived_distribution_mutations_are_bounded_conflicts(monkeypatch, method, path, replacement):
    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id}

    async def archived_conflict(conn, **kwargs):
        raise RepositoryConflictError("capability_distribution_archived")

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", fake_get_skill)
    patch_repository(monkeypatch, route_module, replacement, archived_conflict)
    client = TestClient(create_app())

    response = getattr(client, method)(path, headers=admin_headers(), json={})

    assert (response.status_code, response.json()["detail"]) == (409, "capability_distribution_archived")


def test_toggle_aliases_update_distribution_and_audit(monkeypatch):
    calls = []

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id}

    async def fake_toggle(conn, **kwargs):
        calls.append(kwargs)
        return distribution_row(status="disabled")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(kwargs)
        return "aud-capdist-toggle"

    route_module = configure_admin_route(monkeypatch)
    patch_repository(monkeypatch, route_module, "get_skill", fake_get_skill)
    patch_repository(
        monkeypatch,
        route_module,
        "toggle_capability_distribution_row",
        fake_toggle,
    )
    patch_repository(monkeypatch, route_module, "append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.patch(
        "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle",
        headers=admin_headers(),
        json={"is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["audit_action"] == "capability_distribution.toggled"
    assert calls[0]["enabled"] is False
    assert calls[1]["action"] == "capability_distribution.toggled"
    assert calls[1]["payload_json"]["admin_bypass"] is True


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/admin/capability-distributions/skill/qa-file-reviewer", {}),
        ("patch", "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle", {}),
    ],
    ids=["update", "implicit-toggle"],
)
def test_distribution_writes_roll_back_when_response_model_build_fails(
    monkeypatch,
    method,
    path,
    payload,
):
    calls = []

    @asynccontextmanager
    async def recording_transaction():
        calls.append(("tx_enter", {}))
        try:
            yield object()
        except Exception:
            calls.append(("tx_rollback", {}))
            raise
        else:
            calls.append(("tx_commit", {}))

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "status": "active"}

    async def fake_upsert(conn, **kwargs):
        calls.append(("upsert", kwargs))
        return distribution_row()

    async def fake_toggle(conn, **kwargs):
        calls.append(("toggle", kwargs))
        return distribution_row(status="disabled")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-capdist-response-failure"

    def fail_response_build(**kwargs):
        calls.append(("response_build_failed", kwargs))
        raise RuntimeError("response_build_failed")

    route_module = configure_admin_route(monkeypatch)
    monkeypatch.setattr(route_module, "transaction", recording_transaction)
    patch_repository(monkeypatch, route_module, "get_skill", fake_get_skill)
    patch_repository(monkeypatch, route_module, "upsert_capability_distribution_row", fake_upsert)
    patch_repository(monkeypatch, route_module, "toggle_capability_distribution_row", fake_toggle)
    patch_repository(monkeypatch, route_module, "append_audit_log", fake_append_audit_log)
    monkeypatch.setattr(route_module, "CapabilityDistributionWriteResponse", fail_response_build)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = getattr(client, method)(path, headers=admin_headers(), json=payload)

    assert response.status_code == 500
    names = [name for name, _ in calls]
    assert names.count("tx_enter") == 1
    assert "tx_rollback" in names
    assert "tx_commit" not in names
    assert names.index("response_build_failed") < names.index("tx_rollback")
    assert names.count("audit") == 1
    if method == "patch":
        toggle_call = next(call for name, call in calls if name == "toggle")
        assert toggle_call["enabled"] is None


def test_ordinary_user_cannot_access_capability_distribution_management(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("ordinary users must not reach distribution repositories")

    route_module = configure_admin_route(monkeypatch)
    patch_repository(
        monkeypatch,
        route_module,
        "list_capability_distribution_rows",
        fail,
    )
    patch_repository(
        monkeypatch,
        route_module,
        "get_capability_distribution_row",
        fail,
    )
    patch_repository(monkeypatch, route_module, "get_skill", fail)
    client = TestClient(create_app())

    responses = [
        client.get("/api/admin/capability-distributions", headers=user_headers()),
        client.get("/api/admin/capability-distributions/skill/qa-file-reviewer", headers=user_headers()),
        client.put("/api/admin/capability-distributions/skill/qa-file-reviewer", headers=user_headers(), json={}),
        client.patch(
            "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle",
            headers=user_headers(),
            json={},
        ),
    ]

    assert [(response.status_code, response.json()["detail"]) for response in responses] == [(403, "not_ai_admin")] * 4


def test_extra_distribution_request_fields_are_rejected(monkeypatch):
    configure_admin_route(monkeypatch)
    client = TestClient(create_app())

    update = client.put(
        "/api/admin/capability-distributions/skill/qa-file-reviewer",
        headers=admin_headers(),
        json={"unknown": "field"},
    )
    toggle = client.patch(
        "/api/admin/capability-distributions/skill/qa-file-reviewer/toggle",
        headers=admin_headers(),
        json={"enabled": True, "unknown": "field"},
    )

    assert update.status_code == 422
    assert toggle.status_code == 422

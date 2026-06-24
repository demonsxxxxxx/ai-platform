from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def user_headers(permissions: str = "role:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-User-Name": "Ordinary User",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def admin_headers(permissions: str = "role:read,role:manage") -> dict[str, str]:
    return {
        "X-AI-User-ID": "role-admin",
        "X-AI-User-Name": "Role Admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "tenant-a",
        "X-AI-Department-ID": "platform",
        "X-AI-Permissions": permissions,
    }


SENSITIVE_FREE_TEXT = "need token_secret=sk-live-secret-value and password=secret-password"


def assert_no_sensitive_material(value: object) -> None:
    serialized = str(value).lower()
    assert "permission:" not in serialized
    assert "agent:admin" not in serialized
    assert "settings:manage" not in serialized
    assert "raw_payload" not in serialized
    assert "private_payload" not in serialized
    assert "token_secret" not in serialized
    assert "secret-password" not in serialized
    assert "password" not in serialized
    assert "sk-live-secret-value" not in serialized


def install_role_governance_route_fakes(
    monkeypatch,
    *,
    audit_history: list[dict[str, object]] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    from app.routes import role_governance

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-role-governance-contract"

    async def fake_audit_history(conn, **kwargs):
        calls.append(("history", kwargs))
        return audit_history or []

    async def fake_tenant_exists(conn, **kwargs):
        calls.append(("tenant_exists", kwargs))
        return True

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(role_governance, "transaction", fake_transaction)
    monkeypatch.setattr(role_governance.repositories, "tenant_exists", fake_tenant_exists)
    monkeypatch.setattr(role_governance.repositories, "append_audit_log", fake_audit)
    monkeypatch.setattr(role_governance.repositories, "list_role_governance_audit_history", fake_audit_history)
    return calls


def test_role_governance_overview_projects_safe_roles_scope_and_audit(monkeypatch):
    install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/role-governance/overview?workspace_id=workspace-a", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["governance"] == {
        "projection": "safe_role_governance",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "degraded": False,
        "audit_required": True,
        "rollback_available": False,
        "secret_material_projected": False,
    }
    assert body["role_directory"]["roles"][0] == {
        "role_id": "user",
        "name": "User",
        "description": "Baseline authenticated user role for ordinary workbench access.",
        "requestable": False,
        "assignable": False,
        "scope": "tenant",
        "capabilities": ["chat", "files", "skills_discovery", "marketplace_discovery"],
    }
    assert "permissions" not in body["role_directory"]["roles"][0]
    assert {role["role_id"] for role in body["role_directory"]["roles"]} >= {
        "skill_developer",
        "runtime_operator",
        "auditor",
        "tenant_admin",
    }
    assert body["scope"]["tenant_id"] == "tenant-a"
    assert body["scope"]["workspace_id"] == "workspace-a"
    assert body["scope"]["current_department_id"] == "qa"
    assert body["scope"]["departments"][0]["department_id"] == "qa"
    assert body["scope"]["workspaces"][0]["workspace_id"] == "workspace-a"
    assert body["scope"]["skill_availability"][0]["inherited_from"] == "tenant"
    assert body["requests"][0]["requester_id"] == "ordinary"
    assert body["audit"][0]["source"] == "role_governance_projection"
    assert body["audit"][0]["rollback_available"] is False
    assert_no_sensitive_material(body)


def test_role_governance_overview_projects_backed_request_and_audit_history(monkeypatch):
    calls = install_role_governance_route_fakes(
        monkeypatch,
        audit_history=[
            {
                "id": "aud-role-request",
                "user_id": "ordinary",
                "action": "role_governance.request.created",
                "target_type": "role_request",
                "target_id": "skill_developer",
                "payload_json": {
                    "target_type": "role",
                    "target_id": "skill_developer",
                    "reason": "Need Skill workbench access",
                    "workspace_id": "workspace-a",
                },
                "created_at": None,
            }
        ],
    )
    client = TestClient(create_app())

    response = client.get("/api/role-governance/overview?workspace_id=workspace-a", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["requests"] == [
        {
            "request_id": "aud-role-request",
            "requester_id": "ordinary",
            "target_type": "role",
            "target_id": "skill_developer",
            "status": "queued",
            "reason": "Need Skill workbench access",
            "approver_id": None,
            "created_at": None,
            "decided_at": None,
            "audit_id": "aud-role-request",
        }
    ]
    assert body["audit"] == [
        {
            "audit_id": "aud-role-request",
            "action": "role_governance.request.created",
            "target_type": "role_request",
            "target_id": "skill_developer",
            "actor_id": "ordinary",
            "source": "role_governance_projection",
            "status": "recorded",
            "rollback_available": False,
            "created_at": None,
        }
    ]
    assert ("history", {"tenant_id": "tenant-a", "user_id": "ordinary", "limit": 25}) in calls
    assert_no_sensitive_material(body)


def test_role_governance_request_requires_request_permission(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    denied = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": "Need Skill workbench access",
            "workspace_id": "workspace-a",
        },
        headers=user_headers("role:read"),
    )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_permission:role:request"
    assert calls == []


def test_role_governance_user_request_is_audited_without_admin_permission(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": "Need Skill workbench access",
            "workspace_id": "workspace-a",
        },
        headers=user_headers("role:request"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "target_type": "role_request",
        "target_id": "skill_developer",
        "operation": "request",
        "status": "queued",
        "audit_id": "audit-role-governance-contract",
        "message": "role governance request accepted for review",
    }
    assert calls[0] == ("tenant_exists", {"tenant_id": "tenant-a"})
    audit_payload = calls[1][1]
    assert audit_payload["tenant_id"] == "tenant-a"
    assert audit_payload["user_id"] == "ordinary"
    assert audit_payload["action"] == "role_governance.request.created"
    assert audit_payload["target_type"] == "role_request"
    assert audit_payload["target_id"] == "skill_developer"
    assert audit_payload["payload_json"] == {
        "target_type": "role",
        "target_id": "skill_developer",
        "reason": "Need Skill workbench access",
        "department_id": "qa",
        "workspace_id": "workspace-a",
        "source": "role_governance_projection",
        "secret_material_projected": False,
    }
    assert_no_sensitive_material(body)
    assert_no_sensitive_material(calls)


def test_role_governance_write_fails_closed_for_unprovisioned_tenant(monkeypatch):
    from app.routes import role_governance

    class MissingTenantCursor:
        async def fetchone(self):
            return None

    class FakeConnection:
        def __init__(self) -> None:
            self.audit_insert_attempted = False

        async def execute(self, sql, params=()):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith("select 1 from tenants"):
                assert params == ("tenant-a",)
                return MissingTenantCursor()
            if "insert into audit_logs" in normalized:
                self.audit_insert_attempted = True
                raise AssertionError("unprovisioned tenant must fail before audit insert")
            raise AssertionError(f"unexpected sql: {normalized}")

    fake_conn = FakeConnection()

    @asynccontextmanager
    async def fake_transaction():
        yield fake_conn

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(role_governance, "transaction", fake_transaction)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": "Need Skill workbench access",
            "workspace_id": "workspace-a",
        },
        headers=user_headers("role:request"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tenant_not_authorized"
    assert fake_conn.audit_insert_attempted is False
    assert_no_sensitive_material(response.json())


def test_role_governance_audit_payload_sanitizes_free_text_fields(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    request = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": SENSITIVE_FREE_TEXT,
            "workspace_id": "workspace-a",
        },
        headers=user_headers("role:request"),
    )
    approve = client.post(
        "/api/role-governance/approvals/req-1/approve",
        json={"decision_note": SENSITIVE_FREE_TEXT, "rollback_id": "rb-1"},
        headers=admin_headers(),
    )
    rollback = client.post(
        "/api/role-governance/audit/audit-1/rollback",
        json={"reason": SENSITIVE_FREE_TEXT},
        headers=admin_headers(),
    )

    assert request.status_code == 200
    assert approve.status_code == 200
    assert rollback.status_code == 200
    assert [payload["payload_json"] for name, payload in calls if name == "audit"] == [
        {
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": "[redacted-private]",
            "department_id": "qa",
            "workspace_id": "workspace-a",
            "source": "role_governance_projection",
            "secret_material_projected": False,
        },
        {
            "operation": "approve",
            "decision_note": "[redacted-private]",
            "has_rollback_id": True,
            "department_id": "platform",
            "source": "role_governance_projection",
            "secret_material_projected": False,
        },
        {
            "operation": "rollback",
            "reason": "[redacted-private]",
            "department_id": "platform",
            "source": "role_governance_projection",
            "secret_material_projected": False,
        },
    ]
    assert_no_sensitive_material(calls)


def test_role_governance_rejects_permission_like_role_targets_before_audit(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    for target_id in ("role:manage", "settings:manage", "token_secret"):
        response = client.post(
            "/api/role-governance/requests",
            json={
                "target_type": "role",
                "target_id": target_id,
                "reason": "Need Skill workbench access",
                "workspace_id": "workspace-a",
            },
            headers=user_headers("role:request"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "unsupported_role_governance_target"

    assert calls == []


def test_role_governance_allows_known_department_agent_target(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "department_agent",
            "target_id": "platform",
            "reason": "Need platform agent handoff review",
            "workspace_id": "workspace-a",
        },
        headers=user_headers("role:request"),
    )

    assert response.status_code == 200
    assert response.json()["target_id"] == "platform"
    assert calls[0] == ("tenant_exists", {"tenant_id": "tenant-a"})
    assert calls[1][1]["payload_json"]["target_type"] == "department_agent"
    assert calls[1][1]["payload_json"]["target_id"] == "platform"
    assert_no_sensitive_material(response.json())
    assert_no_sensitive_material(calls)


def test_role_governance_admin_write_ids_reject_permission_like_values(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    approval = client.post(
        "/api/role-governance/approvals/role:manage/approve",
        json={"decision_note": "approved"},
        headers=admin_headers(),
    )
    rollback = client.post(
        "/api/role-governance/audit/settings:manage/rollback",
        json={"reason": "undo mistaken approval"},
        headers=admin_headers(),
    )

    assert approval.status_code == 400
    assert approval.json()["detail"] == "unsupported_role_governance_request_id"
    assert rollback.status_code == 400
    assert rollback.json()["detail"] == "unsupported_role_governance_audit_id"
    assert calls == []


def test_role_governance_admin_approval_rejection_and_rollback_fail_closed(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    denied_approval = client.post(
        "/api/role-governance/approvals/req-1/approve",
        json={"decision_note": "approved"},
        headers=user_headers(),
    )
    assert denied_approval.status_code == 403
    assert denied_approval.json()["detail"] == "missing_permission:role:manage"

    approved = client.post(
        "/api/role-governance/approvals/req-1/approve",
        json={"decision_note": "approved", "rollback_id": "rb-1"},
        headers=admin_headers(),
    )
    assert approved.status_code == 200
    assert approved.json() == {
        "target_type": "role_request",
        "target_id": "req-1",
        "operation": "approve",
        "status": "queued",
        "audit_id": "audit-role-governance-contract",
        "message": "role governance approve accepted for audited execution",
    }

    denied_rejection = client.post(
        "/api/role-governance/approvals/req-1/reject",
        json={"decision_note": "not enough context"},
        headers=user_headers(),
    )
    assert denied_rejection.status_code == 403
    assert denied_rejection.json()["detail"] == "missing_permission:role:manage"

    rejected = client.post(
        "/api/role-governance/approvals/req-1/reject",
        json={"decision_note": "not enough context"},
        headers=admin_headers(),
    )
    assert rejected.status_code == 200
    assert rejected.json()["operation"] == "reject"

    denied_rollback = client.post(
        "/api/role-governance/audit/audit-1/rollback",
        json={"reason": "undo mistaken approval"},
        headers=user_headers(),
    )
    assert denied_rollback.status_code == 403
    assert denied_rollback.json()["detail"] == "missing_permission:role:manage"

    rollback = client.post(
        "/api/role-governance/audit/audit-1/rollback",
        json={"reason": "undo mistaken approval"},
        headers=admin_headers(),
    )
    assert rollback.status_code == 200
    assert rollback.json() == {
        "target_type": "role_audit",
        "target_id": "audit-1",
        "operation": "rollback",
        "status": "queued",
        "audit_id": "audit-role-governance-contract",
        "message": "role governance rollback accepted for audited execution",
    }

    tenant_checks = [payload for name, payload in calls if name == "tenant_exists"]
    assert tenant_checks == [{"tenant_id": "tenant-a"}, {"tenant_id": "tenant-a"}, {"tenant_id": "tenant-a"}]
    audit_actions = [payload["action"] for name, payload in calls if name == "audit"]
    assert audit_actions == [
        "role_governance.approval.approve_requested",
        "role_governance.approval.reject_requested",
        "role_governance.rollback.requested",
    ]
    assert all(payload["tenant_id"] == "tenant-a" for name, payload in calls if name == "audit")
    assert all(payload["payload_json"]["secret_material_projected"] is False for name, payload in calls if name == "audit")
    assert_no_sensitive_material(calls)


def test_role_governance_rejects_secret_bearing_or_invalid_request_payload(monkeypatch):
    calls = install_role_governance_route_fakes(monkeypatch)
    client = TestClient(create_app())

    secret_response = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "skill_developer",
            "reason": "Need Skill workbench access",
            "private_payload": "sk-live-secret-value",
        },
        headers=user_headers("role:request"),
    )
    assert secret_response.status_code == 422
    assert_no_sensitive_material(secret_response.json())

    unsafe_id = client.post(
        "/api/role-governance/requests",
        json={
            "target_type": "role",
            "target_id": "../platform_admin",
            "reason": "Need Skill workbench access",
        },
        headers=user_headers("role:request"),
    )
    assert unsafe_id.status_code == 400
    assert "target_id" in unsafe_id.json()["detail"]
    assert calls == []

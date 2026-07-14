from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers(*, user_id="user-a", roles="user", tenant_id="tenant-a", permissions=None):
    if permissions is None and set(roles.split(",")).intersection({"admin", "developer", "platform_admin"}):
        permissions = "settings:manage"
    result = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": tenant_id,
    }
    if permissions:
        result["X-AI-Permissions"] = permissions
    return result


def permission_row(**overrides):
    values = {
        "id": "tpr-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "tool_id": "ragflow-knowledge-search",
        "tool_call_id": "call-a",
        "action": "execute",
        "risk_level": "low",
        "write_capable": False,
        "status": "pending",
        "decision": None,
        "reason": "",
        "request_payload_json": {},
        "decision_payload_json": {},
    }
    values.update(overrides)
    return values


def test_tool_permission_request_uses_registry_risk_and_emits_event(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        assert tenant_id == "tenant-a"
        assert tool_id == "ragflow-knowledge-search"
        return {"id": tool_id, "risk_level": "high", "write_capable": True}

    async def fake_create_tool_permission_request(conn, **kwargs):
        calls.append(("request", kwargs))
        return permission_row(risk_level=kwargs["risk_level"], write_capable=kwargs["write_capable"])

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.create_tool_permission_request", fake_create_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/request",
        headers=headers(),
        json={
            "tool_id": "ragflow-knowledge-search",
            "tool_call_id": "call-a",
            "risk_level": "low",
            "write_capable": False,
            "request_payload": {"query": "SOP"},
        },
    )

    assert response.status_code == 200
    body = response.json()["permission_request"]
    assert body["permission_request_id"] == "tpr-a"
    assert body["risk_level"] == "high"
    assert body["write_capable"] is True
    assert calls[1][1]["event_type"] == "tool_permission_requested"


def test_tool_permission_request_redacts_reason_before_event_payload(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "risk_level": "high", "write_capable": True}

    async def fake_create_tool_permission_request(conn, **kwargs):
        return permission_row(reason=kwargs["reason"], risk_level=kwargs["risk_level"], write_capable=kwargs["write_capable"])

    async def fake_append_event(conn, **kwargs):
        calls.append(kwargs)
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.create_tool_permission_request", fake_create_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/request",
        headers=headers(),
        json={
            "tool_id": "ragflow-knowledge-search",
            "tool_call_id": "call-a",
            "reason": "needs command token=hidden /var/lib/ai-platform/run-a/private.log",
        },
    )

    assert response.status_code == 200
    event_payload = calls[0]["payload"]
    assert event_payload["reason"] == ""
    assert "hidden" not in str(event_payload)
    assert "/var/lib/ai-platform" not in str(event_payload)


def test_same_tenant_admin_decides_another_users_request_and_audits_actor(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_for_tenant(conn, *, tenant_id, run_id, request_id):
        assert (tenant_id, run_id, request_id) == ("tenant-a", "run-a", "tpr-a")
        return permission_row(
            user_id="run-owner",
            risk_level="high",
            write_capable=True,
            reason="需要运行写入命令",
            request_payload_json={"command": "operator-secret-command"},
        )

    async def fake_decide_tool_permission_request(conn, **kwargs):
        calls.append(("decision", kwargs))
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="high",
            write_capable=True,
            request_payload_json={"command_sha256": "a" * 64},
            expires_at="2026-06-05T12:15:00Z",
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant", fake_get_tool_permission_request_for_tenant)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(user_id="admin-a", roles="admin"),
        json={"decision": "allow_once", "reason": "read-only query"},
    )

    assert response.status_code == 200
    assert response.json()["permission_request"]["decision"] == "allow_once"
    assert response.json()["permission_request"]["expires_at"] == "2026-06-05T12:15:00Z"
    assert "operator-secret-command" not in response.text
    assert calls[0][1]["expires_in_seconds"] == 900
    assert calls[0][1]["user_id"] == "run-owner"
    assert calls[1][1]["event_type"] == "tool_permission_decided"
    assert calls[1][1]["payload"] == {
        "visible_to_user": True,
        "permission_request_id": "tpr-a",
        "tool_id": "ragflow-knowledge-search",
        "tool_call_id": "call-a",
        "action": "execute",
        "risk_level": "high",
        "write_capable": True,
        "decision": "allow_once",
        "reason": "read-only query",
        "status": "decided",
        "expires_at": "2026-06-05T12:15:00Z",
    }
    assert calls[2][1]["action"] == "tool.permission.decision"
    assert calls[2][1]["user_id"] == "admin-a"


def test_tool_permission_decision_serializes_datetime_expiry_for_event_payload(monkeypatch):
    calls = []
    expires_at = datetime(2026, 6, 5, 12, 15, tzinfo=timezone.utc)

    async def fake_get_tool_permission_request_for_tenant(conn, *, tenant_id, run_id, request_id):
        return permission_row(risk_level="medium", write_capable=False)

    async def fake_decide_tool_permission_request(conn, **kwargs):
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="medium",
            write_capable=False,
            expires_at=expires_at,
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(kwargs)
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(kwargs)
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant", fake_get_tool_permission_request_for_tenant)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(roles="admin"),
        json={"decision": "allow_once", "reason": "read-only query"},
    )

    assert response.status_code == 200
    assert calls[0]["event_type"] == "tool_permission_decided"
    assert calls[0]["payload"]["expires_at"] == "2026-06-05T12:15:00+00:00"


def test_tool_permission_decision_redacts_reason_before_event_payload(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_for_tenant(conn, *, tenant_id, run_id, request_id):
        return permission_row(risk_level="medium", write_capable=True)

    async def fake_decide_tool_permission_request(conn, **kwargs):
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="medium",
            write_capable=True,
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant", fake_get_tool_permission_request_for_tenant)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(roles="admin"),
        json={"decision": "deny", "reason": "deny token=hidden /var/lib/ai-platform/run-a/private.log"},
    )

    assert response.status_code == 200
    event_payload = calls[0][1]["payload"]
    assert event_payload["reason"] == ""
    assert "hidden" not in str(event_payload)
    assert "/var/lib/ai-platform" not in str(event_payload)


def test_ordinary_run_owner_cannot_decide_permission_requests(monkeypatch):
    async def fail_direct_lookup(*args, **kwargs):
        raise AssertionError("ordinary users must be rejected before direct lookup")

    async def fail_inbox_lookup(*args, **kwargs):
        raise AssertionError("ordinary users must be rejected before inbox lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant", fail_direct_lookup)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant", fail_inbox_lookup)
    client = TestClient(create_app())

    direct = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(roles="user"),
        json={"decision": "allow_once"},
    )
    inbox = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(roles="user"),
        json={"decision": "deny"},
    )

    assert direct.status_code == 403
    assert inbox.status_code == 403
    assert direct.json()["detail"] == "not_ai_admin"
    assert inbox.json()["detail"] == "not_ai_admin"


def test_admin_direct_decision_hides_cross_tenant_or_run_mismatches(monkeypatch):
    async def fake_get_tool_permission_request_for_tenant(conn, *, tenant_id, run_id, request_id):
        assert (tenant_id, run_id, request_id) == ("tenant-a", "run-other", "tpr-other")
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant", fake_get_tool_permission_request_for_tenant)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-other/tool-permissions/tpr-other/decision",
        headers=headers(user_id="admin-a", roles="admin", tenant_id="tenant-a"),
        json={"decision": "deny"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "tool_permission_request_not_found"


def test_tool_permission_response_hides_internal_request_and_decision_payloads(monkeypatch):
    async def fake_get_tool_permission_request(conn, *, tenant_id, user_id, run_id, request_id):
        return permission_row(
            reason="operator pasted command token=tool-reason-token /var/lib/ai-platform/run-a",
            request_payload_json={
                "source": "claude_agent_sdk_hook",
                "tool_name": "Bash",
                "tool_input_keys": ["command"],
                "command_length": 39,
                "command": "python write_business_system.py --id 123",
                "raw_command": "python write_business_system.py --id 123",
                "command_text": "python write_business_system.py --id 123",
                "command_sha256": "a" * 64,
                "input_sha256": "c" * 64,
                "fingerprint": "bash:write-system",
                "command_fingerprint": "bash:write-system",
                "input_fingerprint": "mcp:input",
            },
            decision_payload_json={
                "source": "operator_decision",
                "tool_name": "Bash",
                "command": "python write_business_system.py --id 123",
                "raw_command": "python write_business_system.py --id 123",
                "command_text": "python write_business_system.py --id 123",
                "command_sha256": "b" * 64,
                "input_sha256": "d" * 64,
                "fingerprint": "bash:write-system",
                "command_fingerprint": "bash:write-system",
                "input_fingerprint": "mcp:input",
            }
        )

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request", fake_get_tool_permission_request)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/runs/run-a/tool-permissions/tpr-a",
        headers=headers(),
    )

    assert response.status_code == 200
    permission_request = response.json()["permission_request"]
    assert "request_payload" not in permission_request
    assert "decision_payload" not in permission_request
    assert permission_request["reason"] == ""
    assert "tool-reason-token" not in str(permission_request)
    assert "/var/lib/ai-platform" not in str(permission_request)
    assert "python write_business_system.py" not in str(permission_request)
    assert "bash:write-system" not in str(permission_request)


def test_tool_permission_inbox_rejects_ordinary_users_without_fetching(monkeypatch):
    async def fail_inbox_lookup(*args, **kwargs):
        raise AssertionError("unauthorized principals must not query the governance inbox")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant",
        fail_inbox_lookup,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/tool-permissions/inbox?status=pending&limit=10",
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


@pytest.mark.parametrize(
    "roles,permissions,expected_detail",
    [
        ("admin", "", "missing_permission:settings:manage"),
        ("user", "settings:manage", "not_ai_admin"),
    ],
)
def test_tool_permission_governance_requires_admin_and_settings_manage_capability(
    monkeypatch,
    roles,
    permissions,
    expected_detail,
):
    async def fail_lookup(*args, **kwargs):
        raise AssertionError("failed-closed governance must reject before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant",
        fail_lookup,
    )
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant",
        fail_lookup,
    )
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant",
        fail_lookup,
    )
    client = TestClient(create_app())
    auth = headers(user_id="principal-a", roles=roles, permissions=permissions)

    responses = [
        client.get("/api/ai/tool-permissions/inbox", headers=auth),
        client.post(
            "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
            headers=auth,
            json={"decision": "deny"},
        ),
        client.post(
            "/api/ai/tool-permissions/inbox/tpr-a/decision",
            headers=auth,
            json={"decision": "deny"},
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert [response.json()["detail"] for response in responses] == [
        expected_detail,
        expected_detail,
        expected_detail,
    ]


def test_tool_permission_inbox_lists_tenant_requests_for_admin(monkeypatch):
    calls = []

    async def fake_list_tool_permission_inbox_for_tenant(conn, *, tenant_id, status, limit):
        calls.append((tenant_id, status, limit))
        return [
            permission_row(
                user_id="run-owner",
                reason="operator token=admin-inbox-secret",
                request_payload_json={"command": "admin-inbox-secret-command"},
            )
        ]

    async def fail_current_user_inbox(*args, **kwargs):
        raise AssertionError("admin governance inbox must not filter to the admin user")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant",
        fake_list_tool_permission_inbox_for_tenant,
    )
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.list_tool_permission_inbox",
        fail_current_user_inbox,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/tool-permissions/inbox?status=pending&limit=10",
        headers=headers(user_id="admin-a", roles="admin"),
    )

    assert response.status_code == 200
    assert calls == [("tenant-a", "pending", 10)]
    body = response.json()
    inbox_request = body["permission_requests"][0]
    assert inbox_request["request_id"] == "tpr-a"
    assert inbox_request["allowed_decisions"] == ["allow_once", "deny"]
    assert set(inbox_request) == {
        "request_id",
        "run_id",
        "tool_id",
        "tool_display",
        "risk_level",
        "write_capable",
        "status",
        "expires_at",
        "allowed_decisions",
    }
    serialized = str(body)
    assert "admin-inbox-secret" not in serialized
    assert "admin-inbox-secret-command" not in serialized
    assert "reason" not in serialized


def test_tool_permission_inbox_allows_run_scope_only_for_a_fingerprinted_request(monkeypatch):
    async def fake_list_tool_permission_inbox_for_tenant(conn, *, tenant_id, status, limit):
        return [
            permission_row(
                id="tpr-bash",
                tool_id="Bash",
                request_payload_json={
                    "command_sha256": "a" * 64,
                    "command": "secret command",
                },
            )
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant",
        fake_list_tool_permission_inbox_for_tenant,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/tool-permissions/inbox",
        headers=headers(user_id="admin-a", roles="admin"),
    )

    assert response.status_code == 200
    inbox_request = response.json()["permission_requests"][0]
    assert inbox_request["allowed_decisions"] == ["allow_once", "allow_for_run", "deny"]
    assert "a" * 64 not in str(inbox_request)
    assert "secret command" not in str(inbox_request)


def test_tool_permission_inbox_status_filters_pass_through(monkeypatch):
    calls = []

    async def fake_list_tool_permission_inbox(conn, *, tenant_id, status, limit):
        calls.append((status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant", fake_list_tool_permission_inbox)
    client = TestClient(create_app())

    decided_response = client.get(
        "/api/ai/tool-permissions/inbox?status=decided&limit=7",
        headers=headers(user_id="admin-a", roles="admin"),
    )
    all_response = client.get(
        "/api/ai/tool-permissions/inbox?status=all&limit=9",
        headers=headers(user_id="admin-a", roles="admin"),
    )

    assert decided_response.status_code == 200
    assert all_response.status_code == 200
    assert calls == [("decided", 7), ("all", 9)]


def test_tool_permission_inbox_admin_decision_writes_event_and_audit(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_by_id_for_tenant(conn, *, tenant_id, request_id):
        assert (tenant_id, request_id) == ("tenant-a", "tpr-a")
        return permission_row(
            user_id="run-owner",
            risk_level="high",
            write_capable=True,
            reason="needs write",
            request_payload_json={"command_sha256": "a" * 64},
        )

    async def fake_decide_tool_permission_request(conn, **kwargs):
        calls.append(("decision", kwargs))
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="high",
            write_capable=True,
            request_payload_json={"command_sha256": "a" * 64},
            expires_at="2026-06-05T12:15:00Z",
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant", fake_get_tool_permission_request_by_id_for_tenant)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(user_id="admin-a", roles="admin"),
        json={"decision": "allow_for_run", "reason": "approved from inbox", "expires_in_seconds": 1200},
    )

    assert response.status_code == 200
    assert response.json()["permission_request"]["request_id"] == "tpr-a"
    assert response.json()["permission_request"]["allowed_decisions"] == [
        "allow_once",
        "allow_for_run",
        "deny",
    ]
    assert calls[0][1]["run_id"] == "run-a"
    assert calls[0][1]["user_id"] == "run-owner"
    assert calls[0][1]["expires_in_seconds"] == 1200
    assert calls[1][1]["event_type"] == "tool_permission_decided"
    assert calls[1][1]["payload"]["permission_request_id"] == "tpr-a"
    assert calls[1][1]["payload"]["decision"] == "allow_for_run"
    assert calls[2][1]["action"] == "tool.permission.decision"
    assert calls[2][1]["user_id"] == "admin-a"


def test_tool_permission_inbox_decision_hides_cross_tenant_request(monkeypatch):
    async def fake_get_tool_permission_request_by_id_for_tenant(conn, *, tenant_id, request_id):
        assert (tenant_id, request_id) == ("tenant-a", "tpr-other")
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant", fake_get_tool_permission_request_by_id_for_tenant)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-other/decision",
        headers=headers(roles="admin"),
        json={"decision": "deny", "reason": "not mine"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "tool_permission_request_not_found"


def test_tool_permission_inbox_rejects_unfingerprinted_allow_for_run_before_update(monkeypatch):
    async def fake_get_tool_permission_request_by_id_for_tenant(conn, *, tenant_id, request_id):
        return permission_row(tool_id="ragflow-knowledge-search", request_payload_json={})

    async def fail_decide_tool_permission_request(conn, **kwargs):
        raise AssertionError("unsupported allow_for_run must fail before atomic update")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant",
        fake_get_tool_permission_request_by_id_for_tenant,
    )
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.decide_tool_permission_request",
        fail_decide_tool_permission_request,
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(user_id="admin-a", roles="admin"),
        json={"decision": "allow_for_run"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tool_permission_decision_not_supported"


def test_direct_admin_decision_rejects_unfingerprinted_allow_for_run_before_update(monkeypatch):
    async def fake_get_tool_permission_request_for_tenant(conn, *, tenant_id, run_id, request_id):
        assert (tenant_id, run_id, request_id) == ("tenant-a", "run-a", "tpr-a")
        return permission_row(tool_id="ragflow-knowledge-search", request_payload_json={})

    async def fail_decide_tool_permission_request(conn, **kwargs):
        raise AssertionError("unsupported allow_for_run must fail before atomic update")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.get_tool_permission_request_for_tenant",
        fake_get_tool_permission_request_for_tenant,
    )
    monkeypatch.setattr(
        "app.routes.tool_permissions.repositories.decide_tool_permission_request",
        fail_decide_tool_permission_request,
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(user_id="admin-a", roles="admin"),
        json={"decision": "allow_for_run"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tool_permission_decision_not_supported"


def test_tool_permission_inbox_decision_returns_409_for_already_decided_request(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_by_id_for_tenant(conn, *, tenant_id, request_id):
        return permission_row(status="decided", decision="deny")

    async def fake_decide_tool_permission_request(conn, **kwargs):
        calls.append(kwargs)
        return None

    async def fail_append_event(conn, **kwargs):
        raise AssertionError("already-decided inbox request must not emit a new event")

    async def fail_append_audit_log(conn, **kwargs):
        raise AssertionError("already-decided inbox request must not emit a new audit log")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id_for_tenant", fake_get_tool_permission_request_by_id_for_tenant)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fail_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(roles="admin"),
        json={"decision": "allow_once", "reason": "too late"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tool_permission_request_not_pending"
    assert calls[0]["request_id"] == "tpr-a"

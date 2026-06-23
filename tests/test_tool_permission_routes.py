from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers():
    return {
        "X-AI-User-ID": "user-a",
        "X-AI-User-Name": "User A",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
    }


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


def test_tool_permission_decision_writes_event_and_audit(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request(conn, *, tenant_id, user_id, run_id, request_id):
        assert (tenant_id, user_id, run_id, request_id) == ("tenant-a", "user-a", "run-a", "tpr-a")
        return permission_row(risk_level="high", write_capable=True, reason="需要运行写入命令")

    async def fake_decide_tool_permission_request(conn, **kwargs):
        calls.append(("decision", kwargs))
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="high",
            write_capable=True,
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
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request", fake_get_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(),
        json={"decision": "allow_once", "reason": "read-only query"},
    )

    assert response.status_code == 200
    assert response.json()["permission_request"]["decision"] == "allow_once"
    assert response.json()["permission_request"]["expires_at"] == "2026-06-05T12:15:00Z"
    assert calls[0][1]["expires_in_seconds"] == 900
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


def test_tool_permission_decision_serializes_datetime_expiry_for_event_payload(monkeypatch):
    calls = []
    expires_at = datetime(2026, 6, 5, 12, 15, tzinfo=timezone.utc)

    async def fake_get_tool_permission_request(conn, *, tenant_id, user_id, run_id, request_id):
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
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request", fake_get_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(),
        json={"decision": "allow_once", "reason": "read-only query"},
    )

    assert response.status_code == 200
    assert calls[0]["event_type"] == "tool_permission_decided"
    assert calls[0]["payload"]["expires_at"] == "2026-06-05T12:15:00+00:00"


def test_tool_permission_decision_redacts_reason_before_event_payload(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request(conn, *, tenant_id, user_id, run_id, request_id):
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
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request", fake_get_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers(),
        json={"decision": "deny", "reason": "deny token=hidden /var/lib/ai-platform/run-a/private.log"},
    )

    assert response.status_code == 200
    event_payload = calls[0][1]["payload"]
    assert event_payload["reason"] == ""
    assert "hidden" not in str(event_payload)
    assert "/var/lib/ai-platform" not in str(event_payload)


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


def test_tool_permission_inbox_lists_current_user_requests(monkeypatch):
    calls = []

    async def fake_list_tool_permission_inbox(conn, *, tenant_id, user_id, status, limit):
        calls.append((tenant_id, user_id, status, limit))
        return [
            permission_row(
                id="tpr-pending",
                run_id="run-a",
                tool_call_id="call-pending",
                status="pending",
                reason="operator command token=secret-token /var/lib/ai-platform/run-a",
                request_payload_json={
                    "source": "claude_agent_sdk_hook",
                    "command": "python private.py",
                    "command_sha256": "a" * 64,
                    "input_sha256": "b" * 64,
                },
            )
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.list_tool_permission_inbox", fake_list_tool_permission_inbox)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/tool-permissions/inbox?status=pending&limit=10",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [("tenant-a", "user-a", "pending", 10)]
    assert body["total"] == 1
    assert body["permission_requests"][0]["permission_request_id"] == "tpr-pending"
    assert body["permission_requests"][0]["decision_endpoint"] == "/api/ai/tool-permissions/inbox/tpr-pending/decision"
    assert "request_payload" not in body["permission_requests"][0]
    assert "decision_payload" not in body["permission_requests"][0]
    serialized = str(body)
    assert "secret-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "python private.py" not in serialized
    assert "aaaaaaaa" not in serialized


def test_tool_permission_inbox_status_filters_pass_through(monkeypatch):
    calls = []

    async def fake_list_tool_permission_inbox(conn, *, tenant_id, user_id, status, limit):
        calls.append((status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.list_tool_permission_inbox", fake_list_tool_permission_inbox)
    client = TestClient(create_app())

    decided_response = client.get(
        "/api/ai/tool-permissions/inbox?status=decided&limit=7",
        headers=headers(),
    )
    all_response = client.get(
        "/api/ai/tool-permissions/inbox?status=all&limit=9",
        headers=headers(),
    )

    assert decided_response.status_code == 200
    assert all_response.status_code == 200
    assert calls == [("decided", 7), ("all", 9)]


def test_tool_permission_inbox_decision_writes_event_and_audit(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_by_id(conn, *, tenant_id, user_id, request_id):
        assert (tenant_id, user_id, request_id) == ("tenant-a", "user-a", "tpr-a")
        return permission_row(risk_level="high", write_capable=True, reason="needs write")

    async def fake_decide_tool_permission_request(conn, **kwargs):
        calls.append(("decision", kwargs))
        return permission_row(
            status="decided",
            decision=kwargs["decision"],
            reason=kwargs["reason"],
            risk_level="high",
            write_capable=True,
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
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id", fake_get_tool_permission_request_by_id)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(),
        json={"decision": "allow_for_run", "reason": "approved from inbox", "expires_in_seconds": 1200},
    )

    assert response.status_code == 200
    assert response.json()["permission_request"]["decision"] == "allow_for_run"
    assert response.json()["permission_request"]["decision_endpoint"] == "/api/ai/tool-permissions/inbox/tpr-a/decision"
    assert calls[0][1]["run_id"] == "run-a"
    assert calls[0][1]["expires_in_seconds"] == 1200
    assert calls[1][1]["event_type"] == "tool_permission_decided"
    assert calls[1][1]["payload"]["permission_request_id"] == "tpr-a"
    assert calls[1][1]["payload"]["decision"] == "allow_for_run"
    assert calls[2][1]["action"] == "tool.permission.decision"


def test_tool_permission_inbox_decision_returns_404_for_other_user_request(monkeypatch):
    async def fake_get_tool_permission_request_by_id(conn, *, tenant_id, user_id, request_id):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id", fake_get_tool_permission_request_by_id)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-other/decision",
        headers=headers(),
        json={"decision": "deny", "reason": "not mine"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "tool_permission_request_not_found"


def test_tool_permission_inbox_decision_returns_409_for_already_decided_request(monkeypatch):
    calls = []

    async def fake_get_tool_permission_request_by_id(conn, *, tenant_id, user_id, request_id):
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
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request_by_id", fake_get_tool_permission_request_by_id)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.decide_tool_permission_request", fake_decide_tool_permission_request)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_event", fail_append_event)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/tool-permissions/inbox/tpr-a/decision",
        headers=headers(),
        json={"decision": "allow_once", "reason": "too late"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tool_permission_request_not_pending"
    assert calls[0]["request_id"] == "tpr-a"

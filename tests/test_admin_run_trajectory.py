from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.runs.application.admin_trajectory import project_admin_trajectory_page


def _row(sequence, event_type, payload, *, visible=True, metadata=None):
    return {
        "id": f"evt_{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "stage": "agent_kernel",
        "visible_to_user": visible,
        "payload_json": {**payload, "__stream_v4": {"version": 1, **(metadata or {})}},
        "created_at": "2026-09-23T00:00:00Z",
    }


def test_trajectory_projects_typed_committed_events_without_private_payloads():
    rows = [
        _row(1, "message.delta", {"delta": "secret fragment"}, metadata={"attempt_id": "attempt_a", "message_id": "msg_a", "execution_lease_id": "lease_private"}),
        _row(2, "tool.started", {"operation_id": "op_a", "category": "read", "display_name": "Read"}, metadata={"attempt_id": "attempt_a"}),
        _row(3, "tool.completed", {"operation_id": "op_a", "category": "read", "display_name": "Read", "duration_ms": 12, "result_summary": "Read completed"}, metadata={"attempt_id": "attempt_a"}),
        _row(4, "tool.failed", {"operation_id": "op_b", "category": "execute", "display_name": "Execute", "duration_ms": 2, "failure_category": "timeout"}),
        _row(5, "tool.completed", {"operation_id": "op_private", "category": "read", "display_name": "Private", "duration_ms": 1}, visible=False),
        _row(6, "tool.completed", {"operation_id": "op_invalid", "category": "read", "display_name": "Read", "duration_ms": 1, "raw_output": "secret"}),
        _row(7, "custom.executor.event", {"raw_output": "secret"}),
        {**_row(8, "message.delta", {"delta": "legacy"}), "stage": "legacy"},
    ]

    result = project_admin_trajectory_page(rows, sanitize_text=lambda value: value)

    assert [event["kind"] for event in result["events"]] == ["message", "action", "observation", "error"]
    assert [event["sequence"] for event in result["events"]] == [1, 2, 3, 4]
    assert result["events"][0]["attempt_id"] == "attempt_a"
    assert result["events"][1]["operation_id"] == result["events"][2]["operation_id"] == "op_a"
    assert result["omitted"] == {"private": 1, "unsupported": 2, "invalid": 1}
    serialized = str(result)
    assert "secret fragment" not in serialized
    assert "raw_output" not in serialized
    assert "lease_private" not in serialized
    assert "op_private" not in serialized


def test_trajectory_does_not_accept_unknown_fields_or_forged_event_ids():
    rows = [
        _row(1, "run.failed", {"terminal_event_id": "terminal_a", "hydrate_required": True, "projection_version": "ai-platform.chat-public-projection.v1", "code": "run_failed", "default_message": "safe", "detail": None}),
        {**_row(2, "message.started", {}), "id": "evt/unsafe"},
        _row(3, "message.delta", {"delta": "x", "prompt": "private"}),
    ]
    result = project_admin_trajectory_page(rows, sanitize_text=lambda value: value)
    assert len(result["events"]) == 1
    assert result["events"][0]["kind"] == "error"
    assert result["events"][0]["outcome"] == "run_failed"
    assert result["omitted"]["invalid"] == 2


@asynccontextmanager
async def _transaction():
    yield object()


def _headers(role="admin", tenant="tenant_a"):
    return {
        "x-ai-user-id": "user_a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": tenant,
        "x-ai-roles": role,
        "x-ai-gateway-secret": "test-secret",
    }


def _auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


def test_admin_trajectory_route_is_read_only_tenant_scoped_and_paginated(monkeypatch):
    calls = []

    async def get_run(_conn, *, tenant_id, run_id):
        calls.append(("run", tenant_id, run_id))
        return {"id": run_id} if tenant_id == "tenant_a" else None

    async def list_events(_conn, *, tenant_id, run_id, after_sequence, limit):
        calls.append(("events", tenant_id, run_id, after_sequence, limit))
        return [
            _row(11, "message.started", {}),
            _row(12, "message.delta", {"delta": "private text"}),
        ]

    monkeypatch.setattr("app.auth.get_settings", _auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", _transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.get_run", get_run)
    monkeypatch.setattr("app.routes.admin_runs.repositories.list_run_events", list_events)
    client = TestClient(create_app())

    denied = client.get("/api/ai/admin/runs/run_a/trajectory", headers=_headers("user"))
    assert denied.status_code == 403
    missing = client.get("/api/ai/admin/runs/run_a/trajectory", headers=_headers(tenant="tenant_b"))
    assert missing.status_code == 404
    response = client.get("/api/ai/admin/runs/run_a/trajectory?after_sequence=10&limit=1", headers=_headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["contract_version"] == "ai-platform.admin-run-trajectory.v1"
    assert response.json()["has_more"] is True
    assert response.json()["next_after_sequence"] == 11
    assert [event["sequence"] for event in response.json()["events"]] == [11]
    assert calls == [
        ("run", "tenant_b", "run_a"),
        ("run", "tenant_a", "run_a"),
        ("events", "tenant_a", "run_a", 10, 2),
    ]

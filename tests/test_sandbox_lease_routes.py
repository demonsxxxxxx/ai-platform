from contextlib import asynccontextmanager

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


def lease_row(**overrides):
    values = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "sandbox_mode": "ephemeral",
        "provider": "fake",
        "status": "active",
        "browser_enabled": False,
        "resource_limits_json": {},
        "user_visible_payload_json": {"workspace": "/workspace", "inputs": "/workspace/inputs"},
        "lease_payload_json": {},
        "release_reason": "",
    }
    values.update(overrides)
    return values


def test_create_sandbox_lease_records_run_scoped_lease_and_event(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_sandbox_lease(conn, **kwargs):
        calls.append(("lease", kwargs))
        return lease_row(
            sandbox_mode=kwargs["sandbox_mode"],
            provider=kwargs["provider"],
            resource_limits_json=kwargs["resource_limits_json"],
            lease_payload_json=kwargs["lease_payload_json"],
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.create_sandbox_lease", fake_create_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases",
        headers=headers(),
        json={
            "sandbox_mode": "ephemeral",
            "provider": "fake",
            "ttl_seconds": 600,
            "resource_limits": {"max_seconds": 60},
            "lease_payload": {"purpose": "test"},
        },
    )

    assert response.status_code == 200
    body = response.json()["sandbox_lease"]
    assert body["lease_id"] == "lease-a"
    assert body["workspace"] == {"workspace": "/workspace", "inputs": "/workspace/inputs"}
    assert calls[1][1]["event_type"] == "sandbox_lease_created"


def test_renew_expired_sandbox_lease_fails_closed(monkeypatch):
    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return lease_row()

    async def fake_renew_sandbox_lease(conn, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.renew_sandbox_lease", fake_renew_sandbox_lease)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/renew",
        headers=headers(),
        json={"ttl_seconds": 600},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "sandbox_lease_not_active"


def test_release_sandbox_lease_records_release_event(monkeypatch):
    calls = []

    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return lease_row()

    async def fake_release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs))
        return lease_row(status="released", release_reason=kwargs["reason"])

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.release_sandbox_lease", fake_release_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/release",
        headers=headers(),
        json={"reason": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["sandbox_lease"]["status"] == "released"
    assert calls[1][1]["event_type"] == "sandbox_lease_released"

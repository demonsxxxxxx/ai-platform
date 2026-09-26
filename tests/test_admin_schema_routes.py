from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.support.db_transactions import opaque_transaction


def _settings():
    return Settings(frontend_poc_auth_enabled=True)


def _headers(roles):
    return {
        "X-AI-User-ID": "user-a",
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": "default",
    }


def test_admin_apply_schema_route_is_retired(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", _settings)

    response = TestClient(create_app()).post(
        "/api/ai/admin/apply-schema", headers=_headers("platform_admin")
    )

    assert response.status_code == 404


def test_admin_retention_status_exposes_unsupported_policy_and_age_backlog(monkeypatch):
    configured = Settings(frontend_poc_auth_enabled=True, run_event_retention_days=7)
    calls = []

    async def fake_backlog(_conn, *, retention_days):
        calls.append(retention_days)
        return {
            "run_events_age_eligible": 12,
            "object_delete_dead_letter": 1,
            "object_delete_reconcile_required": 1,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: configured)
    monkeypatch.setattr("app.routes.health.get_settings", lambda: configured)
    monkeypatch.setattr("app.routes.health.transaction", opaque_transaction)
    monkeypatch.setattr("app.persistence.retention.get_data_retention_backlog", fake_backlog)

    response = TestClient(create_app()).get(
        "/api/ai/admin/retention/status",
        headers=_headers("platform_admin"),
    )

    assert response.status_code == 200
    assert response.json()["policy"]["unsupported_not_implemented"] == ["run_events"]
    assert response.json()["backlog"]["run_events_age_eligible"] == 12
    assert response.json()["alerts"] == {
        "object_delete_dead_letter": True,
        "object_delete_reconcile_required": True,
    }
    assert calls[0]["run_events"] == 7


def test_admin_can_requeue_dead_letter_object_deletion(monkeypatch):
    calls = []

    async def fake_requeue(_conn, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.auth.get_settings", _settings)
    monkeypatch.setattr("app.routes.health.transaction", opaque_transaction)
    monkeypatch.setattr(
        "app.persistence.object_deletions.requeue_dead_letter_object_deletion",
        fake_requeue,
    )

    response = TestClient(create_app()).post(
        "/api/ai/admin/retention/object-deletions/out-a/requeue",
        headers=_headers("platform_admin"),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "requeued", "outbox_id": "out-a"}
    assert calls == [{"outbox_id": "out-a", "tenant_id": "default"}]


def test_admin_requeue_fails_closed_for_wrong_state(monkeypatch):
    async def fake_requeue(_conn, **_kwargs):
        return False

    monkeypatch.setattr("app.auth.get_settings", _settings)
    monkeypatch.setattr("app.routes.health.transaction", opaque_transaction)
    monkeypatch.setattr(
        "app.persistence.object_deletions.requeue_dead_letter_object_deletion",
        fake_requeue,
    )

    response = TestClient(create_app()).post(
        "/api/ai/admin/retention/object-deletions/out-a/requeue",
        headers=_headers("platform_admin"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "object_deletion_not_requeueable"

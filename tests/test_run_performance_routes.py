"""HTTP ownership and safe projection for Run performance observations."""
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.runs.api import RunDiagnosticsService
from tests.test_admin_run_detail import auth_settings, headers

SUBMISSION = "ba58a18e-4719-4a94-a9bb-ab5c248602e8"


class ReadConnection:
    async def execute(self, sql):
        assert sql == "set local statement_timeout = '5s'"


@asynccontextmanager
async def transaction():
    yield ReadConnection()


class RecordingDiagnostics(RunDiagnosticsService):
    def __init__(self):
        object.__setattr__(self, "calls", [])

    async def read_monitor(self, conn, **kwargs):
        self.calls.append(kwargs)
        return {"measurement": "run_lifecycle.v1", "runs": []}

    async def read_admin_investigation(self, conn, **kwargs):
        self.calls.append(kwargs)
        return None if kwargs["run_id"] == "missing" else {"run_id": kwargs["run_id"]}

    async def record_browser_latency(self, conn, **kwargs):
        self.calls.append(kwargs)
        return {"recorded": True}


@pytest.fixture
def monitor_client(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", transaction)
    monkeypatch.setattr("app.routes.chat.transaction", transaction)
    diagnostics = RecordingDiagnostics()
    app = create_app()
    app.state.run_diagnostics_service = diagnostics
    client = TestClient(app)
    yield client, diagnostics
    client.close()


@pytest.mark.parametrize("path", ["/admin/runs/monitor", "/admin/runs/run-a/investigation"])
def test_monitor_routes_require_ai_admin(monitor_client, path):
    client, diagnostics = monitor_client
    response = client.get(f"/api/ai{path}", headers=headers("user"))
    assert response.status_code == 403 and response.json()["detail"] == "not_ai_admin"
    assert diagnostics.calls == []


def test_monitor_route_passes_only_authorized_scope_and_is_read_only(monitor_client, monkeypatch):
    client, diagnostics = monitor_client
    async def forbidden(*args, **kwargs):
        raise AssertionError("performance query must not invoke live queue or maintenance")
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", forbidden)
    response = client.get("/api/ai/admin/runs/monitor", headers=headers(), params={
        "hours": 168, "user_id": "user-a", "agent_id": "agent-a", "model_id": "model-a",
        "model_gateway_revision": 3, "status": "failed", "order": "slow", "limit": 50,
    })
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert diagnostics.calls == [{
        "tenant_id": "default", "hours": 168, "from_at": None, "to_at": None,
        "user_id": "user-a", "agent_id": "agent-a", "model_id": "model-a",
        "model_gateway_revision": 3, "status": "failed", "order": "slow", "limit": 50,
    }]


@pytest.mark.parametrize("params,expected", [({"hours": 169}, 422), ({"limit": 101}, 422), ({"order": "unsafe"}, 422), ({"status": "unsafe"}, 400)])
def test_monitor_route_rejects_unbounded_or_unknown_scope(monitor_client, params, expected):
    client, diagnostics = monitor_client
    assert client.get("/api/ai/admin/runs/monitor", headers=headers(), params=params).status_code == expected
    assert diagnostics.calls == []


def test_investigation_missing_run_is_tenant_scoped(monitor_client):
    client, diagnostics = monitor_client
    response = client.get("/api/ai/admin/runs/missing/investigation", headers=headers())
    assert response.status_code == 404 and response.json()["detail"] == "run_not_found"
    assert diagnostics.calls[0]["tenant_id"] == "default"


@pytest.mark.parametrize("case", ["valid", "missing-submission", "missing-run", "wrong-run"])
def test_browser_latency_uses_principal_submission_and_authorized_outcome(monitor_client, monkeypatch, case):
    client, diagnostics = monitor_client
    scopes = []
    async def get_submission(conn, **kwargs):
        scopes.append(kwargs)
        if case == "missing-submission":
            return None
        return {"submission_id": SUBMISSION, "state": "queued", "run_id": "run-a",
                "outcome_json": {"session_id": "session-a", "run_id": "run-a", "status": "queued"}}
    async def get_run(conn, **kwargs):
        scopes.append(kwargs)
        return None if case == "missing-run" else {"status": "running"}
    monkeypatch.setattr("app.routes.chat.persistence_chat_submissions.get_chat_submission", get_submission)
    monkeypatch.setattr("app.routes.chat.runs_creation.get_authorized_run", get_run)
    response = client.post(f"/api/ai/chat/submissions/{SUBMISSION}/latency", headers=headers("user"), json={
        "run_id": "run-other" if case == "wrong-run" else "run-a", "duration_ms": 1200,
    })
    assert response.headers["cache-control"] == "private, no-store"
    assert scopes[0] == {"tenant_id": "default", "user_id": "admin-a", "submission_id": SUBMISSION}
    if len(scopes) > 1:
        assert scopes[1] == {"tenant_id": "default", "user_id": "admin-a", "run_id": "run-a"}
    if case == "valid":
        assert response.status_code == 200 and response.json() == {"recorded": True}
        assert diagnostics.calls == [{"tenant_id": "default", "run_id": "run-a", "duration_ms": 1200}]
    else:
        assert response.status_code == 404 and diagnostics.calls == []


@pytest.mark.parametrize("body", [
    {"run_id": "run-a", "duration_ms": True},
    {"run_id": "run-a", "duration_ms": -1},
    {"run_id": "run-a", "duration_ms": 86400001},
    {"run_id": "run-a", "duration_ms": 1200, "text": "PRIVATE_TEXT_MARKER"},
    {"run_id": "PRIVATE_URL https://secret.test", "duration_ms": 1200},
])
def test_browser_latency_validation_never_echoes_rejected_fields(monitor_client, body):
    client, diagnostics = monitor_client
    response = client.post(f"/api/ai/chat/submissions/{UUID(SUBMISSION)}/latency", headers=headers("user"), json=body)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_browser_latency"}
    assert "no-store" in response.headers["cache-control"]
    assert diagnostics.calls == []
    assert "PRIVATE" not in response.text

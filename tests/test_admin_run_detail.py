from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


class EmptyPropagationCursor:
    async def fetchall(self):
        return []


class EmptyPropagationConnection:
    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if normalized.startswith("select child.id") and "from runs child" in normalized:
            return EmptyPropagationCursor()
        raise AssertionError(f"unexpected fake transaction sql: {normalized}")


@asynccontextmanager
async def fake_transaction():
    yield EmptyPropagationConnection()


def headers(roles="developer"):
    return {
        "x-ai-user-id": "admin-a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": roles,
        "x-ai-gateway-secret": "test-secret",
    }


def test_admin_run_detail_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_a", headers=headers("user"))

    assert response.status_code == 403


def test_admin_run_list_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs", headers=headers("user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_run_list_returns_tenant_scoped_summaries(monkeypatch):
    calls = []

    async def fake_list_admin_runs(conn, *, tenant_id, user_id=None, status=None, limit=50):
        calls.append((tenant_id, user_id, status, limit))
        return [
            {
                "run_id": "run_a",
                "session_id": "ses_a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "queued",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
                "error_code": None,
                "error_message": None,
            }
        ]

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "default"
        assert run_id == "run_a"
        return 2

    async def fake_get_queue_insight(tenant_id):
        assert tenant_id == "default"
        return {
            "tenant_id": tenant_id,
            "reason": "workers_busy",
            "depths": {"tenant_queued": 4, "tenant_processing": 1},
            "workers": {"active": 1},
            "capacity": {"available_worker_slots": 0},
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.list_admin_runs", fake_list_admin_runs, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs?user_id=user-a&status=queued&limit=25", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 25
    assert data["runs"][0]["run_id"] == "run_a"
    assert data["runs"][0]["user_id"] == "user-a"
    assert data["runs"][0]["queue_position"] == 2
    assert data["runs"][0]["queue_insight"]["reason"] == "workers_busy"
    assert calls == [("default", "user-a", "queued", 25)]


def test_admin_run_list_sanitizes_secret_like_error_fields(monkeypatch):
    async def fake_list_admin_runs(conn, *, tenant_id, user_id=None, status=None, limit=50):
        return [
            {
                "run_id": "run_a",
                "session_id": "ses_a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "failed",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
                "error_code": "executor_failure token=admin-list-code-token",
                "error_message": "failed token=admin-list-message-token /var/lib/ai-platform/run-a/out.log",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.list_admin_runs", fake_list_admin_runs, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs", headers=headers())

    assert response.status_code == 200
    run = response.json()["runs"][0]
    assert run["error_code"] == "executor_failure token=[redacted-secret]"
    assert run["error_message"] == ""
    assert "admin-list-code-token" not in str(run)
    assert "admin-list-message-token" not in str(run)
    assert "/var/lib/ai-platform" not in str(run)


def test_admin_run_cancel_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_a/cancel", headers=headers("user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_run_cancel_records_admin_actor(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        calls.append((tenant_id, admin_user_id, run_id))
        return {"run_id": run_id, "status": "cancel_requested"}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_a/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run_a", "session_id": None, "status": "cancel_requested"}
    assert calls == [("default", "admin-a", "run_a")]


def test_admin_cancel_queued_run_removes_queued_payload(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancelled"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_queued/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [("remove", "default", "run_queued")]


def test_admin_run_detail_returns_explainability_contract(monkeypatch):
    async def fake_get_admin_run_detail(conn, *, tenant_id, run_id):
        return {
            "run": {
                "run_id": run_id,
                "session_id": "ses_a",
                "user_id": "user-a",
                "status": "succeeded",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "created_at": None,
                "started_at": None,
                "finished_at": None,
                "input": {
                    "input": {"message": "审核文档"},
                    "intent": {"intent": "document_review", "selected_capability": "document_review"},
                },
                "result": {"message": "完成"},
            },
            "events": [{"event_id": "evt_a", "type": "run_succeeded", "stage": "worker", "message": "Run succeeded"}],
            "artifacts": [{"artifact_id": "art_a", "label": "审核 Word", "artifact_type": "reviewed_docx"}],
            "skill_snapshots": [
                {
                    "skill_id": "qa-file-reviewer",
                    "skill_version": "hash-a",
                    "content_hash": "hash-a",
                    "source": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                    "staged": True,
                    "used": True,
                    "usage": {
                        "used_skills_source": "executor_hook",
                        "inferred_used": False,
                    },
                    "created_at": None,
                }
            ],
            "audit": [],
        }

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        raise AssertionError("succeeded admin run should not query queue position")

    async def fake_get_queue_insight(tenant_id):
        raise AssertionError("succeeded admin run should not query queue insight")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.get_admin_run_detail", fake_get_admin_run_detail)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_a", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["run_id"] == "run_a"
    assert data["run"]["input"]["intent"]["selected_capability"] == "document_review"
    assert data["artifacts"][0]["artifact_id"] == "art_a"
    assert data["skill_snapshots"][0]["skill_id"] == "qa-file-reviewer"
    assert data["skill_snapshots"][0]["used"] is True
    assert data["skill_snapshots"][0]["usage"]["used_skills_source"] == "executor_hook"


def test_admin_run_detail_includes_live_queue_context_for_queued_run(monkeypatch):
    async def fake_get_admin_run_detail(conn, *, tenant_id, run_id):
        return {
            "run": {
                "run_id": run_id,
                "session_id": "ses-a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "queued",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "input": {"input": {"message": "build feature"}},
                "result": {},
            },
            "events": [],
            "steps": [],
            "artifacts": [],
            "audit": [],
        }

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "default"
        assert run_id == "run_queued"
        return 3

    async def fake_get_queue_insight(tenant_id):
        assert tenant_id == "default"
        return {
            "tenant_id": tenant_id,
            "reason": "worker_capacity_full",
            "depths": {"tenant_queued": 6, "tenant_processing": 1},
            "workers": {"active": 1},
            "capacity": {"available_worker_slots": 0},
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.get_admin_run_detail", fake_get_admin_run_detail)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_queued", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["queue_position"] == 3
    assert data["run"]["queue_insight"]["reason"] == "worker_capacity_full"

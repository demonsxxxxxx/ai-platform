from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def headers(permissions: str = "persona_preset:read,artifact:download") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def install_projection_route_fakes(monkeypatch, *, artifacts=None, sessions=None):
    from app.routes import frontend_projections

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []
    artifact_rows = list(artifacts or [])
    session_rows = list(sessions or [])

    async def fake_list_revealed_artifacts(conn, **kwargs):
        calls.append(("list_revealed_artifacts", kwargs))
        return [dict(row) for row in artifact_rows]

    async def fake_list_revealed_sessions(conn, **kwargs):
        calls.append(("list_revealed_sessions", kwargs))
        return [dict(row) for row in session_rows]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(frontend_projections, "transaction", fake_transaction)
    monkeypatch.setattr(
        frontend_projections.repositories,
        "list_revealed_artifacts",
        fake_list_revealed_artifacts,
        raising=False,
    )
    monkeypatch.setattr(
        frontend_projections.repositories,
        "list_revealed_artifact_sessions",
        fake_list_revealed_sessions,
        raising=False,
    )
    return calls


def test_persona_presets_read_projection_returns_default_catalog(monkeypatch):
    install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/persona-presets/?limit=20", headers=headers("persona_preset:read"))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["skip"] == 0
    assert body["limit"] == 20
    assert body["presets"][0]["id"]
    assert body["presets"][0]["scope"] == "global"
    assert body["presets"][0]["visibility"] == "public"
    assert body["presets"][0]["status"] == "published"


def test_persona_presets_fail_closed_without_read_permission(monkeypatch):
    install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/persona-presets/", headers=headers("artifact:download"))

    assert response.status_code == 403
    assert response.json()["detail"] == "missing_permission:persona_preset:read"


def test_persona_presets_project_use_snapshot_and_local_preference(monkeypatch):
    install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    use_response = client.post(
        "/api/persona-presets/default-general-agent/use",
        headers=headers("persona_preset:read"),
    )
    preference_response = client.patch(
        "/api/persona-presets/default-general-agent/preference",
        json={"is_favorite": True, "is_pinned": False},
        headers=headers("persona_preset:read"),
    )
    copy_denied_response = client.post(
        "/api/persona-presets/default-general-agent/copy",
        headers=headers("persona_preset:read"),
    )

    assert use_response.status_code == 200
    assert use_response.json()["preset_id"] == "default-general-agent"
    assert use_response.json()["missing_skill_names"] == []
    assert preference_response.status_code == 200
    assert preference_response.json()["is_favorite"] is True
    assert preference_response.json()["is_pinned"] is False
    assert copy_denied_response.status_code == 403
    assert copy_denied_response.json()["detail"] == "missing_permission:persona_preset:write"


def test_revealed_files_read_projection_returns_empty_shapes(monkeypatch):
    calls = install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    list_response = client.get("/api/files/revealed?page=2&page_size=25", headers=headers("artifact:download"))
    grouped_response = client.get("/api/files/revealed/grouped?page=1&page_size=10", headers=headers("artifact:download"))
    stats_response = client.get("/api/files/revealed/stats", headers=headers("artifact:download"))
    sessions_response = client.get("/api/files/revealed/sessions", headers=headers("artifact:download"))

    assert list_response.status_code == 200
    assert list_response.json() == {"items": [], "total": 0, "page": 2, "page_size": 25}
    assert grouped_response.status_code == 200
    assert grouped_response.json() == {"sessions": [], "total_sessions": 0, "page": 1, "page_size": 10}
    assert stats_response.status_code == 200
    assert stats_response.json() == {
        "total": 0,
        "image": 0,
        "video": 0,
        "document": 0,
        "code": 0,
        "project": 0,
        "other": 0,
    }
    assert sessions_response.status_code == 200
    assert sessions_response.json() == []
    assert any(name == "list_revealed_artifacts" for name, _ in calls)
    assert any(name == "list_revealed_sessions" for name, _ in calls)


def test_revealed_files_project_authorized_artifacts(monkeypatch):
    artifacts = [
        {
            "id": "art_report",
            "storage_key": "tenants/default/report.pdf",
            "label": "Reviewed Report",
            "content_type": "application/pdf",
            "size_bytes": 2048,
            "run_id": "run_a",
            "session_id": "ses_a",
            "session_name": "QA session",
            "trace_id": "trace_a",
            "workspace_id": "default",
            "user_id": "ordinary",
            "artifact_type": "reviewed_docx",
            "created_at": "2026-06-28T08:00:00Z",
        }
    ]
    sessions = [{"session_id": "ses_a", "session_name": "QA session", "file_count": 1}]
    install_projection_route_fakes(monkeypatch, artifacts=artifacts, sessions=sessions)
    client = TestClient(create_app())

    list_response = client.get("/api/files/revealed", headers=headers("artifact:download"))
    grouped_response = client.get("/api/files/revealed/grouped", headers=headers("artifact:download"))
    stats_response = client.get("/api/files/revealed/stats", headers=headers("artifact:download"))
    sessions_response = client.get("/api/files/revealed/sessions", headers=headers("artifact:download"))

    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["id"] == "art_report"
    assert item["file_key"] == "art_report"
    assert item["file_name"] == "Reviewed Report"
    assert item["file_type"] == "document"
    assert item["url"] == "/api/ai/artifacts/art_report/download"
    assert item["session_id"] == "ses_a"
    assert item["session_name"] == "QA session"
    assert item["is_favorite"] is False
    assert grouped_response.json()["sessions"][0]["files"][0]["id"] == "art_report"
    assert stats_response.json()["document"] == 1
    assert sessions_response.json() == [{"session_id": "ses_a", "session_name": "QA session", "file_count": 1}]


def test_revealed_files_fail_closed_without_artifact_permission(monkeypatch):
    install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/files/revealed", headers=headers("persona_preset:read"))

    assert response.status_code == 403
    assert response.json()["detail"] == "missing_permission:artifact:download"

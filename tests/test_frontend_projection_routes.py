from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def headers(permissions: str = "artifact:download") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def install_projection_route_fakes(monkeypatch, *, artifacts=None, sessions=None, session_state=None):
    from app.routes import frontend_projections

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []
    artifact_rows = list(artifacts or [])
    session_rows = list(sessions or [])

    def visible(rows):
        if session_state is not None and session_state.get("status") != "active":
            return []
        return [dict(row) for row in rows]

    async def fake_list_revealed_artifacts(conn, **kwargs):
        calls.append(("list_revealed_artifacts", kwargs))
        return visible(artifact_rows)

    async def fake_list_revealed_session_artifacts(conn, **kwargs):
        calls.append(("list_revealed_session_artifacts", kwargs))
        return visible(artifact_rows)

    async def fake_list_revealed_sessions(conn, **kwargs):
        calls.append(("list_revealed_sessions", kwargs))
        return visible(session_rows)

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(frontend_projections, "transaction", fake_transaction)
    monkeypatch.setattr(
        frontend_projections.repositories,
        "list_revealed_artifacts",
        fake_list_revealed_artifacts,
        raising=False,
    )
    monkeypatch.setattr(
        frontend_projections.artifact_persistence,
        "list_revealed_session_artifacts",
        fake_list_revealed_session_artifacts,
    )
    monkeypatch.setattr(
        frontend_projections.repositories,
        "list_revealed_artifact_sessions",
        fake_list_revealed_sessions,
        raising=False,
    )
    return calls


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


def test_revealed_session_files_are_not_limited_by_generic_projection_cap(monkeypatch):
    artifacts = [
        {
            "id": f"art_{index:03d}",
            "storage_key": f"tenants/default/report-{index:03d}.pdf",
            "label": f"Report {index:03d}",
            "content_type": "application/pdf",
            "size_bytes": index,
            "run_id": "run_a",
            "session_id": "ses_a",
            "session_name": "QA session",
            "trace_id": "trace_a",
            "workspace_id": "default",
            "user_id": "ordinary",
            "artifact_type": "document",
            "created_at": "2026-08-04T08:00:00Z",
        }
        for index in range(501)
    ]
    calls = install_projection_route_fakes(monkeypatch, artifacts=artifacts)
    client = TestClient(create_app())

    response = client.get(
        "/api/files/revealed/session/ses_a",
        headers=headers("artifact:download"),
    )

    assert response.status_code == 200
    assert len(response.json()) == 501
    assert response.json()[500]["id"] == "art_500"
    _, call = next(
        item for item in calls if item[0] == "list_revealed_session_artifacts"
    )
    assert call == {
        "tenant_id": "default",
        "user_id": "ordinary",
        "session_id": "ses_a",
    }


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
    sessions = [
        {
            "session_id": "ses_a",
            "session_name": "QA session",
            "file_count": 1,
            "updated_at": "2026-06-28T08:00:00Z",
        }
    ]
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
    assert item["preview_url"] == "/api/ai/artifacts/art_report/preview"
    assert item["download_url"] == "/api/ai/artifacts/art_report/download"
    assert item["url"] == item["preview_url"]
    assert item["session_id"] == "ses_a"
    assert item["session_name"] == "QA session"
    assert item["is_favorite"] is False
    assert grouped_response.json()["sessions"][0]["files"][0]["id"] == "art_report"
    assert stats_response.json()["document"] == 1
    assert sessions_response.json() == [{"session_id": "ses_a", "session_name": "QA session", "file_count": 1}]


def test_revealed_files_keep_non_xlsx_workbooks_download_only(monkeypatch):
    artifacts = [
        {
            "id": "art-legacy",
            "storage_key": "tenants/default/legacy.xlsm",
            "label": "misleading.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 128,
            "run_id": "run_a",
            "session_id": "ses_a",
            "workspace_id": "default",
            "user_id": "ordinary",
            "artifact_type": "spreadsheet",
            "created_at": "2026-06-28T08:00:00Z",
        }
    ]
    install_projection_route_fakes(monkeypatch, artifacts=artifacts)
    client = TestClient(create_app())

    response = client.get("/api/files/revealed", headers=headers("artifact:download"))

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["preview_url"] is None
    assert item["url"] is None
    assert item["download_url"] == "/api/ai/artifacts/art-legacy/download"


def test_revealed_files_and_session_filters_disappear_after_session_delete(monkeypatch):
    session_state = {"status": "active"}
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
    sessions = [
        {
            "session_id": "ses_a",
            "session_name": "QA session",
            "file_count": 1,
            "updated_at": "2026-06-28T08:00:00Z",
        }
    ]
    install_projection_route_fakes(
        monkeypatch,
        artifacts=artifacts,
        sessions=sessions,
        session_state=session_state,
    )
    client = TestClient(create_app())

    active_files = client.get("/api/files/revealed", headers=headers())
    active_sessions = client.get("/api/files/revealed/sessions", headers=headers())
    assert [item["id"] for item in active_files.json()["items"]] == ["art_report"]
    assert [item["session_id"] for item in active_sessions.json()] == ["ses_a"]

    session_state["status"] = "deleted"
    deleted_files = client.get("/api/files/revealed", headers=headers())
    deleted_grouped = client.get("/api/files/revealed/grouped", headers=headers())
    deleted_stats = client.get("/api/files/revealed/stats", headers=headers())
    deleted_sessions = client.get("/api/files/revealed/sessions", headers=headers())

    assert deleted_files.json()["items"] == []
    assert deleted_files.json()["total"] == 0
    assert deleted_grouped.json()["sessions"] == []
    assert deleted_stats.json()["total"] == 0
    assert deleted_sessions.json() == []
    assert "art_report" not in "".join(
        response.text for response in (deleted_files, deleted_grouped, deleted_stats, deleted_sessions)
    )


def test_revealed_files_original_path_does_not_expose_storage_key(monkeypatch):
    artifacts = [
        {
            "id": "art_report",
            "storage_key": "tenants/default/workspaces/default/sessions/ses_a/runs/run_a/artifacts/1/private-report.docx",
            "label": "",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
    install_projection_route_fakes(monkeypatch, artifacts=artifacts)
    client = TestClient(create_app())

    response = client.get("/api/files/revealed", headers=headers("artifact:download"))

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["file_name"] == "private-report.docx"
    assert item["original_path"] == "private-report.docx"
    serialized = str(item).lower()
    assert "tenants/default/workspaces" not in serialized
    assert "storage_key" not in serialized


def test_revealed_files_fail_closed_without_artifact_permission(monkeypatch):
    install_projection_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/files/revealed", headers=headers(""))
    session_response = client.get(
        "/api/files/revealed/session/ses_a",
        headers=headers(""),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "missing_permission:artifact:download"
    assert session_response.status_code == 403
    assert session_response.json()["detail"] == "missing_permission:artifact:download"

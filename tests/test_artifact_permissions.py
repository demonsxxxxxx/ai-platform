from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_artifact_download_must_use_authenticated_principal():
    from app.auth import principal_from_trusted_headers

    assert principal_from_trusted_headers({}) is None


class FakeCursor:
    async def fetchone(self):
        return None


class RecordingConnection:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), params))
        return FakeCursor()


@pytest.mark.asyncio
async def test_get_authorized_run_scopes_by_tenant_run_and_user():
    from app.repositories import get_authorized_run

    conn = RecordingConnection()

    await get_authorized_run(conn, tenant_id="tenant-a", user_id="user-b", run_id="run-a")

    sql, params = conn.executed[-1]
    assert "where tenant_id = %s and id = %s and user_id = %s" in sql
    assert params == ("tenant-a", "run-a", "user-b")


@pytest.mark.asyncio
async def test_get_authorized_artifact_scopes_by_tenant_artifact_and_run_owner():
    from app.repositories import get_authorized_artifact

    conn = RecordingConnection()

    await get_authorized_artifact(conn, tenant_id="tenant-a", user_id="user-b", artifact_id="art-a")

    sql, params = conn.executed[-1]
    assert "where artifacts.tenant_id = %s and artifacts.id = %s and runs.user_id = %s" in sql
    assert params == ("tenant-a", "art-a", "user-b")


def route_auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_route_transaction():
    yield object()


def admin_headers():
    return {
        "x-ai-user-id": "admin-a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": "developer",
        "x-ai-gateway-secret": "test-secret",
    }


def test_admin_download_uses_artifact_and_writes_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        return None

    async def fake_get_admin_artifact(conn, *, tenant_id, artifact_id):
        return {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "target_user_id": "user-a",
            "storage_key": "tenants/default/artifacts/report.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(kwargs)
        return "aud-a"

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            return b"docx-bytes"

    monkeypatch.setattr("app.auth.get_settings", route_auth_settings)
    monkeypatch.setattr("app.routes.files.transaction", fake_route_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.get_admin_artifact", fake_get_admin_artifact)
    monkeypatch.setattr("app.routes.files.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)
    client = TestClient(create_app())

    response = client.get("/api/ai/artifacts/art-a/download", headers=admin_headers())

    assert response.status_code == 200
    assert calls[0]["action"] == "admin_artifact_downloaded"
    assert calls[0]["trace_id"] == "trace_run_a"
    assert calls[0]["payload_json"]["target_user_id"] == "user-a"
    assert calls[0]["payload_json"]["run_id"] == "run-a"


def test_owner_preview_uses_authorized_artifact_and_inline_response(monkeypatch):
    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        assert (tenant_id, user_id, artifact_id) == ("default", "user-a", "art-a")
        return {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "storage_key": "tenants/default/artifacts/report.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            assert storage_key == "tenants/default/artifacts/report.docx"
            return b"docx-bytes"

    monkeypatch.setattr("app.auth.get_settings", route_auth_settings)
    monkeypatch.setattr("app.routes.files.transaction", fake_route_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/artifacts/art-a/preview",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "user",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert response.content == b"docx-bytes"
    assert response.headers["x-artifact-id"] == "art-a"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_preview_denied_for_non_owner_does_not_read_storage_or_admin_fallback(monkeypatch):
    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        assert (tenant_id, user_id, artifact_id) == ("default", "user-b", "art-a")
        return None

    async def fake_get_admin_artifact(conn, *, tenant_id, artifact_id):
        raise AssertionError("ordinary user preview must not use admin artifact fallback")

    class ForbiddenStorage:
        def get_bytes(self, *, storage_key):
            raise AssertionError("unauthorized preview must not read storage")

    monkeypatch.setattr("app.auth.get_settings", route_auth_settings)
    monkeypatch.setattr("app.routes.files.transaction", fake_route_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.get_admin_artifact", fake_get_admin_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", ForbiddenStorage)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/artifacts/art-a/preview",
        headers={
            "x-ai-user-id": "user-b",
            "x-ai-user-name": "User B",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "user",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "artifact_not_found"


def test_preview_rejects_non_allowlisted_content_type(monkeypatch):
    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        return {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "storage_key": "tenants/default/artifacts/archive.zip",
            "content_type": "application/zip",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            raise AssertionError("non-previewable artifact should not be read")

    monkeypatch.setattr("app.auth.get_settings", route_auth_settings)
    monkeypatch.setattr("app.routes.files.transaction", fake_route_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/artifacts/art-a/preview",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "user",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "artifact_preview_not_allowed"


def test_admin_preview_fallback_writes_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        return None

    async def fake_get_admin_artifact(conn, *, tenant_id, artifact_id):
        return {
            "id": artifact_id,
            "tenant_id": tenant_id,
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "target_user_id": "user-a",
            "storage_key": "tenants/default/artifacts/report.pdf",
            "content_type": "application/pdf",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(kwargs)
        return "aud-a"

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            return b"pdf-bytes"

    monkeypatch.setattr("app.auth.get_settings", route_auth_settings)
    monkeypatch.setattr("app.routes.files.transaction", fake_route_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.get_admin_artifact", fake_get_admin_artifact)
    monkeypatch.setattr("app.routes.files.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)
    client = TestClient(create_app())

    response = client.get("/api/ai/artifacts/art-a/preview", headers=admin_headers())

    assert response.status_code == 200
    assert response.content == b"pdf-bytes"
    assert calls[0]["action"] == "admin_artifact_previewed"
    assert calls[0]["trace_id"] == "trace_run_a"
    assert calls[0]["payload_json"]["admin_user_id"] == "admin-a"
    assert calls[0]["payload_json"]["target_user_id"] == "user-a"
    assert calls[0]["payload_json"]["artifact_id"] == "art-a"
    assert calls[0]["payload_json"]["run_id"] == "run-a"

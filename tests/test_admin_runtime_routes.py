from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime.sandbox.contracts import ContainerStatus, StopResult
from app.settings import Settings


def admin_headers():
    return {
        "X-AI-User-ID": "dev-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "default",
    }


def user_headers():
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
    }


@asynccontextmanager
async def fake_transaction():
    yield object()


def patch_empty_leases(monkeypatch):
    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return []

    monkeypatch.setattr("app.routes.admin_runtime.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)


def test_admin_runtime_containers_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_runtime_containers_returns_provider_status(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    patch_empty_leases(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert response.json() == {
        "total_active": 0,
        "ephemeral_containers": 0,
        "persistent_containers": 0,
        "containers": [],
        "sandbox_leases": [],
    }


def test_admin_runtime_containers_counts_provider_status(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return [
                ContainerStatus(
                    container_id="exec-run-a",
                    container_name="executor-exec-run-a",
                    provider="fake",
                    status="running",
                    tenant_id="default",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-a",
                    sandbox_mode="ephemeral",
                ),
                ContainerStatus(
                    container_id="exec-run-b",
                    container_name="executor-exec-run-b",
                    provider="fake",
                    status="running",
                    tenant_id="default",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-b",
                    run_id="run-b",
                    sandbox_mode="persistent",
                ),
            ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    patch_empty_leases(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_active"] == 2
    assert body["ephemeral_containers"] == 1
    assert body["persistent_containers"] == 1
    assert [container["run_id"] for container in body["containers"]] == ["run-a", "run-b"]
    assert body["sandbox_leases"] == []


def test_admin_runtime_total_active_excludes_stopped_containers(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return [
                ContainerStatus(
                    container_id="exec-run-a",
                    container_name="executor-exec-run-a",
                    provider="docker",
                    status="running",
                    tenant_id="default",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-a",
                    sandbox_mode="ephemeral",
                ),
                ContainerStatus(
                    container_id="exec-run-b",
                    container_name="executor-exec-run-b",
                    provider="docker",
                    status="exited",
                    tenant_id="default",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-b",
                    run_id="run-b",
                    sandbox_mode="ephemeral",
                ),
            ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    patch_empty_leases(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_active"] == 1
    assert body["ephemeral_containers"] == 1
    assert len(body["containers"]) == 2
    assert body["sandbox_leases"] == []


def test_admin_runtime_containers_includes_sandbox_leases(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return [
            {
                "id": "lease-a",
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "sandbox_mode": "ephemeral",
                "provider": "fake",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
                "lease_payload_json": {},
                "release_reason": "",
            }
        ]

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox_leases"][0]["lease_id"] == "lease-a"
    assert body["sandbox_leases"][0]["status"] == "active"


def test_admin_runtime_containers_filters_foreign_tenant_sandbox_leases(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        assert tenant_id == "default"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return [
            {
                "id": "lease-a",
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "sandbox_mode": "ephemeral",
                "provider": "fake",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {},
                "lease_payload_json": {},
                "release_reason": "",
            },
            {
                "id": "lease-foreign",
                "tenant_id": "tenant-b",
                "workspace_id": "workspace-b",
                "user_id": "user-b",
                "session_id": "session-b",
                "run_id": "run-b",
                "trace_id": "trace-b",
                "sandbox_mode": "ephemeral",
                "provider": "fake",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {},
                "lease_payload_json": {},
                "release_reason": "",
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert [lease["lease_id"] for lease in response.json()["sandbox_leases"]] == ["lease-a"]


def test_admin_runtime_containers_cleans_expired_leases_before_listing(monkeypatch):
    calls = []

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        calls.append(("cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert calls == [
        ("containers", {"tenant_id": "default"}),
        ("cleanup", "default", "expired"),
        ("list", "default", None, 100),
    ]


def test_admin_runtime_containers_cleans_provider_orphans_before_listing(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return []

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        calls.append(("db_cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert calls == [
        ("provider_cleanup", {"tenant_id": "default"}, "admin_runtime"),
        ("containers", {"tenant_id": "default"}),
        ("db_cleanup", "default", "expired"),
        ("list", "default", None, 100),
    ]


def test_admin_runtime_containers_fails_closed_when_provider_cleanup_reports_failure(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return [
                StopResult(
                    container_id="exec-run-a",
                    status="failed",
                    message="Container cleanup failed",
                )
            ]

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_provider_cleanup_failed"
    assert calls == [("provider_cleanup", {"tenant_id": "default"}, "admin_runtime")]


def test_admin_runtime_containers_fails_closed_when_sandbox_cleanup_fails(monkeypatch):
    calls = []

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        calls.append(("cleanup", tenant_id, reason))
        raise RuntimeError("cleanup failed")

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 500
    assert calls == [
        ("containers", {"tenant_id": "default"}),
        ("cleanup", "default", "expired"),
    ]

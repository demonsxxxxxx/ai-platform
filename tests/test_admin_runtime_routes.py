from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routes.admin_runtime import _backpressure_snapshot
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError
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


def patch_db_only_cleanup(monkeypatch):
    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
    )


def patch_empty_leases(monkeypatch):
    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return []

    monkeypatch.setattr("app.routes.admin_runtime.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)


def test_admin_runtime_containers_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_runtime_multi_agent_dispatch_cleanup_requires_admin(monkeypatch):
    async def fail_cleanup(*args, **kwargs):
        raise AssertionError("ordinary users must fail before cleanup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_multi_agent_dispatch_claims",
        fail_cleanup,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runtime/multi-agent/dispatch/cleanup", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_runtime_multi_agent_dispatch_cleanup_returns_same_tenant_expired_claims(monkeypatch):
    calls = []

    @asynccontextmanager
    async def cleanup_transaction():
        yield object()

    async def fake_cleanup(conn, *, tenant_id, cleaned_by, limit=100):
        calls.append((tenant_id, cleaned_by, limit))
        return [
            {
                "step_id": "step-code",
                "run_id": "run-ready",
                "step_key": "code",
                "dispatch_id": "dispatch-code",
                "status": "pending",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.transaction", cleanup_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_multi_agent_dispatch_claims",
        fake_cleanup,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runtime/multi-agent/dispatch/cleanup", headers=admin_headers())

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": "default",
        "expired_count": 1,
        "expired_claims": [
            {
                "step_id": "step-code",
                "run_id": "run-ready",
                "step_key": "code",
                "dispatch_id": "dispatch-code",
                "status": "pending",
            }
        ],
    }
    assert calls == [("default", "dev-admin", 100)]


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

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox_leases"][0]["lease_id"] == "lease-a"
    assert body["sandbox_leases"][0]["status"] == "active"


def test_admin_runtime_containers_lists_only_active_sandbox_leases(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        assert status == "active"
        return [
            {
                "id": "lease-active",
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
                "lease_payload_json": {"container_id": "exec-run-a"},
                "release_reason": "",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert [lease["lease_id"] for lease in response.json()["sandbox_leases"]] == ["lease-active"]


def test_admin_runtime_containers_can_include_released_sandbox_lease_history(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        if status == "active":
            return []
        assert status is None
        return [
            {
                "id": "lease-released",
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "sandbox_mode": "ephemeral",
                "provider": "docker",
                "status": "released",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
                "lease_payload_json": {"container_name": "executor-run-a", "token": "secret-token"},
                "release_reason": "expired",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers?include_lease_history=true", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox_leases"] == []
    assert body["sandbox_lease_history"][0]["lease_id"] == "lease-released"
    assert body["sandbox_lease_history"][0]["status"] == "released"
    assert "secret-token" not in str(body["sandbox_lease_history"])


def test_admin_runtime_containers_filters_foreign_tenant_sandbox_leases(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
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
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
    )
    patch_db_only_cleanup(monkeypatch)
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

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert calls == [
        ("cleanup", "default", "expired"),
        ("list", "default", "active", 100),
        ("containers", {"tenant_id": "default"}),
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

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("db_cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert calls == [
        ("provider_cleanup", {"tenant_id": "default"}, "admin_runtime"),
        ("db_cleanup", "default", "expired"),
        ("list", "default", "active", 100),
        ("containers", {"tenant_id": "default"}),
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


def test_admin_runtime_containers_fails_closed_for_malformed_provider_cleanup_result(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return [{"container_id": "exec-run-a", "status": "unexpected"}]

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


def test_admin_runtime_containers_fails_closed_for_dict_provider_cleanup_success(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return [{"container_id": "exec-run-a", "status": "stopped"}]

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

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("cleanup", tenant_id, reason))
        raise SandboxRuntimeCleanupError([{"container_id": "exec-run-a", "message": "cleanup failed"}])

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert calls == [
        ("cleanup", "default", "expired"),
    ]


def test_admin_runtime_overview_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/overview", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_runtime_overview_returns_same_tenant_snapshot(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return []

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
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
                    sandbox_mode="persistent",
                ),
            ]

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("runtime_cleanup", tenant_id, reason))
        return []

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        calls.append(("db_cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("leases", tenant_id, status, limit))
        assert tenant_id == "default"
        if status == "active":
            return [{"id": "lease-active", "tenant_id": "default", "status": "active"}]
        return [
            {"id": "lease-active", "tenant_id": "default", "status": "active"},
            {"id": "lease-released", "tenant_id": "default", "status": "released"},
            {"id": "lease-expired", "tenant_id": "default", "status": "expired"},
            {"id": "lease-foreign", "tenant_id": "tenant-b", "status": "active"},
        ]

    async def fake_get_queue_status():
        calls.append(("queue_status",))
        return {"depths": {"queued": 2}}

    async def fake_get_queue_insight(tenant_id, **kwargs):
        assert kwargs == {"include_user_breakdown": True}
        calls.append(("queue_insight", tenant_id, kwargs))
        return {
            "tenant_id": tenant_id,
            "reason": "workers_busy",
            "capacity": {
                "max_active_worker_runs": 3,
                "processing_saturated": False,
                "available_worker_slots": 1,
                "queue_tenant_processing_limit": 2,
                "queue_user_processing_limit": 1,
                "queue_lease_scan_limit": 50,
            },
            "queue_sample": {
                "queued_scan_limit": 500,
                "queued_sampled": 2,
                "queued_sample_complete": True,
            },
            "throttling": {
                "tenant_processing": 1,
                "tenant_processing_limit": 2,
                "tenant_processing_saturated": False,
                "user_processing_limit": 1,
                "users": {
                    "user-a": {"queued": 1, "processing": 1, "processing_saturated": True},
                    "user-b": {"queued": 0, "processing": 0, "processing_saturated": False},
                },
            },
            "keys": {"queued": "ai-platform:runs:queued"},
            "raw_queue_payload": {"token": "queue-secret-token"},
        }

    async def fake_run_summary(conn, *, tenant_id, limit=10):
        calls.append(("run_summary", tenant_id, limit))
        return {
            "total": 3,
            "by_status": {"queued": 1, "running": 1, "failed": 1},
            "active": 2,
            "terminal": 1,
            "recent_failures": [],
        }

    async def fake_observability_summary(conn, *, tenant_id):
        calls.append(("observability", tenant_id))
        return {
            "event_count": 4,
            "artifact_count": 1,
            "error_count": 1,
            "error_types": {"executor_failure": 1},
            "latency_ms": {"avg": 20, "max": 30},
            "token_counts": {"input": 10, "output": 12, "total": 22},
            "estimated_cost_minor": 7,
        }

    async def fake_admission_summary(conn, *, tenant_id, limit, top_user_limit=10):
        calls.append(("admission", tenant_id, limit, top_user_limit))
        return {
            "policy_active": True,
            "max_active_runs_per_user": limit,
            "active_runs": 3,
            "active_users": 2,
            "saturated_users": 1,
            "top_users": [{"user_id": "user-a", "active": 3, "saturated": True}],
        }

    def fake_pool_status():
        calls.append(("database_pool",))
        return {
            "configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10.0, "max_waiting": 100},
            "open": True,
            "stats": {
                "pool_available": 1,
                "requests_waiting": 0,
                "database_url": "postgresql://user:pool-secret-password@db.example/internal",
                "token": "pool-secret-token",
            },
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fake_cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_get_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_get_queue_insight)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_run_summary",
        fake_run_summary,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_observability_summary",
        fake_observability_summary,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_admission_summary",
        fake_admission_summary,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.get_pool_status", fake_pool_status, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "default"
    assert body["queue"]["status"]["depths"]["queued"] == 2
    assert body["queue"]["tenant_insight"]["reason"] == "workers_busy"
    assert body["runs"]["active"] == 2
    assert body["sandbox"]["containers"] == {
        "total": 2,
        "running": 1,
        "by_status": {"running": 1, "exited": 1},
        "ephemeral_running": 1,
        "persistent_running": 0,
    }
    assert body["sandbox"]["leases"] == {
        "active": 1,
        "released": 1,
        "expired": 1,
        "history_included": True,
    }
    assert body["observability"]["token_counts"]["total"] == 22
    assert body["admission"]["saturated_users"] == 1
    assert body["admission"]["top_users"] == [{"user_id": "user-a", "active": 3, "saturated": True}]
    assert body["backpressure"]["reasons"] == [
        "active_run_limit_saturated",
        "workers_busy",
        "queue_user_quota_saturated",
    ]
    assert body["backpressure"]["queue"] == {
        "reason": "workers_busy",
        "worker_capacity": {
            "max_active_worker_runs": 3,
            "processing_saturated": False,
            "available_worker_slots": 1,
        },
        "quota": {
            "tenant_processing_limit": 2,
            "tenant_processing_saturated": False,
            "user_processing_limit": 1,
            "saturated_users": 1,
        },
        "sample": {"queued_scan_limit": 500, "queued_sampled": 2, "queued_sample_complete": True},
    }
    assert body["backpressure"]["database_pool"] == {
        "open": True,
        "requests_waiting": 0,
        "max_waiting": 100,
        "waiting_saturated": False,
    }
    assert "ai-platform:runs:queued" not in str(body["backpressure"])
    assert "queue-secret-token" not in str(body["backpressure"])
    assert body["database_pool"] == {
        "configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10.0, "max_waiting": 100},
        "open": True,
        "stats": {"pool_available": 1, "requests_waiting": 0},
    }
    assert "pool-secret-password" not in str(body["database_pool"])
    assert "pool-secret-token" not in str(body["database_pool"])
    assert "db.example" not in str(body["database_pool"])
    assert calls == [
        ("provider_cleanup", {"tenant_id": "default"}, "admin_runtime"),
        ("runtime_cleanup", "default", "expired"),
        ("db_cleanup", "default", "expired"),
        ("leases", "default", "active", 100),
        ("leases", "default", None, 100),
        ("containers", {"tenant_id": "default"}),
        ("run_summary", "default", 10),
        ("observability", "default"),
        ("admission", "default", 3, 10),
        ("queue_status",),
        ("queue_insight", "default", {"include_user_breakdown": True}),
        ("database_pool",),
    ]


def test_admin_runtime_overview_sanitizes_summary_payloads(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            return []

    async def fake_queue_status():
        return {}

    async def fake_queue_insight(tenant_id, **_kwargs):
        return {
            "tenant_id": tenant_id,
            "reason": "tenant_quota_full token=queue-reason-token",
            "capacity": {
                "max_active_worker_runs": 2,
                "processing_saturated": True,
                "available_worker_slots": 0,
                "queue_tenant_processing_limit": 1,
                "queue_user_processing_limit": 1,
            },
            "queue_sample": {
                "queued_scan_limit": 500,
                "queued_sampled": 1,
                "queued_sample_complete": True,
            },
            "throttling": {
                "tenant_processing_saturated": True,
                "user_processing_limit": 1,
                "users": {"user-secret": {"queued": 1, "processing": 1, "processing_saturated": True}},
            },
            "keys": {"queued": "ai-platform:runs:queued"},
            "raw_queue_payload": "raw_queue_payload token=queue-payload-token",
            "storage_key": "tenant/default/runs/run-a/private-output.json",
        }

    async def fake_run_summary(conn, *, tenant_id, limit=10):
        return {
            "total": 1,
            "by_status": {"failed": 1},
            "active": 0,
            "terminal": 1,
            "recent_failures": [
                {
                    "run_id": "run-failed",
                    "skill_id": "qa-file-reviewer",
                    "error_code": "executor_failure token=route-code-token",
                    "error_message": "failed token=route-message-token /var/lib/ai-platform/run-a/out.log",
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                }
            ],
        }

    async def fake_observability_summary(conn, *, tenant_id):
        return {
            "event_count": 1,
            "artifact_count": 0,
            "error_count": 1,
            "error_types": {"executor_failure token=route-error-type-token": 1},
            "latency_ms": {"avg": None, "max": None},
            "token_counts": {"input": 0, "output": 0, "total": 0},
            "estimated_cost_minor": 0,
        }

    async def fake_admission_summary(conn, *, tenant_id, limit, top_user_limit=10):
        return {
            "policy_active": True,
            "max_active_runs_per_user": limit,
            "active_runs": 3,
            "active_users": 1,
            "saturated_users": 1,
            "top_users": [
                {
                    "user_id": "user-a",
                    "active": 3,
                    "saturated": True,
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                }
            ],
        }

    def fake_pool_status():
        return {
            "configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10.0, "max_waiting": 1},
            "open": True,
            "stats": {"requests_waiting": 1, "token": "pool-secret-token"},
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.get_admin_runtime_run_summary", fake_run_summary, raising=False)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_observability_summary",
        fake_observability_summary,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_admission_summary",
        fake_admission_summary,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.get_pool_status", fake_pool_status, raising=False)
    patch_empty_leases(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 200
    serialized = str(response.json())
    assert "qa-file-reviewer" not in serialized
    assert "skill_id" not in serialized
    assert "route-code-token" not in serialized
    assert "route-message-token" not in serialized
    assert "route-error-type-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "runtime_private_payload" not in serialized
    backpressure_serialized = str(response.json()["backpressure"])
    assert "queue-reason-token" not in backpressure_serialized
    assert "queue-payload-token" not in backpressure_serialized
    assert "pool-secret-token" not in backpressure_serialized
    assert "ai-platform:runs:queued" not in backpressure_serialized
    assert "raw_queue_payload" not in backpressure_serialized
    assert "storage_key" not in backpressure_serialized
    assert "private-output.json" not in backpressure_serialized
    assert response.json()["observability"]["latency_ms"] == {"avg": None, "max": None}


def test_admin_runtime_backpressure_omits_worker_available_from_reasons():
    snapshot = _backpressure_snapshot(
        admission={
            "policy_active": True,
            "max_active_runs_per_user": 3,
            "active_runs": 1,
            "active_users": 1,
            "saturated_users": 0,
            "top_users": [],
        },
        queue_insight={
            "reason": "worker_available",
            "capacity": {
                "max_active_worker_runs": 3,
                "processing_saturated": False,
                "available_worker_slots": 2,
            },
            "throttling": {
                "tenant_processing_limit": 2,
                "tenant_processing_saturated": False,
                "user_processing_limit": 1,
                "users": {},
            },
            "queue_sample": {
                "queued_scan_limit": 500,
                "queued_sampled": 0,
                "queued_sample_complete": True,
            },
        },
        database_pool={
            "configured": {"max_waiting": 100},
            "open": True,
            "stats": {"requests_waiting": 0},
        },
    )

    assert snapshot["queue"]["reason"] == "worker_available"
    assert snapshot["reasons"] == []


def test_admin_runtime_overview_fails_closed_when_provider_cleanup_reports_failure(monkeypatch):
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

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_provider_cleanup_failed"
    assert calls == [("provider_cleanup", {"tenant_id": "default"}, "admin_runtime")]


def test_admin_runtime_overview_fails_closed_for_malformed_provider_cleanup_result(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return [{"container_id": "exec-run-a", "status": "unexpected"}]

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_provider_cleanup_failed"
    assert calls == [("provider_cleanup", {"tenant_id": "default"}, "admin_runtime")]


def test_admin_runtime_overview_fails_closed_for_dict_provider_cleanup_success(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return [{"container_id": "exec-run-a", "status": "stopped"}]

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_provider_cleanup_failed"
    assert calls == [("provider_cleanup", {"tenant_id": "default"}, "admin_runtime")]


@pytest.mark.parametrize("failing_queue_call", ["status", "insight"])
def test_admin_runtime_overview_does_not_mask_queue_failure(monkeypatch, failing_queue_call):
    calls = []

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            return []

    async def fake_queue_status():
        calls.append("queue_status")
        if failing_queue_call == "status":
            raise RuntimeError("redis unavailable")
        return {}

    async def fake_queue_insight(tenant_id, **_kwargs):
        calls.append("queue_insight")
        if failing_queue_call == "insight":
            raise RuntimeError("redis unavailable")
        return {"tenant_id": tenant_id}

    async def fake_run_summary(conn, *, tenant_id, limit=10):
        return {"total": 0, "by_status": {}, "active": 0, "terminal": 0, "recent_failures": []}

    async def fake_observability_summary(conn, *, tenant_id):
        return {
            "event_count": 0,
            "artifact_count": 0,
            "error_count": 0,
            "error_types": {},
            "latency_ms": {"avg": None, "max": None},
            "token_counts": {"input": 0, "output": 0, "total": 0},
            "estimated_cost_minor": 0,
        }

    async def fake_admission_summary(conn, *, tenant_id, limit, top_user_limit=10):
        return {
            "policy_active": True,
            "max_active_runs_per_user": limit,
            "active_runs": 0,
            "active_users": 0,
            "saturated_users": 0,
            "top_users": [],
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.get_admin_runtime_run_summary", fake_run_summary, raising=False)
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_observability_summary",
        fake_observability_summary,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.get_admin_runtime_admission_summary",
        fake_admission_summary,
        raising=False,
    )
    patch_empty_leases(monkeypatch)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 500
    assert response.text != ""
    if failing_queue_call == "status":
        assert calls == ["queue_status"]
    else:
        assert calls == ["queue_status", "queue_insight"]


def test_admin_runtime_overview_fails_closed_when_sandbox_cleanup_fails(monkeypatch):
    calls = []

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("cleanup", tenant_id, reason))
        raise SandboxRuntimeCleanupError([{"container_id": "exec-run-a", "message": "cleanup failed"}])

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("list", tenant_id, status, limit))
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fake_cleanup_expired_sandbox_runtime_leases,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert calls == [("cleanup", "default", "expired")]

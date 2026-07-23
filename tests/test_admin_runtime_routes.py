from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routes.admin_runtime import _backpressure_snapshot
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError
from app.runtime.sandbox.contracts import ContainerStatus, StopResult
from app.settings import Settings


ADMIN_PROOF_KEY = "admin-runtime-proof-key-with-enough-entropy-2026"


@pytest.fixture(autouse=True)
def signed_runtime_proof_key(monkeypatch):
    monkeypatch.setattr(
        "app.execution_boundary.get_settings",
        lambda: Settings(sandbox_egress_proof_signing_key=ADMIN_PROOF_KEY),
    )


def signed_runtime_lease(
    *,
    run_id: str,
    provider: str = "docker",
    tenant_id: str = "default",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict:
    from app.execution_boundary import (
        build_governed_egress_proof,
        governed_egress_authorized_native_tool_scope,
        governed_egress_authorized_skill_scope,
    )

    workspace_id = "workspace-a"
    user_id = "user-a"
    session_id = f"session-{run_id}"
    container_id = f"exec-{run_id}"
    container_name = f"executor-{container_id}"
    image = "registry.test/executor@sha256:" + "a" * 64
    proof = build_governed_egress_proof(
        signing_key=ADMIN_PROOF_KEY,
        provider=provider,
        runtime_subject="docker-internal-bridge" if provider == "docker" else "runsc",
        policy_subject="network-a:internal" if provider == "docker" else "gateway-policy-a",
        callback_subject="http://api.sandbox.internal:8020",
        denial_subject="network-a:default-deny" if provider == "docker" else "deny-a",
        network_id="network-a" if provider == "docker" else "profile-a",
        network_name="ai-platform-sandbox-egress-internal-v1" if provider == "docker" else "opensandbox-a",
        network_internal=provider == "docker",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        image_subject=image,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=governed_egress_authorized_skill_scope(skill_ids=[], mcp_tool_ids=[]),
        authorized_native_tool_scope=governed_egress_authorized_native_tool_scope([]),
        lease_identity=f"{provider}:{container_name}:{container_id}",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "provider": provider,
        "lease_payload_json": {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "container_id": container_id,
            "container_name": container_name,
            "labels": {},
            **{
                f"governed_egress_{field}": proof[field]
                for field in (
                    "image_subject_sha256",
                    "image_digest_sha256",
                    "authorized_skill_scope_sha256",
                    "authorized_native_tool_scope_sha256",
                )
            },
            "governed_egress_proof": proof,
        },
    }


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


def patch_real_leases(monkeypatch, *run_ids):
    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return [
            {
                **signed_runtime_lease(run_id=run_id, tenant_id=tenant_id),
                "id": f"lease-{run_id}",
                "trace_id": f"trace-{run_id}",
                "sandbox_mode": "ephemeral",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
            }
            for run_id in run_ids
        ]

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


def test_admin_runtime_queue_omits_raw_redis_keys(monkeypatch):
    async def fake_queue_status():
        return {
            "depths": {"queued": 2, "processing": 1, "dead_letter": 0},
            "keys": {"queued": "ai-platform:runs:queued"},
            "workers": ["worker-a"],
            "raw_queue_payload": "token=queue-secret",
        }

    async def fake_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "default"
        assert kwargs == {"include_user_breakdown": True}
        return {
            "tenant_id": tenant_id,
            "reason": "worker_available",
            "keys": {"queued": "ai-platform:runs:queued"},
            "raw_queue_payload": "token=insight-secret",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/queue", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"] == {
        "depths": {"queued": 2, "processing": 1, "dead_letter": 0},
        "workers": ["worker-a"],
    }
    serialized = str(payload)
    assert "ai-platform:runs:queued" not in serialized
    assert "raw_queue_payload" not in serialized
    assert "queue-secret" not in serialized
    assert "insight-secret" not in serialized


def test_admin_runtime_queue_sanitizes_processing_state(monkeypatch):
    async def fake_queue_status():
        return {
            "depths": {"queued": 1, "processing": 2, "dead_letter": 0},
            "processing_state": {
                "active": 1,
                "stale": 1,
                "reclaimable": 1,
                "missing_metadata": 0,
                "worker_id": "worker-secret",
            },
            "keys": {"processing": "ai-platform:runs:processing"},
        }

    async def fake_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "default"
        assert kwargs == {"include_user_breakdown": True}
        return {
            "tenant_id": tenant_id,
            "reason": "processing_lease_reclaimable",
            "processing_state": {
                "active": 1,
                "stale": 1,
                "reclaimable": 1,
                "missing_metadata": 0,
                "run_id": "run-secret",
            },
            "raw_queue_payload": "token=queue-secret",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/queue", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["processing_state"] == {
        "active": 1,
        "stale": 1,
        "reclaimable": 1,
        "missing_metadata": 0,
    }
    assert body["tenant_insight"]["reason"] == "processing_lease_reclaimable"
    assert body["tenant_insight"]["processing_state"] == {
        "active": 1,
        "stale": 1,
        "reclaimable": 1,
        "missing_metadata": 0,
    }
    serialized = str(body)
    assert "worker-secret" not in serialized
    assert "run-secret" not in serialized
    assert "queue-secret" not in serialized
    assert "ai-platform:runs:processing" not in serialized


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


def test_admin_runtime_containers_excludes_fake_unbacked_provider_status(monkeypatch):
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
    assert body["total_active"] == 0
    assert body["ephemeral_containers"] == 0
    assert body["persistent_containers"] == 0
    assert body["containers"] == []
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
    patch_real_leases(monkeypatch, "run-a", "run-b")
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_active"] == 1
    assert body["ephemeral_containers"] == 1
    assert len(body["containers"]) == 2
    assert [lease["run_id"] for lease in body["sandbox_leases"]] == ["run-a", "run-b"]


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
                "provider": "docker",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
                "lease_payload_json": {
                    "source": "sandbox_runtime",
                    "evidence_class": "runtime_lease_projection",
                },
                "release_reason": "",
                **signed_runtime_lease(run_id="run-a"),
            },
            {
                "id": "lease-placeholder",
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-placeholder",
                "trace_id": "trace-placeholder",
                "sandbox_mode": "ephemeral",
                "provider": "docker",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
                "lease_payload_json": {
                    "source": "sdk_only_lifecycle_placeholder",
                    "evidence_class": "sdk_only_lifecycle_placeholder",
                },
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
    assert [lease["lease_id"] for lease in body["sandbox_leases"]] == ["lease-a"]
    assert body["sandbox_leases"][0]["status"] == "active"


def test_admin_runtime_placeholder_cleanup_failure_does_not_break_real_projection(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    async def placeholder_cleanup_failure(*args, **kwargs):
        raise SandboxRuntimeCleanupError(
            [{"container_id": "lease-placeholder", "message": "Unsupported sandbox provider: fake"}]
        )

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        return [
            {
                "id": "lease-placeholder",
                "tenant_id": tenant_id,
                "run_id": "run-placeholder",
                "provider": "fake",
                "status": "active",
                "lease_payload_json": {
                    "source": "sdk_only_lifecycle_placeholder",
                    "evidence_class": "sdk_only_lifecycle_placeholder",
                },
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", lease_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        placeholder_cleanup_failure,
    )
    patch_db_only_cleanup(monkeypatch)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/containers", headers=admin_headers())

    assert response.status_code == 200
    assert response.json()["sandbox_leases"] == []


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
                        "run_id": "run-a",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "sandbox_mode": "ephemeral",
                "provider": "docker",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {"workspace": "/workspace"},
                "lease_payload_json": {
                    "container_id": "exec-run-a",
                    "source": "sandbox_runtime",
                    "evidence_class": "runtime_lease_projection",
                },
                "release_reason": "",
                **signed_runtime_lease(run_id="run-a"),
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

    repository_rows = []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        if status == "active":
            return []
        assert status is None
        rows = [
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
                "lease_payload_json": {
                    "container_name": "executor-run-a",
                    "token": "secret-token",
                    "source": "sandbox_runtime",
                    "evidence_class": "runtime_lease_projection",
                },
                "release_reason": "expired",
                **signed_runtime_lease(run_id="run-a"),
            },
            {
                "id": "lease-placeholder-history",
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-placeholder",
                "trace_id": "trace-placeholder",
                "sandbox_mode": "ephemeral",
                "provider": "fake",
                "status": "released",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {},
                "lease_payload_json": {
                    "source": "sdk_only_lifecycle_placeholder",
                    "evidence_class": "sdk_only_lifecycle_placeholder",
                },
                "release_reason": "run_succeeded",
            },
        ]
        repository_rows.extend(rows)
        return rows

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
    assert [row["id"] for row in repository_rows] == ["lease-released", "lease-placeholder-history"]
    assert [lease["lease_id"] for lease in body["sandbox_lease_history"]] == ["lease-released"]
    assert "secret-token" not in str(body["sandbox_lease_history"])


def test_admin_runtime_hides_stale_active_proof_but_keeps_signed_terminal_history(monkeypatch):
    class FakeProvider:
        async def list_runtime_containers(self, filters):
            assert filters == {"tenant_id": "default"}
            return []

    @asynccontextmanager
    async def lease_transaction():
        yield object()

    now = datetime.now(timezone.utc)
    issued_at = now - timedelta(minutes=2)
    expires_at = now - timedelta(seconds=1)

    def runtime_row(lease_id: str, status: str, lease: dict) -> dict:
        return {
            "id": lease_id,
            "trace_id": f"trace-{lease_id}",
            "sandbox_mode": "ephemeral",
            "status": status,
            "browser_enabled": False,
            "resource_limits_json": {},
            "user_visible_payload_json": {"workspace": "/workspace"},
            **lease,
        }

    active_stale = runtime_row(
        "lease-active-stale",
        "active",
        signed_runtime_lease(run_id="active-stale", issued_at=issued_at, expires_at=expires_at),
    )
    released_historical = runtime_row(
        "lease-released-historical",
        "released",
        signed_runtime_lease(run_id="released-historical", issued_at=issued_at, expires_at=expires_at),
    )
    forged_historical = runtime_row(
        "lease-forged-historical",
        "released",
        signed_runtime_lease(run_id="forged-historical", issued_at=issued_at, expires_at=expires_at),
    )
    forged_historical["lease_payload_json"]["governed_egress_proof"]["signature"] = "0" * 64

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        assert tenant_id == "default"
        assert reason == "expired"
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        assert tenant_id == "default"
        return [active_stale] if status == "active" else [active_stale, released_historical, forged_historical]

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
    assert response.json()["sandbox_leases"] == []
    assert [lease["lease_id"] for lease in response.json()["sandbox_lease_history"]] == [
        "lease-released-historical"
    ]


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
                "provider": "docker",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {},
                "release_reason": "",
                **signed_runtime_lease(run_id="run-a"),
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
                "provider": "docker",
                "status": "active",
                "browser_enabled": False,
                "resource_limits_json": {},
                "user_visible_payload_json": {},
                "lease_payload_json": {
                    "source": "sandbox_runtime",
                    "evidence_class": "runtime_lease_projection",
                },
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
            return [
                {
                    "id": "lease-active",
                    "status": "active",
                    **signed_runtime_lease(run_id="run-a"),
                }
            ]
        return [
            {
                "id": "lease-active",
                "status": "active",
                **signed_runtime_lease(run_id="run-a"),
            },
            {
                "id": "lease-released",
                "status": "released",
                **signed_runtime_lease(run_id="run-a"),
            },
            {
                "id": "lease-expired",
                "status": "expired",
                **signed_runtime_lease(run_id="run-a", provider="opensandbox"),
            },
            {
                "id": "lease-foreign",
                "tenant_id": "tenant-b",
                "provider": "docker",
                "status": "active",
                "lease_payload_json": {
                    "source": "sandbox_runtime",
                    "evidence_class": "runtime_lease_projection",
                },
            },
        ]

    async def fake_get_queue_status():
        calls.append(("queue_status",))
        return {
            "depths": {"queued": 2},
            "keys": {"queued": "ai-platform:runs:queued"},
            "workers": ["worker-a"],
            "raw_queue_payload": "token=queue-secret-token",
        }

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
            "error_count": 6,
            "error_types": {
                "executor_failure": 1,
                "sandbox_runtime_cleanup_failed": 2,
                "model_gateway_timeout": 3,
            },
            "latency_ms": {
                "avg": 20,
                "max": 30,
                "p50": 21,
                "p95": 28,
                "p99": 29,
                "sandbox_workdir": "/tmp/private-run",
            },
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

    runtime_settings = Settings(frontend_poc_auth_enabled=True, model_gateway_request_concurrency_limit=12)
    monkeypatch.setattr("app.auth.get_settings", lambda: runtime_settings)
    monkeypatch.setattr("app.routes.admin_runtime.get_settings", lambda: runtime_settings)
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
    assert body["queue"]["status"] == {"depths": {"queued": 2}, "workers": ["worker-a"]}
    assert body["queue"]["tenant_insight"]["reason"] == "workers_busy"
    assert body["runs"]["active"] == 2
    assert body["sandbox"]["containers"] == {
        "total": 1,
        "running": 1,
        "by_status": {"running": 1},
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
    assert body["observability"]["latency_ms"] == {
        "avg": 20,
        "max": 30,
        "p50": 21,
        "p95": 28,
        "p99": 29,
    }
    assert body["observability"]["error_categories"] == {
        "executor": 1,
        "sandbox": 2,
        "model_gateway": 3,
    }
    assert body["capacity"]["schema_version"] == "ai-platform.capacity-baseline.v1"
    assert body["capacity"]["limits"]["worker"]["max_active_worker_runs"] == 3
    assert body["capacity"]["limits"]["database_pool"]["max_size"] == 10
    assert body["capacity"]["limits"]["queue"]["tenant_processing_quota_enabled"] is False
    assert body["capacity"]["limits"]["sandbox"]["container_provider"] == "fake"
    assert body["capacity"]["limits"]["model_gateway"] == {
        "provider": "openai_compatible",
        "request_concurrency_limit": None,
        "configured_request_concurrency_limit": 12,
        "limit_enforcement": "not_implemented",
        "capacity_evidence": "unproven_without_load_test",
    }
    assert "model_gateway_concurrency_unbounded_by_platform" in body["capacity"]["warnings"]
    assert "model_gateway_configured_limit_not_enforced" in body["capacity"]["warnings"]
    assert body["capacity"]["production_default_policy"] == "do_not_raise_without_recorded_load_test_evidence"
    assert "password" not in str(body["capacity"]).lower()
    assert "api_key" not in str(body["capacity"]).lower()
    assert "database_url" not in str(body["capacity"]).lower()
    assert body["governance"]["schema_version"] == "ai-platform.governance-readiness.v1"
    assert body["governance"]["status"] == "partial_blocked"
    assert "tool_permission" in body["governance"]["domains"]
    assert "evidence" not in body["governance"]["domains"]["frontend_projection"]
    skill_dashboard = body["governance"]["domains"]["skill_governance"]["evidence"][
        "admin_skill_release_dashboard"
    ]
    assert skill_dashboard["schema_version"] == "ai-platform.skill-release-dashboard-readiness.v1"
    assert "dashboard_contract" not in skill_dashboard
    assert body["observability_readiness"]["schema_version"] == "ai-platform.observability-readiness.v1"
    assert body["observability_readiness"]["status"] == "partial_blocked"
    assert "runtime_metrics" in body["observability_readiness"]["domains"]
    assert "formal_error_taxonomy_contract" not in body["observability_readiness"]["open_gaps"]
    assert (
        "formal_error_taxonomy_contract"
        in body["observability_readiness"]["domains"]["error_taxonomy"]["implemented"]
    )
    export_acceptance = body["observability_readiness"]["domains"]["alerts_and_exports"]["evidence"][
        "release_evidence"
    ]["export_acceptance"]
    assert export_acceptance["schema_version"] == "ai-platform.release-evidence-export-acceptance.v1"
    assert export_acceptance["safe_entry_count"] >= 1
    assert "entries" not in export_acceptance
    assert "blocked_entries" not in export_acceptance
    assert "excluded_entries" not in export_acceptance
    assert "callback_token" not in str(body["governance"]).lower()
    assert "sandbox_workspace_root" not in str(body["governance"]).lower()
    assert ".claude/skills" not in str(body["governance"])
    assert "callback_token" not in str(body["observability_readiness"]).lower()
    assert "sandbox_workspace_root" not in str(body["observability_readiness"]).lower()
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
    assert body["backpressure"]["model_gateway"] == {
        "provider": "openai_compatible",
        "request_concurrency_limit": None,
        "configured_request_concurrency_limit": 12,
        "limit_enabled": False,
        "limit_enforced": False,
        "limit_enforcement": "not_implemented",
        "config_only": True,
        "capacity_evidence": "unproven_without_load_test",
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
    assert "ai-platform:runs:queued" not in str(body)
    assert "raw_queue_payload" not in str(body)
    assert "queue-secret-token" not in str(body)
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


def test_admin_runtime_overview_can_skip_maintenance_cleanup_for_probe_snapshots(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            raise AssertionError("probe snapshot must not run provider cleanup")

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return []

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fail_runtime_cleanup(conn, *, tenant_id=None, reason="expired", **kwargs):
        raise AssertionError("probe snapshot must not run sandbox runtime cleanup")

    async def fail_db_cleanup(conn, *, tenant_id=None, reason="expired"):
        raise AssertionError("probe snapshot must not run DB sandbox lease cleanup")

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("leases", tenant_id, status, limit))
        return []

    async def fake_get_queue_status():
        calls.append(("queue_status",))
        return {"depths": {"queued": 0}, "workers": []}

    async def fake_get_queue_insight(tenant_id, **kwargs):
        calls.append(("queue_insight", tenant_id, kwargs))
        return {"tenant_id": tenant_id, "reason": "worker_available"}

    async def fake_run_summary(conn, *, tenant_id, limit=10):
        calls.append(("run_summary", tenant_id, limit))
        return {"total": 0, "by_status": {}, "active": 0, "terminal": 0, "recent_failures": []}

    async def fake_observability_summary(conn, *, tenant_id):
        calls.append(("observability", tenant_id))
        return {"event_count": 0, "artifact_count": 0, "error_count": 0}

    async def fake_admission_summary(conn, *, tenant_id, limit, top_user_limit=10):
        calls.append(("admission", tenant_id, limit, top_user_limit))
        return {"active_runs": 0, "active_users": 0, "saturated_users": 0, "top_users": []}

    def fake_pool_status():
        calls.append(("database_pool",))
        return {"configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10.0, "max_waiting": 100}}

    runtime_settings = Settings(frontend_poc_auth_enabled=True)
    monkeypatch.setattr("app.auth.get_settings", lambda: runtime_settings)
    monkeypatch.setattr("app.routes.admin_runtime.get_settings", lambda: runtime_settings)
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases",
        fail_runtime_cleanup,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases",
        fail_db_cleanup,
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

    response = client.get(
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox"]["containers"]["total"] == 0
    assert body["queue"]["status"]["depths"] == {"queued": 0}
    assert calls == [
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
        return {
            "depths": {"queued": 1},
            "keys": {"queued": "ai-platform:runs:queued"},
            "raw_queue_payload": "token=queue-status-token",
        }

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
            "latency_ms": {"avg": None, "max": None, "p50": None, "p95": None, "p99": None},
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
    body = response.json()
    serialized = str(
        {
            "runs": body["runs"],
            "observability": body["observability"],
            "admission": body["admission"],
            "queue": body["queue"],
            "backpressure": body["backpressure"],
        }
    )
    assert "qa-file-reviewer" not in serialized
    assert "skill_id" not in serialized
    assert "route-code-token" not in serialized
    assert "route-message-token" not in serialized
    assert "route-error-type-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "runtime_private_payload" not in serialized
    assert "queue-status-token" not in serialized
    backpressure_serialized = str(body["backpressure"])
    assert "queue-reason-token" not in backpressure_serialized
    assert "queue-payload-token" not in backpressure_serialized
    assert "pool-secret-token" not in backpressure_serialized
    assert "ai-platform:runs:queued" not in backpressure_serialized
    assert "raw_queue_payload" not in backpressure_serialized
    assert "storage_key" not in backpressure_serialized
    assert "private-output.json" not in backpressure_serialized
    assert response.json()["observability"]["latency_ms"] == {
        "avg": None,
        "max": None,
        "p50": None,
        "p95": None,
        "p99": None,
    }


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


def test_admin_runtime_overview_reports_container_list_unavailable_without_docker_socket(monkeypatch):
    calls = []

    from app.runtime.sandbox.container_provider import DockerUnavailableError

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            raise DockerUnavailableError("docker socket unavailable")

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("leases", tenant_id, status, limit))
        return []

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

    async def fake_queue_status():
        return {"mode": "memory"}

    async def fake_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id}

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
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
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox"]["list_runtime_containers_status"] == "unavailable"
    assert body["sandbox"]["container_observation_degraded"] is True
    assert body["sandbox"]["containers"] == {
        "total": 0,
        "running": 0,
        "by_status": {},
        "ephemeral_running": 0,
        "persistent_running": 0,
    }
    assert body["sandbox"]["leases"]["active"] == 0
    assert body["capacity"]["schema_version"] == "ai-platform.capacity-baseline.v1"
    assert "docker socket unavailable" not in response.text.lower()
    assert calls == [
        ("leases", "default", "active", 100),
        ("leases", "default", None, 100),
        ("containers", {"tenant_id": "default"}),
    ]


def test_admin_runtime_overview_fails_closed_when_container_list_has_unexpected_provider_error(monkeypatch):
    calls = []

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            raise RuntimeError("provider invariant broken")

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        return []

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

    async def fake_queue_status():
        return {"mode": "memory"}

    async def fake_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id}

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_queue_insight)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
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
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
        headers=admin_headers(),
    )

    assert response.status_code == 500
    assert "provider invariant broken" not in response.text.lower()
    assert calls == [("containers", {"tenant_id": "default"})]

import pytest

from app.runtime.sandbox.contracts import StopResult


def expired_lease_row(**overrides):
    row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "sandbox_mode": "ephemeral",
        "provider": "docker",
        "status": "active",
        "browser_enabled": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_runtime_leases_stops_runtime_before_release(monkeypatch):
    from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases

    calls = []
    row = expired_lease_row()

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        calls.append(("list_expired", tenant_id, limit))
        return [row]

    async def fake_release_stopped_sandbox_leases(conn, *, tenant_id, reason, lease_ids, trace_id=None):
        calls.append(("release", tenant_id, reason, lease_ids, trace_id))
        return [row]

    class FakeProvider:
        async def stop(self, lease, *, reason):
            calls.append(("stop", lease.provider, lease.container_name, lease.tenant_id, lease.run_id, reason))
            return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    monkeypatch.setattr(
        "app.routes.sandbox_runtime_cleanup.repositories.list_expired_active_sandbox_leases",
        fake_list_expired_active_sandbox_leases,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_runtime_cleanup.repositories.release_stopped_sandbox_leases",
        fake_release_stopped_sandbox_leases,
    )

    cleaned = await cleanup_expired_sandbox_runtime_leases(
        object(),
        tenant_id="tenant-a",
        provider_factory=lambda provider_name: FakeProvider(),
    )

    assert cleaned == [row]
    assert calls == [
        ("list_expired", "tenant-a", 100),
        ("stop", "docker", "executor-exec-run-a", "tenant-a", "run-a", "expired"),
        ("release", "tenant-a", "expired", ["lease-a"], None),
    ]


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_runtime_leases_releases_only_stopped_leases_on_partial_failure(monkeypatch):
    from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases

    calls = []
    stopped_row = expired_lease_row(id="lease-stopped", run_id="run-stopped", trace_id="trace-stopped")
    failed_row = expired_lease_row(id="lease-failed", run_id="run-failed", trace_id="trace-failed")

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        return [stopped_row, failed_row]

    async def fake_release_stopped_sandbox_leases(conn, *, tenant_id, reason, lease_ids, trace_id=None):
        calls.append(("release", tenant_id, reason, lease_ids, trace_id))
        return [stopped_row]

    class FakeProvider:
        async def stop(self, lease, *, reason):
            calls.append(("stop", lease.run_id, reason))
            if lease.run_id == "run-failed":
                return StopResult(container_id=lease.container_id, status="failed", message="stop failed")
            return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    monkeypatch.setattr(
        "app.routes.sandbox_runtime_cleanup.repositories.list_expired_active_sandbox_leases",
        fake_list_expired_active_sandbox_leases,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_runtime_cleanup.repositories.release_stopped_sandbox_leases",
        fake_release_stopped_sandbox_leases,
    )

    with pytest.raises(SandboxRuntimeCleanupError):
        await cleanup_expired_sandbox_runtime_leases(
            object(),
            tenant_id="tenant-a",
            provider_factory=lambda provider_name: FakeProvider(),
        )

    assert calls == [
        ("stop", "run-stopped", "expired"),
        ("stop", "run-failed", "expired"),
        ("release", "tenant-a", "expired", ["lease-stopped"], None),
    ]

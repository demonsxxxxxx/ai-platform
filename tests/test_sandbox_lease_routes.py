from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime.sandbox.contracts import StopResult
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers():
    return {
        "X-AI-User-ID": "user-a",
        "X-AI-User-Name": "User A",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
    }


def lease_row(**overrides):
    values = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "sandbox_mode": "ephemeral",
        "provider": "fake",
        "status": "active",
        "browser_enabled": False,
        "resource_limits_json": {},
        "user_visible_payload_json": {"workspace": "/workspace", "inputs": "/workspace/inputs"},
        "lease_payload_json": {},
        "release_reason": "",
    }
    values.update(overrides)
    return values


def verified_runtime_lease_row(**overrides):
    return lease_row(
        provider="docker",
        runtime_container_id="exec-run-a",
        runtime_container_name="executor-exec-run-a",
        runtime_executor_url="http://executor.test",
        runtime_workspace_container_path="/workspace",
        runtime_handle_verified_at="2026-07-27T00:00:00Z",
        **overrides,
    )


def has_verified_runtime_handle(row):
    return bool(
        row.get("runtime_container_id")
        and row.get("runtime_container_name")
        and row.get("runtime_executor_url")
        and row.get("runtime_workspace_container_path")
        and row.get("runtime_handle_verified_at")
    )


def test_create_sandbox_lease_records_run_scoped_lease_and_event(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_sandbox_lease(conn, **kwargs):
        calls.append(("lease", kwargs))
        return lease_row(
            sandbox_mode=kwargs["sandbox_mode"],
            provider=kwargs["provider"],
            resource_limits_json=kwargs["resource_limits_json"],
            lease_payload_json=kwargs["lease_payload_json"],
        )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.sandbox_leases.sandbox_lease_repository.create_sandbox_lease", fake_create_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases",
        headers=headers(),
        json={
            "sandbox_mode": "ephemeral",
            "provider": "fake",
            "ttl_seconds": 600,
            "resource_limits": {"max_seconds": 60},
            "lease_payload": {"purpose": "test"},
        },
    )

    assert response.status_code == 200
    body = response.json()["sandbox_lease"]
    assert body["lease_id"] == "lease-a"
    assert body["workspace"] == {"workspace": "/workspace", "inputs": "/workspace/inputs"}
    assert calls[1][1]["event_type"] == "sandbox_lease_created"


@pytest.mark.parametrize("provider", ("docker", "opensandbox"))
def test_public_create_never_persists_an_unverified_active_real_provider_lease(monkeypatch, provider):
    persisted_rows = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_sandbox_lease(conn, **kwargs):
        row = lease_row(
            provider=kwargs["provider"],
            runtime_container_id=kwargs.get("runtime_container_id"),
            runtime_container_name=kwargs.get("runtime_container_name"),
            runtime_executor_url=kwargs.get("runtime_executor_url"),
            runtime_workspace_container_path=kwargs.get("runtime_workspace_container_path"),
            runtime_handle_verified_at=(
                "2026-07-27T00:00:00Z"
                if all(
                    kwargs.get(field)
                    for field in (
                        "runtime_container_id",
                        "runtime_container_name",
                        "runtime_executor_url",
                        "runtime_workspace_container_path",
                    )
                )
                else None
            ),
        )
        persisted_rows.append(row)
        return row

    async def fake_append_event(conn, **kwargs):
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.sandbox_leases.sandbox_lease_repository.create_sandbox_lease", fake_create_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases",
        headers=headers(),
        json={
            "sandbox_mode": "persistent",
            "provider": provider,
            "ttl_seconds": 600,
            "resource_limits": {},
            "lease_payload": {},
        },
    )

    unverified_active_rows = [
        row for row in persisted_rows if row["status"] == "active" and not has_verified_runtime_handle(row)
    ]
    assert unverified_active_rows == [], "real-provider create persisted an active lease without a verified handle"
    if response.is_success and response.json()["sandbox_lease"]["status"] == "active":
        assert persisted_rows and has_verified_runtime_handle(persisted_rows[-1])


def test_renew_expired_sandbox_lease_fails_closed(monkeypatch):
    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return lease_row()

    async def fake_renew_sandbox_lease(conn, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.renew_sandbox_lease", fake_renew_sandbox_lease)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/renew",
        headers=headers(),
        json={"ttl_seconds": 600},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "sandbox_lease_not_active"


def test_release_sandbox_lease_records_release_event(monkeypatch):
    calls = []

    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return lease_row()

    async def fake_release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs))
        return lease_row(status="released", release_reason=kwargs["reason"])

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.release_sandbox_lease", fake_release_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/release",
        headers=headers(),
        json={"reason": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["sandbox_lease"]["status"] == "released"
    assert calls[1][1]["event_type"] == "sandbox_lease_released"


def test_public_real_provider_release_stops_before_db_release(monkeypatch):
    calls = []
    transaction_count = 0
    state = verified_runtime_lease_row()

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal transaction_count
        transaction_count += 1
        yield object()

    class StopSucceededProvider:
        async def stop(self, lease, *, reason):
            calls.append(("stop", lease.provider, lease.container_id, reason))
            return StopResult(
                container_id=lease.container_id,
                status="stopped",
                message="stopped",
            )

    async def fake_get_sandbox_lease(conn, **kwargs):
        return dict(state)

    async def fake_release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["lease_id"], kwargs["reason"]))
        state.update(status="released", release_reason=kwargs["reason"])
        return dict(state)

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", tracked_transaction)
    monkeypatch.setattr(
        "app.routes.sandbox_leases.repositories.get_sandbox_lease",
        fake_get_sandbox_lease,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_leases.repositories.release_sandbox_lease",
        fake_release_sandbox_lease,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_leases.repositories.append_event",
        fake_append_event,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_leases.create_container_provider",
        lambda provider_name=None: StopSucceededProvider(),
    )

    response = TestClient(create_app()).post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/release",
        headers=headers(),
        json={"reason": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["sandbox_lease"]["status"] == "released"
    assert transaction_count == 1
    assert calls == [
        ("stop", "docker", "exec-run-a", "cancelled"),
        ("release", "lease-a", "cancelled"),
        ("event", "sandbox_lease_released"),
    ]


def test_public_release_is_idempotent_after_release(monkeypatch):
    released = verified_runtime_lease_row(status="released", release_reason="cancelled")

    async def fake_get_sandbox_lease(conn, **kwargs):
        return dict(released)

    async def fail_release(*args, **kwargs):
        raise AssertionError("released lease must not be mutated again")

    def fail_provider_factory(*args, **kwargs):
        raise AssertionError("released lease must not stop the provider again")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.sandbox_leases.repositories.get_sandbox_lease",
        fake_get_sandbox_lease,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_leases.repositories.release_sandbox_lease",
        fail_release,
    )
    monkeypatch.setattr(
        "app.routes.sandbox_leases.create_container_provider",
        fail_provider_factory,
    )

    response = TestClient(create_app()).post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/release",
        headers=headers(),
        json={"reason": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["sandbox_lease"]["status"] == "released"


def test_public_release_does_not_mark_real_provider_lease_released_without_confirmed_stop(monkeypatch):
    state = verified_runtime_lease_row()
    stop_calls = []
    release_mutations = []

    class StopFailedProvider:
        async def stop(self, lease, *, reason):
            stop_calls.append((lease.provider, lease.container_id, reason))
            return StopResult(container_id=lease.container_id, status="failed", message="provider stop failed")

    provider = StopFailedProvider()

    def provider_factory(provider_name=None):
        assert provider_name in {None, "docker"}
        return provider

    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return dict(state)

    async def fake_release_sandbox_lease(conn, **kwargs):
        release_mutations.append(kwargs)
        state.update(status="released", release_reason=kwargs["reason"])
        return dict(state)

    async def fake_append_event(conn, **kwargs):
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.release_sandbox_lease", fake_release_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.runtime.sandbox.container_provider.create_container_provider", provider_factory)
    monkeypatch.setattr("app.routes.sandbox_leases.create_container_provider", provider_factory, raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/runs/run-a/sandbox/leases/lease-a/release",
        headers=headers(),
        json={"reason": "cancelled"},
    )

    assert state["status"] in {"active", "cleanup_pending"}, (
        "DB lease became released without a confirmed provider stop"
    )
    assert release_mutations == []
    assert response.status_code >= 400
    assert stop_calls in ([], [("docker", "exec-run-a", "cancelled")])


@pytest.mark.parametrize("mismatch", ("tenant", "workspace", "run"))
def test_release_denied_lookup_has_no_provider_or_db_side_effects(monkeypatch, mismatch):
    provider_calls = []

    def fail_provider_factory(provider_name=None):
        provider_calls.append(provider_name)
        raise AssertionError("denied lease must not reach its provider")

    async def fake_get_sandbox_lease(conn, *, tenant_id, user_id, run_id, lease_id):
        return None

    async def fail_release_sandbox_lease(conn, **kwargs):
        raise AssertionError("denied lease must not mutate its row")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.sandbox_leases.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.get_sandbox_lease", fake_get_sandbox_lease)
    monkeypatch.setattr("app.routes.sandbox_leases.repositories.release_sandbox_lease", fail_release_sandbox_lease)
    monkeypatch.setattr("app.runtime.sandbox.container_provider.create_container_provider", fail_provider_factory)
    monkeypatch.setattr("app.routes.sandbox_leases.create_container_provider", fail_provider_factory, raising=False)
    client = TestClient(create_app())

    request_headers = headers()
    request_run_id = "run-a"
    if mismatch == "tenant":
        request_headers["X-AI-Tenant-ID"] = "tenant-b"
    elif mismatch == "run":
        request_run_id = "run-b"

    response = client.post(
        f"/api/ai/runs/{request_run_id}/sandbox/leases/lease-a/release",
        headers=request_headers,
        json={"reason": "cancelled"},
    )

    assert response.status_code == 404
    assert provider_calls == []

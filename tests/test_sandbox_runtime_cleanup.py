import pytest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.execution_boundary import (
    GOVERNED_EGRESS_PROOF_LABEL,
    build_governed_egress_proof,
    governed_egress_proof_label,
)
from app.runtime.sandbox.container_provider import _opensandbox_governed_runtime_subject
from app.runtime.sandbox.contracts import StopResult


TEST_PROOF_KEY = "cleanup-test-proof-key-with-enough-entropy-2026"
TEST_PROOF_NOW = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)


def opensandbox_cleanup_proof():
    return build_governed_egress_proof(
        signing_key=TEST_PROOF_KEY,
        provider="opensandbox",
        runtime_subject=_opensandbox_governed_runtime_subject("runsc", "runtime-subject-a"),
        policy_subject="gateway-policy-subject-a",
        callback_subject="callback-boundary-subject-a",
        denial_subject="gateway-deny-audit-subject-a:gateway-deny-counter-subject-a",
        network_id="profile-a",
        network_name="http://opensandbox.local:8080",
        network_internal=False,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        image_subject="registry.example/ai-platform@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope="cleanup-skill-scope",
        authorized_native_tool_scope="cleanup-native-tool-scope",
        lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
        issued_at=TEST_PROOF_NOW,
        expires_at=TEST_PROOF_NOW + timedelta(seconds=120),
    )


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
        "runtime_container_id": f"exec-{overrides.get('run_id', 'run-a')}",
        "runtime_container_name": f"executor-exec-{overrides.get('run_id', 'run-a')}",
        "runtime_executor_url": "http://executor.test",
        "runtime_workspace_container_path": "/workspace",
        "runtime_handle_verified_at": "2026-07-11T00:00:00Z",
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
async def test_cleanup_expired_sandbox_runtime_leases_uses_verified_handle_not_lease_payload(monkeypatch):
    from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases

    calls = []
    proof = opensandbox_cleanup_proof()
    row = expired_lease_row(
        provider="opensandbox",
        runtime_container_id="osb-run-a",
        runtime_container_name="opensandbox-run-a",
        runtime_executor_url="http://opensandbox-executor.test:18000",
        runtime_workspace_container_path="/workspace",
        lease_payload_json={
            "container_id": "attacker-container",
            "container_name": "attacker-container",
            "executor_url": "http://attacker.invalid",
            "workspace_host_path": "/tmp/private/workspace",
            "workspace_container_path": "/attacker-workspace",
            "labels": {
                "ai-platform.provider_backend": "opensandbox",
                "ai-platform.egress.policy": "opensandbox-network-policy",
            },
            "governed_egress_proof": proof,
        },
    )

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        return [row]

    async def fake_release_stopped_sandbox_leases(conn, *, tenant_id, reason, lease_ids, trace_id=None):
        calls.append(("release", tenant_id, reason, lease_ids, trace_id))
        return [row]

    class FakeProvider:
        async def stop(self, lease, *, reason):
            calls.append(
                (
                    "stop",
                    lease.provider,
                    lease.container_id,
                        lease.container_name,
                        lease.executor_url,
                        lease.workspace_host_path,
                        lease.workspace_container_path,
                        lease.labels,
                        reason,
                    )
                )
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
    assert calls[0] == (
        "stop",
        "opensandbox",
        "osb-run-a",
        "opensandbox-run-a",
        "http://opensandbox-executor.test:18000",
        "",
        "/workspace",
        {GOVERNED_EGRESS_PROOF_LABEL: governed_egress_proof_label(proof)},
        "expired",
    )
    assert calls[1] == ("release", "tenant-a", "expired", ["lease-a"], None)


@pytest.mark.asyncio
async def test_opensandbox_cleanup_without_signed_proof_retains_db_lease(monkeypatch):
    from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases

    row = expired_lease_row(
        provider="opensandbox",
        runtime_container_id="osb-run-a",
        runtime_container_name="opensandbox-run-a",
        runtime_executor_url="http://opensandbox-executor.test:18000",
        lease_payload_json={"labels": {"ai-platform.owner": "sandbox-runtime"}},
    )
    releases = []

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        return [row]

    async def fake_release_stopped_sandbox_leases(conn, **kwargs):
        releases.append(kwargs)
        return [row]

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
            provider_factory=lambda _provider_name: pytest.fail("provider must not receive an unverifiable lease"),
        )

    assert releases == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ("ambiguous-identity", "get-info-404"))
async def test_production_opensandbox_cleanup_retains_db_lease_when_stop_is_unconfirmed(monkeypatch, failure_mode):
    from opensandbox.exceptions import SandboxApiException

    from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases
    from app.runtime.sandbox.container_provider import OpenSandboxContainerProvider

    proof = opensandbox_cleanup_proof()
    image = "registry.example/ai-platform@sha256:" + "a" * 64
    metadata = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.browser_enabled": "false",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.executor.requested_image": image,
        "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
        "ai-platform.executor.user": "10001:10001",
        "ai-platform.executor.uid": "10001",
        "ai-platform.executor.gid": "10001",
        "ai-platform.executor.identity_evidence": "authenticated-runtime-endpoint",
        "ai-platform.external_egress.profile_version": "v1",
        "ai-platform.external_egress.profile_id": "profile-a",
        "ai-platform.external_egress.runtime_identity": "runsc",
        "ai-platform.runtime_subject": "runtime-subject-a",
        "ai-platform.external_egress.gateway_policy_subject": "gateway-policy-subject-a",
        "ai-platform.external_egress.callback_boundary_subject": "callback-boundary-subject-a",
        "ai-platform.external_egress.deny_audit_subject": "gateway-deny-audit-subject-a",
        "ai-platform.external_egress.deny_counter_subject": "gateway-deny-counter-subject-a",
        "ai-platform.external_egress.profile_requested_image": image,
        "ai-platform.external_egress.profile_requested_image_digest": "sha256:" + "a" * 64,
        "ai-platform.external_egress.profile_expires_at": proof["expires_at"],
    }
    remote = SimpleNamespace(
        id="osb-run-a",
        metadata=metadata,
        status=SimpleNamespace(state="RUNNING"),
        kill_calls=0,
        close_calls=0,
    )

    def get_info():
        if failure_mode == "get-info-404":
            raise SandboxApiException("sandbox metadata is unavailable", status_code=404)
        return {
            "id": remote.id,
            "metadata": dict(remote.metadata),
            "status": {"state": remote.status.state},
        }

    def kill():
        remote.kill_calls += 1

    def close():
        remote.close_calls += 1

    remote.get_info = get_info
    remote.kill = kill
    remote.close = close

    class FakeSandboxClass:
        @classmethod
        def connect(cls, sandbox_id, **_kwargs):
            assert sandbox_id == remote.id
            return remote

    class FakeConnectionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Settings:
        sandbox_egress_proof_signing_key = TEST_PROOF_KEY
        sandbox_egress_proof_key_id = "current"
        sandbox_egress_proof_previous_keys_json = ""
        opensandbox_api_key = ""
        opensandbox_domain = "opensandbox.local:8080"
        opensandbox_protocol = "http"
        opensandbox_request_timeout_seconds = 30
        opensandbox_use_server_proxy = False

    provider = OpenSandboxContainerProvider(
        sandbox_class=FakeSandboxClass,
        connection_config_class=FakeConnectionConfig,
        utcnow=lambda: TEST_PROOF_NOW + timedelta(days=1),
    )
    row = expired_lease_row(
        provider="opensandbox",
        runtime_container_id="osb-run-a",
        runtime_container_name="opensandbox-run-a",
        runtime_executor_url="http://opensandbox-executor.test:18000",
        lease_payload_json={"governed_egress_proof": proof},
    )
    releases = []

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        return [row]

    async def fake_release_stopped_sandbox_leases(conn, **kwargs):
        releases.append(kwargs)
        return [row]

    monkeypatch.setattr("app.runtime.sandbox.container_provider.get_settings", lambda: Settings())
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
            provider_factory=lambda _provider_name: provider,
        )

    assert remote.kill_calls == 0
    assert remote.close_calls == 0
    assert releases == []
    assert provider._leases["opensandbox-run-a"].container_id == "osb-run-a"


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

    @asynccontextmanager
    async def fake_transaction():
        yield object()

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
    monkeypatch.setattr("app.routes.sandbox_runtime_cleanup.transaction", fake_transaction)

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


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_runtime_leases_partial_failure_uses_committed_release_transaction(monkeypatch):
    from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases

    calls = []
    outer_conn = object()
    committed_conn = object()
    stopped_row = expired_lease_row(id="lease-stopped", run_id="run-stopped", trace_id="trace-stopped")
    failed_row = expired_lease_row(id="lease-failed", run_id="run-failed", trace_id="trace-failed")

    async def fake_list_expired_active_sandbox_leases(conn, *, tenant_id=None, limit=100):
        assert conn is outer_conn
        return [stopped_row, failed_row]

    async def fake_release_stopped_sandbox_leases(conn, *, tenant_id, reason, lease_ids, trace_id=None):
        calls.append(("release_conn", conn is committed_conn, conn is outer_conn, lease_ids))
        return [stopped_row]

    @asynccontextmanager
    async def fake_transaction():
        calls.append(("committed_transaction", "enter"))
        yield committed_conn
        calls.append(("committed_transaction", "exit"))

    class FakeProvider:
        async def stop(self, lease, *, reason):
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
    monkeypatch.setattr("app.routes.sandbox_runtime_cleanup.transaction", fake_transaction, raising=False)

    with pytest.raises(SandboxRuntimeCleanupError):
        await cleanup_expired_sandbox_runtime_leases(
            outer_conn,
            tenant_id="tenant-a",
            provider_factory=lambda provider_name: FakeProvider(),
        )

    assert calls == [
        ("committed_transaction", "enter"),
        ("release_conn", True, False, ["lease-stopped"]),
        ("committed_transaction", "exit"),
    ]

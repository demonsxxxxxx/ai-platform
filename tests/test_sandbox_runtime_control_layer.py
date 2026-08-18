import pytest
from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal
from app.models import SandboxLeaseReleaseRequest
from app.routes import admin_runtime, runtime_callbacks, sandbox_leases
from app.routes.sandbox_runtime_cleanup import (
    SandboxRuntimeCleanupError,
    stop_sandbox_leases,
)
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox import executor_app
from app.runtime.sandbox.contracts import ExecutorCallbackEvent, StopResult
from app.runtime.sandbox.executor_app import _CallbackBatchIdFactory


class _FakeTransaction:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _SingleRowCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


def _callback() -> ExecutorCallbackEvent:
    return ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        batch_id="callback-batch-a",
        status="running",
        progress=20,
        new_message={"type": "assistant", "delta": "hello"},
        state_patch={"current_step": "thinking"},
        events=[
            AgentEvent(
                type="assistant_delta",
                message="hello",
                payload={"delta": "hello"},
            ),
            AgentEvent(
                type="execution_progress",
                message="private progress",
                payload={"progress": 20},
                admin_only=True,
            ),
        ],
    )


def test_callback_batch_ids_are_restart_namespaced_and_monotonic():
    first = _CallbackBatchIdFactory("executor-boot-a")
    second = _CallbackBatchIdFactory("executor-boot-b")

    assert [first.next_id(), first.next_id(), second.next_id()] == [
        "callback-executor-boot-a-1",
        "callback-executor-boot-a-2",
        "callback-executor-boot-b-1",
    ]


@pytest.mark.asyncio
async def test_release_lookup_holds_a_row_lock_for_provider_stop_ordering():
    calls = []

    class Connection:
        async def execute(self, statement, parameters):
            calls.append((statement, parameters))
            return _SingleRowCursor({"id": "lease-a", "status": "active"})

    row = await repositories.get_sandbox_lease(
        Connection(),
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
    )

    assert row == {"id": "lease-a", "status": "active"}
    assert "for update" in calls[0][0].lower()
    assert calls[0][1] == ("tenant-a", "user-a", "run-a", "lease-a")


@pytest.mark.asyncio
async def test_current_attempt_lookup_enforces_first_class_payload_consistency():
    calls = []

    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, statement, parameters):
            calls.append((statement, parameters))
            return Cursor()

    await repositories.list_current_sandbox_runtime_leases_for_attempt(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )

    normalized = " ".join(calls[0][0].split())
    assert "lease_payload_json ->> 'attempt_id' = %s" in normalized
    assert "attempt_id is null or attempt_id = lease_payload_json ->> 'attempt_id'" in normalized
    assert calls[0][1] == ("tenant-a", "run-a", "attempt-a")


@pytest.mark.asyncio
async def test_provider_stop_exception_becomes_recoverable_cleanup_failure():
    row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_mode": "ephemeral",
        "provider": "docker",
        "browser_enabled": False,
        "runtime_container_id": "container-a",
        "runtime_container_name": "executor-container-a",
        "runtime_executor_url": "http://executor.test",
        "runtime_workspace_container_path": "/workspace",
        "runtime_handle_verified_at": "2026-08-05T00:00:00Z",
    }

    class RaisingProvider:
        async def stop(self, lease, *, reason):
            raise RuntimeError("private provider detail")

    with pytest.raises(SandboxRuntimeCleanupError) as exc_info:
        await stop_sandbox_leases(
            [row],
            reason="cancelled",
            provider_factory=lambda provider: RaisingProvider(),
        )

    assert exc_info.value.failures == [
        {
            "container_id": "container-a",
            "message": "Sandbox provider stop raised an exception",
        }
    ]
    assert "private provider detail" not in str(exc_info.value.failures)


@pytest.mark.asyncio
async def test_provider_failed_result_is_sanitized_before_persistence():
    row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_mode": "ephemeral",
        "provider": "docker",
        "browser_enabled": False,
        "runtime_container_id": "container-a",
        "runtime_container_name": "executor-container-a",
        "runtime_executor_url": "http://executor.test",
        "runtime_workspace_container_path": "/workspace",
        "runtime_handle_verified_at": "2026-08-05T00:00:00Z",
    }

    class FailedProvider:
        async def stop(self, lease, *, reason):
            return StopResult(
                container_id=lease.container_id,
                status="failed",
                message="private provider detail",
            )

    with pytest.raises(SandboxRuntimeCleanupError) as exc_info:
        await stop_sandbox_leases(
            [row],
            reason="cancelled",
            provider_factory=lambda provider: FailedProvider(),
        )

    assert exc_info.value.failures == [
        {"container_id": "container-a", "message": "Sandbox provider stop failed"}
    ]


@pytest.mark.asyncio
async def test_explicit_release_binds_failure_to_lease_and_surfaces_audit_outage(monkeypatch):
    captured = []
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User",
        tenant_id="tenant-a",
        roles=["user"],
    )
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

    async def get_lease(conn, **kwargs):
        return row

    async def fail_stop(*args, **kwargs):
        raise SandboxRuntimeCleanupError(
            [{"container_id": "container-a", "message": "Sandbox provider stop failed"}]
        )

    async def fail_audit(conn, **kwargs):
        captured.append(kwargs)
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(sandbox_leases, "transaction", _FakeTransaction)
    monkeypatch.setattr(sandbox_leases.repositories, "get_sandbox_lease", get_lease)
    monkeypatch.setattr(sandbox_leases, "stop_sandbox_leases", fail_stop)
    monkeypatch.setattr(
        sandbox_leases.repositories,
        "record_sandbox_runtime_cleanup_outcome",
        fail_audit,
    )

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_leases.release_sandbox_lease(
            "run-a",
            "lease-a",
            SandboxLeaseReleaseRequest(reason="cancelled"),
            principal,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "sandbox_cleanup_audit_unavailable"
    assert captured[0]["lease_ids"] == ["lease-a"]


@pytest.mark.asyncio
async def test_admin_orphan_cleanup_failure_writes_tenant_audit(monkeypatch):
    audits = []
    principal = AuthPrincipal(
        user_id="admin-a",
        display_name="Admin",
        tenant_id="tenant-a",
        roles=["ai_admin"],
    )

    class FailedProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            return [
                StopResult(
                    container_id="container-a",
                    status="failed",
                    message="private provider detail",
                )
            ]

    async def append_audit_log(conn, **kwargs):
        audits.append(kwargs)
        return "audit-a"

    monkeypatch.setattr(admin_runtime, "transaction", _FakeTransaction)
    monkeypatch.setattr(admin_runtime.repositories, "append_audit_log", append_audit_log)

    with pytest.raises(HTTPException) as exc_info:
        await admin_runtime._cleanup_provider_orphans(FailedProvider(), principal)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "sandbox_provider_cleanup_failed"
    assert audits == [
        {
            "tenant_id": "tenant-a",
            "user_id": "admin-a",
            "action": "sandbox.runtime.orphan_cleanup.failed",
            "target_type": "sandbox_runtime",
            "target_id": "tenant-a",
            "payload_json": {
                "failure_count": 1,
                "failures": [
                    {
                        "container_id": "container-a",
                        "message": "Provider orphan cleanup failed",
                    }
                ],
            },
        }
    ]
    assert "private provider detail" not in str(audits)


@pytest.mark.asyncio
async def test_admin_orphan_cleanup_surfaces_audit_outage(monkeypatch):
    principal = AuthPrincipal(
        user_id="admin-a",
        display_name="Admin",
        tenant_id="tenant-a",
        roles=["ai_admin"],
    )

    class FailedProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            return [StopResult(container_id="container-a", status="failed", message="private detail")]

    async def fail_audit(conn, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(admin_runtime, "transaction", _FakeTransaction)
    monkeypatch.setattr(admin_runtime.repositories, "append_audit_log", fail_audit)

    with pytest.raises(HTTPException) as exc_info:
        await admin_runtime._cleanup_provider_orphans(FailedProvider(), principal)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "sandbox_cleanup_audit_unavailable"


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.asyncio
async def test_executor_callback_persists_one_attempt_scoped_idempotent_batch(
    monkeypatch,
    duplicate,
):
    batches = []

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {
            "tenant_id": "tenant-a",
            "session_id": "session-a",
            "status": "running",
        }

    async def exact_lease(conn, *, tenant_id, run_id, attempt_id):
        return [
            {
                "attempt_id": attempt_id,
                "lease_payload_json": {"attempt_id": attempt_id},
            }
        ]

    async def append_event_batch(conn, **kwargs):
        batches.append(kwargs)
        return {
            "accepted": True,
            "duplicate": duplicate,
            "callback_received_at": "2026-08-17T00:00:00Z",
        }

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("batched callback must not use per-event persistence")

    class _Authority:
        attempt_id = "attempt-a"
        state = "confirmed"
        tenant_scope = "tenant-a"
        stream_incarnation = 1

    class _Bridge:
        async def append(self, envelope):
            return "1-0"

        async def aclose(self):
            return None

    async def get_authority(conn, *, tenant_id, run_id):
        return _Authority()

    monkeypatch.setattr(runtime_callbacks, "transaction", _FakeTransaction)
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        exact_lease,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_event_batch)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "RedisStreamBridge", _Bridge)

    response = await runtime_callbacks.record_executor_callback(_callback())

    expected_response = {
        "accepted": True,
        "batch_id": "callback-batch-a",
        "event_count": 3,
    }
    if duplicate:
        expected_response["deduplicated"] = True
    assert response == expected_response
    assert executor_app._callback_acknowledges_exact_batch(
        response,
        batch_id="callback-batch-a",
        event_count=2,
    )
    assert not executor_app._callback_acknowledges_exact_batch(
        {**response, "event_count": 2},
        batch_id="callback-batch-a",
        event_count=2,
    )
    assert not executor_app._callback_acknowledges_exact_batch(
        {**response, "event_count": 4},
        batch_id="callback-batch-a",
        event_count=2,
    )
    assert not executor_app._callback_acknowledges_exact_batch(
        response,
        batch_id="callback-batch-other",
        event_count=2,
    )
    assert len(batches) == 1
    assert {
        key: batches[0][key]
        for key in ("tenant_id", "run_id", "attempt_id", "batch_id")
    } == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "batch_id": "callback-batch-a",
    }
    assert [event["event_type"] for event in batches[0]["events"]] == [
        "executor_callback",
        "tool_call_delta",
        "executor_private_event",
    ]


@pytest.mark.asyncio
async def test_executor_callback_rejects_disagreeing_first_class_attempt_binding(monkeypatch):
    async def get_run_identity(conn, *, run_id, for_update=False):
        return {
            "tenant_id": "tenant-a",
            "session_id": "session-a",
            "status": "running",
        }

    async def inconsistent_lease(conn, *, tenant_id, run_id, attempt_id):
        return [
            {
                "attempt_id": attempt_id,
                "lease_payload_json": {"attempt_id": "attempt-old"},
            }
        ]

    async def fail_append(*args, **kwargs):
        raise AssertionError("inconsistent attempt binding must not append events")

    monkeypatch.setattr(runtime_callbacks, "transaction", _FakeTransaction)
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        inconsistent_lease,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", fail_append)

    with pytest.raises(HTTPException) as exc_info:
        await runtime_callbacks.record_executor_callback(_callback())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "sandbox_runtime_attempt_mismatch"

"""Sandbox-owned cancellation settlement and route-free composition contracts."""

import asyncio
from contextlib import asynccontextmanager
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.sandbox.api import (
    SandboxCleanupOutcomes,
    SandboxCleanupPersistenceError,
    SandboxRuntimeCleanup,
    SandboxRuntimeCleanupError,
)


def cleanup_harness(*, partial_failure=False, persistence_failure=None, cancelled=False):
    calls = []
    connection = object()
    active = False
    stopped = [
        {"id": "lease-a", "run_id": "run-a"},
        {"id": "lease-b", "run_id": "run-b"},
    ]
    failed = {"id": "lease-failed", "run_id": "run-failed"}

    @asynccontextmanager
    async def transaction():
        nonlocal active
        assert not active
        calls.append(("begin",))
        if persistence_failure == "begin":
            raise OSError("database unavailable")
        active = True
        try:
            yield connection
            if persistence_failure == "commit":
                raise OSError("commit unavailable")
        except BaseException:
            calls.append(("rollback",))
            raise
        else:
            calls.append(("commit",))
        finally:
            active = False

    async def release(conn, **kwargs):
        assert active and conn is connection
        calls.append(("release", kwargs))
        if persistence_failure == "release":
            raise OSError("release unavailable")
        return [next(row for row in stopped if row["id"] in kwargs["lease_ids"])]

    async def append_event(conn, **kwargs):
        assert active and conn is connection
        calls.append(("event", kwargs))

    async def outcome(conn, **kwargs):
        assert active and conn is connection
        calls.append(("outcome", kwargs))
        if persistence_failure == "outcome":
            raise OSError("audit unavailable")

    class Provider:
        async def stop(self, lease, *, reason):
            assert not active
            calls.append(("stop", lease.container_id, reason))
            if cancelled:
                raise asyncio.CancelledError
            status = "failed" if lease.container_id == "lease-failed" else "stopped"
            return SimpleNamespace(status=status, container_id=lease.container_id)

    def recover(row, *, allow_historical_internal_test_cleanup):
        assert allow_historical_internal_test_cleanup
        return SimpleNamespace(provider="docker", container_id=row["id"])

    cleanup = SandboxRuntimeCleanup(
        transaction_factory=transaction,
        lease_repository=SimpleNamespace(release_stopped_sandbox_leases=release),
        outcomes=SimpleNamespace(record_sandbox_runtime_cleanup_outcome=outcome),
        append_event=append_event,
        handle_recovery=SimpleNamespace(from_persisted_row=recover),
        settings=lambda: SimpleNamespace(),
    )
    rows = [*stopped, failed] if partial_failure else stopped
    return cleanup, rows, lambda _name: Provider(), calls


@pytest.mark.parametrize("role,reason", [("owner", "cancel_requested"), ("admin", "admin_cancel_requested")])
@pytest.mark.parametrize("partial_failure", [False, True])
async def test_cancel_cleanup_settles_same_scope_after_stop_before_reporting_failure(role, reason, partial_failure):
    cleanup, rows, provider_factory, calls = cleanup_harness(partial_failure=partial_failure)
    operation = cleanup.cleanup_cancelled_run_leases(
        rows,
        tenant_id="tenant-a",
        run_id="cancelled-run",
        user_id="actor-a",
        requested_by_role=role,
        reason=reason,
        trace_id="trace-a",
        provider_factory=provider_factory,
    )
    if partial_failure:
        with pytest.raises(SandboxRuntimeCleanupError) as caught:
            await operation
        assert caught.value.stopped_leases == rows[:2]
        assert caught.value.failed_leases == [rows[2]]
    else:
        await operation
    assert [call[0] for call in calls] == (
        ["stop"] * len(rows) + ["begin", "release", "event", "release", "event", "outcome", "commit"]
    )
    releases = [call[1] for call in calls if call[0] == "release"]
    assert releases == [
        {"tenant_id": "tenant-a", "run_id": "run-a", "reason": reason, "lease_ids": ["lease-a"]},
        {"tenant_id": "tenant-a", "run_id": "run-b", "reason": reason, "lease_ids": ["lease-b"]},
    ]
    events = [call[1] for call in calls if call[0] == "event"]
    assert [event["payload"].get("requested_by_role") for event in events] == (
        ["admin", "admin"] if role == "admin" else [None, None]
    )
    outcome = next(call[1] for call in calls if call[0] == "outcome")
    assert outcome == {
        "tenant_id": "tenant-a", "run_id": "cancelled-run", "trace_id": "trace-a",
        "user_id": "actor-a", "requested_by_role": role, "reason": reason,
        "status": "failed" if partial_failure else "succeeded",
        "lease_ids": ["lease-failed"] if partial_failure else ["lease-a", "lease-b"],
        "failures": [{"container_id": "lease-failed", "message": "Sandbox provider stop failed"}] if partial_failure else [],
    }


@pytest.mark.parametrize("persistence_failure", ["begin", "release", "outcome", "commit"])
@pytest.mark.parametrize("partial_failure", [False, True])
async def test_cancel_cleanup_persistence_failure_takes_precedence(persistence_failure, partial_failure):
    cleanup, rows, provider_factory, calls = cleanup_harness(
        partial_failure=partial_failure, persistence_failure=persistence_failure,
    )
    with pytest.raises(SandboxCleanupPersistenceError) as caught:
        await cleanup.cleanup_cancelled_run_leases(
            rows, tenant_id="tenant-a", run_id="run-a", user_id="actor-a",
            requested_by_role="owner", reason="cancel_requested", trace_id=None,
            provider_factory=provider_factory,
        )
    assert isinstance(caught.value.__cause__, OSError)
    assert ("commit",) not in calls
    if persistence_failure != "begin":
        assert calls[-1] == ("rollback",)


async def test_cancel_cleanup_no_stopped_leases_does_not_open_settlement_transaction():
    cleanup, _rows, provider_factory, calls = cleanup_harness()
    await cleanup.cleanup_cancelled_run_leases(
        [], tenant_id="tenant-a", run_id="run-a", user_id="actor-a",
        requested_by_role="owner", reason="cancel_requested", trace_id=None,
        provider_factory=provider_factory,
    )
    assert calls == []


async def test_cancel_cleanup_cancellation_does_not_report_or_settle_provider_success():
    cleanup, rows, provider_factory, calls = cleanup_harness(cancelled=True)
    with pytest.raises(asyncio.CancelledError):
        await cleanup.cleanup_cancelled_run_leases(
            rows, tenant_id="tenant-a", run_id="run-a", user_id="actor-a",
            requested_by_role="owner", reason="cancel_requested", trace_id=None,
            provider_factory=provider_factory,
        )
    assert calls == [("stop", "lease-a", "cancel_requested")]


@pytest.mark.parametrize("first", ["app.sandbox.api", "app.runtime.sandbox.contracts", "app.runtime.sandbox.container_provider"])
def test_cleanup_composition_imports_without_http_routes(first):
    script = f"""
import importlib
import sys
importlib.import_module({first!r})
from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.sandbox.api import SandboxRuntimeCleanup
assert isinstance(build_sandbox_runtime_cleanup(), SandboxRuntimeCleanup)
assert not any(name == 'app.routes' or name.startswith('app.routes.') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_record_sandbox_runtime_cleanup_outcome_writes_event_and_audit():
    calls = []
    connection = object()

    async def fake_append_event(conn, **kwargs):
        assert conn is connection
        calls.append(("event", kwargs))
        return "evt-cleanup"

    async def fake_append_audit_log(conn, **kwargs):
        assert conn is connection
        calls.append(("audit", kwargs))
        return "aud-cleanup"

    outcomes = SandboxCleanupOutcomes(append_event=fake_append_event, append_audit_log=fake_append_audit_log)

    await outcomes.record_sandbox_runtime_cleanup_outcome(
        connection,
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace-a",
        user_id="user-a",
        requested_by_role="owner",
        reason="cancel_requested",
        status="failed",
        lease_ids=["lease-a"],
        failures=[{"container_id": "lease-a", "message": "runtime handle missing"}],
    )

    assert calls == [
        (
            "event",
            {
                "tenant_id": "tenant-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "event_type": "sandbox_runtime_cleanup_failed",
                "stage": "sandbox",
                "message": "Sandbox runtime cleanup failed",
                "payload": {
                    "visible_to_user": False,
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failure_count": 1,
                    "requested_by_role": "owner",
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                },
            },
        ),
        (
            "audit",
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "action": "sandbox.runtime.cleanup.failed",
                "target_type": "run",
                "target_id": "run-a",
                "trace_id": "trace-a",
                "payload_json": {
                    "run_id": "run-a",
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                    "requested_by_role": "owner",
                },
            },
        ),
    ]



@pytest.mark.parametrize("status", ["failed", "succeeded", "legacy_other"])
@pytest.mark.parametrize("role", [None, "", "owner", "admin", "maintenance"])
@pytest.mark.parametrize("items", [None, [], ["lease-private"]])
async def test_cleanup_outcome_exact_payload_matrix(status, role, items):
    calls = []
    conn = object()
    failures = None if items is None else ([{"container_id": "lease-private", "message": "private-marker"}] if items else [])

    async def event(connection, **kwargs):
        assert connection is conn
        calls.append(("event", kwargs))

    async def audit(connection, **kwargs):
        assert connection is conn
        calls.append(("audit", kwargs))

    await SandboxCleanupOutcomes(append_event=event, append_audit_log=audit).record_sandbox_runtime_cleanup_outcome(
        conn, tenant_id="tenant-a", run_id="run-a", reason="expired", status=status,
        requested_by_role=role, lease_ids=items, failures=failures,
    )
    payload = {"visible_to_user": False, "reason": "expired", "status": status,
               "lease_ids": items or [], "failure_count": len(failures or [])}
    if role:
        payload["requested_by_role"] = role
    if failures:
        payload["failures"] = failures
    assert calls == [
        ("event", {"tenant_id": "tenant-a", "run_id": "run-a", "trace_id": None,
                   "event_type": "sandbox_runtime_cleanup_failed" if status == "failed" else "sandbox_runtime_cleanup_succeeded",
                   "stage": "sandbox", "message": "Sandbox runtime cleanup failed" if status == "failed" else "Sandbox runtime cleanup succeeded",
                   "payload": payload}),
        ("audit", {"tenant_id": "tenant-a", "user_id": None, "action": f"sandbox.runtime.cleanup.{status}",
                   "target_type": "run", "target_id": "run-a", "trace_id": None,
                   "payload_json": {"run_id": "run-a", "reason": "expired", "status": status,
                                    "lease_ids": items or [], "failures": failures or [], "requested_by_role": role}}),
    ]


@pytest.mark.parametrize("fault", ["event", "audit"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_cleanup_outcome_writer_failure_identity(fault, cancelled):
    error = asyncio.CancelledError("cancel") if cancelled else RuntimeError("writer")
    calls = []

    async def event(*args, **kwargs):
        calls.append("event")
        if fault == "event":
            raise error

    async def audit(*args, **kwargs):
        calls.append("audit")
        if fault == "audit":
            raise error

    with pytest.raises(type(error)) as caught:
        await SandboxCleanupOutcomes(append_event=event, append_audit_log=audit).record_sandbox_runtime_cleanup_outcome(
            object(), tenant_id="tenant-a", run_id="run-a", reason="expired", status="failed",
        )
    assert caught.value is error
    assert calls == (["event"] if fault == "event" else ["event", "audit"])


async def test_cleanup_outcome_task_cancel_during_audit():
    entered = asyncio.Event()
    calls = []
    seen = []

    async def event(*args, **kwargs):
        calls.append("event")

    async def audit(*args, **kwargs):
        calls.append("audit")
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as error:
            seen.append(error)
            raise

    task = asyncio.create_task(SandboxCleanupOutcomes(append_event=event, append_audit_log=audit).record_sandbox_runtime_cleanup_outcome(
        object(), tenant_id="tenant-a", run_id="run-a", reason="expired", status="failed",
    ))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value is seen[0]
    assert calls == ["event", "audit"]


async def test_cleanup_composition_both_writers_late_bound(monkeypatch):
    from app.bootstrap import sandbox_cleanup

    calls = []
    conn = object()

    async def before(*args, **kwargs):
        raise AssertionError("captured writer before construction")

    monkeypatch.setattr(sandbox_cleanup.run_events_postgres, "append_event", before)
    monkeypatch.setattr(sandbox_cleanup.audit_postgres, "append_audit_log", before)
    outcomes = sandbox_cleanup.build_sandbox_cleanup_outcomes()
    cleanup = sandbox_cleanup.build_sandbox_runtime_cleanup()

    async def event(connection, **kwargs):
        assert connection is conn
        calls.append("event")

    async def audit(connection, **kwargs):
        assert connection is conn
        calls.append("audit")

    monkeypatch.setattr(sandbox_cleanup.run_events_postgres, "append_event", event)
    monkeypatch.setattr(sandbox_cleanup.audit_postgres, "append_audit_log", audit)
    for writer in [outcomes, cleanup._outcomes]:
        assert isinstance(writer, SandboxCleanupOutcomes)
        await writer.record_sandbox_runtime_cleanup_outcome(conn, tenant_id="tenant-a", run_id="run-a", reason="expired", status="failed")
    assert calls == ["event", "audit", "event", "audit"]


@pytest.mark.parametrize("fault", [None, "later-audit", "cancel"])
async def test_expiry_groups_failure_scopes_after_successful_release(fault):
    calls = []
    conn = object()
    active = False
    stopped = [{"id": "stopped", "tenant_id": "tenant-a", "run_id": "run-a"}]
    failed = [
        {"id": "failed-a1", "tenant_id": "tenant-a", "run_id": "run-a", "trace_id": "trace-first"},
        {"id": "failed-a2", "tenant_id": "tenant-a", "run_id": "run-a", "trace_id": "trace-second"},
        {"id": "failed-b", "tenant_id": "tenant-b", "run_id": "run-b", "trace_id": "trace-b"},
    ]
    failures = [{"container_id": row["id"], "message": "private-marker"} for row in failed]
    business_error = SandboxRuntimeCleanupError(failures, stopped_leases=stopped, failed_leases=failed)

    @asynccontextmanager
    async def transaction():
        nonlocal active
        active = True
        calls.append(("begin",))
        try:
            yield conn
        except BaseException:
            calls.append(("rollback",))
            raise
        else:
            calls.append(("commit",))
        finally:
            active = False

    async def list_rows(connection, **kwargs):
        assert connection is conn and active
        assert kwargs == {"tenant_id": None, "limit": 100}
        return stopped + failed

    async def stop(*args, **kwargs):
        assert active
        calls.append(("stop",))
        raise business_error

    async def release(connection, **kwargs):
        assert connection is conn and active
        assert kwargs == {"tenant_id": "tenant-a", "reason": "expired", "lease_ids": ["stopped"]}
        calls.append(("release",))
        return stopped

    async def event(connection, **kwargs):
        assert connection is conn and active
        calls.append(("event", kwargs))

    async def audit(connection, **kwargs):
        assert connection is conn and active
        calls.append(("audit", kwargs))
        if kwargs["tenant_id"] == "tenant-b":
            if fault == "later-audit":
                raise RuntimeError("later group")
            if fault == "cancel":
                raise asyncio.CancelledError()

    cleanup = SandboxRuntimeCleanup(
        transaction_factory=transaction,
        lease_repository=SimpleNamespace(list_expired_active_sandbox_leases=list_rows),
        outcomes=SandboxCleanupOutcomes(append_event=event, append_audit_log=audit),
        append_event=event, handle_recovery=None, settings=lambda: SimpleNamespace(),
    )
    cleanup.stop_sandbox_leases = stop
    cleanup.release_stopped_sandbox_leases = release
    expected = asyncio.CancelledError if fault == "cancel" else RuntimeError if fault else SandboxRuntimeCleanupError
    with pytest.raises(expected) as caught:
        await cleanup.cleanup_expired_sandbox_runtime_leases(provider_factory=None)
    assert not active
    if fault is None:
        assert caught.value is business_error
    assert [call[0] for call in calls] == ["begin", "stop", "release", "event", "audit", "event", "audit", "rollback" if fault else "commit"]
    events = [call[1] for call in calls if call[0] == "event"]
    assert [(event["tenant_id"], event["run_id"], event["trace_id"]) for event in events] == [
        ("tenant-a", "run-a", "trace-first"), ("tenant-b", "run-b", "trace-b"),
    ]
    assert [event["payload"]["lease_ids"] for event in events] == [["failed-a1", "failed-a2"], ["failed-b"]]
    assert all(event["payload"]["requested_by_role"] == "maintenance" for event in events)

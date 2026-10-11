"""Sandbox-owned cancellation settlement and route-free composition contracts."""

import asyncio
from contextlib import asynccontextmanager
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.sandbox.api import (
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

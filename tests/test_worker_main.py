import asyncio
import json
import sys

import pytest

import app.worker_main as worker_main
from app import repositories
from app.queue import QueueMessage
from app.worker import WorkerOutcome
from app.worker_main import run_once


_ORIGINAL_MEMORY_CLEANUP_FOR_WORKER = worker_main.cleanup_expired_memory_records_for_worker
_ORIGINAL_PERMISSION_TERMINALIZATION_MAINTENANCE = worker_main.progress_pending_tool_permission_terminalizations_for_worker
_ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE = worker_main.reconcile_stale_runs_for_worker


def test_write_worker_runtime_heartbeat_records_process_commit(monkeypatch, tmp_path):
    commit = "8" * 40
    heartbeat = tmp_path / "worker-runtime-heartbeat.json"
    monkeypatch.setenv("AI_PLATFORM_RUNTIME_COMMIT", commit)
    monkeypatch.setattr("app.worker_main.worker_runtime_heartbeat_path", lambda: heartbeat)

    worker_main.write_worker_runtime_heartbeat("worker-a")

    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ai-platform.worker-runtime-heartbeat.v1"
    assert payload["worker_id"] == "worker-a"
    assert payload["runtime_commit"] == commit
    assert payload["pid"] > 0
    assert payload["observed_at"]


def test_worker_runtime_heartbeat_uses_runtime_owned_tmpdir(monkeypatch, tmp_path):
    runtime_tmp = tmp_path / "runtime-tmp"
    monkeypatch.setenv("TMPDIR", str(runtime_tmp))

    assert worker_main.worker_runtime_heartbeat_path() == runtime_tmp / "ai-platform-worker-runtime-heartbeat.json"


@pytest.mark.asyncio
async def test_worker_runtime_heartbeat_refreshes_until_cancelled(monkeypatch):
    calls: list[str] = []

    def fake_write(worker_id: str):
        calls.append(worker_id)

    async def fake_sleep(_seconds: float):
        if len(calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr("app.worker_main.write_worker_runtime_heartbeat", fake_write)
    monkeypatch.setattr("app.worker_main.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await worker_main._worker_runtime_heartbeat_until_done("worker-process")

    assert calls == ["worker-process", "worker-process"]


@pytest.fixture(autouse=True)
def default_sandbox_cleanup(monkeypatch):
    async def cleanup_expired_sandbox_leases():
        return []

    async def cleanup_expired_memory_records_for_worker(settings=None):
        return []

    async def progress_pending_tool_permission_terminalizations_for_worker(settings=None):
        return []

    async def reconcile_stale_runs_for_worker(settings=None):
        return []

    async def renew_run_reconciliation_fence(_fence, *, ttl_seconds):
        return ttl_seconds > 0

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_sandbox_leases",
        cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        cleanup_expired_memory_records_for_worker,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        progress_pending_tool_permission_terminalizations_for_worker,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        reconcile_stale_runs_for_worker,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.queue.renew_run_reconciliation_fence",
        renew_run_reconciliation_fence,
        raising=False,
    )


@pytest.mark.asyncio
async def test_run_worker_maintenance_uses_configured_queue_visibility_timeout(monkeypatch):
    calls = []

    class Settings:
        queue_lease_visibility_timeout_seconds = 12

    async def dispatch_multi_agent_ready_steps_for_worker(settings):
        calls.append(("dispatch", settings.queue_lease_visibility_timeout_seconds))

    async def progress_pending_tool_permission_terminalizations_for_worker(settings):
        calls.append(("permission_terminalization", settings.queue_lease_visibility_timeout_seconds))
        return [{"tenant_id": "tenant-a", "run_id": "run-a", "completed": False}]

    async def reconcile_stale_runs_for_worker(settings):
        calls.append(("stale_run_reconciliation", settings.queue_lease_visibility_timeout_seconds))
        return []

    async def reclaim_expired_leases(**kwargs):
        calls.append(("reclaim", kwargs))
        return {"reclaimed": 0, "dead_lettered": 0}

    monkeypatch.setattr(
        "app.worker_main.dispatch_multi_agent_ready_steps_for_worker",
        dispatch_multi_agent_ready_steps_for_worker,
    )
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        progress_pending_tool_permission_terminalizations_for_worker,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        reconcile_stale_runs_for_worker,
        raising=False,
    )
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)

    await worker_main.run_worker_maintenance(Settings())

    assert calls == [
        ("permission_terminalization", 12),
        ("dispatch", 12),
        ("reclaim", {"visibility_timeout_seconds": 12}),
        ("stale_run_reconciliation", 12),
    ]


@pytest.mark.asyncio
async def test_worker_maintenance_keeps_multi_agent_dispatch_deferred_before_candidate_scan(monkeypatch):
    from app import multi_agent_dispatcher

    class Settings:
        queue_lease_visibility_timeout_seconds = 12
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        default_tenant_id = "default"

    calls: list[tuple[str, object]] = []

    def forbidden_transaction():
        raise AssertionError("maintenance dispatch must not claim or write deferred work")

    async def forbidden_candidates(*_args, **_kwargs):
        raise AssertionError("maintenance dispatch must not scan deferred candidates")

    async def reclaim_expired_leases(**kwargs):
        calls.append(("reclaim", kwargs))
        return {"reclaimed": 0, "dead_lettered": 0}

    monkeypatch.setattr(multi_agent_dispatcher, "transaction", forbidden_transaction)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        forbidden_candidates,
        raising=False,
    )
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)

    await worker_main.run_worker_maintenance(Settings())

    assert calls == [("reclaim", {"visibility_timeout_seconds": 12})]


@pytest.mark.asyncio
async def test_permission_terminalization_maintenance_drains_bounded_durable_run_work_items(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        tool_permission_terminalization_maintenance_limit = 2

    async def list_runs(conn, *, limit):
        calls.append(("list", limit))
        return [
            {"tenant_id": "tenant-a", "run_id": "run-a"},
            {"tenant_id": "tenant-b", "run_id": "run-b"},
        ]

    async def drain(**kwargs):
        calls.append(("drain", kwargs["tenant_id"], kwargs["run_id"], kwargs["max_batches"]))
        return repositories.ToolPermissionTerminalizationProgress(
            completed=kwargs["run_id"] == "run-a",
            status="failed",
        )

    async def recovery_candidates(_conn, *, limit):
        calls.append(("recovery", limit))
        return []

    async def parent_recovery_candidates(_conn, *, limit):
        calls.append(("parent_recovery", limit))
        return []

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        _ORIGINAL_PERMISSION_TERMINALIZATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_runs_requiring_tool_permission_terminalization", list_runs)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_terminal_children_requiring_reconciliation", recovery_candidates)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_parent_runs_requiring_finalization", parent_recovery_candidates)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", drain)

    rows = await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())

    assert calls == [
        ("list", 2),
        ("drain", "tenant-a", "run-a", 4),
        ("drain", "tenant-b", "run-b", 4),
        ("recovery", 2),
        ("parent_recovery", 2),
    ]
    assert rows == [
        {"tenant_id": "tenant-a", "run_id": "run-a", "completed": True, "status": "failed", "did_transition": False, "needs_reconcile": False},
        {"tenant_id": "tenant-b", "run_id": "run-b", "completed": False, "status": "failed", "did_transition": False, "needs_reconcile": False},
    ]


@pytest.mark.asyncio
async def test_permission_terminalization_maintenance_reconciles_only_one_final_transition(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        tool_permission_terminalization_maintenance_limit = 3

    async def list_runs(_conn, *, limit):
        assert limit == 3
        return [
            {"tenant_id": "tenant-a", "run_id": "partial"},
            {"tenant_id": "tenant-a", "run_id": "final"},
            {"tenant_id": "tenant-a", "run_id": "retry"},
        ]

    async def drain(**kwargs):
        status = kwargs["run_id"]
        return {
            "partial": repositories.ToolPermissionTerminalizationProgress(False, "failed"),
            "final": repositories.ToolPermissionTerminalizationProgress(True, "failed", True, True),
            "retry": repositories.ToolPermissionTerminalizationProgress(True, "failed"),
        }[status]

    async def reconcile(**kwargs):
        calls.append((kwargs["tenant_id"], kwargs["run_id"], kwargs["progress"].did_transition))

    async def recovery_candidates(_conn, *, limit):
        assert limit == 3
        return []

    async def parent_recovery_candidates(_conn, *, limit):
        assert limit == 3
        return []

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        _ORIGINAL_PERMISSION_TERMINALIZATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_runs_requiring_tool_permission_terminalization", list_runs)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_terminal_children_requiring_reconciliation", recovery_candidates)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_parent_runs_requiring_finalization", parent_recovery_candidates)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", drain)
    monkeypatch.setattr("app.worker_main.reconcile_terminalized_permission_run", reconcile)

    await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())
    assert calls == [("tenant-a", "final", True)]


@pytest.mark.asyncio
async def test_permission_terminalization_maintenance_recovers_committed_handed_off_child(monkeypatch):
    """A crash after child terminal commit is recovered from durable handed-off state."""

    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        tool_permission_terminalization_maintenance_limit = 2

    async def list_runs(_conn, *, limit):
        assert limit == 2
        return []

    recovery_rounds = [[{"tenant_id": "tenant-a", "run_id": "child-a", "status": "cancelled"}], []]

    async def recovery_candidates(_conn, *, limit):
        assert limit == 2
        return recovery_rounds.pop(0)

    async def parent_recovery_candidates(_conn, *, limit):
        assert limit == 2
        return []

    async def reconcile(**kwargs):
        calls.append((kwargs["tenant_id"], kwargs["run_id"], kwargs.get("progress")))

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        _ORIGINAL_PERMISSION_TERMINALIZATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_runs_requiring_tool_permission_terminalization", list_runs)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_terminal_children_requiring_reconciliation", recovery_candidates)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_parent_runs_requiring_finalization", parent_recovery_candidates)
    monkeypatch.setattr("app.worker_main.reconcile_terminalized_permission_run", reconcile)

    rows = await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())
    retry_rows = await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())

    assert rows == []
    assert retry_rows == []
    assert calls == [("tenant-a", "child-a", None)]


@pytest.mark.asyncio
async def test_permission_terminalization_maintenance_recovers_parent_rollup_after_two_last_children(monkeypatch):
    """Maintenance retries a parent once both concurrent last-child reconciliations left no hand-off."""

    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        tool_permission_terminalization_maintenance_limit = 2

    async def list_runs(_conn, *, limit):
        assert limit == 2
        return []

    async def child_recovery(_conn, *, limit):
        assert limit == 2
        return []

    parent_rounds = [[{"tenant_id": "tenant-a", "run_id": "parent-a"}], []]

    async def parent_recovery(_conn, *, limit):
        assert limit == 2
        return parent_rounds.pop(0)

    async def finalize_parent(_conn, *, tenant_id, parent_run_id):
        calls.append((tenant_id, parent_run_id))
        return {"parent_run_id": parent_run_id, "status": "failed"}

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.progress_pending_tool_permission_terminalizations_for_worker",
        _ORIGINAL_PERMISSION_TERMINALIZATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_runs_requiring_tool_permission_terminalization", list_runs)
    monkeypatch.setattr("app.worker_main.repositories.list_multi_agent_terminal_children_requiring_reconciliation", child_recovery)
    monkeypatch.setattr(
        "app.worker_main.repositories.list_multi_agent_parent_runs_requiring_finalization",
        parent_recovery,
        raising=False,
    )
    monkeypatch.setattr("app.worker_main.repositories.finalize_multi_agent_parent_run_if_ready", finalize_parent)

    await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())
    await worker_main.progress_pending_tool_permission_terminalizations_for_worker(Settings())

    assert calls == [("tenant-a", "parent-a")]


@pytest.mark.asyncio
async def test_stale_run_maintenance_terminalizes_cancel_requested_orphan_once(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            calls.append(("tx_enter",))
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("tx_exit", exc_type))
            return False

    class Settings:
        stale_run_reconciliation_limit = 4
        stale_run_reconciliation_seconds = 900
        cancel_requested_orphan_reconciliation_seconds = 5
        queue_metadata_fallback_scan_limit = 500
        stale_run_reconciliation_fence_ttl_seconds = 300

    candidate = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "run_id": "run-cancel",
        "status": "running",
        "cancel_requested_at": "2026-07-21T11:07:58Z",
        "stale_before": "2026-07-21T11:00:00Z",
        "cancel_requested_before": "2026-07-21T11:07:58Z",
    }

    async def list_candidates(_conn, *, stale_after_seconds, cancel_requested_after_seconds, limit):
        calls.append(("list", stale_after_seconds, cancel_requested_after_seconds, limit))
        return [candidate]

    fence = worker_main.queue.RunReconciliationFence("tenant-a", "run-cancel", "token", "fence")

    async def acquire_fence(**kwargs):
        calls.append(("fence", kwargs["tenant_id"], kwargs["run_id"], kwargs["scan_limit"], kwargs["ttl_seconds"]))
        return fence

    async def release_fence(released):
        calls.append(("release", released.owner_token))
        return True

    async def stage(_conn, **kwargs):
        calls.append(("stage", kwargs["run_id"], kwargs["expected_status"], kwargs["terminal_status"]))
        return {"tenant_id": "tenant-a", "run_id": "run-cancel", "terminal_status": "cancelled"}

    async def drain(**kwargs):
        calls.append(("drain", kwargs["tenant_id"], kwargs["run_id"]))
        return repositories.ToolPermissionTerminalizationProgress(True, "cancelled", True, True)

    async def reconcile(**kwargs):
        calls.append(("reconcile", kwargs["tenant_id"], kwargs["run_id"]))

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire_fence)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", release_fence)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", drain)
    monkeypatch.setattr("app.worker_main.reconcile_terminalized_permission_run", reconcile)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [
        {"tenant_id": "tenant-a", "run_id": "run-cancel", "status": "cancelled", "did_transition": True}
    ]
    assert ("fence", "tenant-a", "run-cancel", 500, 300) in calls
    assert ("stage", "run-cancel", "running", "cancelled") in calls
    assert ("drain", "tenant-a", "run-cancel") in calls
    assert ("reconcile", "tenant-a", "run-cancel") in calls
    assert ("release", "token") in calls


@pytest.mark.asyncio
async def test_stale_run_maintenance_fails_interrupted_run_but_never_cleans_owner_race(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        stale_run_reconciliation_limit = 4
        stale_run_reconciliation_seconds = 900
        queue_metadata_fallback_scan_limit = 500
        stale_run_reconciliation_fence_ttl_seconds = 300

    candidates = [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-failed",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        },
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-owner-race",
            "status": "queued",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        },
    ]

    async def list_candidates(_conn, **_kwargs):
        return candidates

    async def acquire_fence(**kwargs):
        if kwargs["run_id"] == "run-owner-race":
            return None
        return worker_main.queue.RunReconciliationFence("tenant-a", kwargs["run_id"], "token", "fence")

    async def release_fence(_fence):
        calls.append(("release",))
        return True

    async def stage(_conn, **kwargs):
        calls.append(("stage", kwargs["run_id"], kwargs["terminal_status"], kwargs["error_code"]))
        return {"tenant_id": kwargs["tenant_id"], "run_id": kwargs["run_id"], "terminal_status": "failed"}

    async def drain(**kwargs):
        calls.append(("drain", kwargs["run_id"]))
        return repositories.ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def reconcile(**kwargs):
        calls.append(("reconcile", kwargs["run_id"]))

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire_fence)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", release_fence)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", drain)
    monkeypatch.setattr("app.worker_main.reconcile_terminalized_permission_run", reconcile)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [
        {"tenant_id": "tenant-a", "run_id": "run-failed", "status": "failed", "did_transition": True},
        {"tenant_id": "tenant-a", "run_id": "run-owner-race", "status": "owned", "did_transition": False},
    ]
    assert calls == [
        ("stage", "run-failed", "failed", "stale_run_interrupted"),
        ("drain", "run-failed"),
        ("reconcile", "run-failed"),
        ("release",),
    ]


@pytest.mark.asyncio
async def test_stale_run_maintenance_cas_loss_is_a_noop(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        stale_run_reconciliation_limit = 1
        stale_run_reconciliation_seconds = 900
        queue_metadata_fallback_scan_limit = 50
        stale_run_reconciliation_fence_ttl_seconds = 300

    async def list_candidates(_conn, **_kwargs):
        return [{
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-cas-lost",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        }]

    fence = worker_main.queue.RunReconciliationFence("tenant-a", "run-cas-lost", "token", "fence")

    async def acquire_fence(**_kwargs):
        return fence

    async def release_fence(released):
        calls.append(("release", released.owner_token))
        return True

    async def stage(_conn, **kwargs):
        calls.append(("stage", kwargs["run_id"]))
        return None

    async def forbidden_drain(**_kwargs):
        raise AssertionError("a lost CAS must not drain or clean anything")

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire_fence)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", release_fence)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", forbidden_drain)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [
        {"tenant_id": "tenant-a", "run_id": "run-cas-lost", "status": "cas_lost", "did_transition": False}
    ]
    assert calls == [("stage", "run-cas-lost"), ("release", "token")]


@pytest.mark.asyncio
async def test_stale_run_fence_is_held_until_db_commit_then_released(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            calls.append("tx_enter")
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("tx_commit" if exc_type is None else "tx_rollback")
            return False

    class Settings:
        stale_run_reconciliation_limit = 1
        stale_run_reconciliation_seconds = 900
        stale_run_reconciliation_fence_ttl_seconds = 300
        queue_metadata_fallback_scan_limit = 50

    async def list_candidates(_conn, **_kwargs):
        return [{
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-order",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        }]

    fence = worker_main.queue.RunReconciliationFence("tenant-a", "run-order", "token", "fence")

    async def acquire(**_kwargs):
        calls.append("fence_acquired")
        return fence

    async def stage(_conn, **_kwargs):
        calls.append("db_stage")
        return None

    async def release(_fence):
        calls.append("fence_released")
        return True

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", release)

    await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert calls == [
        "tx_enter",
        "tx_commit",
        "fence_acquired",
        "tx_enter",
        "db_stage",
        "tx_commit",
        "fence_released",
    ]


@pytest.mark.asyncio
async def test_stale_run_db_error_retains_bounded_fence(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        stale_run_reconciliation_limit = 1
        stale_run_reconciliation_seconds = 900
        stale_run_reconciliation_fence_ttl_seconds = 300
        queue_metadata_fallback_scan_limit = 50

    async def list_candidates(_conn, **_kwargs):
        return [{
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-db-error",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        }]

    async def acquire(**_kwargs):
        return worker_main.queue.RunReconciliationFence("tenant-a", "run-db-error", "token", "fence")

    async def stage(_conn, **_kwargs):
        raise RuntimeError("db uncertain")

    async def forbidden_release(_fence):
        calls.append("release")

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", forbidden_release)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [
        {"tenant_id": "tenant-a", "run_id": "run-db-error", "status": "db_unknown", "did_transition": False}
    ]
    assert calls == []


@pytest.mark.asyncio
async def test_stale_run_fence_renewal_loss_aborts_before_staging_a_terminal_intent(monkeypatch):
    calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("rollback" if exc_type is not None else "commit")
            return False

    class Settings:
        stale_run_reconciliation_limit = 1
        stale_run_reconciliation_seconds = 900
        stale_run_reconciliation_fence_ttl_seconds = 30
        queue_metadata_fallback_scan_limit = 50

    async def list_candidates(_conn, **_kwargs):
        return [{
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-token-lost",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        }]

    async def acquire(**_kwargs):
        return worker_main.queue.RunReconciliationFence("tenant-a", "run-token-lost", "token", "fence")

    async def renewal_lost(_fence, *, ttl_seconds):
        calls.append(("renew", ttl_seconds))
        return False

    async def forbidden_stage(_conn, **_kwargs):
        raise AssertionError("terminal intent must not stage after fence-token loss")

    async def forbidden_release(_fence):
        raise AssertionError("lost fence remains bounded until Redis TTL expiry")

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire)
    monkeypatch.setattr("app.worker_main.queue.renew_run_reconciliation_fence", renewal_lost)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", forbidden_stage)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", forbidden_release)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [
        {"tenant_id": "tenant-a", "run_id": "run-token-lost", "status": "fence_renewal_failed", "did_transition": False}
    ]
    assert calls == ["commit", ("renew", 30)]


@pytest.mark.asyncio
async def test_stale_run_fence_renews_through_stage_and_drain_transactions(monkeypatch):
    renewal_calls = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Settings:
        stale_run_reconciliation_limit = 1
        stale_run_reconciliation_seconds = 900
        stale_run_reconciliation_fence_ttl_seconds = 30
        queue_metadata_fallback_scan_limit = 50

    async def list_candidates(_conn, **_kwargs):
        return [{
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "run_id": "run-renewed",
            "status": "running",
            "cancel_requested_at": None,
            "stale_before": "2026-07-21T11:00:00Z",
        }]

    async def acquire(**_kwargs):
        return worker_main.queue.RunReconciliationFence("tenant-a", "run-renewed", "token", "fence")

    async def renew(_fence, *, ttl_seconds):
        renewal_calls.append(ttl_seconds)
        return True

    async def stage(_conn, **_kwargs):
        return {"run_id": "run-renewed"}

    async def drain(**_kwargs):
        return repositories.ToolPermissionTerminalizationProgress(True, "failed", True, False)

    async def release(_fence):
        return True

    monkeypatch.setattr("app.worker_main.transaction", Transaction)
    monkeypatch.setattr(
        "app.worker_main.reconcile_stale_runs_for_worker",
        _ORIGINAL_STALE_RUN_RECONCILIATION_MAINTENANCE,
    )
    monkeypatch.setattr("app.worker_main.repositories.list_stale_run_reconciliation_candidates", list_candidates)
    monkeypatch.setattr("app.worker_main.queue.acquire_run_reconciliation_fence", acquire)
    monkeypatch.setattr("app.worker_main.queue.renew_run_reconciliation_fence", renew)
    monkeypatch.setattr("app.worker_main.repositories.stage_stale_run_reconciliation", stage)
    monkeypatch.setattr("app.worker_main.drain_run_tool_permission_terminalization", drain)
    monkeypatch.setattr("app.worker_main.queue.release_run_reconciliation_fence", release)

    results = await worker_main.reconcile_stale_runs_for_worker(Settings())

    assert results == [{"tenant_id": "tenant-a", "run_id": "run-renewed", "status": "failed", "did_transition": True}]
    assert len(renewal_calls) >= 5
    assert set(renewal_calls) == {30}


@pytest.mark.asyncio
async def test_run_once_acknowledges_completed_message(monkeypatch):
    calls = []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        return WorkerOutcome(status="succeeded", run_id="run-a")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "succeeded"
    assert calls == [("reclaim",), ("lease", "worker-a"), ("process", "run-a", "worker-a"), ("ack", "raw-run", "msg-a")]


@pytest.mark.asyncio
async def test_run_once_keeps_queue_maintenance_running_during_long_processing(monkeypatch):
    calls = []
    maintenance_seen_during_processing = asyncio.Event()

    class Settings:
        max_active_worker_runs = 3
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 0
        queue_lease_scan_limit = 50
        worker_maintenance_interval_seconds = 0.01

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))
        if ("process_started",) in calls and calls.count(("reclaim",)) >= 2:
            maintenance_seen_during_processing.set()

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id, max_processing_runs))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process_started",))
        await asyncio.wait_for(maintenance_seen_during_processing.wait(), timeout=0.5)
        calls.append(("process_finished",))
        return WorkerOutcome(status="succeeded", run_id=payload["run_id"])

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "succeeded"
    assert calls.count(("reclaim",)) >= 2
    assert calls.index(("reclaim",)) < calls.index(("process_started",))
    assert calls.index(("process_started",)) < calls.index(("process_finished",))
    assert calls.index(("process_finished",)) < calls.index(("ack", "raw-run", "msg-a"))


@pytest.mark.asyncio
async def test_run_once_dead_letters_unhandled_outcome(monkeypatch):
    calls = []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        return WorkerOutcome(status="dead_letter", run_id=None, error_code="bad", error_message="bad payload")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "dead_letter"
    assert calls == [("reclaim",), ("fail", "raw-run", "bad", "bad payload", "msg-a", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_acknowledges_cancelled_message(monkeypatch):
    calls = []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        return WorkerOutcome(status="cancelled", run_id="run-a")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "cancelled"
    assert calls == [
        ("reclaim",),
        ("lease", "worker-a"),
        ("process", "run-a", "worker-a"),
        ("ack", "raw-run", "msg-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_does_not_ack_cancelled_message_before_execution_owner_finishes(monkeypatch):
    calls = []
    processing = asyncio.Event()
    quiescent = asyncio.Event()

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        processing.set()
        await quiescent.wait()
        return WorkerOutcome(status="cancelled", run_id=payload["run_id"])

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(*args, **kwargs):
        calls.append(("fail",))

    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)

    task = asyncio.create_task(
        run_once(
            timeout_seconds=1,
            worker_id="worker-a",
            heartbeat_interval_seconds=60,
            run_initial_maintenance=False,
            run_background_maintenance=False,
        )
    )
    await asyncio.wait_for(processing.wait(), timeout=0.5)

    assert calls == []

    quiescent.set()
    outcome = await asyncio.wait_for(task, timeout=0.5)

    assert outcome.status == "cancelled"
    assert calls == [("ack", "raw-run", "msg-a")]


@pytest.mark.asyncio
async def test_run_once_dead_letters_process_exception(monkeypatch):
    calls = []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        raise RuntimeError("boom")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "dead_letter"
    assert outcome.run_id == "run-a"
    assert outcome.error_code == "worker_process_exception"
    assert outcome.error_message == "boom"
    assert calls == [
        ("reclaim",),
        ("lease", "worker-a"),
        ("process", "run-a", "worker-a"),
        ("fail", "raw-run", "worker_process_exception", "boom", "msg-a", "worker-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_returns_idle_without_message(monkeypatch):
    async def reclaim_expired_leases(**_kwargs):
        return {"reclaimed": 0, "dead_lettered": 0}

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        return None

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert outcome.run_id is None


@pytest.mark.asyncio
async def test_run_once_passes_global_worker_capacity_to_queue(monkeypatch):
    calls = []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", timeout_seconds, worker_id, max_processing_runs))
        return None

    class Settings:
        max_active_worker_runs = 3

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("reclaim",), ("lease", 1, "worker-a", 3)]


@pytest.mark.asyncio
async def test_run_once_passes_queue_quota_settings_to_queue(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        queue_tenant_processing_limit = 2
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("reclaim",))

    async def lease_run(
        timeout_seconds=5,
        worker_id="worker",
        max_processing_runs=None,
        tenant_processing_limit=None,
        user_processing_limit=None,
        lease_scan_limit=None,
    ):
        calls.append(
            (
                "lease",
                timeout_seconds,
                worker_id,
                max_processing_runs,
                tenant_processing_limit,
                user_processing_limit,
                lease_scan_limit,
            )
        )
        return None

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("reclaim",), ("lease", 1, "worker-a", 3, 2, 1, 25)]


@pytest.mark.asyncio
async def test_run_forever_closes_database_pool_when_cancelled(monkeypatch):
    calls = []

    async def fake_run_once(registry=None, timeout_seconds=5, worker_id=None):
        calls.append(("run_once", timeout_seconds, worker_id is not None))
        raise asyncio.CancelledError()

    async def fake_close_pool():
        calls.append(("close_pool",))

    monkeypatch.setattr("app.worker_main.run_once", fake_run_once)
    monkeypatch.setattr("app.worker_main.close_pool", fake_close_pool, raising=False)

    with pytest.raises(asyncio.CancelledError):
        await worker_main.run_forever(poll_timeout_seconds=2)

    assert calls == [("run_once", 2, True), ("close_pool",)]


@pytest.mark.asyncio
async def test_run_forever_continues_after_transient_run_once_error(monkeypatch):
    calls = []
    continued = asyncio.Event()

    async def fake_run_once(registry=None, timeout_seconds=5, worker_id=None):
        calls.append(("run_once", timeout_seconds, worker_id is not None))
        if len(calls) == 1:
            raise TimeoutError("Timeout reading from redis:6379")
        continued.set()
        raise asyncio.CancelledError()

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))

    async def fake_close_pool():
        calls.append(("close_pool",))

    monkeypatch.setattr("app.worker_main.run_once", fake_run_once)
    monkeypatch.setattr("app.worker_main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.worker_main.close_pool", fake_close_pool, raising=False)

    with pytest.raises(asyncio.CancelledError):
        await worker_main.run_forever(poll_timeout_seconds=2, idle_sleep_seconds=0.25)

    assert continued.is_set()
    assert calls == [
        ("run_once", 2, True),
        ("sleep", 0.25),
        ("run_once", 2, True),
        ("close_pool",),
    ]


@pytest.mark.asyncio
async def test_run_worker_pool_starts_configured_parallel_workers(monkeypatch):
    started = asyncio.Event()
    calls = []

    class Settings:
        worker_maintenance_interval_seconds = 60.0

    async def fake_run_worker_maintenance(settings):
        calls.append(("maintenance", settings.worker_maintenance_interval_seconds))

    async def fake_run_worker_slot(*, worker_id, poll_timeout_seconds, idle_sleep_seconds):
        calls.append(("slot", bool(worker_id), poll_timeout_seconds, idle_sleep_seconds))
        if len([call for call in calls if call[0] == "slot"]) == 3:
            started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.run_worker_maintenance", fake_run_worker_maintenance)
    monkeypatch.setattr("app.worker_main._run_worker_slot", fake_run_worker_slot)

    task = asyncio.create_task(worker_main.run_worker_pool(worker_count=3, poll_timeout_seconds=2, idle_sleep_seconds=0.25))
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls == [
        ("maintenance", 60.0),
        ("slot", True, 2, 0.25),
        ("slot", True, 2, 0.25),
        ("slot", True, 2, 0.25),
    ]


@pytest.mark.asyncio
async def test_run_worker_slot_continues_after_transient_run_once_error(monkeypatch):
    calls = []
    continued = asyncio.Event()

    async def fake_run_once(
        registry=None,
        timeout_seconds=5,
        worker_id=None,
        run_initial_maintenance=True,
        run_background_maintenance=True,
    ):
        calls.append(
            (
                "run_once",
                timeout_seconds,
                worker_id,
                run_initial_maintenance,
                run_background_maintenance,
            )
        )
        if len(calls) == 1:
            raise TimeoutError("Timeout reading from redis:6379")
        continued.set()
        raise asyncio.CancelledError()

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))

    monkeypatch.setattr("app.worker_main.run_once", fake_run_once)
    monkeypatch.setattr("app.worker_main.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await worker_main._run_worker_slot(
            worker_id="worker-a",
            poll_timeout_seconds=2,
            idle_sleep_seconds=0.25,
        )

    assert continued.is_set()
    assert calls == [
        ("run_once", 2, "worker-a", False, False),
        ("sleep", 0.25),
        ("run_once", 2, "worker-a", False, False),
    ]


@pytest.mark.asyncio
async def test_run_worker_pool_clamps_invalid_worker_count_to_one(monkeypatch):
    calls = []

    async def fake_run_forever(poll_timeout_seconds=5, idle_sleep_seconds=0.5):
        calls.append(("run_forever", poll_timeout_seconds, idle_sleep_seconds))

    monkeypatch.setattr("app.worker_main.run_forever", fake_run_forever)

    await worker_main.run_worker_pool(worker_count=0, poll_timeout_seconds=4, idle_sleep_seconds=0.75)

    assert calls == [("run_forever", 4, 0.75)]


def test_worker_main_once_closes_database_pool(monkeypatch, capsys):
    calls = []

    async def fake_run_once(timeout_seconds=5):
        calls.append(("run_once", timeout_seconds))
        return WorkerOutcome(status="idle", run_id=None)

    async def fake_close_pool():
        calls.append(("close_pool",))

    monkeypatch.setattr(sys, "argv", ["worker", "--once", "--timeout", "7"])
    monkeypatch.setattr("app.worker_main.run_once", fake_run_once)
    monkeypatch.setattr("app.worker_main.close_pool", fake_close_pool, raising=False)

    worker_main.main()

    assert calls == [("run_once", 7), ("close_pool",)]
    assert "WorkerOutcome(status='idle'" in capsys.readouterr().out


def test_worker_main_uses_configured_worker_concurrency(monkeypatch):
    calls = []

    class Settings:
        worker_concurrency = 4

    async def fake_run_worker_pool(*, worker_count, poll_timeout_seconds=5):
        calls.append(("run_worker_pool", worker_count, poll_timeout_seconds))

    monkeypatch.setattr(sys, "argv", ["worker", "--timeout", "9"])
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.run_worker_pool", fake_run_worker_pool)

    worker_main.main()

    assert calls == [("run_worker_pool", 4, 9)]


@pytest.mark.asyncio
async def test_run_once_cleans_expired_sandbox_leases_before_leasing_queue(monkeypatch):
    calls = []

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))
        return []

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("sandbox_cleanup",), ("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_cleans_expired_memory_records_across_tenant_workspaces(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

        def __getattr__(self, name):
            if name in {"default_tenant_id", "default_workspace_id"}:
                raise AssertionError("worker memory cleanup must not depend on default scope")
            raise AttributeError(name)

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))

    async def cleanup_expired_memory_records(conn, **_kwargs):
        raise AssertionError("worker must use all-scope memory cleanup")

    async def cleanup_expired_memory_records_across_scopes(conn, *, limit):
        calls.append(("memory_cleanup_all_scopes", conn, limit))
        return [
            {
                "id": "mem-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "content": "secret content must not be audited",
                "metadata_json": {"api_key": "hidden"},
            },
            {
                "id": "mem-b",
                "tenant_id": "tenant-b",
                "workspace_id": "workspace-b",
                "user_id": "user-b",
                "content": "other secret content must not be audited",
                "metadata_json": {"private_payload": "hidden"},
            },
        ]

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-id"

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id, max_processing_runs))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr(
        "app.worker_main.repositories.cleanup_expired_memory_records_across_scopes",
        cleanup_expired_memory_records_across_scopes,
        raising=False,
    )
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls[0] == ("sandbox_cleanup",)
    assert calls[1] == ("memory_cleanup_all_scopes", "conn", 25)
    audit_calls = [call[1] for call in calls if call[0] == "audit"]
    assert [audit["tenant_id"] for audit in audit_calls] == ["tenant-a", "tenant-b"]
    assert [audit["target_id"] for audit in audit_calls] == ["workspace-a", "workspace-b"]
    assert [audit["action"] for audit in audit_calls] == ["worker.memory.retention.cleanup"] * 2
    assert [audit["user_id"] for audit in audit_calls] == [None, None]
    assert [audit["target_type"] for audit in audit_calls] == ["memory_retention", "memory_retention"]
    assert audit_calls[0]["payload_json"] == {
        "workspace_id": "workspace-a",
        "deleted_count": 1,
        "memory_record_ids": ["mem-a"],
        "target_user_ids": ["user-a"],
        "reason": "retention_expired",
        "source": "worker",
    }
    assert audit_calls[1]["payload_json"] == {
        "workspace_id": "workspace-b",
        "deleted_count": 1,
        "memory_record_ids": ["mem-b"],
        "target_user_ids": ["user-b"],
        "reason": "retention_expired",
        "source": "worker",
    }
    assert calls[-2:] == [("queue_reclaim",), ("lease", "worker-a", 3)]
    assert "secret content" not in str(audit_calls)
    assert "api_key" not in str(audit_calls)
    assert "private_payload" not in str(audit_calls)


@pytest.mark.asyncio
async def test_run_once_cleans_expired_memory_records_when_due(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))

    async def cleanup_expired_memory_records_across_scopes(conn, *, limit):
        calls.append(("memory_cleanup", conn, limit))
        return [
            {
                "id": "mem-expired",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "content": "do not audit this secret content",
                "metadata_json": {"api_key": "hidden"},
            }
        ]

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-a"

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id, max_processing_runs))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr(
        "app.worker_main.repositories.cleanup_expired_memory_records_across_scopes",
        cleanup_expired_memory_records_across_scopes,
    )
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls[0] == ("sandbox_cleanup",)
    assert calls[1] == ("memory_cleanup", "conn", 25)
    assert calls[2][0] == "audit"
    assert calls[2][1]["action"] == "worker.memory.retention.cleanup"
    assert calls[2][1]["user_id"] is None
    assert calls[2][1]["target_type"] == "memory_retention"
    assert calls[2][1]["target_id"] == "workspace-a"
    assert calls[2][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "deleted_count": 1,
        "memory_record_ids": ["mem-expired"],
        "target_user_ids": ["user-a"],
        "reason": "retention_expired",
        "source": "worker",
    }
    assert calls[3:] == [("queue_reclaim",), ("lease", "worker-a", 3)]
    assert "do not audit this secret content" not in str(calls[2])
    assert "hidden" not in str(calls[2])


@pytest.mark.asyncio
async def test_run_once_does_not_audit_memory_cleanup_when_no_records_deleted(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records_across_scopes(conn, *, limit):
        calls.append(("memory_cleanup", limit))
        return []

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr(
        "app.worker_main.repositories.cleanup_expired_memory_records_across_scopes",
        cleanup_expired_memory_records_across_scopes,
    )
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("memory_cleanup", 25), ("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_skips_memory_cleanup_until_interval_elapsed(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 3600.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records_across_scopes(conn, *, limit):
        calls.append(("memory_cleanup",))
        return []

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr(
        "app.worker_main.repositories.cleanup_expired_memory_records_across_scopes",
        cleanup_expired_memory_records_across_scopes,
    )
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    first = await run_once(timeout_seconds=1, worker_id="worker-a")
    second = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert first.status == "idle"
    assert second.status == "idle"
    assert calls == [
        ("memory_cleanup",),
        ("queue_reclaim",),
        ("lease", "worker-a"),
        ("queue_reclaim",),
        ("lease", "worker-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_skips_memory_cleanup_when_disabled(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = False
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            raise AssertionError("disabled memory cleanup must not open a transaction")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records(conn, **kwargs):
        raise AssertionError("disabled memory cleanup must not scan memory records")

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_does_not_reclaim_queue_when_sandbox_cleanup_fails(monkeypatch):
    calls = []

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))
        raise RuntimeError("sandbox cleanup failed")

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    with pytest.raises(RuntimeError, match="sandbox cleanup failed"):
        await run_once(timeout_seconds=1, worker_id="worker-a")

    assert calls == [("sandbox_cleanup",)]


@pytest.mark.asyncio
async def test_run_once_dispatches_multi_agent_ready_steps_before_queue_lease(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "default"
        multi_agent_dispatch_worker_enabled = True

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))

    async def cleanup_expired_memory_records_for_worker(settings=None):
        calls.append(("memory_cleanup", settings))
        return []

    async def dispatch_multi_agent_ready_steps_for_worker(settings=None):
        calls.append(("multi_agent_dispatch", settings))
        return [{"run_id": "run-parent", "status": "queued"}]

    async def reclaim_expired_leases(**_kwargs):
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None, **_quota_kwargs):
        calls.append(("lease", worker_id, max_processing_runs))
        return None

    settings = Settings()
    monkeypatch.setattr("app.worker_main.get_settings", lambda: settings)
    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        cleanup_expired_memory_records_for_worker,
    )
    monkeypatch.setattr(
        "app.worker_main.dispatch_multi_agent_ready_steps_for_worker",
        dispatch_multi_agent_ready_steps_for_worker,
    )
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [
        ("sandbox_cleanup",),
        ("memory_cleanup", settings),
        ("multi_agent_dispatch", settings),
        ("queue_reclaim",),
        ("lease", "worker-a", 3),
    ]

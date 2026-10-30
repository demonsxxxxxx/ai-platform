from tests.support.terminal_timing import terminal_stage_logs as terminal_stage_logs
import app.runs.infrastructure.postgres as _owner_runs_infrastructure_postgres
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bootstrap import execution_supervision as _worker_supervision
from app.platform.tracing import safe_exception_log_fields

from app.context.api import ProviderSessionConflictError
from app.executor_reconciler import (
    PermanentExecutorReconciliationError,
    _context_payload,
    _reconciliation_request,
    _finish_terminal_reconciliation_failure as _finish_terminal_reconciliation_failure_impl,
    _persist_probe_terminal as _persist_probe_terminal_impl,
    _terminalize_reconciliation_failure as _terminalize_reconciliation_failure_impl,
    probe_suspect_executor_tasks_once as probe_suspect_executor_tasks_once_impl,
    reconcile_pending_executor_terminals_once as reconcile_pending_executor_terminals_once_impl,
    run_executor_terminal_reconciler as run_executor_terminal_reconciler_impl,
)
from app.executors.base import ExecutorResult, RunPayload
from app.execution.application.executor_reconciliation import sandbox_reconciliation_payload
from app.sandbox.infrastructure import leases_postgres as sandbox_lease_repository
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.api import ReconciliationAuthorityError
from app.runtime.sandbox.executor_signals import (
    ExecutorReconciliationSignalCursor,
    ExecutorSignalUnavailable,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)
from app.worker import WorkerOutcome


def _log_payload(record):
    metadata = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
    return {
        "message": record.getMessage(),
        "extra": {key: value for key, value in record.__dict__.items() if key not in metadata},
    }


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    async def execute(self, sql, _params):
        return _Cursor({"id": "lease-a"})

    @asynccontextmanager
    async def transaction(self):
        yield


@asynccontextmanager
async def _transaction():
    yield _Connection()


class _LegacySupervisionStore:
    """Model persisted legacy ownership for reconciler unit tests."""

    def __init__(self) -> None:
        self._claims: dict[str, dict[str, object]] = {}
        self._next_claim = 0

    async def read_domain_control(self, _connection, *, domain):
        return {"domain": domain.value, "mode": "legacy", "owner_version": 1}

    async def claim_domain_work(
        self,
        _connection,
        *,
        domain,
        owner_mode,
        worker_id,
        ttl_seconds,
        capacity=1,
    ):
        assert owner_mode == "legacy"
        in_flight = sum(
            claim["domain"] == domain.value for claim in self._claims.values()
        )
        if in_flight >= capacity:
            return None
        self._next_claim += 1
        claim = {
            "domain": domain.value,
            "owner_mode": owner_mode,
            "owner_version": 1,
            "worker_id": worker_id,
            "ttl_seconds": ttl_seconds,
            "claim_token": f"test-claim-{self._next_claim}",
        }
        self._claims[str(claim["claim_token"])] = claim
        return claim

    async def claim_is_current(self, _connection, claim):
        return self._claims.get(str(claim["claim_token"])) == claim

    async def release_domain_work(self, _connection, claim):
        return self._claims.pop(str(claim["claim_token"]), None) is not None


class _EventPersistence:
    async def append_terminal_row(self, _conn, *, tenant_id, run_id):
        return None


_TEST_V4_CAPABILITIES = SimpleNamespace(event_persistence=_EventPersistence())
_TEST_RUN_LIFECYCLE = SimpleNamespace()


async def _unexpected_attempt_operation(*_args, **_kwargs):
    raise AssertionError("test must provide the expected attempt lifecycle operation")


_TEST_ATTEMPT_LIFECYCLE = SimpleNamespace(
    request_cancel=_unexpected_attempt_operation,
    terminalize=_unexpected_attempt_operation,
)


async def _terminalize_reconciliation_failure(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    kwargs.setdefault("attempt_lifecycle", _TEST_ATTEMPT_LIFECYCLE)
    kwargs.setdefault("lifecycle", _TEST_RUN_LIFECYCLE)
    kwargs.setdefault("run_diagnostics", None)
    kwargs.setdefault("reconciliation_error_code", "test_reconciliation_failure")
    return await _terminalize_reconciliation_failure_impl(*args, **kwargs)


async def _finish_terminal_reconciliation_failure(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    kwargs.setdefault("attempt_lifecycle", _TEST_ATTEMPT_LIFECYCLE)
    kwargs.setdefault("lifecycle", _TEST_RUN_LIFECYCLE)
    kwargs.setdefault("run_diagnostics", None)
    return await _finish_terminal_reconciliation_failure_impl(*args, **kwargs)


async def reconcile_pending_executor_terminals_once(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    kwargs.setdefault("attempt_lifecycle", _TEST_ATTEMPT_LIFECYCLE)
    kwargs.setdefault("lifecycle", _TEST_RUN_LIFECYCLE)
    return await reconcile_pending_executor_terminals_once_impl(*args, **kwargs)


async def run_executor_terminal_reconciler(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    kwargs.setdefault("attempt_lifecycle", _TEST_ATTEMPT_LIFECYCLE)
    kwargs.setdefault("lifecycle", _TEST_RUN_LIFECYCLE)
    return await run_executor_terminal_reconciler_impl(*args, **kwargs)


async def probe_suspect_executor_tasks_once(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    return await probe_suspect_executor_tasks_once_impl(*args, **kwargs)


async def _persist_probe_terminal(*args, **kwargs):
    kwargs.setdefault("lease_port", sandbox_lease_repository)
    return await _persist_probe_terminal_impl(*args, **kwargs)


@pytest.fixture(autouse=True)
def _fake_executor_signal_cursor(monkeypatch):
    async def initialize():
        return ExecutorReconciliationSignalCursor("0-0")

    async def no_retry(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.executor_reconciler._next_terminal_retry_delay", no_retry)
    monkeypatch.setattr(
        "app.executor_reconciler.initialize_executor_reconciliation_signal_cursor",
        initialize,
    )


@pytest.fixture(autouse=True)
def _fake_persisted_supervision_owner(monkeypatch):
    """Keep business-SQL fakes isolated while requiring a legacy owner claim."""
    monkeypatch.setattr(_worker_supervision, "transaction", _transaction)
    monkeypatch.setattr(_worker_supervision, "supervision_postgres", _LegacySupervisionStore())


def _lease_row() -> dict[str, object]:
    return {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "executor_reconciliation_claim_token": "claim-a",
        "executor_terminal_json": {"status": "succeeded", "message": "done"},
    }




def _generation_bound_lease_row(*, legacy_snapshot=False):
    row = _lease_row()
    payload = RunPayload(
        **{key: row[key] for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )},
        agent_id="agent-a", skill_id=None, file_ids=[], input={}, owner_generation=4,
        execution_kind="harness_chat", schema_version="ai-platform.run-payload.v2",
    )
    snapshot = sandbox_reconciliation_payload(payload)
    if legacy_snapshot:
        snapshot["execution_payload"].pop("owner_generation", None)
    row["lease_payload_json"] = {"attempt_id": payload.attempt_id, "owner_generation": 4}
    row["executor_reconciliation_context_json"] = {
        "adapter_name": "claude-agent-worker", "adapter_context": {}, "run_payload": snapshot,
    }
    return row


@pytest.mark.parametrize("legacy_snapshot", [False, True])
def test_reconciler_preserves_exact_lease_generation_through_real_snapshot(legacy_snapshot):
    row = _generation_bound_lease_row(legacy_snapshot=legacy_snapshot)
    if not legacy_snapshot:
        assert row["executor_reconciliation_context_json"]["run_payload"]["execution_payload"]["owner_generation"] == 4
    _, payload = _context_payload(row)
    assert payload.owner_generation == 4
    assert _reconciliation_request(row, payload).owner_generation == 4


@pytest.mark.parametrize("defect", ["snapshot_generation", "lease_attempt", "string_generation", "bool_generation", "zero_generation"])
def test_reconciler_rejects_invalid_or_conflicting_lease_generation(defect):
    row = _generation_bound_lease_row()
    if defect == "snapshot_generation":
        row["executor_reconciliation_context_json"]["run_payload"]["execution_payload"]["owner_generation"] = 3
    elif defect == "lease_attempt":
        row["lease_payload_json"]["attempt_id"] = "other-attempt"
    else:
        row["lease_payload_json"]["owner_generation"] = {
            "string_generation": "4", "bool_generation": True, "zero_generation": 0,
        }[defect]
    with pytest.raises(ReconciliationAuthorityError, match="executor_reconciliation_owner_generation_mismatch"):
        _context_payload(row)


def test_reconciliation_request_restores_sanitized_tool_policy_subjects():
    subject = {
        "identity": "Skill",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "declared_identities": ["Skill"],
        "allowed_skill_names": ["qa-file-reviewer"],
        "mcp_server_config": {"secret": "must not persist"},
    }
    payload = SimpleNamespace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        owner_generation=1,
        agent_id="agent-a",
        skill_id="qa-file-reviewer",
        model_value="model-a",
        model_id=None,
        trace_id="trace-a",
        input={"_runtime_tool_policy_subjects": [subject]},
    )

    request = _reconciliation_request({"sandbox_mode": "persistent"}, payload)

    assert request.tool_policy_subjects == [
        {key: value for key, value in subject.items() if key != "mcp_server_config"}
    ]


def test_reconciler_restores_top_level_run_payload_context():
    row = _lease_row()
    row["executor_reconciliation_context_json"] = {
        "schema_version": "ai-platform.executor-reconciliation.v1",
        "run_payload": {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
            "agent_id": "agent-a",
            "skill_id": None,
            "file_ids": [],
            "input": {},
            "execution_kind": "harness_chat",
            "trace_id": "trace-a",
            "schema_version": "ai-platform.run-payload.v2",
            "skill_manifests": [],
        },
        "adapter_context": {},
    }

    context, payload = _context_payload(row)

    assert context["adapter_context"] == {}
    assert payload.run_id == "run-a"
    assert payload.attempt_id == "attempt-a"


def test_reconciler_restores_versioned_execution_payload_without_metadata_leakage():
    row = _lease_row()
    row["executor_reconciliation_context_json"] = {
        "schema_version": "ai-platform.executor-reconciliation.v1",
        "run_payload": {
            "schema_version": "ai-platform.executor-reconciliation-snapshot.v2",
            "execution_payload": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "attempt_id": "attempt-a",
                "agent_id": "agent-a",
                "skill_id": None,
                "file_ids": [],
                "input": {},
                "execution_kind": "harness_chat",
                "trace_id": "trace-a",
                "schema_version": "ai-platform.run-payload.v2",
                "skill_manifests": [],
                "agent_profile": {
                    "agent_id": "agent-a",
                    "revision": 1,
                    "content_hash": "a" * 64,
                    "skill_set": [],
                },
            },
            "metadata": {"agent_profile_expected": True},
        },
        "adapter_context": {},
    }

    context, payload = _context_payload(row, row["executor_terminal_json"])

    assert context["adapter_context"] == {}
    assert payload.run_id == "run-a"
    assert "diagnostics" not in row["executor_terminal_json"]


@pytest.mark.asyncio
async def test_reconciler_uses_spare_capacity_while_first_completion_is_blocked(monkeypatch):
    from app import executor_reconciler

    stop = asyncio.Event()
    first_started = asyncio.Event()
    finish_first = asyncio.Event()
    second_finished = asyncio.Event()
    first_finished = asyncio.Event()
    calls = 0
    active = 0
    peak = 0

    async def reconcile(**_kwargs):
        nonlocal calls, active, peak
        calls += 1
        active += 1
        peak = max(peak, active)
        try:
            if calls == 1:
                first_started.set()
                await finish_first.wait()
            else:
                await first_started.wait()
                second_finished.set()
                stop.set()
                finish_first.set()
            return 1
        finally:
            active -= 1
            if active == 0:
                first_finished.set()

    async def no_work(**_kwargs):
        return 0

    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", no_work)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_work)
    task = asyncio.create_task(run_executor_terminal_reconciler(stop))
    try:
        await asyncio.wait_for(second_finished.wait(), timeout=0.5)
        await asyncio.wait_for(task, timeout=0.5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert peak == 2
    assert first_finished.is_set()


@pytest.mark.asyncio
async def test_reconciler_scans_while_signal_initialization_is_blocked(monkeypatch):
    from app import executor_reconciler

    stop = asyncio.Event()
    initializing = asyncio.Event()
    initialization_finished = asyncio.Event()

    async def initialize():
        initializing.set()
        try:
            await asyncio.Event().wait()
        finally:
            initialization_finished.set()

    async def reconcile(**_kwargs):
        await initializing.wait()
        stop.set()
        return 0

    async def no_work(**_kwargs):
        return 0

    monkeypatch.setattr(executor_reconciler, "initialize_executor_reconciliation_signal_cursor", initialize)
    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", no_work)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_work)
    await asyncio.wait_for(run_executor_terminal_reconciler(stop), timeout=0.5)
    assert initialization_finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_reconciler_holds_bounded_slots_until_claimed_work_finishes_on_shutdown(
    monkeypatch, concurrency
):
    from app import executor_reconciler

    stop = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    monkeypatch.setattr(
        executor_reconciler,
        "get_settings",
        lambda: SimpleNamespace(supervision_settlement_capacity=concurrency),
    )

    async def reconcile(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == concurrency:
            started.set()
        await release.wait()
        return 1

    async def no_work(**_kwargs):
        return 0

    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", no_work)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_work)
    task = asyncio.create_task(run_executor_terminal_reconciler(stop, concurrency=concurrency))
    try:
        await asyncio.wait_for(started.wait(), 0.5)
        await asyncio.sleep(0.02)
        assert calls == concurrency
        stop.set()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 0.5)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == concurrency


@pytest.mark.asyncio
async def test_reconciler_retries_at_due_time_without_notification_or_fallback_delay(monkeypatch):
    from app import executor_reconciler

    stop = asyncio.Event()
    scans = []

    async def unavailable():
        raise ExecutorSignalUnavailable("synthetic outage")

    async def reconcile(**_kwargs):
        scans.append(asyncio.get_running_loop().time())
        if len(scans) == 2:
            stop.set()
        return 0

    async def retry_delay(*_args, **_kwargs):
        return 0.025

    async def no_work(**_kwargs):
        return 0

    monkeypatch.setattr(executor_reconciler, "_RECONCILIATION_FALLBACK_SECONDS", 1.0)
    monkeypatch.setattr(executor_reconciler, "initialize_executor_reconciliation_signal_cursor", unavailable)
    monkeypatch.setattr(executor_reconciler, "_next_terminal_retry_delay", retry_delay)
    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", no_work)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_work)
    await asyncio.wait_for(run_executor_terminal_reconciler(stop, concurrency=1), 0.5)
    assert 0.02 <= scans[1] - scans[0] <= 0.25


@pytest.mark.asyncio
async def test_reconciler_drains_backlog_without_waiting_for_maintenance(monkeypatch):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    scans = 0

    async def reconcile(**_kwargs):
        nonlocal scans
        scans += 1
        if scans == 3:
            stop_event.set()
            return 0
        return 1

    async def unexpected(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        reconcile,
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        unexpected,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "probe_suspect_executor_tasks_once",
        unexpected,
    )

    await run_executor_terminal_reconciler(stop_event, concurrency=1)

    assert scans == 3


@pytest.mark.asyncio
async def test_reconciler_signal_failure_retains_bounded_polling_fallback(monkeypatch):
    from time import monotonic
    from app import executor_reconciler

    stop_event = asyncio.Event()
    scans = 0
    failed_at = None
    rescanned_at = None

    async def reconcile(**_kwargs):
        nonlocal scans, rescanned_at
        scans += 1
        if scans == 3:
            rescanned_at = monotonic()
            stop_event.set()
        return 0

    async def no_work(**_kwargs):
        return 0

    async def unavailable(**_kwargs):
        nonlocal failed_at
        if failed_at is None:
            failed_at = monotonic()
        raise ExecutorSignalUnavailable("redis unavailable")

    monkeypatch.setattr(executor_reconciler, "_RECONCILIATION_FALLBACK_SECONDS", 0.05)
    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", no_work)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_work)
    monkeypatch.setattr(executor_reconciler, "wait_for_executor_reconciliation_signal", unavailable)
    await asyncio.wait_for(run_executor_terminal_reconciler(stop_event, concurrency=1), timeout=1)
    assert 2 <= scans <= 3
    assert failed_at is not None and rescanned_at is not None
    assert 0.04 <= rescanned_at - failed_at <= 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_phase", ["cleanup", "probe"])
async def test_terminal_signal_is_consumed_while_maintenance_is_blocked(
    monkeypatch, slow_phase,
):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    maintenance_started = asyncio.Event()
    finish_maintenance = asyncio.Event()
    maintenance_finished = asyncio.Event()
    terminal_scanned = asyncio.Event()
    scans = 0
    phase_calls = 0

    async def reconcile(**_kwargs):
        nonlocal scans
        scans += 1
        if scans == 2:
            assert maintenance_started.is_set()
            assert not maintenance_finished.is_set()
            terminal_scanned.set()
            stop_event.set()
            finish_maintenance.set()
        return 0

    async def blocked(**_kwargs):
        nonlocal phase_calls
        phase_calls += 1
        maintenance_started.set()
        try:
            await finish_maintenance.wait()
        finally:
            maintenance_finished.set()

    async def no_work(**_kwargs):
        return 0

    async def signal(**_kwargs):
        await maintenance_started.wait()
        await asyncio.sleep(0.01)
        return True

    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        blocked if slow_phase == "cleanup" else no_work,
    )
    monkeypatch.setattr(
        executor_reconciler, "probe_suspect_executor_tasks_once",
        blocked if slow_phase == "probe" else no_work,
    )
    monkeypatch.setattr(executor_reconciler, "wait_for_executor_reconciliation_signal", signal)
    task = asyncio.create_task(run_executor_terminal_reconciler(stop_event, concurrency=1))
    try:
        await asyncio.wait_for(terminal_scanned.wait(), timeout=1)
        await asyncio.wait_for(task, timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert phase_calls == 1
    assert maintenance_finished.is_set()


@pytest.mark.asyncio
async def test_terminal_reconciler_shutdown_joins_cancellation_resistant_maintenance(monkeypatch):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def reconcile(**_kwargs):
        return 0

    async def cleanup(**_kwargs):
        started.set()
        try:
            await release.wait()
        finally:
            finished.set()

    async def no_probe(**_kwargs):
        return 0

    async def signal(**_kwargs):
        await started.wait()
        stop_event.set()
        return True

    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", cleanup)
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_probe)
    monkeypatch.setattr(executor_reconciler, "wait_for_executor_reconciliation_signal", signal)
    task = asyncio.create_task(run_executor_terminal_reconciler(stop_event, concurrency=1))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(stop_event.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        release.set()
        await asyncio.wait_for(task, timeout=1)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert finished.is_set()


@pytest.mark.asyncio
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_terminal_artifact_conversion_uses_storage_bridge(monkeypatch, caplog):
    from app.platform import tracing
    now = [10.0]
    monkeypatch.setattr(tracing, "monotonic", lambda: now[0])
    caplog.set_level(logging.INFO)
    from app import executor_reconciler

    class Lease:
        def model_copy(self, *, update):
            assert update == {"workspace_host_path": "/workspace"}
            return self

    class Provider:
        fail_collection = False

        async def collect_workspace(
            self, _lease, _request, _workspace, response_files=()
        ):
            assert list(response_files) == ["output/final.txt"]
            now[0] += 2.25
            await asyncio.sleep(0)
            if self.fail_collection:
                raise RuntimeError("workspace collection failed")
            return Path("/private/artifact-snapshot")

    class Adapter:
        def reconcile_sandbox_terminal(self, **_kwargs):
            raise AssertionError("the event loop must not call conversion directly")

    class Registry:
        def get(self, name):
            assert name == "claude"
            return Adapter()

    provider = Provider()
    lease = Lease()
    bridged = []
    adapter_contexts = []
    terminal_results = []
    abandonment_callbacks = []
    reserved = []
    bridge_failure = [None]

    async def reserve_cleanup(**kwargs):
        reserved.append(kwargs)
        return "art_cleanup_a"

    async def bridge(operation, **kwargs):
        bridged.append(operation.__name__)
        adapter_contexts.append(kwargs["adapter_context"])
        terminal_results.append(kwargs["terminal_result"])
        abandonment_callbacks.append(kwargs["on_abandoned"])
        receipt_id = await asyncio.to_thread(
            kwargs["adapter_context"]["_reserve_artifact_storage"],
            "private/reconciliations/claim-a/result.txt",
        )
        assert receipt_id == "art_cleanup_a"
        now[0] += 0.75
        if bridge_failure[0] is not None:
            raise bridge_failure[0]
        return _result()

    monkeypatch.setattr(
        executor_reconciler,
        "_context_and_payload",
        lambda _row: (
            {"adapter_name": "claude", "adapter_context": {}},
            {"status": "succeeded", "response_files": ["output/final.txt"]},
            SimpleNamespace(attempt_id="attempt-a"),
        ),
    )
    monkeypatch.setattr(
        executor_reconciler, "_reconciliation_request", lambda *_args: object()
    )
    monkeypatch.setattr(
        executor_reconciler,
        "SandboxWorkspaceManager",
        lambda: SimpleNamespace(
            prepare=lambda _request: SimpleNamespace(workspace_host_path="/workspace")
        ),
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "container_lease_from_persisted_row", lambda _row: lease
    )
    monkeypatch.setattr(
        executor_reconciler, "_container_provider_for_lease", lambda _lease: provider
    )
    monkeypatch.setattr(executor_reconciler, "run_storage_io", bridge)
    monkeypatch.setattr(
        executor_reconciler,
        "_reserve_reconciliation_artifact_cleanup",
        reserve_cleanup,
    )

    (
        result,
        actual_provider,
        actual_lease,
    ) = await executor_reconciler._collect_workspace_and_convert_result(
        {
            "id": "lease-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "provider": "docker",
        },
        registry=Registry(),
        claim_token="claim-a",
        lease_port=sandbox_lease_repository,
    )

    assert result == _result()
    assert actual_provider is provider
    assert actual_lease is lease
    assert bridged == ["reconcile_sandbox_terminal"]
    assert adapter_contexts[0]["_artifact_storage_scope"] == "attempt-a"
    assert adapter_contexts[0]["_artifact_workspace"] == str(
        Path("/private/artifact-snapshot")
    )
    assert (
        abandonment_callbacks[0].__self__
        is adapter_contexts[0]["_artifact_collection_abandoned"]
    )
    assert not adapter_contexts[0]["_artifact_collection_abandoned"].is_set()
    assert reserved == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "storage_key": "private/reconciliations/claim-a/result.txt",
        }
    ]

    provider.fail_collection = True
    await executor_reconciler._collect_workspace_and_convert_result(
        {
            "id": "lease-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "provider": "docker",
        },
        registry=Registry(),
        claim_token="claim-a",
        lease_port=sandbox_lease_repository,
    )
    collection_diagnostics = terminal_results[-1]["runtime_diagnostics"]
    assert terminal_results[-1]["status"] == "failed"
    assert collection_diagnostics["error_code"] == (
        "sandbox_workspace_collection_failed"
    )
    assert collection_diagnostics["failure_stage"] == "workspace_collection"
    assert [
        item["type"] for item in collection_diagnostics["sdk"]["exception_chain"]
    ] == ["RuntimeError"]

    provider.fail_collection = False
    for failure in (RuntimeError("private upload error"), asyncio.CancelledError("private cancel"), ValueError("private conversion error")):
        bridge_failure[0] = failure
        expected = PermanentExecutorReconciliationError if isinstance(failure, ValueError) else type(failure)
        with pytest.raises(expected) as caught:
            await executor_reconciler._collect_workspace_and_convert_result(
                {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a", "provider": "docker"},
                registry=Registry(), claim_token="claim-a",
                lease_port=sandbox_lease_repository,
            )
        if isinstance(failure, ValueError):
            assert caught.value.__cause__ is failure
        else:
            assert caught.value is failure
        assert adapter_contexts[-1]["_artifact_collection_abandoned"].is_set()

    records = [record for record in caplog.records if record.msg == "run_terminal_stage"]
    assert [record.terminal_phase for record in records] == [
        "reconciler_workspace_snapshot", "reconciler_result_conversion",
    ] * 5
    assert [record.terminal_phase_duration_ms for record in records] == [2250, 750] * 5
    assert [record.terminal_phase_outcome for record in records] == [
        "ok", "ok", "error", "ok", "ok", "error", "ok", "cancelled", "ok", "error",
    ]
    assert all(record.run_id == "run-a" and record.attempt_id == "attempt-a" for record in records)
    logged = repr([_log_payload(record) for record in records])
    for secret in (
        "final.txt",
        "workspace collection failed",
        "/private/artifact-snapshot",
        "private/reconciliations/claim-a/result.txt",
        "private upload error",
        "private cancel",
        "private conversion error",
    ):
        assert secret not in logged


@pytest.mark.asyncio
async def test_saved_continuation_transfers_attempt_claim_without_provider_io(monkeypatch):
    from app import executor_reconciler

    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    continuation = {
        **{key: scope[key] for key in ("tenant_id", "run_id", "attempt_id")},
        "final_sequence": 2,
        "workspace_receipt": {"state": "saved"},
        "stop_evidence": "provider_stopped",
    }
    stage = {
        "schema_version": "ai-platform.terminal-reconciliation.v1",
        "scope": scope,
        "owner_generation": 7,
        "container_id": "container-a",
        "artifact_workspace": None,
        "terminal_result": {"run_id": "run-a", "status": "failed"},
        "continuation": continuation,
    }
    payload = SimpleNamespace(
        **scope,
        owner_generation=7,
        model_value="model-a",
        model_id=None,
        trace_id="trace-a",
        agent_id="agent-a",
        skill_id=None,
        input={},
    )
    request = SimpleNamespace(**scope, owner_generation=7)
    lease_row = {
        **scope,
        "id": "lease-a",
        "provider": "fake",
        "settlement_storage_version": "attempt.v1",
        "runtime_container_id": "container-a",
        "lease_payload_json": {
            "attempt_id": "attempt-a",
            "owner_generation": 7,
            "terminal_reconciliation": stage,
        },
    }
    calls = []

    class Lease:
        container_id = "container-a"

        def model_copy(self, *, update):
            assert update == {"workspace_host_path": "/workspace"}
            return self

    class Provider:
        async def collect_workspace(self, *_args, **_kwargs):
            pytest.fail("a saved checkpoint must not recollect workspace data")

        async def stop(self, *_args, **_kwargs):
            pytest.fail("a saved checkpoint must not reconnect to its producer")

    class Adapter:
        def reconcile_sandbox_terminal(self, **_kwargs):
            raise AssertionError("conversion is dispatched through the storage bridge")

    class Registry:
        def get(self, name):
            assert name == "claude-agent-worker"
            return Adapter()

    async def record(_lease_row, **kwargs):
        calls.append((kwargs["claim_token"], kwargs["stage"]))

    async def bridge(operation, **kwargs):
        assert operation.__name__ == "reconcile_sandbox_terminal"
        assert kwargs["terminal_result"] == stage["terminal_result"]
        return _result()

    monkeypatch.setattr(
        executor_reconciler,
        "_context_and_payload",
        lambda _row: (
            {"adapter_name": "claude-agent-worker", "adapter_context": {}},
            stage["terminal_result"],
            payload,
        ),
    )
    monkeypatch.setattr(executor_reconciler, "_reconciliation_request", lambda *_args: request)
    monkeypatch.setattr(
        executor_reconciler,
        "SandboxWorkspaceManager",
        lambda: SimpleNamespace(
            prepare=lambda _request: SimpleNamespace(workspace_host_path="/workspace")
        ),
    )
    lease = Lease()
    provider = Provider()
    monkeypatch.setattr(executor_reconciler, "_reconciliation_runtime_lease", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(executor_reconciler, "_container_provider_for_lease", lambda _lease: provider)
    monkeypatch.setattr(executor_reconciler, "_record_reconciliation_stage", record)
    monkeypatch.setattr(executor_reconciler, "run_storage_io", bridge)

    result, actual_provider, actual_lease = await executor_reconciler._collect_workspace_and_convert_result(
        lease_row,
        registry=Registry(),
        claim_token="claim-b",
        lease_port=sandbox_lease_repository,
    )

    assert calls == [("claim-b", stage)]
    assert result.executor_payload["continuation_settlement"] == continuation
    assert actual_provider is provider
    assert actual_lease is lease


def test_reconciler_quarantines_invalid_persisted_payload_without_run_authority(monkeypatch):
    row = _lease_row()
    row["executor_reconciliation_context_json"] = {
        "run_payload": {"schema_version": "invalid"},
        "adapter_context": {},
    }

    def fail_restore(*_args):
        raise ValueError("agent_profile_instructions_invalid")

    monkeypatch.setattr(
        "app.executor_reconciler.restored_sandbox_run_payload",
        fail_restore,
    )

    with pytest.raises(ReconciliationAuthorityError) as exc_info:
        _context_payload(row, row["executor_terminal_json"])

    assert exc_info.value.code == "executor_reconciliation_run_payload_invalid"
    assert "agent_profile_instructions_invalid" not in str(exc_info.value)


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id"],
)
def test_reconciler_rejects_persisted_payload_identity_mismatch_before_use(
    monkeypatch,
    field,
):
    row = _lease_row()
    row["executor_reconciliation_context_json"] = {
        "run_payload": {},
        "adapter_context": {},
    }
    payload_identity = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    payload_identity[field] = f"other-{field}"
    monkeypatch.setattr(
        "app.executor_reconciler.restored_sandbox_run_payload",
        lambda *_args: SimpleNamespace(**payload_identity),
    )

    with pytest.raises(ReconciliationAuthorityError) as exc_info:
        _context_payload(row, row["executor_terminal_json"])

    assert exc_info.value.code == "executor_reconciliation_identity_mismatch"


def test_reconciler_does_not_equate_missing_and_empty_identity(monkeypatch):
    row = _lease_row()
    row["attempt_id"] = None
    row["executor_reconciliation_context_json"] = {
        "adapter_context": {},
        "run_payload": {"present": True},
    }
    restored = SimpleNamespace(
        **{
            identity_field: ("" if identity_field == "attempt_id" else row[identity_field])
            for identity_field in (
                "tenant_id",
                "workspace_id",
                "user_id",
                "session_id",
                "run_id",
                "attempt_id",
            )
        }
    )
    monkeypatch.setattr(
        "app.executor_reconciler.restored_sandbox_run_payload",
        lambda *_args, **_kwargs: restored,
    )

    with pytest.raises(ReconciliationAuthorityError) as exc_info:
        _context_payload(row, row["executor_terminal_json"])

    assert exc_info.value.code == "executor_reconciliation_identity_mismatch"


@pytest.mark.asyncio
async def test_reconciler_entrypoint_rejects_identity_mismatch_before_workspace_or_provider(
    monkeypatch,
):
    row = _lease_row()
    row["executor_reconciliation_context_json"] = {
        "run_payload": {},
        "adapter_context": {},
    }
    payload_identity = {
        "tenant_id": "other-tenant",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    calls = []

    async def claim(_conn, **_kwargs):
        return [row]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def finish(_lease_row, **kwargs):
        pytest.fail("unknown scope must not terminalize a Run")

    async def quarantine(_lease_row, **kwargs):
        calls.append(("quarantine", kwargs["error_code"]))

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        "app.executor_reconciler.restored_sandbox_run_payload",
        lambda *_args: SimpleNamespace(**payload_identity),
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(
        "app.executor_reconciler.SandboxWorkspaceManager.prepare",
        lambda *_args: pytest.fail("workspace must not be prepared for mismatched identity"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler._container_provider_for_lease",
        lambda *_args: pytest.fail("provider must not receive mismatched identity"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler._finish_terminal_reconciliation_failure",
        finish,
    )

    monkeypatch.setattr("app.executor_reconciler._quarantine_failed_reconciliation", quarantine)
    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert calls == [("quarantine", "executor_reconciliation_identity_mismatch")]


def _result() -> ExecutorResult:
    return ExecutorResult(
        status="succeeded",
        adapter_version="opensandbox/1",
        executor_type="claude_agent_sdk",
        executor_version="1",
        capabilities={},
        result={"message": "done"},
        executor_payload={},
    )


def _suspect_lease_row() -> dict[str, object]:
    return {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "executor_reconciliation_claim_token": "claim-a",
        "executor_reconciliation_attempt_count": 1,
        "executor_reconciliation_context_json": {
            "adapter_name": "opensandbox",
            "executor_request": {},
            "adapter_context": {},
        },
    }


@pytest.mark.asyncio
async def test_unknown_receipt_probe_recovers_only_exact_dispatch_generation(monkeypatch):
    from app.executor_reconciler import _record_probe_dispatch_accepted

    accepted_contexts = []
    outcomes = []

    class LeasePort:
        async def read_unknown_receipt_probe_recovery_facts(self, _conn, **kwargs):
            if kwargs["owner_generation"] != 4:
                return None
            return {
                "owner_generation": 4,
                "status": "active",
                "attempt_id": "attempt-a",
                "lease_payload_json": {"owner_generation": 4},
                "dispatch_generation": 4,
                "dispatch_lease_id": "lease-a",
                "dispatch_provider": "fake",
                "dispatch_task_id": "attempt-a",
                "mapping_state": "unknown",
                "dispatch_state": "unknown",
                "intent_recovery_context_json": {
                    "adapter_name": "opensandbox", "frozen": "intent"
                },
                "accepted_context_json": None,
                "receipt_json": {"run_id": "run-a", "status": "succeeded"},
            }

        async def record_sandbox_executor_accepted(self, _conn, **kwargs):
            accepted_contexts.append(kwargs)

    class DispatchIntents:
        async def record_outcome(self, _conn, **kwargs):
            outcomes.append(kwargs)
            return {"status": "accepted"}

    @asynccontextmanager
    async def transaction():
        yield object()

    monkeypatch.setattr("app.executor_reconciler.transaction", transaction)
    lease_row = {
        **_suspect_lease_row(),
        "lease_payload_json": {"owner_generation": 4, "attempt_id": "attempt-a"},
        "provider": "fake",
        "executor_probe_claim_token": "probe-a",
        "settlement_receipt_present": True,
        "settlement_mapping_state": "unknown",
        "executor_reconciliation_intent_context_json": {
            "adapter_name": "opensandbox", "frozen": "intent"
        },
    }
    status = {"status": "completed", "run_id": "run-a", "attempt_id": "attempt-a"}
    recovered = await _record_probe_dispatch_accepted(
        lease_row,
        status,
        dispatch_intents=DispatchIntents(),
        lease_port=LeasePort(),
    )
    assert recovered is True
    assert accepted_contexts[0]["reconciliation_context"] == {
        "adapter_name": "opensandbox", "frozen": "intent"
    }
    assert outcomes[0]["outcome"] == "accepted"

    accepted_contexts.clear()
    outcomes.clear()
    stale_row = {**lease_row, "lease_payload_json": {"owner_generation": 3, "attempt_id": "attempt-a"}}
    recovered = await _record_probe_dispatch_accepted(
        stale_row,
        status,
        dispatch_intents=DispatchIntents(),
        lease_port=LeasePort(),
    )
    assert recovered is False
    assert accepted_contexts == []
    assert outcomes == []


@pytest.mark.asyncio
async def test_unknown_receipt_dispatch_cas_failure_rolls_back_acceptance(monkeypatch):
    from app.executor_reconciler import _record_probe_dispatch_accepted

    transactions = []
    accepted_contexts = []

    class LeasePort:
        async def read_unknown_receipt_probe_recovery_facts(self, _conn, **_kwargs):
            return {
                "owner_generation": 4,
                "status": "active",
                "attempt_id": "attempt-a",
                "lease_payload_json": {"owner_generation": 4},
                "dispatch_generation": 4,
                "dispatch_lease_id": "lease-a",
                "dispatch_provider": "fake",
                "dispatch_task_id": "attempt-a",
                "mapping_state": "unknown",
                "dispatch_state": "unknown",
                "intent_recovery_context_json": {"frozen": "intent"},
                "accepted_context_json": None,
                "receipt_json": {"run_id": "run-a", "status": "succeeded"},
            }

        async def record_sandbox_executor_accepted(self, _conn, **kwargs):
            accepted_contexts.append(kwargs)

    class DispatchIntents:
        async def record_outcome(self, _conn, **_kwargs):
            return None

    @asynccontextmanager
    async def transaction():
        record = {"rollback": False}
        transactions.append(record)
        try:
            yield object()
        except Exception:
            record["rollback"] = True
            raise

    monkeypatch.setattr("app.executor_reconciler.transaction", transaction)
    lease_row = {
        **_suspect_lease_row(),
        "lease_payload_json": {"owner_generation": 4, "attempt_id": "attempt-a"},
        "provider": "fake",
        "executor_probe_claim_token": "probe-a",
        "settlement_receipt_present": True,
        "settlement_mapping_state": "unknown",
        "executor_reconciliation_intent_context_json": {"frozen": "intent"},
    }
    recovered = await _record_probe_dispatch_accepted(
        lease_row,
        {"status": "completed", "run_id": "run-a", "attempt_id": "attempt-a"},
        dispatch_intents=DispatchIntents(),
        lease_port=LeasePort(),
    )
    assert recovered is False
    assert accepted_contexts
    assert transactions == [{"rollback": True}]


@pytest.mark.asyncio
async def test_unknown_receipt_probe_keeps_unavailable_provider_recoverable(monkeypatch):
    released = []
    row = {
        **_suspect_lease_row(),
        "lease_payload_json": {"owner_generation": 4, "attempt_id": "attempt-a"},
        "provider": "fake",
        "settlement_receipt_present": True,
        "settlement_mapping_state": "unknown",
        "settlement_dispatch_state": "unknown",
        "executor_reconciliation_intent_context_json": {
            "adapter_name": "opensandbox", "executor_request": {}, "adapter_context": {},
        },
    }

    async def claim(_conn, **_kwargs):
        return [row]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def release(_conn, **kwargs):
        released.append(kwargs)
        return True

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            return "http://executor", {}

    class Client:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 10.0

        async def get_status(self, *_args, **_kwargs):
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(sandbox_lease_repository, "claim_sandbox_executor_suspects", claim)
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr("app.executor_reconciler.create_container_provider", lambda _name: Provider())
    monkeypatch.setattr("app.executor_reconciler.SandboxExecutorClient", Client)
    monkeypatch.setattr(sandbox_lease_repository, "release_sandbox_executor_probe_claim", release)

    assert await probe_suspect_executor_tasks_once() == 0
    assert len(released) == 1
    assert released[0]["lease_id"] == "lease-a"
    assert isinstance(released[0]["claim_token"], str)
    assert released[0]["error"] == "executor_probe_unknown_receipt_unavailable"


@pytest.mark.asyncio
async def test_probe_releases_active_executor_for_future_heartbeat_checks(monkeypatch):
    released = []
    claim_tokens = []
    selected_provider_names = []

    async def claim(_conn, **kwargs):
        claim_tokens.append(kwargs["claim_token"])
        return [_suspect_lease_row()]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def release(_conn, **kwargs):
        released.append(kwargs)
        return True

    class Provider:
        async def executor_control_endpoint(self, lease, _request):
            assert lease is not None
            return "http://executor", {"Authorization": "Bearer control"}

    def create_provider(provider_name):
        selected_provider_names.append(provider_name)
        return Provider()

    class Client:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 10.0

        async def get_status(self, executor_url, *, run_id, attempt_id, executor_headers):
            assert executor_url == "http://executor"
            assert run_id == "run-a"
            assert attempt_id == "attempt-a"
            assert executor_headers == {"Authorization": "Bearer control"}
            return {"status": "running"}

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        create_provider,
    )
    monkeypatch.setattr("app.executor_reconciler.SandboxExecutorClient", Client)
    monkeypatch.setattr(
        sandbox_lease_repository, "release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 0
    assert selected_provider_names == ["fake"]
    assert claim_tokens and released == [
        {
            "lease_id": "lease-a",
            "claim_token": claim_tokens[0],
        }
    ]


@pytest.mark.asyncio
async def test_probe_persists_private_diagnostics_before_bounded_terminal_receipt(
    monkeypatch,
):
    calls = []

    async def record_terminal(conn, **kwargs):
        calls.append(("receipt", conn, kwargs))

    class RecordingDiagnostics:
        async def capture_failure_result(self, conn, **kwargs):
            calls.append(("diagnostics", conn, kwargs))
            return {"message": "safe"}

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "record_sandbox_executor_terminal",
        record_terminal,
    )
    private = {
        "schema_version": "ai-platform.sdk-runtime-diagnostics.v1",
        "error_code": "provider_timeout",
        "failure_source": "sdk_exception",
        "failure_stage": "model_wait",
    }
    terminal_result = {
        "status": "failed",
        "run_id": "run-a",
        "error_code": "executor_failed",
        "error_message": "Executor failed",
        "runtime_diagnostics": private,
        "sdk_turn_diagnostics": {
            "runtime_diagnostics": {"token": "nested-private"}
        },
    }

    await _persist_probe_terminal(
        _suspect_lease_row(),
        executor_status="failed",
        terminal_result=terminal_result,
        claim_token="claim-a",
        run_diagnostics=RecordingDiagnostics(),
    )

    assert [item[0] for item in calls] == ["receipt", "diagnostics"]
    assert calls[0][1] is calls[1][1]
    assert "runtime_diagnostics" not in str(calls[0][2]["terminal_result"])
    assert calls[1][2]["result_json"]["runtime_diagnostics"] == private


@pytest.mark.asyncio
async def test_probe_persists_only_structural_protocol_evidence(
    monkeypatch,
):
    calls = []

    async def record_terminal(conn, **kwargs):
        calls.append(("receipt", conn, kwargs))

    class RecordingDiagnostics:
        async def capture_executor_protocol_failure(self, conn, **kwargs):
            calls.append(("protocol", conn, kwargs))

        async def capture_failure_result(self, *_args, **_kwargs):
            raise AssertionError("invalid payload diagnostics must not be captured")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "record_sandbox_executor_terminal",
        record_terminal,
    )
    reported_result = {
        "status": "completed",
        "run_id": "run-a",
        "message": "",
        "error_code": "PRIVATE_REPORTED_ERROR_CODE",
        "runtime_diagnostics": {
            "schema_version": "ai-platform.sdk-runtime-diagnostics.v1",
            "error_code": "provider_timeout",
        },
    }
    validation_errors = [
        {
            "loc": [],
            "type": "value_error",
            "msg": "successful terminal result requires output",
        }
    ]

    await _persist_probe_terminal(
        _suspect_lease_row(),
        executor_status="failed",
        terminal_result={
            "run_id": "run-a",
            "status": "failed",
            "error_code": "executor_protocol_invalid",
            "error_message": "Sandbox executor returned an invalid terminal result",
        },
        claim_token="claim-a",
        run_diagnostics=RecordingDiagnostics(),
        protocol_failure={
            "task_status": "callback_failed",
            "terminal_result": reported_result,
            "validation_errors": validation_errors,
        },
    )

    assert [item[0] for item in calls] == ["receipt", "protocol"]
    assert calls[0][1] is calls[1][1]
    assert calls[1][2]["terminal_result"] is reported_result
    assert calls[1][2]["validation_errors"] is validation_errors
    assert "runtime_diagnostics" not in str(calls[0][2]["terminal_result"])


@pytest.mark.asyncio
async def test_probe_keeps_terminal_receipt_when_protocol_diagnostics_fail(
    monkeypatch,
):
    calls = []

    async def record_terminal(conn, **kwargs):
        calls.append(("receipt", conn, kwargs))

    class FailingPersistence:
        async def append_observation(self, conn, **_kwargs):
            calls.append(("protocol", conn))
            raise RuntimeError("diagnostic storage unavailable")

    run_diagnostics = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=FailingPersistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "record_sandbox_executor_terminal",
        record_terminal,
    )

    await _persist_probe_terminal(
        _suspect_lease_row(),
        executor_status="failed",
        terminal_result={
            "run_id": "run-a",
            "status": "failed",
            "error_code": "executor_protocol_invalid",
            "error_message": "Sandbox executor returned an invalid terminal result",
        },
        claim_token="claim-a",
        run_diagnostics=run_diagnostics,
        protocol_failure={
            "task_status": "completed",
            "terminal_result": {"run_id": "run-a", "status": "completed"},
            "validation_errors": [],
        },
    )

    assert [item[0] for item in calls] == ["receipt", "protocol"]
    assert calls[0][1] is calls[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "task_status",
        "result_status",
        "success_message",
        "result_run_id",
        "expected_executor_status",
    ),
    [
        ("completed", "completed", "done", "run-a", "completed"),
        ("succeeded", "succeeded", "done", "run-a", "completed"),
        ("callback_failed", "completed", "done", "run-a", "completed"),
        ("callback_failed", "succeeded", "done", "run-a", "completed"),
        ("failed", "failed", None, "run-a", "failed"),
        ("callback_failed", "failed", None, "run-a", "failed"),
        ("cancelled", "cancelled", None, "run-a", "cancelled"),
        ("canceled", "cancelled", None, "run-a", "cancelled"),
        ("callback_failed", "canceled", None, "run-a", "cancelled"),
        ("completed", "failed", None, "run-a", "protocol_invalid"),
        ("failed", "completed", "done", "run-a", "protocol_invalid"),
        ("finished", "completed", "done", "run-a", "protocol_invalid"),
        ("callback_failed", "completed", "", "run-a", "completed"),
        ("completed", "completed", "done", "run-other", "protocol_invalid"),
    ],
)
async def test_probe_preserves_matching_terminal_status_and_rejects_contradictions(
    monkeypatch,
    task_status,
    result_status,
    success_message,
    result_run_id,
    expected_executor_status,
):
    persisted = []

    async def claim(_conn, **_kwargs):
        return [_suspect_lease_row()]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def persist(lease_row, **kwargs):
        persisted.append((lease_row, kwargs))

    terminal_result = {"run_id": result_run_id, "status": result_status}
    if result_status in {"completed", "succeeded"}:
        terminal_result["message"] = success_message
    else:
        terminal_result.update(
            error_code="executor_failed",
            error_message="Executor failed",
        )

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            return "http://executor", {"Authorization": "Bearer control"}

    class Client:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 10.0

        async def get_status(self, _executor_url, **_kwargs):
            return {
                "status": task_status,
                "terminal_result": terminal_result,
            }

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        lambda _provider_name: Provider(),
    )
    monkeypatch.setattr("app.executor_reconciler.SandboxExecutorClient", Client)
    monkeypatch.setattr("app.executor_reconciler._persist_probe_terminal", persist)

    assert await probe_suspect_executor_tasks_once() == 1
    assert len(persisted) == 1
    assert persisted[0][0]["id"] == "lease-a"
    assert persisted[0][1].pop("lease_port") is sandbox_lease_repository
    assert isinstance(persisted[0][1].pop("claim_token"), str)
    assert persisted[0][1].pop("run_diagnostics") is None
    protocol_failure = persisted[0][1].pop("protocol_failure", None)
    if expected_executor_status == "protocol_invalid":
        assert protocol_failure is not None
        assert protocol_failure["task_status"] == task_status
        assert protocol_failure["terminal_result"] == terminal_result
        assert protocol_failure["validation_errors"]
        assert "input" not in str(protocol_failure["validation_errors"])
        if result_run_id != "run-a":
            assert protocol_failure["validation_errors"] == [
                {
                    "loc": ["run_id"],
                    "type": "run_id_mismatch",
                    "msg": "Terminal result Run does not match the claimed Run",
                }
            ]
        assert persisted[0][1] == {
            "executor_status": "failed",
            "terminal_result": {
                "run_id": "run-a",
                "status": "failed",
                "message": "",
                "error_code": "executor_protocol_invalid",
                "error_message": "Sandbox executor returned an invalid terminal result",
            },
        }
    else:
        assert protocol_failure is None
        expected_result = {**terminal_result}
        expected_result.setdefault("message", "")
        expected_result.setdefault("response_files", [])
        assert persisted[0][1] == {
            "executor_status": expected_executor_status,
            "terminal_result": expected_result,
        }


@pytest.mark.asyncio
async def test_probe_cancellation_releases_claim_before_propagating(monkeypatch):
    claim_tokens = []
    released = []

    async def claim(_conn, **kwargs):
        claim_tokens.append(kwargs["claim_token"])
        second = {**_suspect_lease_row(), "id": "lease-b"}
        return [_suspect_lease_row(), second]

    async def release(_conn, **kwargs):
        released.append(kwargs)
        return True

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            raise __import__("asyncio").CancelledError

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        lambda _provider_name: Provider(),
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "release_sandbox_executor_probe_claim",
        release,
    )

    with pytest.raises(__import__("asyncio").CancelledError):
        await probe_suspect_executor_tasks_once()

    assert claim_tokens and released == [
        {
            "lease_id": "lease-a",
            "claim_token": claim_tokens[0],
            "error": "probe_cancelled",
        },
        {
            "lease_id": "lease-b",
            "claim_token": claim_tokens[0],
            "error": "probe_cancelled",
        },
    ]


@pytest.mark.asyncio
async def test_probe_terminalizes_authoritatively_missing_sandbox_immediately(monkeypatch):
    persisted = []
    released = []

    async def claim(_conn, **_kwargs):
        return [_suspect_lease_row()]

    async def persist(lease_row, **kwargs):
        persisted.append((lease_row, kwargs))

    async def release(_conn, **kwargs):
        released.append(kwargs)
        return True

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            raise RuntimeError("provider-confirmed sandbox loss")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        lambda _provider_name: Provider(),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.is_authoritative_not_found_error", lambda _exc: True
    )
    monkeypatch.setattr("app.executor_reconciler._persist_probe_terminal", persist)
    monkeypatch.setattr(
        sandbox_lease_repository, "release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 1
    assert len(persisted) == 1
    assert persisted[0][0]["id"] == "lease-a"
    assert persisted[0][1].pop("lease_port") is sandbox_lease_repository
    assert isinstance(persisted[0][1].pop("claim_token"), str)
    assert persisted[0][1].pop("run_diagnostics") is None
    terminal_result = persisted[0][1].pop("terminal_result")
    private = terminal_result.pop("runtime_diagnostics")
    assert persisted[0][1] == {"executor_status": "failed"}
    assert terminal_result == {
        "run_id": "run-a",
        "status": "failed",
        "error_code": "sandbox_executor_lost",
        "error_message": "Sandbox executor stopped responding",
    }
    assert private == {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": "sandbox_executor_lost",
        "failure_source": "executor_probe",
        "failure_stage": "provider_endpoint",
        "sdk": {
            "exception_type": "RuntimeError",
            "errors": [{"reason": "provider_absent"}],
        },
    }
    assert "provider-confirmed sandbox loss" not in str(private)
    assert released == []


@pytest.mark.asyncio
async def test_probe_retry_persists_only_safe_error_code(monkeypatch):
    released = []

    async def claim(_conn, **_kwargs):
        return [_suspect_lease_row()]

    async def release(_conn, **kwargs):
        released.append(kwargs)
        return True

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            raise RuntimeError("provider credential must never be stored")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects", claim
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        lambda _provider_name: Provider(),
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "release_sandbox_executor_probe_claim", release
    )

    assert await probe_suspect_executor_tasks_once() == 0
    assert len(released) == 1
    assert released[0]["error"] == "sandbox_executor_probe_failed"


def test_opensandbox_probe_error_facts_keep_only_typed_safe_fields():
    from opensandbox.exceptions import SandboxApiException, SandboxError
    from app.runtime.sandbox.providers.opensandbox.startup import opensandbox_probe_error_facts

    error = SandboxApiException(
        "private provider response",
        status_code=404,
        error=SandboxError("DOCKER::SANDBOX_NOT_FOUND", "private error"),
        request_id="request_2026",
    )
    assert opensandbox_probe_error_facts(error) == {
        "http_status": 404,
        "request_id": "request_2026",
    }
    assert opensandbox_probe_error_facts(RuntimeError("private provider response")) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("creates_checkpoint", [False, True])
async def test_reconciler_exits_claim_transaction_before_terminalization_and_release(monkeypatch, creates_checkpoint):
    calls = []
    transaction_active = {}
    transaction_count = 0

    class OwnerConnection:
        def __init__(self, index):
            self.index = index

    @asynccontextmanager
    async def fenced_transaction():
        nonlocal transaction_count
        transaction_count += 1
        conn = OwnerConnection(transaction_count)
        transaction_active[conn.index] = True
        try:
            yield conn
        finally:
            transaction_active[conn.index] = False

    async def claim(conn, **kwargs):
        assert transaction_active[conn.index] is True
        assert kwargs["limit"] == 1
        return [_lease_row()]

    async def has_claim(conn, **_kwargs):
        assert transaction_active[conn.index] is True
        calls.append(("has_claim", conn.index))
        return True

    async def get_run(conn, **kwargs):
        assert transaction_active[conn.index] is True
        assert kwargs["for_update"] is False
        calls.append(("get_run", conn.index))
        return {"id": "run-a", "status": "running"}

    async def collect(lease_row, **_kwargs):
        assert all(active is False for active in transaction_active.values())
        calls.append(("collect", lease_row["id"], transaction_count))
        if creates_checkpoint:
            lease_row.setdefault("lease_payload_json", {})["terminal_reconciliation"] = {
                "continuation": {"stop_evidence": "provider_stopped"},
            }
        return _result(), object(), object()

    async def terminalize(**kwargs):
        assert all(active is False for active in transaction_active.values())
        assert kwargs["transaction_factory"] is fenced_transaction
        calls.append(("terminalize", kwargs["claim_token"], transaction_count))
        return WorkerOutcome("succeeded", "run-a")

    async def release(conn, **kwargs):
        assert transaction_active[conn.index] is True
        calls.append(("cleanup_pending", kwargs["claim_token"], kwargs["lease_id"], transaction_count))
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", fenced_transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr("app.executor_reconciler.reconcile_executor_terminal_result", terminalize)
    monkeypatch.setattr(sandbox_lease_repository, "mark_sandbox_executor_reconciliation_cleanup_pending", release)

    processed = await reconcile_pending_executor_terminals_once(
        worker_id="worker-a",
        limit=8,
    )

    assert processed == 1
    assert [call[0] for call in calls] == [
        "has_claim",
        "get_run",
        "collect",
        "terminalize",
        *([] if creates_checkpoint else ["cleanup_pending"]),
    ]
    assert calls[0] == ("has_claim", 2)
    assert calls[1] == ("get_run", 2)
    assert calls[2] == ("collect", "lease-a", 2)
    assert calls[3][0] == "terminalize"
    assert calls[3][2] == 2
    if not creates_checkpoint:
        assert calls[4] == ("cleanup_pending", calls[3][1], "lease-a", 3)
    assert all(active is False for active in transaction_active.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_status", ["active", "released"])
async def test_terminal_run_scanner_transfers_attempt_marker_before_lease_finalize(
    monkeypatch, lease_status,
):
    calls = []
    row = _lease_row()
    scope = {key: row[key] for key in (
        "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
    )}
    stage = {
        "schema_version": "ai-platform.terminal-reconciliation.v1",
        "scope": scope,
        "owner_generation": 4,
        "container_id": "container-a",
        "terminal_result": {"run_id": "run-a", "status": "failed"},
        "continuation": {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
            "stop_evidence": "provider_stopped",
        },
    }
    row.update(
        settlement_storage_version="attempt.v1",
        status=lease_status,
        released_at="2026-10-28T00:00:00Z" if lease_status == "released" else None,
        runtime_container_id="container-a",
        lease_payload_json={
            "attempt_id": "attempt-a",
            "owner_generation": 4,
            "terminal_reconciliation": stage,
        },
    )

    class LeasePort:
        async def claim_sandbox_executor_reconciliations(self, _conn, **kwargs):
            row["executor_reconciliation_claim_token"] = kwargs["claim_token"]
            return [row]

        async def has_sandbox_executor_reconciliation_claim(self, _conn, **kwargs):
            return kwargs["claim_token"] == row["executor_reconciliation_claim_token"]

        async def record_sandbox_reconciliation_stage(self, _conn, **kwargs):
            calls.append(("transfer", kwargs["claim_token"], kwargs["stage"]))
            return True

        async def finalize_stopped_executor_checkpoint(self, _conn, **kwargs):
            calls.append(("finalize", kwargs["claim_token"], kwargs["expected_stage"]))
            return True

    port = LeasePort()

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "failed"}

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(
        "app.executor_reconciler._collect_workspace_and_convert_result",
        lambda *_args, **_kwargs: pytest.fail("a terminal Run only needs lease finalization"),
    )

    assert await reconcile_pending_executor_terminals_once(
        lease_port=port,
        attempt_lifecycle=_TEST_ATTEMPT_LIFECYCLE,
        lifecycle=_TEST_RUN_LIFECYCLE,
    ) == 1

    assert len(calls) == 2
    assert calls[0] == ("transfer", row["executor_reconciliation_claim_token"], stage)
    assert calls[1] == ("finalize", row["executor_reconciliation_claim_token"], stage)


@pytest.mark.asyncio
async def test_reconciler_cancellation_releases_entire_claimed_batch(monkeypatch):
    claim_tokens = []
    retried = []

    async def claim(_conn, **kwargs):
        claim_tokens.append(kwargs["claim_token"])
        second = {**_lease_row(), "id": "lease-b", "run_id": "run-b"}
        return [_lease_row(), second]

    async def get_run(_conn, **_kwargs):
        raise __import__("asyncio").CancelledError

    async def retry(_conn, **kwargs):
        retried.append(kwargs)
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(
        sandbox_lease_repository, "retry_sandbox_executor_reconciliation",
        retry,
    )

    with pytest.raises(__import__("asyncio").CancelledError):
        await reconcile_pending_executor_terminals_once(worker_id="worker-a")

    assert claim_tokens and retried == [
        {
            "lease_id": "lease-a",
            "claim_token": claim_tokens[0],
            "error": "reconciler_cancelled",
        },
        {
            "lease_id": "lease-b",
            "claim_token": claim_tokens[0],
            "error": "reconciler_cancelled",
        },
    ]


@pytest.mark.asyncio
async def test_reconciler_requeues_receipt_after_transient_failure(monkeypatch):
    retried = []
    row = _lease_row()
    row["executor_terminal_reconciliation_attempt_count"] = 4

    async def claim(_conn, **_kwargs):
        return [row]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        raise RuntimeError("temporary workspace failure")

    async def retry(_conn, **kwargs):
        retried.append(kwargs)
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr(
        sandbox_lease_repository, "retry_sandbox_executor_reconciliation",
        retry,
    )

    processed = await reconcile_pending_executor_terminals_once(worker_id="worker-a")

    assert processed == 1
    assert len(retried) == 1
    assert retried[0]["lease_id"] == "lease-a"
    assert retried[0]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_reconciler_times_out_work_before_stale_claim_takeover(monkeypatch):
    from app import executor_reconciler

    assert (
        executor_reconciler._RECONCILIATION_WORK_TIMEOUT_SECONDS
        < executor_reconciler._RECONCILIATION_CLAIM_STALE_SECONDS
    )
    transaction_active = False
    collection_cancelled = asyncio.Event()
    retried = []

    @asynccontextmanager
    async def fenced_transaction():
        nonlocal transaction_active
        assert transaction_active is False
        transaction_active = True
        try:
            yield _Connection()
        finally:
            transaction_active = False

    async def claim(_conn, **_kwargs):
        assert transaction_active is True
        return [_lease_row()]

    async def get_run(_conn, **_kwargs):
        assert transaction_active is True
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        assert transaction_active is False
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            collection_cancelled.set()
            raise

    async def retry(_conn, **kwargs):
        assert transaction_active is True
        retried.append(kwargs)
        return True

    monkeypatch.setattr(executor_reconciler, "transaction", fenced_transaction)
    monkeypatch.setattr(
        executor_reconciler,
        "_RECONCILIATION_WORK_TIMEOUT_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        sandbox_lease_repository,
        "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run', get_run)
    monkeypatch.setattr(executor_reconciler, "_collect_workspace_and_convert_result", collect)
    monkeypatch.setattr(
        sandbox_lease_repository,
        "retry_sandbox_executor_reconciliation",
        retry,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_executor_terminal_result",
        lambda **_kwargs: pytest.fail("timed-out collection must not terminalize the Run"),
    )
    monkeypatch.setattr(
        executor_reconciler,
        "_mark_reconciliation_for_cleanup",
        lambda *_args, **_kwargs: pytest.fail("timed-out collection must not hand off cleanup"),
    )

    processed = await reconcile_pending_executor_terminals_once(worker_id="worker-a")

    assert processed == 1
    assert collection_cancelled.is_set()
    assert transaction_active is False
    assert len(retried) == 1
    assert retried[0]["lease_id"] == "lease-a"
    assert retried[0]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_reconciler_keeps_transient_failures_eligible_after_many_attempts(monkeypatch):
    from app import executor_reconciler

    rows = []
    for attempt_count in (500, 501):
        row = _lease_row()
        row["executor_terminal_reconciliation_attempt_count"] = attempt_count
        rows.append(row)
    retried = []
    collect_calls = 0

    async def claim(_conn, **_kwargs):
        return [rows.pop(0)]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        nonlocal collect_calls
        collect_calls += 1
        raise RuntimeError("terminal work failed")

    async def retry(_conn, **kwargs):
        retried.append(kwargs)
        return True

    monkeypatch.setattr(executor_reconciler, "transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository,
        "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run', get_run)
    monkeypatch.setattr(executor_reconciler, "_collect_workspace_and_convert_result", collect)
    monkeypatch.setattr(
        executor_reconciler,
        "_finish_terminal_reconciliation_failure",
        lambda *_args, **_kwargs: pytest.fail("transient failure must remain retryable"),
    )
    monkeypatch.setattr(
        sandbox_lease_repository,
        "retry_sandbox_executor_reconciliation",
        retry,
    )

    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert collect_calls == 2
    assert [item["error"] for item in retried] == ["RuntimeError", "RuntimeError"]


@pytest.mark.asyncio
async def test_reconciler_drains_ready_rows_without_idle_wait(monkeypatch):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    scan_results = iter((1, 1, 1, 0))
    scans = []

    async def reconcile(**_kwargs):
        result = next(scan_results)
        scans.append(result)
        if result == 0:
            stop_event.set()
        return result

    async def no_work(**_kwargs):
        return []

    async def no_probe(**_kwargs):
        return 0

    async def unexpected_wait(**_kwargs):
        pytest.fail("ready reconciliation rows must drain before the idle wait")

    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        no_work,
    )
    monkeypatch.setattr(executor_reconciler, "probe_suspect_executor_tasks_once", no_probe)
    monkeypatch.setattr(executor_reconciler, "wait_for_executor_reconciliation_signal", unexpected_wait)

    await run_executor_terminal_reconciler(stop_event, worker_id="worker-a")

    assert scans == [1, 1, 1, 0]


@pytest.mark.asyncio
async def test_reconciler_waits_when_no_terminal_or_probe_progress(monkeypatch):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    calls = []

    async def retry_only(**_kwargs):
        calls.append("reconcile")
        return 0

    async def no_work(**_kwargs):
        calls.append("cleanup")
        return []

    async def running_probe(**_kwargs):
        calls.append("probe")
        return 0

    async def wait(**_kwargs):
        while "reconcile" not in calls:
            await asyncio.sleep(0)
        calls.append("wait")
        stop_event.set()

    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        retry_only,
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        no_work,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "probe_suspect_executor_tasks_once",
        running_probe,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "wait_for_executor_reconciliation_signal",
        wait,
    )

    await run_executor_terminal_reconciler(stop_event, worker_id="worker-a")

    assert set(calls) == {"reconcile", "cleanup", "probe", "wait"}
    assert calls.count("reconcile") <= 2


@pytest.mark.asyncio
async def test_reconciler_observes_signal_written_during_empty_database_scan(monkeypatch):
    from app import executor_reconciler
    from app.runtime.sandbox import executor_signals

    class SignalRedis:
        def __init__(self):
            self.entries = [("4-0", {"wake": "1"})]
            self.xread_after_ids: list[str] = []

        async def xrevrange(self, _key, *, max, min, count):
            assert (max, min, count) == ("+", "-", 1)
            return self.entries[-count:]

        async def xread(self, streams, *, count, block):
            del block
            key, after_id = next(iter(streams.items()))
            self.xread_after_ids.append(after_id)
            after = tuple(int(part) for part in after_id.split("-"))
            available = [
                entry
                for entry in self.entries
                if tuple(int(part) for part in entry[0].split("-")) > after
            ][:count]
            return [(key, available)] if available else []

        async def aclose(self):
            return None

    initialized = asyncio.Event()

    async def initialize():
        cursor = await executor_signals.initialize_executor_reconciliation_signal_cursor()
        initialized.set()
        return cursor

    stop_event = asyncio.Event()
    client = SignalRedis()
    calls: list[str] = []
    scan_count = 0

    async def reconcile(**_kwargs):
        nonlocal scan_count
        scan_count += 1
        calls.append(f"reconcile-{scan_count}")
        if scan_count == 1:
            await initialized.wait()
            client.entries.append(("5-0", {"wake": "1"}))
            return 0
        stop_event.set()
        return 1

    async def no_work(**_kwargs):
        calls.append("cleanup")
        return []

    async def no_probe(**_kwargs):
        calls.append("probe")
        return 0

    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)
    monkeypatch.setattr(
        executor_reconciler,
        "initialize_executor_reconciliation_signal_cursor",
        initialize,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "wait_for_executor_reconciliation_signal",
        executor_signals.wait_for_executor_reconciliation_signal,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        reconcile,
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        no_work,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "probe_suspect_executor_tasks_once",
        no_probe,
    )

    await run_executor_terminal_reconciler(stop_event, concurrency=1, worker_id="worker-a")

    assert "4-0" in client.xread_after_ids
    assert scan_count == 2
    assert set(calls) == {"reconcile-1", "cleanup", "probe", "reconcile-2"}


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_available", [True, False])
async def test_reconciler_immediately_processes_terminal_persisted_by_probe(monkeypatch, redis_available):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    next_probe_started = asyncio.Event()
    finish_next_probe = asyncio.Event()
    next_probe_finished = asyncio.Event()
    calls: list[str] = []

    async def reconcile(**_kwargs):
        if "reconcile" in calls:
            # Observe consumption while the next batch is still blocked, not
            # only when the entire probe backlog has finished.
            await next_probe_started.wait()
            assert not next_probe_finished.is_set()
            stop_event.set()
            finish_next_probe.set()
        calls.append("reconcile")
        return 0

    async def cleanup(**_kwargs):
        calls.append("cleanup")
        return []

    async def persisted_probe(**_kwargs):
        calls.append("probe")
        if calls.count("probe") == 1:
            return 1
        next_probe_started.set()
        try:
            await finish_next_probe.wait()
        finally:
            next_probe_finished.set()

    async def idle_signal(**_kwargs):
        await asyncio.Event().wait()

    async def unavailable_cursor():
        raise ExecutorSignalUnavailable("redis unavailable")

    if not redis_available:
        monkeypatch.setattr(
            executor_reconciler, "initialize_executor_reconciliation_signal_cursor",
            unavailable_cursor,
        )

    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        reconcile,
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        cleanup,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "probe_suspect_executor_tasks_once",
        persisted_probe,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "wait_for_executor_reconciliation_signal",
        idle_signal,
    )

    await asyncio.wait_for(
        run_executor_terminal_reconciler(stop_event, concurrency=1, worker_id="worker-a"), timeout=1,
    )

    assert calls.count("reconcile") == 2
    assert calls.count("probe") == 2
    assert calls.count("cleanup") == 1
    assert next_probe_finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_available", [True, False])
async def test_reconciler_drains_receiptless_terminal_backlog_before_probe_idle(
    monkeypatch, redis_available,
):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    backlog = [
        {**_suspect_lease_row(), "id": f"lease-{index}", "run_id": f"run-{index}"}
        for index in range(20)
    ]
    expected_runs = [str(row["run_id"]) for row in backlog]
    claims = {}
    pending = []
    reconciled = []
    cleanup_calls = 0
    active_probes = 0
    max_active_probes = 0

    async def claim(_conn, *, claim_token, limit, **_kwargs):
        assert limit == 1
        if not backlog:
            return []
        row = backlog.pop(0)
        claims[row["id"]] = claim_token
        return [row]

    async def record(_conn, *, lease_id, run_id, claim_token, **_kwargs):
        assert claims[lease_id] == claim_token
        pending.append(run_id)
        return {"id": lease_id}

    async def get_run(_conn, **_kwargs):
        return {"status": "running"}

    class Provider:
        async def executor_control_endpoint(self, _lease, _request):
            return "http://executor", {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def get_status(self, _url, *, run_id, **_kwargs):
            nonlocal active_probes, max_active_probes
            active_probes += 1
            max_active_probes = max(max_active_probes, active_probes)
            try:
                await asyncio.sleep(0)
                return {
                    "status": "completed",
                    "terminal_result": {
                        "run_id": run_id, "status": "completed", "message": "done",
                    },
                }
            finally:
                active_probes -= 1

    async def reconcile(**_kwargs):
        count = len(pending)
        reconciled.extend(pending)
        pending.clear()
        if len(reconciled) == len(expected_runs):
            stop_event.set()
        return count

    async def cleanup(**_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        return ["released-lease"] if cleanup_calls == 1 else []

    async def idle_signal(**_kwargs):
        await asyncio.Event().wait()

    async def unavailable_cursor():
        raise ExecutorSignalUnavailable("redis unavailable")

    if not redis_available:
        monkeypatch.setattr(
            executor_reconciler, "initialize_executor_reconciliation_signal_cursor",
            unavailable_cursor,
        )
    monkeypatch.setattr(executor_reconciler, "_RECONCILIATION_IDLE_SECONDS", 60)
    monkeypatch.setattr(executor_reconciler, "transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_suspects", claim,
    )
    monkeypatch.setattr(sandbox_lease_repository, "record_sandbox_executor_terminal", record)
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, "get_run", get_run)
    monkeypatch.setattr(executor_reconciler, "_context_payload", lambda _row, **_kwargs: ({}, object()))
    monkeypatch.setattr(executor_reconciler, "_reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(executor_reconciler, "create_container_provider", lambda _name: Provider())
    monkeypatch.setattr(executor_reconciler, "SandboxExecutorClient", Client)
    monkeypatch.setattr(executor_reconciler, "reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases", cleanup,
    )
    monkeypatch.setattr(executor_reconciler, "wait_for_executor_reconciliation_signal", idle_signal)

    await asyncio.wait_for(run_executor_terminal_reconciler(stop_event), timeout=2)

    assert reconciled == expected_runs
    assert cleanup_calls >= 2
    assert max_active_probes == 1
    assert active_probes == 0


@pytest.mark.asyncio
async def test_reconciler_scans_postgres_when_redis_wakeup_is_unavailable(monkeypatch):
    stop_event = __import__("asyncio").Event()
    calls = []

    async def reconcile(**kwargs):
        calls.append(kwargs["worker_id"])
        return 0

    async def cleanup(**_kwargs):
        calls.append("cleanup")
        while "worker-a" not in calls:
            await asyncio.sleep(0)
        stop_event.set()
        return []

    async def unavailable(**_kwargs):
        raise ExecutorSignalUnavailable("redis unavailable")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr("app.executor_reconciler.reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(
        "app.executor_reconciler._sandbox_cleanup.cleanup_failed_sandbox_executor_reconciliation_leases",
        cleanup,
    )
    monkeypatch.setattr("app.executor_reconciler.wait_for_executor_reconciliation_signal", unavailable)

    await run_executor_terminal_reconciler(
        stop_event,
        worker_id="worker-a",
    )

    assert set(calls) == {"worker-a", "cleanup"}


@pytest.mark.asyncio
async def test_reconciler_cursor_failure_uses_polling_without_uncursored_xread(
    monkeypatch,
):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    calls: list[str] = []

    async def unavailable_cursor():
        calls.append("cursor")
        raise ExecutorSignalUnavailable("redis unavailable")

    async def reconcile(**_kwargs):
        calls.append("reconcile")
        return 0

    async def cleanup(**_kwargs):
        calls.append("cleanup")
        stop_event.set()
        return []

    async def no_probe(**_kwargs):
        calls.append("probe")
        return 0

    async def unexpected_wait(**_kwargs):
        pytest.fail("cursor initialization failure must not fall back to XREAD '$'")

    monkeypatch.setattr(
        executor_reconciler,
        "initialize_executor_reconciliation_signal_cursor",
        unavailable_cursor,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        reconcile,
    )
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup, "cleanup_failed_sandbox_executor_reconciliation_leases",
        cleanup,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "probe_suspect_executor_tasks_once",
        no_probe,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "wait_for_executor_reconciliation_signal",
        unexpected_wait,
    )

    await run_executor_terminal_reconciler(stop_event, concurrency=1, worker_id="worker-a")

    assert set(calls) == {"cursor", "reconcile", "cleanup", "probe"}
    assert calls.count("reconcile") == 1


@pytest.mark.asyncio
async def test_reconciler_bounds_signal_init_without_delaying_database_scan(monkeypatch):
    from app import executor_reconciler

    stop_event = asyncio.Event()
    cursor_started = asyncio.Event()
    cursor_cancelled = asyncio.Event()
    scans: list[str] = []

    async def stalled_cursor():
        cursor_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cursor_cancelled.set()

    async def reconcile(**_kwargs):
        scans.append("reconcile")
        stop_event.set()
        return 0

    monkeypatch.setattr(
        executor_reconciler,
        "_RECONCILIATION_SIGNAL_INIT_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "initialize_executor_reconciliation_signal_cursor",
        stalled_cursor,
    )
    monkeypatch.setattr(
        executor_reconciler,
        "reconcile_pending_executor_terminals_once",
        reconcile,
    )

    await run_executor_terminal_reconciler(stop_event, concurrency=1, worker_id="worker-a")

    assert cursor_started.is_set()
    assert cursor_cancelled.is_set()
    assert scans == ["reconcile"]


@pytest.mark.asyncio
async def test_claim_check_locks_and_fences_current_reconciler():
    observed = {}

    class Cursor:
        async def fetchone(self):
            return {"id": "lease-a"}

    class Connection:
        async def execute(self, sql, params):
            observed["sql"] = sql
            observed["params"] = params
            return Cursor()

    claimed = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
        Connection(),
        lease_id="lease-a",
        claim_token="claim-a",
    )

    assert claimed is True
    assert "for update" in observed["sql"].lower()
    assert "status in ('active', 'released')" in observed["sql"].lower()
    assert observed["params"] == ("lease-a", "claim-a")


@pytest.mark.asyncio
async def test_claim_read_inside_owner_lock_does_not_take_a_second_row_lock():
    observed = {}

    class Cursor:
        async def fetchone(self):
            return {"id": "lease-a"}

    class Connection:
        async def execute(self, sql, params):
            observed["sql"] = sql
            observed["params"] = params
            return Cursor()

    claimed = await sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current(
        Connection(),
        lease_id="lease-a",
        claim_token="claim-a",
    )

    assert claimed is True
    assert "for update" not in observed["sql"].lower()
    assert observed["params"] == ("lease-a", "claim-a")


@pytest.mark.asyncio
async def test_probe_and_terminal_claims_use_independent_attempt_counters():
    statements = []

    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, sql, _params):
            statements.append(" ".join(sql.split()).lower())
            return Cursor()

    conn = Connection()
    await sandbox_lease_repository.claim_sandbox_executor_suspects(
        conn,
        claim_token="probe-claim",
        limit=1,
        stale_after_seconds=45,
    )
    await sandbox_lease_repository.claim_sandbox_executor_reconciliations(
        conn,
        claim_token="terminal-claim",
        limit=1,
        stale_after_seconds=300,
    )

    assert "executor_reconciliation_attempt_count = executor_reconciliation_attempt_count + 1" in statements[0]
    assert "executor_terminal_reconciliation_attempt_count" not in statements[0]
    assert statements[0].count("make_interval(secs => %s)") == 4
    assert (
        "lease.executor_probe_claim_token is null and "
        "coalesce(lease.executor_heartbeat_at, lease.updated_at, lease.created_at) "
        "< now() - make_interval(secs => %s)"
    ) in statements[0]
    assert (
        "lease.executor_probe_claim_token is not null and "
        "coalesce(lease.executor_probe_claimed_at, lease.updated_at, lease.created_at) "
        "< now() - make_interval(secs => %s)"
    ) in statements[0]
    assert "executor_reconciliation_status = 'claimed'" in statements[0]
    assert "executor_reconciliation_claimed_at" in statements[0]
    assert "executor_terminal_reconciliation_attempt_count = executor_terminal_reconciliation_attempt_count + 1" in statements[1]
    assert "status in ('active', 'released')" in statements[1]
    assert "order by coalesce(executor_terminal_received_at, created_at) asc, id asc" in statements[1]
    assert "executor_reconciliation_status = 'retry'" in statements[1]
    assert "executor_reconciliation_retry_not_before" in statements[1]
    assert "case executor_reconciliation_status" not in statements[1]
    assert "updated_at <=" not in statements[1]
    assert "executor_reconciliation_attempt_count = executor_reconciliation_attempt_count + 1" not in statements[1]


@pytest.mark.asyncio
async def test_probe_terminal_receipt_rejects_a_stale_claim_token():
    statements = []
    responses = [
        {
            "id": "lease-a",
            "executor_status": "running",
            "executor_terminal_json": None,
        },
        None,
    ]

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params):
            statements.append((" ".join(sql.split()).lower(), params))
            return Cursor(responses.pop(0))

    with pytest.raises(
        sandbox_lease_repository.SandboxExecutorTerminalConflictError,
        match="sandbox_executor_terminal_conflict",
    ):
        await sandbox_lease_repository.record_sandbox_executor_terminal(
            Connection(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            lease_id="lease-a",
            executor_status="failed",
            terminal_result={"run_id": "run-a", "status": "failed"},
            claim_token="stale-probe-claim",
        )

    update_sql, update_params = statements[1]
    assert "executor_reconciliation_status = 'claimed'" in update_sql
    assert "executor_reconciliation_claim_token = %s" in update_sql
    assert update_params[-2:] == ("stale-probe-claim", "stale-probe-claim")


@pytest.mark.asyncio
async def test_probe_terminal_receipt_is_claim_fenced_when_receipt_matches():
    statements = []
    terminal_result = {"run_id": "run-a", "status": "failed"}

    class Cursor:
        async def fetchone(self):
            return {
                "id": "lease-a",
                "executor_status": "failed",
                "executor_terminal_json": terminal_result,
                "executor_reconciliation_claim_token": "newer-claim",
            }

    class Connection:
        async def execute(self, sql, params):
            statements.append((" ".join(sql.split()).lower(), params))
            return Cursor()

    with pytest.raises(
        sandbox_lease_repository.SandboxExecutorTerminalConflictError,
        match="sandbox_executor_terminal_conflict",
    ):
        await sandbox_lease_repository.record_sandbox_executor_terminal(
            Connection(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            lease_id="lease-a",
            executor_status="failed",
            terminal_result=terminal_result,
            claim_token="stale-probe-claim",
        )

    assert len(statements) == 1

    recorded = await sandbox_lease_repository.record_sandbox_executor_terminal(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        executor_status="failed",
        terminal_result=terminal_result,
        claim_token="newer-claim",
    )

    assert recorded["executor_reconciliation_claim_token"] == "newer-claim"
    assert len(statements) == 2


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (
            PermanentExecutorReconciliationError(
                "executor_reconciliation_run_payload_invalid"
            ),
            "executor_reconciliation_run_payload_invalid",
        ),
        (
            ProviderSessionConflictError(
                "provider_session_terminal_receipt_invalid"
            ),
            "provider_session_terminal_receipt_invalid",
        ),
        (
            ProviderSessionConflictError(
                "provider_session_terminal_coverage_invalid"
            ),
            "provider_session_terminal_coverage_invalid",
        ),
    ],
)
@pytest.mark.asyncio
async def test_reconciler_terminalizes_explicit_permanent_failure(
    monkeypatch, failure, expected_error
):
    finished = []
    retried = []
    row = _lease_row()
    row["executor_terminal_reconciliation_attempt_count"] = 1

    async def claim(_conn, **_kwargs):
        return [row]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        try:
            raise OSError("synthetic reconciliation cause")
        except OSError as cause:
            raise failure from cause

    async def finish(lease_row, **kwargs):
        finished.append((lease_row["id"], kwargs))

    async def retry(_conn, **kwargs):
        retried.append(kwargs)
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr("app.executor_reconciler._finish_terminal_reconciliation_failure", finish)
    monkeypatch.setattr(
        sandbox_lease_repository, "retry_sandbox_executor_reconciliation",
        retry,
    )

    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert len(finished) == 1
    assert finished[0][0] == "lease-a"
    assert finished[0][1]["claim_token"]
    assert finished[0][1]["error_code"] == expected_error
    assert finished[0][1]["reconciliation_error"] is failure
    assert retried == []


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostics_unavailable", [False, True])
async def test_terminal_reconciliation_failure_is_claim_fenced_and_published(
    monkeypatch, diagnostics_unavailable,
):
    calls = []
    try:
        raise OSError("/private/workspace token=synthetic-token Bearer synthetic-bearer")
    except OSError as cause:
        try:
            raise PermanentExecutorReconciliationError("artifact_manifest_invalid") from cause
        except PermanentExecutorReconciliationError as error:
            failure = error

    class Progress:
        did_transition = True
        status = "failed"

        @staticmethod
        def is_terminal():
            return True

    async def get_run(_conn, **kwargs):
        calls.append(("get_run", kwargs))
        return {"id": "run-a", "status": "running"}

    async def has_claim(_conn, **kwargs):
        calls.append(("has_claim", kwargs))
        return True

    async def fail_run(_conn, **kwargs):
        calls.append(("fail_run", kwargs))
        return Progress()

    class Persistence:
        async def append_observation(self, _conn, **kwargs):
            if diagnostics_unavailable:
                raise OSError("synthetic diagnostic write failure")
            assert kwargs["observation"]["stage"] == "terminalization"
            return {}

    diagnostics = RunDiagnosticsService(
        persistence=Persistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        exception_log_fields=safe_exception_log_fields,
    )

    class RecordingDiagnostics:
        async def capture_reconciliation_failure(self, _conn, **kwargs):
            calls.append(("diagnostics", kwargs))
            await diagnostics.capture_reconciliation_failure(_conn, **kwargs)

    async def terminalize_attempt(_conn, **kwargs):
        calls.append(("terminalize_attempt", kwargs))
        return {"id": kwargs["attempt_id"], "status": kwargs["status"]}

    async def publish(_transaction_factory, **kwargs):
        calls.append(("publish", kwargs))
        return True

    async def no_provider_lineage(_conn, **_kwargs):
        return None

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(f"{owner}.fail_run_terminalization", fail_run)
    monkeypatch.setattr(
        "app.runs.application.run_terminalization.release_provider_lineage",
        no_provider_lineage,
    )
    monkeypatch.setattr(_TEST_ATTEMPT_LIFECYCLE, "terminalize", terminalize_attempt)
    monkeypatch.setattr(f"{owner}.publish_run_event", publish)

    await _terminalize_reconciliation_failure(
        {
            "id": "lease-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": "rat-a",
            "executor_terminal_json": {
                "run_id": "run-a",
                "status": "failed",
                "error_code": "provider_timeout",
            },
        },
        claim_token="claim-a",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
        run_diagnostics=RecordingDiagnostics(),
        reconciliation_error_code="artifact_manifest_invalid",
        reconciliation_error=failure,
    )

    fail_call = next(value for name, value in calls if name == "fail_run")
    assert fail_call["error_code"] == "terminal_reconciliation_failed"
    assert fail_call["result_json"] == {
        "message": "Executor terminal reconciliation could not be completed.",
    }
    assert [name for name, _value in calls] == [
        "get_run",
        "has_claim",
        "diagnostics",
        "fail_run",
        "terminalize_attempt",
        "publish",
    ]
    diagnostic_call = next(value for name, value in calls if name == "diagnostics")
    assert diagnostic_call["attempt_id"] == "rat-a"
    assert diagnostic_call["terminal_result"]["error_code"] == "provider_timeout"
    assert diagnostic_call["reconciliation_error_code"] == "artifact_manifest_invalid"
    evidence = diagnostic_call["reconciliation_evidence_factory"]()
    assert [node["type"] for node in evidence["sdk"]["exception_chain"]] == [
        "PermanentExecutorReconciliationError", "OSError",
    ]
    assert evidence["sdk"]["exception_chain"][0]["relation"] == "cause"
    terminal_call = next(
        item[1]
        for item in calls
        if isinstance(item, tuple) and item[0] == "terminalize_attempt"
    )
    assert terminal_call == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "rat-a",
        "status": "failed",
        "terminal_reason": "executor_terminal_reconciliation_failed",
        "error_code": "terminal_reconciliation_failed",
    }


@pytest.mark.asyncio
async def test_terminal_reconciliation_failure_honors_existing_cancel_request(monkeypatch):
    calls = []

    class Progress:
        did_transition = True
        status = "cancelled"

        @staticmethod
        def is_terminal():
            return True

    async def has_claim(_conn, **_kwargs):
        calls.append("has_claim")
        return True

    async def get_run(_conn, **_kwargs):
        calls.append("get_run")
        return {
            "id": "run-a",
            "status": "running",
            "cancel_requested_at": "2026-08-30T00:00:00Z",
        }

    async def request_cancel(_conn, **kwargs):
        calls.append(("request_attempt_cancel", kwargs))
        return {"id": kwargs["attempt_id"], "status": "cancel_requested"}

    async def cancel_run(_conn, **kwargs):
        calls.append(("cancel_run", kwargs))
        return Progress()

    async def fail_run(*_args, **_kwargs):
        pytest.fail("cancel-requested reconciliation must not fail the Run")

    async def terminalize_attempt(_conn, **kwargs):
        calls.append(("terminalize_attempt", kwargs))
        return {"id": kwargs["attempt_id"], "status": kwargs["status"]}

    async def publish(_capabilities, **kwargs):
        calls.append(("publish", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(_TEST_ATTEMPT_LIFECYCLE, "request_cancel", request_cancel)
    monkeypatch.setattr(f"{owner}.cancel_run_terminalization", cancel_run)
    monkeypatch.setattr(f"{owner}.fail_run_terminalization", fail_run)
    monkeypatch.setattr(_TEST_ATTEMPT_LIFECYCLE, "terminalize", terminalize_attempt)
    monkeypatch.setattr(f"{owner}.publish_run_event", publish)

    await _terminalize_reconciliation_failure(
        {
            "id": "lease-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": "rat-a",
        },
        claim_token="claim-a",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert [call if isinstance(call, str) else call[0] for call in calls] == [
        "get_run",
        "has_claim",
        "request_attempt_cancel",
        "cancel_run",
        "terminalize_attempt",
        "publish",
    ]
    terminal_call = next(
        item[1]
        for item in calls
        if isinstance(item, tuple) and item[0] == "terminalize_attempt"
    )
    assert terminal_call == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "rat-a",
        "status": "cancelled",
        "terminal_reason": "run_cancelled",
        "error_code": None,
    }


@pytest.mark.asyncio
async def test_terminal_reconciliation_failure_does_not_republish_an_already_terminal_run(
    monkeypatch,
):
    calls = []

    async def has_claim(_conn, **_kwargs):
        calls.append("has_claim")
        return True

    async def get_run(_conn, **_kwargs):
        calls.append("get_run")
        return {"id": "run-a", "status": "failed"}

    async def fail_run(*_args, **_kwargs):
        pytest.fail("an already terminal Run must not be terminalized again")

    async def publish(*_args, **_kwargs):
        pytest.fail("a reconciliation failure must not republish another owner's terminal fact")

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(f"{owner}.fail_run_terminalization", fail_run)
    monkeypatch.setattr(f"{owner}.publish_run_event", publish)

    await _terminalize_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert calls == ["get_run", "has_claim"]


@pytest.mark.asyncio
async def test_terminal_reconciliation_failure_cannot_mutate_run_after_claim_loss(monkeypatch):
    calls = []

    async def get_run(_conn, **_kwargs):
        calls.append("get_run")
        return {"id": "run-a", "status": "running"}

    async def has_claim(_conn, **_kwargs):
        calls.append("has_claim")
        return False

    async def fail_run(*_args, **_kwargs):
        calls.append("fail_run")
        raise AssertionError("stale claimant must not fail the run")

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(f"{owner}.fail_run_terminalization", fail_run)

    with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
        await _terminalize_reconciliation_failure(
            {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
            claim_token="stale-claim",
            logger=logging.getLogger(__name__),
            v4_capabilities=_TEST_V4_CAPABILITIES,
        )

    assert calls == ["get_run", "has_claim"]


@pytest.mark.asyncio
async def test_failed_reconciliation_quarantines_only_unverifiable_runtime(monkeypatch):
    calls = []
    failure = ValueError("synthetic conversion error")

    async def terminalize(lease_row, **_kwargs):
        assert _kwargs["reconciliation_error"] is failure
        calls.append(("terminalize", lease_row["id"]))

    async def quarantine(_conn, **kwargs):
        calls.append(("quarantine", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(f"{owner}._terminalize_reconciliation_failure", terminalize)
    monkeypatch.setattr(f"{owner}._sandbox_cleanup.container_lease_from_persisted_row", lambda _row: None)
    monkeypatch.setattr(
        sandbox_lease_repository, "quarantine_sandbox_executor_reconciliation",
        quarantine,
    )

    await _finish_terminal_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        error_code="executor_reconciliation_runtime_handle_invalid",
        logger=logging.getLogger(__name__),
        reconciliation_error=failure,
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert calls == [
        (
            "quarantine",
            {
                "lease_id": "lease-a",
                "claim_token": "claim-a",
                "error": "executor_reconciliation_runtime_handle_invalid",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_terminal_failure_hands_off_cleanup_without_provider_io(monkeypatch):
    calls = []

    async def terminalize(lease_row, **_kwargs):
        calls.append(("terminalize", lease_row["id"]))

    class Provider:
        async def stop(self, _lease, *, reason):
            calls.append(("stop", reason))
            return SimpleNamespace(status="failed")

    def provider_factory(provider_name):
        calls.append(("provider", provider_name))
        return Provider()

    async def has_claim(_conn, **kwargs):
        calls.append(("has_claim", kwargs))
        return True

    async def cleanup_pending(_conn, **kwargs):
        calls.append(("cleanup_pending", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(f"{owner}._terminalize_reconciliation_failure", terminalize)
    monkeypatch.setattr(
        f"{owner}._sandbox_cleanup.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        f"{owner}.create_container_provider",
        provider_factory,
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(
        sandbox_lease_repository, "mark_sandbox_executor_reconciliation_cleanup_pending",
        cleanup_pending,
    )

    await _finish_terminal_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        error_code="RuntimeError",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert calls == [
        ("terminalize", "lease-a"),
        (
            "cleanup_pending",
            {"lease_id": "lease-a", "claim_token": "claim-a", "error": "RuntimeError"},
        ),
    ]


@pytest.mark.asyncio
async def test_reconciliation_repository_writes_are_claim_fenced_and_aggregate_only():
    statements = []
    responses = [
        {"id": "lease-a"},
        {"id": "lease-a"},
        {"id": "lease-a"},
        {
            "pending_receipt_count": 3,
            "released_pending_receipt_count": 1,
            "retry_receipt_count": 2,
            "retry_attempt_count": 7,
            "cleanup_pending_receipt_count": 4,
            "quarantined_receipt_count": 1,
            "max_attempt_count": 5,
            "oldest_pending_receipt_age_seconds": 901,
            "terminalization_slo_breach_count": 1,
        },
    ]

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params):
            statements.append((" ".join(sql.split()).lower(), params))
            return Cursor(responses.pop(0))

    conn = Connection()
    assert await sandbox_lease_repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        claim_token="claim-a",
        reason="executor_reconciled",
    )
    assert await sandbox_lease_repository.quarantine_sandbox_executor_reconciliation(
        conn,
        lease_id="lease-a",
        claim_token="claim-a",
        error="invalid_runtime",
    )
    assert await sandbox_lease_repository.mark_sandbox_executor_reconciliation_cleanup_pending(
        conn,
        lease_id="lease-a",
        claim_token="claim-a",
        error="RuntimeError",
    )
    summary = await sandbox_lease_repository.get_sandbox_executor_reconciliation_summary(
        conn,
        tenant_id="tenant-a",
        slo_seconds=900,
    )

    release_sql, release_params = statements[0]
    assert "set status = 'released'" in release_sql
    assert "executor_reconciliation_status = 'finalized'" in release_sql
    assert "executor_reconciliation_error = ''" in release_sql
    assert "executor_reconciliation_error = null" not in release_sql
    assert "executor_cleanup_claim_token = %s" in release_sql
    assert release_params[-2:] == ("lease-a", "claim-a")

    quarantine_sql, _ = statements[1]
    assert "set status = 'quarantined'" in quarantine_sql
    assert "status in ('active', 'released')" in quarantine_sql
    assert "released_at" not in quarantine_sql

    cleanup_sql, _ = statements[2]
    assert "executor_reconciliation_status = 'cleanup_pending'" in cleanup_sql
    assert "status in ('active', 'released')" in cleanup_sql
    assert "expires_at = least" in cleanup_sql
    assert "released_at" not in cleanup_sql

    assert "runtime_handle_verified_at" in " ".join(statement for statement, _ in statements)
    summary_sql, summary_params = statements[3]
    assert "where tenant_id = %s" in summary_sql
    assert "status in ('active', 'released')" in summary_sql
    assert "released_pending_receipt_count" in summary_sql
    assert summary_params == (900, "tenant-a")
    assert summary == {
        "pending_receipt_count": 3,
        "released_pending_receipt_count": 1,
        "retry_receipt_count": 2,
        "retry_attempt_count": 7,
        "cleanup_pending_receipt_count": 4,
        "quarantined_receipt_count": 1,
        "max_attempt_count": 5,
        "oldest_pending_receipt_age_seconds": 901,
        "terminalization_slo_seconds": 900,
        "terminalization_slo_breach_count": 1,
    }
    assert not {"run_id", "lease_id", "error"} & set(summary)


@pytest.mark.asyncio
async def test_failed_reconciliation_cleanup_repository_is_claim_fenced():
    statements = []
    lease_row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "sandbox_mode": "ephemeral",
        "provider": "fake",
        "runtime_container_id": "container-a",
        "runtime_container_name": "container-a",
        "runtime_executor_url": "http://executor.invalid",
        "runtime_workspace_container_path": "/workspace",
        "runtime_handle_verified_at": "2026-10-26T00:00:00Z",
    }
    responses = [[lease_row], {"id": "lease-a"}, {"id": "lease-a"}, lease_row, {"id": "lease-a"}]

    class Cursor:
        def __init__(self, response):
            self.response = response

        async def fetchone(self):
            return self.response

        async def fetchall(self):
            return self.response

    class Connection:
        async def execute(self, sql, params):
            statements.append((" ".join(sql.split()).lower(), params))
            return Cursor(responses.pop(0))

    conn = Connection()
    claimed = await sandbox_lease_repository.claim_failed_sandbox_executor_reconciliation_cleanups(
        conn,
        claim_token="cleanup-a",
        tenant_id="tenant-a",
        limit=8,
        stale_after_seconds=45,
    )
    assert claimed == [lease_row]
    from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
    recovered_lease = build_sandbox_runtime_cleanup().container_lease_from_persisted_row(
        claimed[0]
    )
    assert recovered_lease is not None
    assert recovered_lease.container_id == "container-a"
    assert await sandbox_lease_repository.has_failed_sandbox_executor_reconciliation_cleanup_claim(
        conn,
        lease_id="lease-a",
        claim_token="cleanup-a",
    )
    assert await sandbox_lease_repository.release_failed_sandbox_executor_reconciliation_cleanup_claim(
        conn,
        lease_id="lease-a",
        claim_token="cleanup-a",
        error="stop_failed",
    )
    finalized = await sandbox_lease_repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        claim_token="cleanup-a",
        reason="executor_reconciliation_cleanup",
    )
    assert finalized == lease_row
    assert await sandbox_lease_repository.quarantine_failed_sandbox_executor_reconciliation_cleanup(
        conn,
        lease_id="lease-a",
        claim_token="cleanup-a",
        error="invalid_runtime",
    )

    claim_sql, claim_params = statements[0]
    assert "status in ('active', 'released')" in claim_sql
    assert "executor_cleanup_status in ('failed', 'cleanup_pending', 'retry', 'claimed')" in claim_sql
    assert "executor_cleanup_retry_not_before" in claim_sql
    assert "executor_cleanup_claimed_at is null" in claim_sql
    assert "%s::text is null or lease.tenant_id = %s" in claim_sql
    assert "lease.runtime_handle_verified_at" in claim_sql
    assert "for update of lease skip locked" in claim_sql
    assert claim_params == (45, "tenant-a", "tenant-a", 8, "cleanup-a", "cleanup-a")

    has_claim_sql, _ = statements[1]
    assert "executor_cleanup_status = 'claimed'" in has_claim_sql
    assert "executor_cleanup_claim_token = %s" in has_claim_sql
    assert "for update" in has_claim_sql

    release_claim_sql, _ = statements[2]
    assert "executor_cleanup_status = 'claimed'" in release_claim_sql
    assert "executor_cleanup_retry_not_before = now() + interval '30 seconds'" in release_claim_sql
    assert "executor_cleanup_claim_token = null" in release_claim_sql
    assert "executor_cleanup_claimed_at = null" in release_claim_sql

    finalize_sql, finalize_params = statements[3]
    assert "set status = 'released'" in finalize_sql
    assert "executor_reconciliation_status = 'finalized'" in finalize_sql
    assert "executor_cleanup_status = 'claimed'" in finalize_sql
    assert "executor_cleanup_claim_token = %s" in finalize_sql
    assert finalize_params[-2:] == ("lease-a", "cleanup-a")

    quarantine_sql, _ = statements[4]
    assert "set status = 'quarantined'" in quarantine_sql
    assert "executor_cleanup_status = 'claimed'" in quarantine_sql
    assert "executor_cleanup_claim_token = %s" in quarantine_sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome_run_id", "expected_release"),
    [
        ("succeeded", "run-a", True),
        ("failed", "run-a", True),
        ("cancelled", "run-a", True),
        ("skipped", "run-a", False),
        ("running", "run-a", False),
        ("dead_letter", None, False),
        ("ownership_lost", "run-a", False),
        ("unexpected", "run-a", False),
        ("succeeded", "run-other", False),
        ("failed", "run-other", False),
        ("cancelled", None, False),
    ],
)
async def test_reconciler_hands_off_cleanup_only_for_exact_run_terminal_outcomes(
    monkeypatch, status, outcome_run_id, expected_release,
):
    row = _lease_row()
    calls = []

    async def claim(_conn, **kwargs):
        row["executor_reconciliation_claim_token"] = kwargs["claim_token"]
        return [row]

    async def has_claim(_conn, **kwargs):
        return kwargs == {
            "lease_id": row["id"],
            "claim_token": row["executor_reconciliation_claim_token"],
        }

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_row, **_kwargs):
        return _result(), object(), object()

    async def terminalize(**_kwargs):
        # The label must not turn a skipped outcome into a terminal fact.
        return WorkerOutcome(status, outcome_run_id, "stale_terminal_state")

    async def retry(_conn, **kwargs):
        calls.append(("retry", kwargs))
        return True

    async def release(_row, **_kwargs):
        calls.append(("cleanup_pending", {}))
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(sandbox_lease_repository, "claim_sandbox_executor_reconciliations", claim)
    monkeypatch.setattr(sandbox_lease_repository, "has_sandbox_executor_reconciliation_claim", has_claim)
    monkeypatch.setattr(sandbox_lease_repository, "retry_sandbox_executor_reconciliation", retry)
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, "get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr("app.executor_reconciler.reconcile_executor_terminal_result", terminalize)
    monkeypatch.setattr(sandbox_lease_repository, "mark_sandbox_executor_reconciliation_cleanup_pending", release)

    assert await reconcile_pending_executor_terminals_once() == 1
    if expected_release:
        assert calls == [("cleanup_pending", {})]
    else:
        assert calls == [("retry", {
            "lease_id": "lease-a",
            "claim_token": row["executor_reconciliation_claim_token"],
            "error": (
                "executor_reconciliation_outcome_run_mismatch"
                if outcome_run_id != "run-a"
                else "executor_reconciliation_run_not_terminal"
            ),
        })]


@pytest.mark.asyncio
async def test_cancel_maintenance_watchdog_allows_endpoint_recovery_before_writeback(monkeypatch):
    from app import executor_reconciler as module
    from app.bootstrap.worker_maintenance import maintenance_phase_until_done
    from app.sandbox.api import SandboxCancelDeliveryService

    stop = asyncio.Event()
    persisted = []
    row = {"run_id": "run-a", "attempt_id": "attempt-a", "cancel_delivery_claim_token": "claim-a"}

    class Repository:
        async def claim_due(self, _conn, **_kwargs):
            return [row], None

        async def record_delivery_result(self, _conn, **kwargs):
            persisted.append(kwargs)
            stop.set()
            return "accepted"

    async def slow_endpoint(_row):
        # Scale the outer watchdog only: this models 25 seconds of provider
        # work and distinguishes it from the retired 20-second wrapper budget.
        await asyncio.sleep(0.25)
        return {"status": "cancel_requested", "run_id": "run-a", "attempt_id": "attempt-a"}

    delivery = SandboxCancelDeliveryService(Repository(), _transaction, slow_endpoint)

    async def phase(name, operation, interval_seconds, phase_budget_seconds, **kwargs):
        if name != "executor-cancel-delivery":
            await asyncio.Event().wait()
        await maintenance_phase_until_done(
            name, operation, interval_seconds, phase_budget_seconds / 100, **kwargs,
        )

    async def wait_forever(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def no_domain_work(*_args, **_kwargs):
        return 0

    async def no_retry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "maintenance_phase_until_done", phase)
    monkeypatch.setattr(module, "initialize_executor_reconciliation_signal_cursor", wait_forever)
    monkeypatch.setattr(module, "run_worker_scheduled_domain_operation", no_domain_work)
    monkeypatch.setattr(module, "_next_terminal_retry_delay", no_retry)
    await asyncio.wait_for(run_executor_terminal_reconciler(
        stop, cancel_delivery=delivery,
    ), timeout=1)
    assert len(persisted) == 1
    assert persisted[0]["accepted"] is True
    assert persisted[0]["claimed_row"] == row

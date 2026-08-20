from contextlib import asynccontextmanager

import pytest

from app.executor_reconciler import (
    _context_payload,
    probe_suspect_executor_tasks_once,
    reconcile_pending_executor_terminals_once,
    run_executor_terminal_reconciler,
)
from app.runtime.sandbox.executor_signals import ExecutorSignalUnavailable
from app.executors.base import ExecutorResult
from app.worker import WorkerOutcome
from app.platform.postgres import sandbox_leases as sandbox_lease_repository


@asynccontextmanager
async def _transaction():
    yield object()


def _lease_row() -> dict[str, object]:
    return {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "executor_reconciliation_claim_token": "claim-a",
        "executor_terminal_json": {"status": "succeeded", "message": "done"},
    }


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
            },
            "metadata": {"agent_profile_expected": True},
        },
        "adapter_context": {},
    }

    context, payload = _context_payload(row, row["executor_terminal_json"])

    assert context["adapter_context"] == {}
    assert payload.run_id == "run-a"
    assert row["executor_terminal_json"]["diagnostics"] == [
        "agent_profile_transport_lost"
    ]


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
async def test_probe_releases_active_executor_for_future_heartbeat_checks(monkeypatch):
    released = []
    claim_tokens = []

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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler.container_lease_from_persisted_row", lambda _row: object()
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider", lambda: Provider()
    )
    monkeypatch.setattr("app.executor_reconciler.SandboxExecutorClient", Client)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 1
    assert claim_tokens and released == [
        {
            "lease_id": "lease-a",
            "claim_token": claim_tokens[0],
        }
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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler.container_lease_from_persisted_row", lambda _row: object()
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider", lambda: Provider()
    )
    monkeypatch.setattr(
        "app.executor_reconciler.is_authoritative_not_found_error", lambda _exc: True
    )
    monkeypatch.setattr("app.executor_reconciler._persist_probe_terminal", persist)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 1
    assert len(persisted) == 1
    assert persisted[0][0]["id"] == "lease-a"
    assert persisted[0][1] == {
        "executor_status": "failed",
        "terminal_result": {
            "run_id": "run-a",
            "status": "failed",
            "error_code": "sandbox_executor_lost",
            "error_message": "Sandbox executor stopped responding",
        },
    }
    assert released == []


@pytest.mark.asyncio
async def test_reconciler_owns_workspace_terminalization_and_release(monkeypatch):
    calls = []

    async def claim(_conn, **_kwargs):
        return [_lease_row()]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(lease_row, **_kwargs):
        calls.append(("collect", lease_row["id"]))
        return _result(), object(), object()

    async def terminalize(**kwargs):
        calls.append(("terminalize", kwargs["claim_token"]))
        return WorkerOutcome("succeeded", "run-a")

    async def release(_lease_row, **kwargs):
        calls.append(("release", kwargs["claim_token"]))

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr("app.executor_reconciler.reconcile_executor_terminal_result", terminalize)
    monkeypatch.setattr("app.executor_reconciler._release_reconciled_lease", release)

    processed = await reconcile_pending_executor_terminals_once(worker_id="worker-a")

    assert processed == 1
    assert calls[0] == ("collect", "lease-a")
    assert calls[1][0] == "terminalize"
    assert calls[2] == ("release", calls[1][1])


@pytest.mark.asyncio
async def test_reconciler_requeues_receipt_after_transient_failure(monkeypatch):
    retried = []

    async def claim(_conn, **_kwargs):
        return [_lease_row()]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        raise RuntimeError("temporary workspace failure")

    async def retry(_conn, **kwargs):
        retried.append(kwargs)
        return True

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.retry_sandbox_executor_reconciliation",
        retry,
    )

    processed = await reconcile_pending_executor_terminals_once(worker_id="worker-a")

    assert processed == 1
    assert len(retried) == 1
    assert retried[0]["lease_id"] == "lease-a"
    assert retried[0]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_reconciler_scans_postgres_when_redis_wakeup_is_unavailable(monkeypatch):
    stop_event = __import__("asyncio").Event()
    calls = []

    async def reconcile(**kwargs):
        calls.append(kwargs["worker_id"])
        stop_event.set()
        return 0

    async def unavailable(**_kwargs):
        raise ExecutorSignalUnavailable("redis unavailable")

    monkeypatch.setattr("app.executor_reconciler.reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr("app.executor_reconciler.wait_for_executor_reconciliation_signal", unavailable)

    await run_executor_terminal_reconciler(
        stop_event,
        worker_id="worker-a",
    )

    assert calls == ["worker-a"]


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
    assert observed["params"] == ("lease-a", "claim-a")

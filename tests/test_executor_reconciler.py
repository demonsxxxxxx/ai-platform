import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.executor_reconciler import (
    PermanentExecutorReconciliationError,
    SandboxReconciliationStopError,
    _context_payload,
    _finish_terminal_reconciliation_failure,
    _release_reconciled_lease,
    _terminalize_reconciliation_failure,
    probe_suspect_executor_tasks_once,
    reconcile_pending_executor_terminals_once,
    run_executor_terminal_reconciler,
)
from app.executors.base import ExecutorResult
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.sandbox.executor_signals import ExecutorSignalUnavailable
from app.worker import WorkerOutcome


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


class _EventPersistence:
    async def append_terminal_row(self, _conn, *, tenant_id, run_id):
        return None


_TEST_V4_CAPABILITIES = SimpleNamespace(event_persistence=_EventPersistence())


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


def test_reconciler_classifies_invalid_persisted_run_payload_as_permanent(monkeypatch):
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

    with pytest.raises(PermanentExecutorReconciliationError) as exc_info:
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

    with pytest.raises(PermanentExecutorReconciliationError) as exc_info:
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

    with pytest.raises(PermanentExecutorReconciliationError) as exc_info:
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
        calls.append(("finish", kwargs["error_code"]))

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr(
        "app.executor_reconciler.restored_sandbox_run_payload",
        lambda *_args: SimpleNamespace(**payload_identity),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
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

    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert calls == [("finish", "executor_reconciliation_identity_mismatch")]


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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        create_provider,
    )
    monkeypatch.setattr("app.executor_reconciler.SandboxExecutorClient", Client)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 1
    assert selected_provider_names == ["fake"]
    assert claim_tokens and released == [
        {
            "lease_id": "lease-a",
            "claim_token": claim_tokens[0],
        }
    ]


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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.create_container_provider",
        lambda _provider_name: Provider(),
    )
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.release_sandbox_executor_probe_claim",
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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_suspects",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler._context_payload", lambda _row: ({}, object()))
    monkeypatch.setattr("app.executor_reconciler._reconciliation_request", lambda *_args: object())
    monkeypatch.setattr(
        "app.executor_reconciler.container_lease_from_persisted_row",
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
        "app.executor_reconciler.sandbox_lease_repository.release_sandbox_executor_probe_claim",
        release,
    )

    processed = await probe_suspect_executor_tasks_once()

    assert processed == 1
    assert len(persisted) == 1
    assert persisted[0][0]["id"] == "lease-a"
    assert isinstance(persisted[0][1].pop("claim_token"), str)
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
async def test_reconciler_exits_claim_transaction_before_terminalization_and_release(monkeypatch):
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
        return _result(), object(), object()

    async def terminalize(**kwargs):
        assert all(active is False for active in transaction_active.values())
        assert kwargs["transaction_factory"] is fenced_transaction
        calls.append(("terminalize", kwargs["claim_token"], transaction_count))
        return WorkerOutcome("succeeded", "run-a")

    async def release(lease_row, **kwargs):
        assert all(active is False for active in transaction_active.values())
        assert kwargs["transaction_factory"] is fenced_transaction
        calls.append(("release", kwargs["claim_token"], lease_row["id"], transaction_count))

    monkeypatch.setattr("app.executor_reconciler.transaction", fenced_transaction)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr("app.executor_reconciler._collect_workspace_and_convert_result", collect)
    monkeypatch.setattr("app.executor_reconciler.reconcile_executor_terminal_result", terminalize)
    monkeypatch.setattr("app.executor_reconciler._release_reconciled_lease", release)

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
        "release",
    ]
    assert calls[0] == ("has_claim", 2)
    assert calls[1] == ("get_run", 2)
    assert calls[2] == ("collect", "lease-a", 2)
    assert calls[3][0] == "terminalize"
    assert calls[3][2] == 2
    assert calls[4] == ("release", calls[3][1], "lease-a", 2)
    assert all(active is False for active in transaction_active.values())


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
        "app.executor_reconciler.sandbox_lease_repository.claim_sandbox_executor_reconciliations",
        claim,
    )
    monkeypatch.setattr("app.executor_reconciler.repositories.get_run", get_run)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.retry_sandbox_executor_reconciliation",
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

    async def cleanup(**_kwargs):
        calls.append("cleanup")
        return []

    async def unavailable(**_kwargs):
        raise ExecutorSignalUnavailable("redis unavailable")

    monkeypatch.setattr("app.executor_reconciler.transaction", _transaction)
    monkeypatch.setattr("app.executor_reconciler.reconcile_pending_executor_terminals_once", reconcile)
    monkeypatch.setattr(
        "app.executor_reconciler.cleanup_failed_sandbox_executor_reconciliation_leases",
        cleanup,
    )
    monkeypatch.setattr("app.executor_reconciler.wait_for_executor_reconciliation_signal", unavailable)

    await run_executor_terminal_reconciler(
        stop_event,
        worker_id="worker-a",
    )

    assert calls == ["worker-a", "cleanup"]


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
    assert statements[0].count("make_interval(secs => %s)") == 2
    assert "executor_reconciliation_status = 'claimed'" in statements[0]
    assert "executor_reconciliation_claimed_at" in statements[0]
    assert "executor_terminal_reconciliation_attempt_count = executor_terminal_reconciliation_attempt_count + 1" in statements[1]
    assert "status in ('active', 'released')" in statements[1]
    assert "case executor_reconciliation_status when 'pending' then 0" in statements[1]
    assert "when 'claimed' then 1" in statements[1]
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


@pytest.mark.parametrize(
    ("failure", "attempt_count", "expected_error"),
    [
        (
            PermanentExecutorReconciliationError("executor_reconciliation_run_payload_invalid"),
            1,
            "executor_reconciliation_run_payload_invalid",
        ),
        (RuntimeError("transient failure exhausted"), 5, "RuntimeError"),
    ],
)
@pytest.mark.asyncio
async def test_reconciler_terminalizes_permanent_or_exhausted_failure(
    monkeypatch,
    failure,
    attempt_count,
    expected_error,
):
    finished = []
    retried = []
    row = _lease_row()
    row["executor_terminal_reconciliation_attempt_count"] = attempt_count

    async def claim(_conn, **_kwargs):
        return [row]

    async def get_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def collect(_lease_row, **_kwargs):
        raise failure

    async def finish(lease_row, **kwargs):
        finished.append((lease_row["id"], kwargs))

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
    monkeypatch.setattr("app.executor_reconciler._finish_terminal_reconciliation_failure", finish)
    monkeypatch.setattr(
        "app.executor_reconciler.sandbox_lease_repository.retry_sandbox_executor_reconciliation",
        retry,
    )

    assert await reconcile_pending_executor_terminals_once(worker_id="worker-a") == 1
    assert len(finished) == 1
    assert finished[0][0] == "lease-a"
    assert finished[0][1]["claim_token"]
    assert finished[0][1]["error_code"] == expected_error
    assert retried == []


@pytest.mark.asyncio
async def test_terminal_reconciliation_failure_is_claim_fenced_and_published(monkeypatch):
    calls = []

    class Progress:
        did_transition = True
        needs_reconcile = True

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

    async def reconcile_child(**kwargs):
        calls.append(("reconcile_child", kwargs))

    async def publish(_transaction_factory, **kwargs):
        calls.append(("publish", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(f"{owner}.repositories.get_run", get_run)
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(f"{owner}.repositories.fail_run", fail_run)
    monkeypatch.setattr(f"{owner}.reconcile_terminalized_permission_run", reconcile_child)
    monkeypatch.setattr(f"{owner}.publish_pending_run_terminal", publish)

    await _terminalize_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    fail_call = next(value for name, value in calls if name == "fail_run")
    assert fail_call["error_code"] == "terminal_reconciliation_failed"
    assert fail_call["result_json"] == {
        "message": "Executor terminal reconciliation could not be completed.",
    }
    assert [name for name, _value in calls] == [
        "has_claim",
        "get_run",
        "fail_run",
        "reconcile_child",
        "publish",
    ]


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
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(f"{owner}.repositories.get_run", get_run)
    monkeypatch.setattr(f"{owner}.repositories.fail_run", fail_run)
    monkeypatch.setattr(f"{owner}.publish_pending_run_terminal", publish)

    await _terminalize_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert calls == ["has_claim", "get_run"]


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
    monkeypatch.setattr(f"{owner}.repositories.get_run", get_run)
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(f"{owner}.repositories.fail_run", fail_run)

    with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
        await _terminalize_reconciliation_failure(
            {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
            claim_token="stale-claim",
            logger=logging.getLogger(__name__),
            v4_capabilities=_TEST_V4_CAPABILITIES,
        )

    assert calls == ["has_claim"]


@pytest.mark.asyncio
async def test_failed_reconciliation_quarantines_only_unverifiable_runtime(monkeypatch):
    calls = []

    async def terminalize(lease_row, **_kwargs):
        calls.append(("terminalize", lease_row["id"]))

    async def quarantine(_conn, **kwargs):
        calls.append(("quarantine", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(f"{owner}.transaction", _transaction)
    monkeypatch.setattr(f"{owner}._terminalize_reconciliation_failure", terminalize)
    monkeypatch.setattr(f"{owner}.container_lease_from_persisted_row", lambda _row: None)
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.quarantine_sandbox_executor_reconciliation",
        quarantine,
    )

    await _finish_terminal_reconciliation_failure(
        {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a"},
        claim_token="claim-a",
        error_code="executor_reconciliation_runtime_handle_invalid",
        logger=logging.getLogger(__name__),
        v4_capabilities=_TEST_V4_CAPABILITIES,
    )

    assert calls == [
        ("terminalize", "lease-a"),
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
async def test_verified_runtime_stop_failure_remains_eligible_for_cleanup(monkeypatch):
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
        f"{owner}.container_lease_from_persisted_row",
        lambda _row: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(
        f"{owner}.create_container_provider",
        provider_factory,
    )
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.mark_sandbox_executor_reconciliation_cleanup_pending",
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
        ("provider", "fake"),
        ("has_claim", {"lease_id": "lease-a", "claim_token": "claim-a"}),
        ("stop", "executor_reconciled"),
        (
            "cleanup_pending",
            {"lease_id": "lease-a", "claim_token": "claim-a", "error": "RuntimeError"},
        ),
    ]


@pytest.mark.asyncio
async def test_release_helper_stops_provider_between_claim_and_finalize_transactions(monkeypatch):
    calls = []
    transaction_state = {"active": False}
    connection = object()

    @asynccontextmanager
    async def fenced_transaction():
        assert transaction_state["active"] is False
        transaction_state["active"] = True
        calls.append("transaction_enter")
        try:
            yield connection
        finally:
            transaction_state["active"] = False
            calls.append("transaction_exit")

    class Provider:
        async def stop(self, lease, *, reason):
            assert transaction_state["active"] is False
            calls.append(("stop", lease, reason))
            return SimpleNamespace(status="stopped")

    async def has_claim(conn, **kwargs):
        assert conn is connection
        assert transaction_state["active"] is True
        calls.append(("has_claim", kwargs))
        return True

    async def release_and_finalize(conn, **kwargs):
        assert conn is connection
        assert transaction_state["active"] is True
        calls.append(("release_and_finalize", kwargs))
        return True

    owner = "app.executor_reconciler"
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.release_and_finalize_sandbox_executor_reconciliation",
        release_and_finalize,
    )

    lease = object()
    await _release_reconciled_lease(
        {
            "id": "lease-a",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "run_id": "run-a",
        },
        provider=Provider(),
        lease=lease,
        claim_token="claim-a",
        transaction_factory=fenced_transaction,
    )

    assert transaction_state["active"] is False
    assert calls == [
        "transaction_enter",
        ("has_claim", {"lease_id": "lease-a", "claim_token": "claim-a"}),
        "transaction_exit",
        ("stop", lease, "executor_reconciled"),
        "transaction_enter",
        (
            "release_and_finalize",
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "run_id": "run-a",
                "lease_id": "lease-a",
                "claim_token": "claim-a",
                "reason": "executor_reconciled",
            },
        ),
        "transaction_exit",
    ]


@pytest.mark.asyncio
async def test_release_helper_stop_timeout_closes_claim_transaction(monkeypatch):
    calls = []
    transaction_state = {"active": False}
    connection = object()
    stop_started = asyncio.Event()
    stop_cancelled = asyncio.Event()

    @asynccontextmanager
    async def fenced_transaction():
        transaction_state["active"] = True
        calls.append("transaction_enter")
        try:
            yield connection
        finally:
            transaction_state["active"] = False
            calls.append("transaction_exit")

    class Provider:
        async def stop(self, _lease, *, reason):
            assert transaction_state["active"] is False
            calls.append(("stop", reason))
            stop_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                stop_cancelled.set()
                raise

    async def has_claim(conn, **_kwargs):
        assert conn is connection
        assert transaction_state["active"] is True
        return True

    async def unexpected_finalize(*_args, **_kwargs):
        pytest.fail("timed-out provider stop must not finalize the lease")

    owner = "app.executor_reconciler"
    monkeypatch.setattr(
        f"{owner}.get_settings",
        lambda: SimpleNamespace(sandbox_cleanup_timeout_seconds=0.001),
    )
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.release_and_finalize_sandbox_executor_reconciliation",
        unexpected_finalize,
    )

    with pytest.raises(
        SandboxReconciliationStopError,
        match="executor_reconciliation_sandbox_stop_failed",
    ):
        await _release_reconciled_lease(
            {
                "id": "lease-a",
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "run_id": "run-a",
            },
            provider=Provider(),
            lease=object(),
            claim_token="claim-a",
            transaction_factory=fenced_transaction,
        )

    await stop_started.wait()
    await stop_cancelled.wait()
    assert transaction_state["active"] is False
    assert calls == [
        "transaction_enter",
        "transaction_exit",
        ("stop", "executor_reconciled"),
    ]


@pytest.mark.asyncio
async def test_release_helper_lost_claim_has_no_provider_side_effect(monkeypatch):
    calls = []

    class Provider:
        async def stop(self, _lease, *, reason):
            calls.append(("stop", reason))
            return SimpleNamespace(status="stopped")

    async def has_claim(_conn, **kwargs):
        calls.append(("has_claim", kwargs))
        return False

    owner = "app.executor_reconciler"
    monkeypatch.setattr(
        f"{owner}.sandbox_lease_repository.has_sandbox_executor_reconciliation_claim",
        has_claim,
    )

    with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
        await _release_reconciled_lease(
            {
                "id": "lease-a",
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "run_id": "run-a",
            },
            provider=Provider(),
            lease=object(),
            claim_token="stale-claim",
            transaction_factory=_transaction,
        )

    assert calls == [
        ("has_claim", {"lease_id": "lease-a", "claim_token": "stale-claim"})
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
    assert await sandbox_lease_repository.release_and_finalize_sandbox_executor_reconciliation(
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
    assert "executor_reconciliation_claim_token = %s" in release_sql
    assert release_params[-2:] == ("lease-a", "claim-a")

    quarantine_sql, _ = statements[1]
    assert "set status = 'quarantined'" in quarantine_sql
    assert "status in ('active', 'released')" in quarantine_sql
    assert "released_at" not in quarantine_sql

    cleanup_sql, _ = statements[2]
    assert "executor_reconciliation_status = 'failed'" in cleanup_sql
    assert "status in ('active', 'released')" in cleanup_sql
    assert "expires_at = least" in cleanup_sql
    assert "released_at" not in cleanup_sql

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
        "user_id": "user-a",
        "run_id": "run-a",
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
    assert "executor_reconciliation_status = 'failed'" in claim_sql
    assert "executor_reconciliation_claimed_at is null" in claim_sql
    assert "%s::text is null or tenant_id = %s" in claim_sql
    assert "for update skip locked" in claim_sql
    assert claim_params == (45, "tenant-a", "tenant-a", 8, "cleanup-a")

    has_claim_sql, _ = statements[1]
    assert "executor_reconciliation_status = 'failed'" in has_claim_sql
    assert "executor_reconciliation_claim_token = %s" in has_claim_sql
    assert "for update" in has_claim_sql

    release_claim_sql, _ = statements[2]
    assert "executor_reconciliation_status = 'failed'" in release_claim_sql
    assert "executor_reconciliation_claim_token = null" in release_claim_sql
    assert "executor_reconciliation_claimed_at = now()" in release_claim_sql

    finalize_sql, finalize_params = statements[3]
    assert "set status = 'released'" in finalize_sql
    assert "executor_reconciliation_status = 'finalized'" in finalize_sql
    assert "executor_reconciliation_status = 'failed'" in finalize_sql
    assert "executor_reconciliation_claim_token = %s" in finalize_sql
    assert finalize_params[-2:] == ("lease-a", "cleanup-a")

    quarantine_sql, _ = statements[4]
    assert "set status = 'quarantined'" in quarantine_sql
    assert "executor_reconciliation_status = 'failed'" in quarantine_sql
    assert "executor_reconciliation_claim_token = %s" in quarantine_sql

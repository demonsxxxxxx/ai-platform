from typing import Any

import pytest

import app.runs.infrastructure.postgres as run_attempt_repository
from app.platform.postgres.errors import RepositoryConflictError
from app.runs.domain.attempt_lifecycle import RunAttemptTransitionError
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.infrastructure.postgres import create_run_attempt, transition_run_attempt


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, params: tuple[Any, ...]):
        self.calls.append((sql, params))
        return _Cursor(self.row)


def _transition_kwargs(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "expected_status": "running",
        "requested_status": "succeeded",
        "expected_owner_kind": "queue_worker",
        "expected_owner_id": "worker-a",
        "expected_owner_generation": 3,
        "terminal_reason": "completed",
    }
    values.update(overrides)
    return values


def _execution_spec(**overrides):
    payload = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "run_payload_schema_version": "ai-platform.run-payload.v2",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
        "execution_kind": "harness_chat",
        "skill_id": None,
        "file_ids": [],
        "input": {"message": "hello"},
        "executor_type": "claude-agent-worker",
        "trace_id": "trace-a",
        "skill_version": "",
        "release_decision": {},
        "skill_manifests": [],
        "context_snapshot_id": "context-a",
        "context_snapshot": {"context_snapshot_id": "context-a"},
        "context_pack": {},
        "model_id": "model-a",
        "model_value": "model-a",
        "agent_profile": {},
    }
    payload.update(overrides)
    return compile_execution_spec(payload)


@pytest.mark.asyncio
async def test_create_run_attempt_binds_created_state_and_exact_canonical_spec():
    spec = _execution_spec()
    conn = _Connection({"id": "attempt-a", "status": "created", "owner_generation": 1})

    row = await create_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        ordinal=1,
        owner_kind="queue_worker",
        owner_id="worker-a",
        queue_attempt_id="queue-attempt-a",
        execution_spec=spec,
    )

    assert row["status"] == "created"
    sql, params = conn.calls[0]
    normalized_sql = " ".join(sql.split())
    assert "'created'" in normalized_sql
    assert "execution_spec_canonical_json" in normalized_sql
    assert sql.count("%s") == len(params)
    canonical_json = spec.canonical_json.decode("utf-8")
    assert params[-3:] == (canonical_json, canonical_json, spec.spec_sha256)


@pytest.mark.asyncio
async def test_create_run_attempt_rejects_spec_identity_drift_before_sql():
    conn = _Connection(None)

    with pytest.raises(
        ValueError,
        match="run_attempt_execution_spec_identity_mismatch",
    ):
        await create_run_attempt(
            conn,
            tenant_id="tenant-other",
            run_id="run-a",
            attempt_id="attempt-a",
            ordinal=1,
            owner_kind="queue_worker",
            owner_id="worker-a",
            queue_attempt_id="queue-attempt-a",
            execution_spec=_execution_spec(),
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_transition_run_attempt_uses_exact_owner_fenced_cas_and_trigger_projection():
    conn = _Connection(
        {
            "id": "attempt-a",
            "status": "succeeded",
            "owner_generation": 4,
        }
    )

    row = await transition_run_attempt(conn, **_transition_kwargs())

    assert row["owner_generation"] == 4
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert sql.count("%s") == len(params)
    normalized_sql = " ".join(sql.split())
    assert "with locked as materialized ( select run_attempts.id" in normalized_sql
    assert "for update of run_attempts, runs" in normalized_sql
    assert "runs.status in ('queued', 'running', %s)" in normalized_sql
    assert "and exists ( select 1 from locked" in normalized_sql
    assert "transitioned as ( update run_attempts" in normalized_sql
    assert "and status = %s and owner_kind = %s and owner_id = %s" in normalized_sql
    assert "and owner_generation = %s" in normalized_sql
    assert "projected as ( update runs" not in normalized_sql
    assert normalized_sql.endswith("select * from transitioned")
    assert params[:11] == (
        "tenant-a",
        "run-a",
        "attempt-a",
        "running",
        "queue_worker",
        "worker-a",
        3,
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    )
    assert params[11:15] == ("succeeded", "queue_worker", "worker-a", 4)
    assert params[-7:] == (
        "tenant-a",
        "run-a",
        "attempt-a",
        "running",
        "queue_worker",
        "worker-a",
        3,
    )


@pytest.mark.asyncio
async def test_transition_run_attempt_rejects_stale_cas_result():
    conn = _Connection(None)

    with pytest.raises(
        RepositoryConflictError,
        match="run_attempt_transition_conflict",
    ):
        await transition_run_attempt(conn, **_transition_kwargs())


@pytest.mark.asyncio
async def test_transition_run_attempt_idempotency_still_checks_owner_fence():
    conn = _Connection(
        {"id": "attempt-a", "status": "running", "owner_generation": 3}
    )

    row = await transition_run_attempt(
        conn,
        **_transition_kwargs(
            requested_status="running",
            terminal_reason="",
        ),
    )

    assert row["status"] == "running"
    sql, params = conn.calls[0]
    assert "select *" in sql
    assert "update run_attempts" not in sql
    assert params[-1] == 3


@pytest.mark.asyncio
async def test_transition_run_attempt_expiry_requires_reconciler_takeover():
    conn = _Connection(None)

    with pytest.raises(
        ValueError,
        match="run_attempt_expiry_reconciler_required",
    ):
        await transition_run_attempt(
            conn,
            **_transition_kwargs(
                requested_status="expired",
                terminal_reason="",
            ),
        )

    assert conn.calls == []

    conn = _Connection(
        {"id": "attempt-a", "status": "expired", "owner_generation": 4}
    )
    row = await transition_run_attempt(
        conn,
        **_transition_kwargs(
            requested_status="expired",
            next_owner_kind="reconciler",
            next_owner_id="reconciler-a",
            terminal_reason="",
        ),
    )

    assert row["status"] == "expired"
    assert conn.calls[0][1][11:15] == (
        "expired",
        "reconciler",
        "reconciler-a",
        4,
    )


@pytest.mark.asyncio
async def test_transition_run_attempt_rejects_illegal_edge_before_sql():
    conn = _Connection(None)

    with pytest.raises(
        RunAttemptTransitionError,
        match="run_attempt_transition_invalid",
    ):
        await transition_run_attempt(
            conn,
            **_transition_kwargs(
                expected_status="queued",
                requested_status="succeeded",
            ),
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_start_worker_run_attempt_advances_one_exact_owner_and_spec(monkeypatch):
    transitions = []
    created = []

    async def get_existing(*_args, **_kwargs):
        return None

    async def create(*_args, **kwargs):
        created.append(kwargs)
        return {
            "id": kwargs["attempt_id"],
            "status": "created",
            "owner_kind": kwargs["owner_kind"],
            "owner_id": kwargs["owner_id"],
            "owner_generation": 1,
        }

    async def transition(*_args, **kwargs):
        transitions.append(kwargs)
        return {
            "id": kwargs["attempt_id"],
            "status": kwargs["requested_status"],
            "owner_kind": kwargs["expected_owner_kind"],
            "owner_id": kwargs["expected_owner_id"],
            "owner_generation": kwargs["expected_owner_generation"] + 1,
        }

    monkeypatch.setattr(
        run_attempt_repository,
        "get_run_attempt_for_queue_attempt",
        get_existing,
    )
    monkeypatch.setattr(run_attempt_repository, "create_run_attempt", create)
    monkeypatch.setattr(run_attempt_repository, "transition_run_attempt", transition)
    conn = _Connection({"next_ordinal": 1})

    row = await run_attempt_repository.start_worker_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        queue_attempt_id="qat-a",
        worker_id="worker-a",
        execution_spec=_execution_spec(),
    )

    assert row["status"] == "running"
    assert row["owner_generation"] == 4
    assert created[0]["queue_attempt_id"] == "qat-a"
    assert created[0]["owner_id"] == "worker-a"
    assert created[0]["attempt_id"].startswith("rat_")
    assert [item["requested_status"] for item in transitions] == [
        "queued",
        "claimed",
        "running",
    ]


@pytest.mark.asyncio
async def test_worker_attempt_fence_rejects_a_different_worker_owner(monkeypatch):
    async def get_attempt(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": "running",
            "queue_attempt_id": "qat-a",
            "owner_kind": "queue_worker",
            "owner_id": "worker-other",
            "owner_generation": 4,
        }

    monkeypatch.setattr(
        run_attempt_repository,
        "get_run_attempt_for_queue_attempt",
        get_attempt,
    )

    with pytest.raises(
        RepositoryConflictError,
        match="run_attempt_worker_authority_stale",
    ):
        await run_attempt_repository.assert_worker_run_attempt_current(
            object(),
            tenant_id="tenant-a",
            run_id="run-a",
            queue_attempt_id="qat-a",
            worker_id="worker-a",
        )


@pytest.mark.asyncio
async def test_cancel_request_preserves_execution_owner_and_advances_generation(monkeypatch):
    transitions = []

    async def get_attempt(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": "running",
            "queue_attempt_id": "qat-a",
            "owner_kind": "queue_worker",
            "owner_id": "worker-a",
            "owner_generation": 4,
        }

    async def transition(*_args, **kwargs):
        transitions.append(kwargs)
        return {
            "id": kwargs["attempt_id"],
            "status": kwargs["requested_status"],
            "owner_kind": kwargs["expected_owner_kind"],
            "owner_id": kwargs["expected_owner_id"],
            "owner_generation": kwargs["expected_owner_generation"] + 1,
        }

    monkeypatch.setattr(run_attempt_repository, "get_run_attempt", get_attempt)
    monkeypatch.setattr(run_attempt_repository, "transition_run_attempt", transition)

    row = await run_attempt_repository.request_run_attempt_cancel(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="rat-a",
    )

    assert row == {
        "id": "rat-a",
        "status": "cancel_requested",
        "owner_kind": "queue_worker",
        "owner_id": "worker-a",
        "owner_generation": 5,
    }
    assert transitions[0]["expected_owner_generation"] == 4
    assert transitions[0]["next_owner_kind"] is None
    assert transitions[0]["next_owner_id"] is None


@pytest.mark.asyncio
async def test_cancel_request_reconciler_takes_over_existing_request(monkeypatch):
    async def get_attempt(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": "cancel_requested",
            "queue_attempt_id": "qat-a",
            "owner_kind": "queue_worker",
            "owner_id": "worker-a",
            "owner_generation": 5,
        }

    monkeypatch.setattr(run_attempt_repository, "get_run_attempt", get_attempt)
    conn = _Connection(
        {
            "id": "rat-a",
            "status": "cancel_requested",
            "owner_kind": "reconciler",
            "owner_id": "stale-run-maintenance",
            "owner_generation": 6,
        }
    )

    row = await run_attempt_repository.request_run_attempt_cancel(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="rat-a",
        next_owner_kind="reconciler",
        next_owner_id="stale-run-maintenance",
    )

    assert row["owner_kind"] == "reconciler"
    assert row["owner_generation"] == 6
    sql, params = conn.calls[0]
    normalized_sql = " ".join(sql.split())
    assert "set owner_kind = %s" in normalized_sql
    assert "owner_generation = owner_generation + 1" in normalized_sql
    assert "status = 'cancel_requested'" in normalized_sql
    assert params == (
        "reconciler",
        "stale-run-maintenance",
        "tenant-a",
        "run-a",
        "rat-a",
        "queue_worker",
        "worker-a",
        5,
    )


@pytest.mark.asyncio
async def test_cancelled_attempt_requires_durable_cancel_request(monkeypatch):
    async def get_attempt(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": "running",
            "queue_attempt_id": "qat-a",
            "owner_kind": "queue_worker",
            "owner_id": "worker-a",
            "owner_generation": 4,
        }

    monkeypatch.setattr(run_attempt_repository, "get_run_attempt", get_attempt)

    with pytest.raises(
        RepositoryConflictError,
        match="run_attempt_cancel_request_missing",
    ):
        await run_attempt_repository.terminalize_run_attempt(
            object(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="rat-a",
            status="cancelled",
            terminal_reason="run_cancelled",
        )


@pytest.mark.asyncio
async def test_owner_cancel_does_not_transfer_attempt_execution_owner(monkeypatch):
    cancel_calls = []

    class Connection:
        async def execute(self, _sql, _params):
            return _Cursor(
                {
                    "id": "run-a",
                    "status": "running",
                    "trace_id": "trace-a",
                    "cancel_requested_newly": True,
                }
            )

    async def get_latest(*_args, **_kwargs):
        return {"id": "rat-a", "status": "running"}

    async def request_cancel(*_args, **kwargs):
        cancel_calls.append(kwargs)
        return {"id": kwargs["attempt_id"], "status": "cancel_requested"}

    async def stage(*_args, **_kwargs):
        return None

    async def append_event(*_args, **_kwargs):
        return "event-a"

    async def append_audit(*_args, **_kwargs):
        return "audit-a"

    async def list_leases(*_args, **_kwargs):
        return []

    monkeypatch.setattr(run_attempt_repository, "get_latest_run_attempt", get_latest)
    monkeypatch.setattr(
        run_attempt_repository,
        "request_run_attempt_cancel",
        request_cancel,
    )
    monkeypatch.setattr(
        run_attempt_repository,
        "_stage_run_tool_permission_terminalization",
        stage,
    )
    persistence = run_attempt_repository.PostgresRunCancellationPersistence(
        append_event=append_event,
        append_audit_log=append_audit,
        list_active_sandbox_leases=list_leases,
    )

    authority = await persistence.begin_owner_request(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        owner_user_id="owner-a",
    )

    assert authority is not None and authority.attempt_id == "rat-a"
    assert cancel_calls == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": "rat-a",
        }
    ]


@pytest.mark.asyncio
async def test_stale_failure_transfers_attempt_to_reconciler_and_expires_owner(
    monkeypatch,
):
    transitions = []

    async def get_latest(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": "running",
            "owner_kind": "queue_worker",
            "owner_id": "worker-a",
            "owner_generation": 4,
        }

    async def transition(*_args, **kwargs):
        transitions.append(kwargs)
        return {
            "id": kwargs["attempt_id"],
            "status": kwargs["requested_status"],
            "owner_kind": kwargs["next_owner_kind"],
            "owner_id": kwargs["next_owner_id"],
            "owner_generation": kwargs["expected_owner_generation"] + 1,
        }

    monkeypatch.setattr(run_attempt_repository, "get_latest_run_attempt", get_latest)
    monkeypatch.setattr(run_attempt_repository, "transition_run_attempt", transition)

    attempt = await run_attempt_repository.prepare_stale_run_attempt_reconciliation(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        terminal_status="failed",
        reconciler_id="stale-run-maintenance",
    )

    assert attempt == {
        "id": "rat-a",
        "status": "expired",
        "owner_kind": "reconciler",
        "owner_id": "stale-run-maintenance",
        "owner_generation": 5,
    }
    assert transitions[0]["expected_status"] == "running"
    assert transitions[0]["requested_status"] == "expired"
    assert transitions[0]["next_owner_kind"] == "reconciler"


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_status", ["running", "cancel_requested"])
async def test_stale_cancel_transfers_active_attempt_before_terminal_drain(
    monkeypatch,
    attempt_status,
):
    cancel_calls = []

    async def get_latest(*_args, **_kwargs):
        return {
            "id": "rat-a",
            "status": attempt_status,
            "owner_kind": "queue_worker",
            "owner_id": "worker-a",
            "owner_generation": 4,
        }

    async def request_cancel(*_args, **kwargs):
        cancel_calls.append(kwargs)
        return {
            "id": kwargs["attempt_id"],
            "status": "cancel_requested",
            "owner_kind": kwargs["next_owner_kind"],
            "owner_id": kwargs["next_owner_id"],
            "owner_generation": 5,
        }

    monkeypatch.setattr(run_attempt_repository, "get_latest_run_attempt", get_latest)
    monkeypatch.setattr(
        run_attempt_repository,
        "request_run_attempt_cancel",
        request_cancel,
    )

    attempt = await run_attempt_repository.prepare_stale_run_attempt_reconciliation(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        terminal_status="cancelled",
        reconciler_id="stale-run-maintenance",
    )

    assert attempt["status"] == "cancel_requested"
    assert attempt["owner_kind"] == "reconciler"
    assert cancel_calls[0]["next_owner_id"] == "stale-run-maintenance"

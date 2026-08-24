from typing import Any

import pytest

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

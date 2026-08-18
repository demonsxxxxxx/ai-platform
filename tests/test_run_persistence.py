import pytest

from app.platform.postgres.errors import RepositoryConflictError
from app.runs.infrastructure import postgres as run_persistence


class SingleRowCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class RecordingConnection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        row = self.rows.pop(0) if self.rows else None
        return SingleRowCursor(row)


@pytest.mark.asyncio
async def test_active_run_admission_locks_before_counting_and_preserves_scope():
    conn = RecordingConnection([None, {"count": 2}])

    observed = await run_persistence.enforce_user_active_run_admission(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        limit=3,
    )

    assert observed == 2
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == (
        '{"tenant_id": "tenant-a", "user_id": "user-a"}',
    )
    assert "status in ('queued', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_active_run_admission_rejects_limit_and_skips_disabled_limit():
    limited = RecordingConnection([None, {"count": 3}])
    with pytest.raises(
        RepositoryConflictError,
        match="user_active_run_limit_exceeded",
    ):
        await run_persistence.enforce_user_active_run_admission(
            limited,
            tenant_id="tenant-a",
            user_id="user-a",
            limit=3,
        )

    disabled = RecordingConnection()
    observed = await run_persistence.enforce_user_active_run_admission(
        disabled,
        tenant_id="tenant-a",
        user_id="user-a",
        limit=0,
    )
    assert observed == 0
    assert disabled.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup",
    [
        run_persistence.get_active_retry_for_source_run,
        run_persistence.get_active_resume_for_source_run,
    ],
)
async def test_active_child_lookup_preserves_owner_scope_and_latest_order(lookup):
    conn = RecordingConnection([{"id": "run-child", "status": "queued"}])

    row = await lookup(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-source",
    )

    assert row == {"id": "run-child", "status": "queued"}
    sql, params = conn.calls[0]
    assert "copied_from_run_id = %s" in sql
    assert "status in ('queued', 'running')" in sql
    assert "order by created_at desc limit 1" in sql
    assert params == ("tenant-a", "user-a", "run-source")


@pytest.mark.asyncio
async def test_run_queries_preserve_scope_projection_and_for_update():
    conn = RecordingConnection(
        [
            {"id": "run-a", "tenant_id": "tenant-a"},
            {
                "id": "run-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            },
        ]
    )

    run = await run_persistence.get_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        for_update=True,
    )
    identity = await run_persistence.get_run_identity(
        conn,
        run_id="run-a",
        for_update=True,
    )

    assert run["id"] == "run-a"
    assert identity["workspace_id"] == "workspace-a"
    run_sql, run_params = conn.calls[0]
    assert "select runs.*" in run_sql
    assert "left join sessions" in run_sql
    assert "sessions.tenant_id = runs.tenant_id" in run_sql
    assert "sessions.id = runs.session_id" in run_sql
    assert "sessions.user_id is not distinct from runs.user_id" in run_sql
    assert run_sql.endswith("for update of runs")
    assert run_params == ("tenant-a", "run-a")
    identity_sql, identity_params = conn.calls[1]
    assert identity_sql == (
        "select id, tenant_id, workspace_id, user_id, session_id, agent_id, "
        "status, context_snapshot_id from runs where id = %s for update"
    )
    assert identity_params == ("run-a",)


@pytest.mark.asyncio
async def test_shared_run_projection_includes_session_agent_profile_pins():
    expected = {
        "id": "run-a",
        "session_admitted_agent_profile_revision": 7,
        "session_admitted_agent_profile_hash": "a" * 64,
    }
    conn = RecordingConnection([expected])

    run = await run_persistence.get_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    assert run == expected
    sql, params = conn.calls[0]
    assert "select runs.*" in sql
    assert (
        "sessions.admitted_agent_profile_revision as "
        "session_admitted_agent_profile_revision"
    ) in sql
    assert (
        "sessions.admitted_agent_profile_hash as session_admitted_agent_profile_hash"
    ) in sql
    assert "sessions.tenant_id = runs.tenant_id" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "sessions.workspace_id = runs.workspace_id" in sql
    assert "sessions.user_id is not distinct from runs.user_id" in sql
    assert "sessions.agent_id = runs.agent_id" in sql
    assert params == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_terminal_intent_preserves_cas_scope_and_unicode_json():
    conn = RecordingConnection(
        [{"id": "run-a", "permission_terminalization_target": "failed"}]
    )

    row = await run_persistence._stage_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        target_status="failed",
        terminal_reason="run_failed",
        result_json={"message": "任务失败"},
        error_code="executor_failed",
        error_message="failed",
    )

    assert row["permission_terminalization_target"] == "failed"
    sql, params = conn.calls[0]
    assert "permission_terminalization_target = case" in sql
    assert "status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert params == (
        "failed",
        "failed",
        "failed",
        "run_failed",
        "failed",
        '{"message": "任务失败"}',
        "failed",
        "executor_failed",
        "failed",
        "failed",
        "tenant-a",
        "run-a",
    )


@pytest.mark.asyncio
async def test_terminal_intent_rejects_invalid_target_before_query():
    conn = RecordingConnection()

    with pytest.raises(
        ValueError,
        match="invalid_run_tool_permission_terminal_target",
    ):
        await run_persistence._stage_run_tool_permission_terminalization(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            target_status="succeeded",
            terminal_reason="invalid",
        )

    assert conn.calls == []

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import uuid
import psycopg
from psycopg import sql as psycopg_sql
from psycopg.rows import dict_row
import pytest
import app.identity.infrastructure.audit_postgres as _repo_owner_app_identity_infrastructure_audit_postgres
import app.runs.infrastructure.control_operations_postgres as _repo_owner_app_runs_infrastructure_control_operations_postgres
import app.runs.infrastructure.creation_postgres as _repo_owner_app_runs_infrastructure_creation_postgres
import app.sandbox.infrastructure.leases_postgres as _repo_owner_app_sandbox_infrastructure_leases_postgres
import app.streaming.infrastructure.run_events_postgres as _repo_owner_app_streaming_infrastructure_run_events_postgres
import app.runs.infrastructure.control_operations_postgres as control_operations_persistence
from app.execution.application import stale_terminalization
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.platform.tracing import standard_trace_id
from app.runs.api import RunAttemptLifecycleService, RunTerminalizationProgress
from app.runs.infrastructure import postgres as run_attempt_persistence
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.infrastructure.lifecycle_postgres import PostgresRunLifecyclePersistence, require_run_result_size
from app.runs.application.cancellation import RunCancellationUseCase
from app.runs.infrastructure.postgres import PostgresRunCancellationPersistence
from app.streaming.infrastructure import v4 as streaming_v4
from app.platform.postgres.errors import RepositoryConflictError
from app.runs.infrastructure.postgres import enforce_user_active_run_admission, get_run_identity
from tests.support.repository_fixtures import RecordingConnection, SingleRowConnection, SingleRowCursor, _run_control_postgres_dsn


async def _record_noop_event(*_args, **_kwargs):
    return "evt-test"


class _CancellationEventWriter:
    async def prepare_pending_authority(self, _conn, **_kwargs):
        return None

    async def append_cancel_requested(self, conn, **kwargs):
        await streaming_v4.append_run_cancel_requested_v4_row(conn, **kwargs)

    async def append_terminal(self, _conn, **_kwargs):
        return None


class _RunAttemptCursor:
    async def fetchone(self):
        return None


class _CancellationTestConnection:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if normalized.startswith("select * from run_attempts"):
            return _RunAttemptCursor()
        return await self._conn.execute(sql, params)


async def _request_owner_cancel(conn, *, tenant_id, user_id, run_id):
    @asynccontextmanager
    async def transaction_factory():
        yield _CancellationTestConnection(conn)

    use_case = RunCancellationUseCase(
        transaction_factory=transaction_factory,
        persistence=PostgresRunCancellationPersistence(
            attempt_lifecycle=RunAttemptLifecycleService(
                persistence=run_attempt_persistence
            ),
            append_event=_repo_owner_app_streaming_infrastructure_run_events_postgres.append_event,
            append_audit_log=_repo_owner_app_identity_infrastructure_audit_postgres.append_audit_log,
            list_active_sandbox_leases=_repo_owner_app_sandbox_infrastructure_leases_postgres.list_active_sandbox_leases_for_run,
        ),
        event_writer=_CancellationEventWriter(),
        progress_terminalization=RunLifecycleService(
            persistence=PostgresRunLifecyclePersistence(),
            append_event=_repo_owner_app_streaming_infrastructure_run_events_postgres.append_event,
            append_audit_log=_repo_owner_app_identity_infrastructure_audit_postgres.append_audit_log,
            validate_result_size=require_run_result_size,
            sanitize_payload=sanitize_public_payload,
            sanitize_text=sanitize_public_text,
            make_trace_id=standard_trace_id,
        ).progress_run_terminalization,
    )
    result = await use_case.request_owner_cancel(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        run_id=run_id,
    )
    return result.as_route_result() if result is not None else None


@pytest.mark.asyncio
async def test_list_stale_run_candidates_requires_progress_staleness_and_no_active_sandbox_lease():
    conn = SingleRowConnection(None)

    await PostgresRunLifecyclePersistence().list_stale_run_reconciliation_candidates(
        conn,
        stale_after_seconds=900,
        limit=25,
    )

    assert "runs.status in ('queued', 'running')" in conn.sql
    assert "greatest( coalesce(latest_event.created_at" in conn.sql
    assert "<= clock_timestamp() - (%s * interval '1 second')" in conn.sql
    assert "not exists ( select 1 from sandbox_leases" in conn.sql
    assert "sandbox_leases.status = 'active'" in conn.sql
    assert "for update of runs skip locked" in conn.sql
    assert conn.params == (900, 900, 25)


@pytest.mark.asyncio
async def test_list_cancel_requested_orphans_bypasses_general_staleness_but_keeps_live_owner_fences():
    conn = SingleRowConnection(None)

    await PostgresRunLifecyclePersistence().list_stale_run_reconciliation_candidates(
        conn,
        stale_after_seconds=900,
        cancel_requested_after_seconds=5,
        limit=25,
    )

    assert "cancel_requested_at <= clock_timestamp() - (%s * interval '1 second')" in conn.sql
    assert "greatest( coalesce(latest_event.created_at" in conn.sql
    assert "not exists ( select 1 from sandbox_leases" in conn.sql
    assert conn.params == (5, 900, 25)


@pytest.mark.asyncio
async def test_receipt_fenced_stale_terminalization_never_cancels_completed_executor(monkeypatch):
    calls = []

    class Connection:
        async def execute(self, sql, params):
            calls.append((" ".join(sql.split()), params))
            return SingleRowCursor(None)

    staged = await stale_terminalization.stage_stale_run_reconciliation(
        Connection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        run_id="run-a",
        expected_status="running",
        stale_before="2026-07-21T11:00:00Z",
        terminal_status="failed",
        error_code="stale_run_interrupted",
        error_message="Run interrupted because no live execution owner remains.",
        append_event=_record_noop_event,
        append_audit_log=_record_noop_event,
    )

    assert staged is None
    assert "executor_terminal_json is not null" in calls[0][0]
    assert "executor_reconciliation_status is distinct from 'finalized'" in calls[0][0]
    assert calls[0][1][4:9] == ("tenant-a", "workspace-a", "user-a", "run-a", "running")


@pytest.mark.parametrize(
    "raw,expected_valid",
    [
        ("0", True),
        ("9223372036854775807", True),
        ("9223372036854775808", False),
        ("999999999999999999999999", False),
        ("-1", False),
        ("not-a-number", False),
    ],
)
def test_queue_admission_ordinal_bigint_guard_boundaries(raw, expected_valid):
    valid = (
        raw.isdigit()
        and len(raw) <= 19
        and (len(raw) < 19 or raw <= "9223372036854775807")
    )
    assert valid is expected_valid


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_locks_before_counting():
    class CountCursor:
        async def fetchone(self):
            return {"count": 2}

    class EmptyCursor:
        async def fetchone(self):
            return None

    class AdmissionConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "count(*) as count" in normalized:
                return CountCursor()
            return EmptyCursor()

    conn = AdmissionConnection()

    observed = await enforce_user_active_run_admission(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        limit=3,
    )

    assert observed == 2
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == ('{"tenant_id": "tenant-a", "user_id": "user-a"}',)
    assert "status in ('queued', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_rejects_at_limit():
    class CountCursor:
        async def fetchone(self):
            return {"count": 3}

    class AdmissionConnection:
        async def execute(self, sql, params):
            return CountCursor()

    with pytest.raises(RepositoryConflictError, match="user_active_run_limit_exceeded"):
        await enforce_user_active_run_admission(
            AdmissionConnection(),
            tenant_id="tenant-a",
            user_id="user-a",
            limit=3,
        )


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_skips_disabled_limit():
    class AdmissionConnection:
        async def execute(self, sql, params):
            raise AssertionError("disabled admission must not lock or count")

    observed = await enforce_user_active_run_admission(
        AdmissionConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        limit=0,
    )

    assert observed == 0


@pytest.mark.asyncio
async def test_run_control_operation_lock_scope_precedes_any_mapping_query():
    conn = RecordingConnection()

    await _repo_owner_app_runs_infrastructure_control_operations_postgres.acquire_run_control_operation_lock(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        source_run_id="run-source",
        action="retry",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    await _repo_owner_app_runs_infrastructure_control_operations_postgres.get_run_control_operation(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        source_run_id="run-source",
        action="retry",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == (
        '{"scope": "run_control_operation", "tenant_id": "tenant-a", "user_id": "user-a", '
        '"source_run_id": "run-source", "action": "retry", '
        '"operation_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"}',
    )
    mapping_sql, mapping_params = conn.calls[1]
    assert "run_control_operation_committed" in mapping_sql
    assert "source.user_id = %s" in mapping_sql
    assert "child.user_id = %s" in mapping_sql
    assert "child.copied_from_run_id = source.id" in mapping_sql
    assert "child.workspace_id" in mapping_sql
    assert "child.agent_id" in mapping_sql
    assert "child.skill_id" in mapping_sql
    assert "child.input_json" in mapping_sql
    assert mapping_params.count("tenant-a") >= 1
    assert mapping_params.count("user-a") == 2
    assert "run-source" in mapping_params
    assert "retry" in mapping_params
    assert "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4" in mapping_params


@pytest.mark.asyncio
async def test_record_run_control_operation_persists_only_safe_exact_lineage(monkeypatch):
    recorded: list[dict[str, object]] = []

    async def append_event(_conn, **kwargs):
        recorded.append(kwargs)
        return "evt-operation"

    monkeypatch.setattr(control_operations_persistence, "append_event", append_event)

    event_id = await _repo_owner_app_runs_infrastructure_control_operations_postgres.record_run_control_operation(
        object(),
        tenant_id="tenant-a",
        source_run_id="run-source",
        child_run_id="run-child",
        action="resume",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        trace_id="trace-source",
    )

    assert event_id == "evt-operation"
    assert recorded == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-source",
            "trace_id": "trace-source",
            "event_type": "run_control_operation_committed",
            "stage": "control",
            "message": "Run control operation committed",
            "visible_to_user": False,
            "payload": {
                "visible_to_user": False,
                "source_run_id": "run-source",
                "child_run_id": "run-child",
                "action": "resume",
                "operation_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
            },
        }
    ]


@pytest.mark.asyncio
async def test_run_control_operation_interleavings_are_exactly_once_in_postgres():
    """Exercise operation-lock creation, GET linearization and scoped resolution on PostgreSQL."""

    dsn = _run_control_postgres_dsn()
    schema_name = f"run_control_operation_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    observer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first: psycopg.AsyncConnection | None = None
    second: psycopg.AsyncConnection | None = None
    tasks: list[asyncio.Task] = []
    operation_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    async def set_search_path(conn: psycopg.AsyncConnection) -> None:
        await conn.execute(psycopg_sql.SQL("set search_path to {}").format(psycopg_sql.Identifier(schema_name)))
        await conn.commit()

    async def backend_pid(conn: psycopg.AsyncConnection) -> int:
        cursor = await conn.execute("select pg_backend_pid() as pid")
        return int((await cursor.fetchone())["pid"])

    async def wait_until_blocked(*, waiter_pid: int, blocker_pid: int) -> None:
        for _ in range(300):
            cursor = await observer.execute(
                "select %s = any(pg_blocking_pids(%s)) as is_blocked",
                (blocker_pid, waiter_pid),
            )
            if (await cursor.fetchone())["is_blocked"]:
                return
            await asyncio.sleep(0)
        raise AssertionError("operation resolver never blocked on the in-flight mutation")

    async def create_or_resolve(conn: psycopg.AsyncConnection, child_run_id: str):
        await _repo_owner_app_runs_infrastructure_control_operations_postgres.acquire_run_control_operation_lock(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        )
        existing = await _repo_owner_app_runs_infrastructure_control_operations_postgres.get_run_control_operation(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        )
        if existing is not None:
            return existing, False
        await conn.execute(
            "select id from runs where tenant_id = %s and id = %s for update",
            ("tenant-a", "run-source"),
        )
        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
              status, copied_from_run_id, session_generation
            ) values (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s)
            """,
            (
                child_run_id,
                "tenant-a",
                "workspace-a",
                "session-a",
                "user-a",
                "agent-a",
                "skill-a",
                "run-source",
                2,
            ),
        )
        await _repo_owner_app_runs_infrastructure_control_operations_postgres.record_run_control_operation(
            conn,
            tenant_id="tenant-a",
            source_run_id="run-source",
            child_run_id=child_run_id,
            action="retry",
            operation_id=operation_id,
            trace_id="trace-source",
        )
        return {"run_id": child_run_id}, True

    try:
        await observer.execute(psycopg_sql.SQL("create schema {}").format(psycopg_sql.Identifier(schema_name)))
        await observer.execute(psycopg_sql.SQL("set search_path to {}").format(psycopg_sql.Identifier(schema_name)))
        await observer.execute(schema_sql)
        await observer.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await observer.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'Workspace A')"
        )
        await observer.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A'), "
            "('user-b', 'tenant-a', 'User B')"
        )
        await observer.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1', 'worker')"
        )
        await observer.execute(
            "insert into agents(id, tenant_id, name, agent_type, default_skill_id) "
            "values ('agent-a', 'tenant-a', 'Agent A', 'assistant', 'skill-a')"
        )
        await observer.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, next_run_generation) "
            "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 1)"
        )
        await observer.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
              trace_id, status, session_generation
            ) values ('run-source', 'tenant-a', 'workspace-a', 'session-a', 'user-a',
                      'agent-a', 'skill-a', 'trace-source', 'failed', 1)
            """
        )
        first = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await set_search_path(first)
        await set_search_path(second)
        first_pid = await backend_pid(first)
        second_pid = await backend_pid(second)

        first_result = await create_or_resolve(first, "run-child-first")
        assert first_result == ({"run_id": "run-child-first"}, True)
        second_task = asyncio.create_task(create_or_resolve(second, "run-child-second"))
        tasks.append(second_task)
        await wait_until_blocked(waiter_pid=second_pid, blocker_pid=first_pid)
        await first.commit()
        second_result = await asyncio.wait_for(second_task, timeout=5)
        await second.commit()

        assert second_result[1] is False
        assert second_result[0]["run_id"] == "run-child-first"
        count_cursor = await observer.execute(
            "select count(*) as count from runs where copied_from_run_id = 'run-source'"
        )
        assert int((await count_cursor.fetchone())["count"]) == 1
        assert await _repo_owner_app_runs_infrastructure_control_operations_postgres.get_run_control_operation(
            observer,
            tenant_id="tenant-a",
            user_id="user-b",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        ) is None
        assert await _repo_owner_app_runs_infrastructure_control_operations_postgres.get_run_control_operation(
            observer,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="resume",
            operation_id=operation_id,
        ) is None

        absent_operation_id = "d9428888-122b-4f2e-86f3-df16c79c7358"
        await _repo_owner_app_runs_infrastructure_control_operations_postgres.acquire_run_control_operation_lock(
            first,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="resume",
            operation_id=absent_operation_id,
        )

        async def resolve_absence_after_lock():
            await _repo_owner_app_runs_infrastructure_control_operations_postgres.acquire_run_control_operation_lock(
                second,
                tenant_id="tenant-a",
                user_id="user-a",
                source_run_id="run-source",
                action="resume",
                operation_id=absent_operation_id,
            )
            return await _repo_owner_app_runs_infrastructure_control_operations_postgres.get_run_control_operation(
                second,
                tenant_id="tenant-a",
                user_id="user-a",
                source_run_id="run-source",
                action="resume",
                operation_id=absent_operation_id,
            )

        absence_task = asyncio.create_task(resolve_absence_after_lock())
        tasks.append(absence_task)
        await wait_until_blocked(waiter_pid=second_pid, blocker_pid=first_pid)
        await first.rollback()
        assert await asyncio.wait_for(absence_task, timeout=5) is None
        await second.commit()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if first is not None:
            await first.close()
        if second is not None:
            await second.close()
        await observer.execute(psycopg_sql.SQL("drop schema if exists {} cascade").format(psycopg_sql.Identifier(schema_name)))
        await observer.close()


@pytest.mark.asyncio
async def test_queued_cancel_orders_one_cancel_request_before_the_finalizer_terminal_event(monkeypatch):
    """Queued cancellation has one owner for each public lifecycle fact, including retries."""

    events = []

    class Connection:
        def __init__(self):
            self.attempt = 0

        async def execute(self, sql, _params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select * from run_attempts"):
                return SingleRowCursor(None)
            if normalized.startswith("with eligible_run as"):
                self.attempt += 1
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "status": "queued",
                        "trace_id": "trace-a",
                        "cancel_requested_newly": self.attempt == 1,
                    }
                )
            raise AssertionError(normalized)

    async def stage(_conn, **_kwargs):
        return {"id": "run-a", "terminalization_target": "cancelled"}

    async def load(_conn, **_kwargs):
        return {
            "id": "run-a", "user_id": "user-a", "trace_id": "trace-a",
            "status": "queued", "terminalization_target": "cancelled",
            "terminalization_reason": "run_cancelled", "terminalization_result_json": {},
        }

    finalized = False

    async def finalize(_conn, **_kwargs):
        nonlocal finalized
        if finalized:
            return {"already_terminal": True, "status": "cancelled"}
        finalized = True
        return {"status": "cancelled", "artifact_count": 0}

    async def record_event(_conn, **kwargs):
        events.append(kwargs["event_type"])
        return f"evt-{len(events)}"

    async def no_leases(*_args, **_kwargs):
        return []

    async def record_cancel_v4(_conn, **_kwargs):
        events.append("v4.run.cancel_requested")

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(PostgresRunLifecyclePersistence, "stage_run_terminalization", staticmethod(stage))
    monkeypatch.setattr(run_attempt_persistence, "stage_run_terminalization", stage)
    monkeypatch.setattr(PostgresRunLifecyclePersistence, "load_staged_terminalization", staticmethod(load))
    monkeypatch.setattr(PostgresRunLifecyclePersistence, "finalize_staged_terminalization", staticmethod(finalize))
    monkeypatch.setattr(_repo_owner_app_streaming_infrastructure_run_events_postgres, "append_event", record_event)
    monkeypatch.setattr(_repo_owner_app_identity_infrastructure_audit_postgres, "append_audit_log", no_audit)
    monkeypatch.setattr(streaming_v4, "append_run_cancel_requested_v4_row", record_cancel_v4)
    monkeypatch.setattr(_repo_owner_app_sandbox_infrastructure_leases_postgres, "list_active_sandbox_leases_for_run", no_leases)
    conn = Connection()

    first = await _request_owner_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    second = await _request_owner_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")

    assert first["status"] == second["status"] == "cancelled"
    assert events == ["cancel_requested", "v4.run.cancel_requested", "run_cancelled"]


def test_terminalization_progress_soft_cancel_intent_is_not_truthy_completion():
    """A recorded cancellation request is not evidence that a run reached cancelled."""

    progress = RunTerminalizationProgress(
        completed=True,
        status="cancel_requested",
    )

    assert bool(progress) is False


@pytest.mark.asyncio
async def test_get_run_identity_can_lock_row_for_callback_race_window():
    conn = RecordingConnection()

    await get_run_identity(conn, run_id="run-a", for_update=True)

    sql, params = conn.calls[0]
    assert sql.endswith("for update")
    assert params == ("run-a",)


@pytest.mark.asyncio
async def test_get_authorized_run_can_lock_row_for_retry_race_window():
    conn = RecordingConnection()

    await _repo_owner_app_runs_infrastructure_creation_postgres.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        for_update=True,
    )

    sql, params = conn.calls[0]
    assert sql.endswith("for update of runs")
    assert "join sessions on sessions.id = runs.session_id" in sql
    assert "sessions.status = 'active'" in sql
    assert params == ("tenant-a", "run-a", "user-a")

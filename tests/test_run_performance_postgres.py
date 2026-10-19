"""Monitor query contract and opt-in real PostgreSQL sample semantics."""
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from app.runs.infrastructure.admin_queries_postgres import get_admin_run_monitor_report
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from tests.support.repository_fixtures import SingleRowCursor
from tests.test_run_diagnostics_postgres import _isolated_postgres_run
from tests.support.db_transactions import event_loop_policy as event_loop_policy


@pytest.mark.asyncio
async def test_monitor_queries_bind_one_closed_tenant_window_and_row_limit():
    class RowsCursor:
        async def fetchall(self):
            return [{"run_id": "run-a"}, {"run_id": "run-b"}]

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, parameters):
            self.calls.append((sql, parameters))
            assert sql.count("%s") == len(parameters)
            if len(self.calls) == 1:
                return SingleRowCursor({"total": 2, "succeeded": 2, "latency": {}, "models": []})
            return RowsCursor()

    start = datetime(2026, 10, 14, tzinfo=timezone.utc)
    conn = Connection()
    report = await get_admin_run_monitor_report(conn, tenant_id="tenant-a", from_at=start,
        to_at=start + timedelta(hours=1), user_id="user-a", agent_id="agent-a", model_id="model-a",
        model_gateway_revision=3, limit=1)
    assert len(conn.calls) == 2 and report["has_more"] is True and len(report["runs"]) == 1
    assert conn.calls[0][1][:5] == ("tenant-a", start, start + timedelta(hours=1), "user-a", "user-a")
    assert conn.calls[1][1][:-1] == conn.calls[0][1][:len(conn.calls[1][1]) - 1]
    assert conn.calls[1][1][-1] == 2
    assert conn.calls[0][1][-4:] == (start, 300.0, start, 300.0)
    assert "percentile_cont(0.95)" in conn.calls[0][0]


@pytest.mark.asyncio
async def test_postgres_monitor_samples_runs_not_events_and_uses_frozen_skill_cohort():
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        created = datetime.now(timezone.utc) - timedelta(hours=1)
        end = created + timedelta(minutes=10)
        await admin.execute("""update runs set status='succeeded', created_at=%s, queued_at=%s,
            started_at=%s, finished_at=%s, model_id='model-a', model_value='test-model', model_gateway_revision=1
            where id='run-a'""", (created, created, created, created + timedelta(seconds=20)))
        for run_id, duration in [("run-normal", 2), ("run-other-version", 100)]:
            await admin.execute("""insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,skill_id,status,
                created_at,queued_at,started_at,finished_at,model_id,model_value,model_gateway_revision)
                values (%s,'tenant-a','workspace-a','session-a','user-a','agent-a','skill-a','succeeded',%s,%s,%s,%s,'model-a','test-model',1)""",
                (run_id, created, created, created, created + timedelta(seconds=duration)))
        for run_id, version in [("run-a", "pin-v2"), ("run-normal", "pin-v2"), ("run-other-version", "pin-v1")]:
            await admin.execute("""insert into run_skill_snapshots(id,tenant_id,run_id,skill_id,skill_version,content_hash)
                values (%s,'tenant-a',%s,'skill-a',%s,%s)""", (f"pin-{run_id}", run_id, version, version))
        for index, (delta, second) in enumerate([( "\n\t ", 0), ("正文", 1), ("第二段", 2), ("重复段", 3)]):
            await admin.execute("""insert into run_events(id,tenant_id,run_id,event_type,stage,sequence,created_at,payload_json)
                values (%s,'tenant-a','run-a','message.delta','message',%s,%s,%s)""",
                (f"evt-{index}", index + 1, created + timedelta(seconds=second), Jsonb({"delta": delta})))
        report = await get_admin_run_monitor_report(conn, tenant_id="tenant-a", from_at=created, to_at=end)
        assert report["counts"]["total"] == 3
        assert report["latency"]["total_ms"]["sample_count"] == 3
        assert report["latency"]["total_ms"]["p50"] == 20000
        assert report["latency"]["server_first_text_ms"] == {"sample_count": 1, "p50": 1000, "p95": 1000, "p99": 1000}
        assert report["latency"]["browser_first_text_ms"]["sample_count"] == 0
        assert foreign["counts"]["total"] == 0 and foreign["runs"] == []
        repository = PostgresRunDiagnosticsRepository()
        await repository.append_browser_latency(conn, tenant_id="tenant-a", run_id="run-a", measurement={
            "measurement": "browser_dom_commit.v1", "visibility": "visible", "duration_ms": 1250, "received_at": end,
        })
        await repository.append_browser_latency(conn, tenant_id="tenant-a", run_id="run-a", measurement={
            "measurement": "browser_dom_commit.v1", "visibility": "visible", "duration_ms": 9999, "received_at": end,
        })
        measured = await get_admin_run_monitor_report(conn, tenant_id="tenant-a", from_at=created, to_at=end)
        assert measured["latency"]["browser_first_text_ms"] == {"sample_count": 1, "p50": 1250, "p95": 1250, "p99": 1250}
        events = await repository.get_admin_investigation_events(conn, tenant_id="tenant-a", run_id="run-a")
        assert events["first_public_text_at"] == created + timedelta(seconds=1)

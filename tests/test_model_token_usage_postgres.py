"""Real PostgreSQL ledger/aggregation checks; no mocked SQL acceptance."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import pytest
from uuid import uuid4

from app.runs.domain.model_token_usage import ModelUsageSnapshot
from app.runs.infrastructure.model_token_usage import PostgresModelTokenUsage
import app.sandbox.infrastructure.leases_postgres as sandbox_lease_reader
from tests.test_run_diagnostics_postgres import _isolated_postgres_run
from tests.support.db_transactions import event_loop_policy as event_loop_policy

AT = datetime(2026, 10, 20, 15, 59, tzinfo=timezone.utc)


async def provider_session(conn, **kwargs):
    return "synthetic-provider"


def snapshot(seq=1, n=1, *, scope="scope", coverage="partial", baseline="fresh", baseline_models=None):
    return ModelUsageSnapshot(scope_id=scope, observation_seq=seq, baseline=baseline, baseline_models=baseline_models or {},
        models={m: dict(input_tokens=n, cache_read_input_tokens=2*n,
            cache_creation_input_tokens=3*n, output_tokens=4*n) for m in ("actual-a", "actual-b")}, coverage=coverage)


async def record(repo, conn, value, at=AT, attempt="attempt-a"):
    return await repo.record(conn, tenant_id="tenant-a", run_id="run-a", attempt_id=attempt,
        snapshot=value, receipt_id=uuid4().hex, recorded_at=at)


async def query(repo, conn, start=AT-timedelta(hours=1), end=AT+timedelta(hours=1), **kwargs):
    return await repo.query(conn, tenant_id="tenant-a", start_at=start, end_at=end,
        user_id=kwargs.get("user_id"), q=kwargs.get("q"), limit=kwargs.get("limit",50), offset=kwargs.get("offset",0))


@pytest.mark.asyncio
async def test_ledger_duplicates_conflict_regression_rollback_and_cross_attempt():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        async with conn.transaction():
            assert await record(repo, conn, snapshot()) == "accepted"
            assert await record(repo, conn, snapshot()) == "duplicate"
            assert await record(repo, conn, snapshot(seq=3,n=2,coverage="reported_final")) == "accepted"
            assert await record(repo, conn, snapshot(seq=2)) == "stale"
            assert await record(repo, conn, snapshot(seq=3,n=3)) == "partial"
            assert await record(repo, conn, snapshot(seq=4,n=1)) == "partial"
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await record(repo, conn, snapshot(seq=5,n=4))
                raise RuntimeError("rollback")
        async with conn.transaction():
            result = await query(repo, conn)
            assert result["data"][0]["total_tokens"] == 40
            assert result["meta"]["coverage"]["status"] == "partial"
            assert await record(repo, conn, snapshot(baseline="restored",baseline_models=asdict(snapshot(n=2))["models"]), attempt="attempt-b") == "accepted"
            assert (await query(repo, conn))["data"][0]["total_tokens"] == 60
        assert await (await admin.execute("select count(*) n from model_token_usage_deltas")).fetchone() == {"n":6}


@pytest.mark.asyncio
async def test_ledger_beijing_midnight_unknown_zero_search_pagination_and_tenant():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        await admin.execute("update runs set auth_source='company-login' where id='run-a'")
        async with conn.transaction():
            await record(repo, conn, snapshot())
            await record(repo, conn, snapshot(seq=2,n=2,coverage="reported_final"), AT+timedelta(minutes=2))
            first = await query(repo, conn, end=datetime(2026,10,20,16,tzinfo=timezone.utc))
            second = await query(repo, conn, start=datetime(2026,10,20,16,tzinfo=timezone.utc))
            assert first["data"][0]["total_tokens"] == second["data"][0]["total_tokens"] == 20
            assert first["summary"]["total_tokens"] == second["summary"]["total_tokens"] == 20
            assert first["data"][0]["work_id"] == "user-a"
            models = await query(repo, conn, user_id="user-a")
            assert [row["model"] for row in models["data"]] == ["actual-a","actual-b"]
            assert (await query(repo, conn, q="USER-A"))["total"] == 1
            assert (await query(repo, conn, q="missing"))["data"] == []
            beyond = await query(repo, conn, offset=100)
            assert beyond["total"] == 1 and beyond["data"] == []
            assert beyond["summary"]["total_tokens"] == 40
            assert (await query(repo, conn, q="missing"))["summary"] is None
            unknown = ModelUsageSnapshot(scope_id="unknown",observation_seq=1,baseline="unknown",models={},coverage="unavailable",reason="baseline_unknown")
            await record(repo, conn, unknown, AT+timedelta(days=2))
            result = await query(repo, conn, start=AT+timedelta(days=1),end=AT+timedelta(days=3))
            assert result["data"] == [] and result["meta"]["coverage"]["reason"] == "baseline_unknown"
            assert result["summary"] is None
            await record(repo, conn, snapshot(n=0,scope="zero",coverage="reported_final",baseline="restored",baseline_models=asdict(snapshot(n=2))["models"]), AT+timedelta(days=4))
            result = await query(repo, conn,start=AT+timedelta(days=3),end=AT+timedelta(days=5))
            assert result["data"][0]["total_tokens"] == 0
            assert result["summary"]["total_tokens"] == 0
            foreign = await repo.query(conn, tenant_id="other",start_at=AT-timedelta(days=1),end_at=AT+timedelta(days=5),user_id=None,q=None,limit=50,offset=0)
            assert foreign["data"] == [] and foreign["summary"] is None


@pytest.mark.asyncio
async def test_summary_covers_all_filtered_users_independently_of_pagination():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        async with conn.transaction():
            await record(repo, conn, snapshot(n=2))
            await conn.execute("insert into users(id,tenant_id,display_name) values ('user-b','tenant-a','Employee B')")
            await conn.execute("""insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title,status)
                values ('session-b','tenant-a','workspace-a','user-b','agent-a','B','active')""")
            await conn.execute("""insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,skill_id,status)
                select 'run-b',tenant_id,workspace_id,'session-b','user-b',agent_id,skill_id,'running'
                from runs where id='run-a'""")
            await repo.record(conn, tenant_id="tenant-a", run_id="run-b", attempt_id="attempt-b",
                snapshot=snapshot(n=1, scope="second-user", baseline="restored", baseline_models=asdict(snapshot(n=2))["models"]),
                receipt_id=uuid4().hex, recorded_at=AT)
            first = await query(repo, conn, limit=1)
            second = await query(repo, conn, limit=1, offset=1)
            assert first["total"] == second["total"] == 2
            assert first["data"][0]["total_tokens"] == 40
            assert second["data"][0]["total_tokens"] == 20
            assert first["summary"] == second["summary"] == {
                "input_tokens": 6, "cache_read_input_tokens": 12,
                "cache_creation_input_tokens": 18, "output_tokens": 24,
                "input_total": 36, "total_tokens": 60,
            }
            filtered = await query(repo, conn, q="Employee B", limit=1, offset=100)
            assert filtered["data"] == [] and filtered["total"] == 1
            assert filtered["summary"]["total_tokens"] == 20


@pytest.mark.asyncio
async def test_window_coverage_is_effective_receipt_fact_not_future_scope_state():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        await admin.execute("insert into schema_migrations(version,checksum_sha256,applied_at) values ('2026.10.20.2','synthetic',%s)", (AT-timedelta(days=1),))
        midnight = datetime(2026,10,20,16,tzinfo=timezone.utc)
        async with conn.transaction():
            await record(repo, conn, snapshot())
            await record(repo, conn, snapshot(seq=2,n=2,coverage="reported_final"), midnight+timedelta(minutes=1))
            assert (await query(repo,conn,end=midnight))["meta"]["coverage"]["status"] == "partial"
            assert (await query(repo,conn,start=midnight))["meta"]["coverage"]["status"] == "reported_final"
            # A rejected final payload cannot falsely restore reported_final.
            await record(repo,conn,snapshot(seq=3,n=1,coverage="reported_final"),midnight+timedelta(minutes=2))
            assert (await query(repo,conn,start=midnight))["meta"]["coverage"]["status"] == "partial"
            assert (await query(repo,conn,end=midnight))["meta"]["coverage"]["status"] == "partial"


@pytest.mark.asyncio
async def test_restored_baseline_prevents_overlap_across_invocations_and_attempts():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        async with conn.transaction():
            await record(repo,conn,snapshot(n=2))
            assert await record(repo,conn,snapshot(scope="restart",n=2)) == "partial"
            assert await record(repo,conn,snapshot(scope="restart_other_attempt",n=2),attempt="b") == "partial"
            baseline = asdict(snapshot(n=2))["models"]
            assert await record(repo,conn,snapshot(scope="next",baseline="restored",baseline_models=baseline)) == "accepted"
            assert (await query(repo,conn))["data"][0]["total_tokens"] == 60
            changed = snapshot(seq=2,scope="next",baseline="restored",baseline_models=asdict(snapshot(n=3))["models"])
            assert await record(repo,conn,changed) == "partial"
            # Cross-Run continuation shares provider lineage, not Run-bound model identity.
            await conn.execute("""insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,skill_id,status)
                select 'run-b',tenant_id,workspace_id,session_id,user_id,agent_id,skill_id,'running' from runs where id='run-a'""")
            value = snapshot(scope="cross_run",baseline="restored",baseline_models=asdict(snapshot(n=3))["models"])
            assert await repo.record(conn,tenant_id="tenant-a",run_id="run-b",attempt_id="c",snapshot=value,receipt_id=uuid4().hex,recorded_at=AT) == "accepted"
            assert (await query(repo,conn))["data"][0]["total_tokens"] == 80


@pytest.mark.asyncio
async def test_real_callback_receipt_ledger_repeat_and_final_fence_rollback(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routes import runtime_callbacks as route
    from app.runs.application.model_token_usage import ModelTokenUsageService
    from app.runtime.sandbox.contracts import ExecutorCallbackEvent

    async with _isolated_postgres_run() as (_, _, admin, conn):
        expired = False

        @asynccontextmanager
        async def transaction():
            async with conn.transaction():
                yield conn

        async def authority(connection, **kwargs):
            row = await (await connection.execute("select * from runs where id='run-a' for update")).fetchone()
            return row, {"id":"synthetic-lease"}

        async def recheck(connection, **kwargs):
            if expired:
                raise HTTPException(409,"sandbox_runtime_attempt_inactive")

        async def heartbeat(connection, **kwargs):
            return {"id":"synthetic-lease"}

        monkeypatch.setattr(route,"transaction",transaction)
        monkeypatch.setattr(route,"_lock_callback_authority",authority)
        monkeypatch.setattr(route,"_recheck_callback_authority",recheck)
        monkeypatch.setattr(route.sandbox_lease_repository,"record_sandbox_executor_heartbeat",heartbeat)
        monkeypatch.setattr(route,"get_settings",lambda:SimpleNamespace(sandbox_lease_ttl_seconds=30))
        service = ModelTokenUsageService(PostgresModelTokenUsage(provider_session=provider_session))
        callback = ExecutorCallbackEvent(session_id="session-a",run_id="run-a",attempt_id="attempt-a",
            callback_token_id="cbt:run-a:attempt-a",batch_id="batch-a",status="running",progress=20,
            model_usage_snapshot=asdict(snapshot()))
        await route.record_executor_callback(callback,capabilities=SimpleNamespace(),model_token_usage=service, lease_port=sandbox_lease_reader)
        await route.record_executor_callback(callback,capabilities=SimpleNamespace(),model_token_usage=service, lease_port=sandbox_lease_reader)
        assert (await (await admin.execute("select count(*) n from model_token_usage_deltas")).fetchone())["n"] == 2
        assert (await (await admin.execute("select count(*) n from run_event_batches")).fetchone())["n"] == 1
        expired = True
        rejected = ExecutorCallbackEvent.model_validate({**callback.model_dump(), "batch_id":"rejected", "model_usage_snapshot":asdict(snapshot(seq=2,n=2))})
        with pytest.raises(HTTPException):
            await route.record_executor_callback(rejected,capabilities=SimpleNamespace(),model_token_usage=service, lease_port=sandbox_lease_reader)
        assert (await (await admin.execute("select count(*) n from run_event_batches")).fetchone())["n"] == 1
        assert (await (await admin.execute("select count(*) n from model_token_usage_observations")).fetchone())["n"] == 1
        assert (await (await admin.execute("select count(*) n from model_token_usage_coverage")).fetchone())["n"] == 1
        assert (await (await admin.execute("select count(*) n from model_token_usage_deltas")).fetchone())["n"] == 2


@pytest.mark.asyncio
async def test_missing_platform_provider_binding_never_counts_executor_claim():
    from app.context.infrastructure.provider_epochs import read_usage_provider_session
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=read_usage_provider_session)
        async with conn.transaction():
            value = replace(snapshot(), provider_session_id="executor-claimed-provider")
            assert await record(repo,conn,value) == "partial"
            assert (await query(repo,conn))["data"] == []
            row = await (await conn.execute("select coverage_reason from model_token_usage_scopes")).fetchone()
            assert row["coverage_reason"] == "provider_identity_unknown"


@pytest.mark.asyncio
async def test_late_zero_delta_final_settles_predecessor_without_recounting_successor():
    async with _isolated_postgres_run() as (_, _, admin, conn):
        repo = PostgresModelTokenUsage(provider_session=provider_session)
        async with conn.transaction():
            first = snapshot(n=10, scope="a")
            assert await record(repo, conn, first) == "accepted"
            successor = snapshot(n=5, scope="b", baseline="restored",
                baseline_models=asdict(first)["models"], coverage="reported_final")
            assert await record(repo, conn, successor) == "accepted"
            assert await record(repo, conn, snapshot(seq=2, n=10, scope="a", coverage="reported_final")) == "accepted"
            # Two models, each with 1+2+3+4 times the input counter.
            assert (await query(repo, conn))["data"][0]["total_tokens"] == 15 * 20
            state = await (await conn.execute("select coverage,coverage_reason from model_token_usage_scopes where scope_id='a'")).fetchone()
            assert state == {"coverage":"reported_final", "coverage_reason":None}
            assert (await (await conn.execute("select count(*) n from model_token_usage_deltas")).fetchone())["n"] == 4
            # Actual new range from A would overlap B and must still be rejected.
            assert await record(repo, conn, snapshot(seq=3, n=11, scope="a", coverage="reported_final")) == "partial"
            assert (await query(repo, conn))["data"][0]["total_tokens"] == 15 * 20
            state = await (await conn.execute("select coverage_reason from model_token_usage_scopes where scope_id='a'")).fetchone()
            assert state["coverage_reason"] == "scope_overlap"


@pytest.mark.asyncio
async def test_real_provider_turn_resolver_binds_identity_and_accepts_usage():
    from app.context.infrastructure.provider_epochs import read_usage_provider_session
    async with _isolated_postgres_run() as (_, _, admin, conn):
        provider_id = str(uuid4())
        # Same minimal head/epoch seed as provider-session recovery tests, using
        # the complete schema's existing Run and session identity constraints.
        async with conn.transaction():
            await conn.execute("""insert into provider_session_heads
                (tenant_id,workspace_id,user_id,session_id,agent_id,engine,active_run_id,active_attempt_id)
                values ('tenant-a','workspace-a','user-a','session-a','agent-a','claude','run-a','attempt-a')""")
            await conn.execute("""insert into provider_session_epochs
                (id,tenant_id,workspace_id,user_id,session_id,agent_id,engine,epoch_number,provider_session_id,state)
                values ('epoch-a','tenant-a','workspace-a','user-a','session-a','agent-a','claude',1,%s,'active')""", (provider_id,))
            await conn.execute("""insert into provider_turn_receipts
                (id,tenant_id,workspace_id,user_id,session_id,agent_id,engine,epoch_id,run_id,attempt_id,
                 execution_spec_sha256,start_sequence,state)
                values ('turn-a','tenant-a','workspace-a','user-a','session-a','agent-a','claude',
                        'epoch-a','run-a','attempt-a',%s,1,'writing')""", ('a' * 64,))
            assert await read_usage_provider_session(conn,tenant_id="tenant-a",run_id="run-a",attempt_id="attempt-a") == provider_id
            assert await read_usage_provider_session(conn,tenant_id="tenant-a",run_id="run-a",attempt_id="wrong-attempt") is None
            assert await read_usage_provider_session(conn,tenant_id="wrong-tenant",run_id="run-a",attempt_id="attempt-a") is None
            repo = PostgresModelTokenUsage(provider_session=read_usage_provider_session)
            value = replace(snapshot(coverage="reported_final"), provider_session_id=provider_id)
            assert await record(repo, conn, value) == "accepted"
            assert (await query(repo, conn))["data"][0]["total_tokens"] == 20
            state = await (await conn.execute("select provider_session_id,coverage from model_token_usage_scopes")).fetchone()
            assert state == {"provider_session_id":provider_id,"coverage":"reported_final"}
            # A real turn must still match the current persisted Run subject.
            await conn.execute("update runs set user_id=null where id='run-a'")
            assert await read_usage_provider_session(conn,tenant_id="tenant-a",run_id="run-a",attempt_id="attempt-a") is None

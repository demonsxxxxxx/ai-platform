"""Real PostgreSQL receipt/Run/Attempt recovery and transactional fences."""
import asyncio
import os
from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.runs.api import ReconciliationAuthorityError
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.infrastructure import postgres
from app.sandbox.api import SandboxReconciliationReceiptAuthority
from app.sandbox.infrastructure.leases_postgres import lock_scoped_reconciliation_receipt
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.support.frozen_receipts import RESTORE, SCOPE, frozen_spec


receipt_authority = SandboxReconciliationReceiptAuthority(lock_scoped_reconciliation_receipt)
restore = partial(RunAttemptLifecycleService(postgres).restore_reconciliation,
                  receipt_authority=receipt_authority.is_current)


@pytest.fixture
async def receipt_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "frozen_receipt_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema}", row_factory=dict_row) as conn:
            yield conn

    try:
        async with connect() as conn:
            await conn.execute("""
                create table runs(id text primary key, tenant_id text, workspace_id text,
                  user_id text, session_id text, agent_id text, status text, cancel_requested_at timestamptz,
                  execution_kind text, skill_id text, context_snapshot_id text, input_json jsonb,
                  model_id text, model_value text, model_gateway_revision integer,
                  auto_compact_window integer, max_output_tokens integer);
                create table run_attempts(id text primary key, tenant_id text, run_id text,
                  status text, owner_kind text, owner_id text, owner_generation bigint, queue_attempt_id text,
                  execution_spec_schema_version text, execution_spec_sha256 text,
                  execution_spec_canonical_json text, execution_spec_json jsonb);
                create table sandbox_leases(id text primary key, tenant_id text, workspace_id text,
                  user_id text, session_id text, run_id text, attempt_id text, status text,
                  executor_terminal_json jsonb, executor_reconciliation_status text,
                  executor_reconciliation_claim_token text, lease_payload_json jsonb);
                create table committed_facts(kind text);
            """)
            spec = frozen_spec()
            await conn.execute("""
                insert into runs values('run-a','tenant-a','workspace-a','user-a','session-a','agent-a',
                'running',null,'harness_chat',null,'context-a',%s,'model-a','claude-test',3,200000,4096)
            """, (Jsonb({"executor_type": "claude-agent-worker"}),))
            await conn.execute("""
                insert into run_attempts values('attempt-a','tenant-a','run-a','running','queue_worker',
                'worker-original',4,'qat-a',%s,%s,%s,%s)
            """, (spec.to_mapping()["schema_version"], spec.spec_sha256, spec.canonical_json.decode(), Jsonb(spec.to_mapping())))
            await conn.execute("""
                insert into sandbox_leases values('lease-a','tenant-a','workspace-a','user-a','session-a',
                'run-a','attempt-a','active','{"status":"completed"}','claimed','claim-a','{"owner_generation":4}')
            """)
        yield connect
    finally:
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_restore_reads_real_canonical_column_and_preserves_legacy_extra_fields(receipt_db):
    async with receipt_db() as conn:
        restored = await restore(conn, **RESTORE)
        assert restored.execution_spec == frozen_spec()
        assert restored.execution_spec.to_mapping()["context_pack"]["conversation_context"]["selected_turn_count"] == 0
        assert restored.attempt["owner_generation"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["tenant_id", "run_id", "attempt_id", "lease_id", "claim_token",
                                 "workspace_id", "user_id", "session_id", "agent_id"])
async def test_restore_rejects_every_cross_scope_or_claim(receipt_db, key):
    args = {**RESTORE, "scope": dict(SCOPE)}
    if key in SCOPE:
        args["scope"][key] = "other"
    else:
        args[key] = "other"
    async with receipt_db() as conn:
        with pytest.raises(ReconciliationAuthorityError):
            await restore(conn, **args)
    async with receipt_db() as conn:
        assert (await (await conn.execute("select status from runs")).fetchone())["status"] == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "update run_attempts set owner_generation=5",
    "update run_attempts set owner_generation=3",
    "update run_attempts set owner_kind='reconciler', owner_generation=5",
    "update run_attempts set status='expired'",
    "update run_attempts set execution_spec_sha256=repeat('0',64)",
    "update run_attempts set execution_spec_canonical_json='{}'",
    "update run_attempts set execution_spec_json='{}'",
    "update runs set model_gateway_revision=4",
    "update runs set context_snapshot_id='other-context'",
    "update sandbox_leases set lease_payload_json='{}'",
    "update sandbox_leases set executor_terminal_json=null",
    "update sandbox_leases set executor_reconciliation_status='finalized'",
    "delete from run_attempts",
])
async def test_real_restore_fails_closed_on_authority_or_canonical_drift(receipt_db, change):
    async with receipt_db() as conn:
        await conn.execute(change)
        with pytest.raises(ReconciliationAuthorityError):
            await restore(conn, **RESTORE)


@pytest.mark.asyncio
async def test_cancellation_allows_only_exact_one_generation_advance(receipt_db):
    async with receipt_db() as conn:
        await conn.execute("update runs set cancel_requested_at=now()")
        await conn.execute("update run_attempts set status='cancel_requested', owner_generation=5")
        restored = await restore(conn, **RESTORE)
        assert restored.attempt["owner_generation"] == 5
        await conn.execute("update run_attempts set owner_generation=6")
        with pytest.raises(ReconciliationAuthorityError, match="owner_stale"):
            await restore(conn, **RESTORE)


@pytest.mark.asyncio
async def test_claim_lock_survives_until_transaction_and_old_claim_cannot_return(receipt_db):
    started = asyncio.Event()
    async def take_claim():
        async with receipt_db() as conn:
            started.set()
            await conn.execute("update sandbox_leases set executor_reconciliation_claim_token='new-claim'")
    task = None
    try:
        async with receipt_db() as conn:
            await restore(conn, **RESTORE)
            task = asyncio.create_task(take_claim())
            await started.wait()
            # The second connection must remain blocked by the receipt row lock.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.1)
        await asyncio.wait_for(task, 5)
        async with receipt_db() as conn:
            with pytest.raises(ReconciliationAuthorityError, match="claim_stale"):
                await restore(conn, **RESTORE)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_at", ["before_persistence", "terminal_cas"])
async def test_result_facts_are_never_committed_after_receipt_or_terminal_fence_loss(receipt_db, lost_at):
    from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService

    async def lock_run(conn, **kwargs):
        return await postgres.get_scoped_reconciliation_run(conn, tenant_id="tenant-a", run_id="run-a", scope=SCOPE)

    async def claim(conn, **kwargs):
        return lost_at != "before_persistence" and await receipt_authority.is_current(conn, **RESTORE)

    async def record(conn, *args, **kwargs):
        await conn.execute("insert into committed_facts values ('result-fact')")

    async def noop(*args, **kwargs):
        return None

    class Attempt:
        async def is_cancel_requested(self, conn):
            return False

        async def complete(self, conn, **kwargs):
            # The existing commit service must rollback all earlier facts when
            # the final owner CAS declines; real PG transaction, no list fake.
            return False

        async def classify_success_commit_block(self, conn):
            return "stale_terminal_state"

    service = WorkerResultCommitService(
        transaction_factory=receipt_db, lock_run=lock_run, reconciliation_claim_current=claim,
        materialize_answer=noop, persist_artifacts=record, persist_skill_snapshots=record,
        persist_assistant=record, append_user_event=record, append_hidden_event=record,
        persist_failure_event=record, public_failure_message=lambda result: "failure",
        prefers_cancelled=lambda result: False,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant-a", run_id="run-a"),
        result=SimpleNamespace(status="succeeded", artifacts=[], executor_payload={}, result={}),
        result_payload={"message": "done"}, artifact_records=[], skill_snapshot={},
        terminal_event_kwargs={}, attempt_id="attempt-a", trace_id="trace-a",
        reconciliation_lease_id="lease-a", reconciliation_claim_token="claim-a",
    )
    if lost_at == "before_persistence":
        with pytest.raises(RuntimeError, match="claim_lost"):
            await service.commit(command, attempt_authority=Attempt(), capabilities=None, release_runtime_lease=noop)
    else:
        assert (await service.commit(command, attempt_authority=Attempt(), capabilities=None,
                                     release_runtime_lease=noop)).status == "skipped"
    async with receipt_db() as conn:
        assert await (await conn.execute("select * from committed_facts")).fetchall() == []
        assert (await (await conn.execute("select status from runs")).fetchone())["status"] == "running"


@pytest.fixture
async def full_receipt_db():
    """Actual complete schema, canonical Attempt writer and Sandbox receipt writer.

    The smaller fixture above isolates read/rollback behavior. This fixture
    deliberately includes every production constraint and lifecycle trigger.
    """
    from pathlib import Path
    from app.platform.postgres import sandbox_leases
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "frozen_receipt_full_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema}", row_factory=dict_row) as conn:
            yield conn

    try:
        async with connect() as conn:
            await conn.execute(Path("app/schema.sql").read_text())
            await conn.execute("""
                insert into tenants(id,name) values('tenant-a','Tenant');
                insert into workspaces(id,tenant_id,name) values('workspace-a','tenant-a','Workspace');
                insert into users(id,tenant_id,display_name) values('user-a','tenant-a','User');
                insert into agents(id,tenant_id,name,agent_type) values('agent-a','tenant-a','Agent','chat');
                insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title)
                  values('session-a','tenant-a','workspace-a','user-a','agent-a','Session');
                insert into model_gateway_revisions(revision,base_url,api_key_ciphertext,key_fingerprint,created_by)
                  values(3,'https://gateway.example.test',decode('00','hex'),'aaaaaaaaaaaaaaaa','fixture');
                insert into runs(id,tenant_id,workspace_id,user_id,session_id,agent_id,
                  execution_kind,status,model_id,model_value,model_gateway_revision,auto_compact_window,max_output_tokens)
                  values('run-a','tenant-a','workspace-a','user-a','session-a','agent-a',
                  'harness_chat','queued','model-a','claude-test',3,200000,4096);
                insert into run_context_snapshots(id,tenant_id,workspace_id,user_id,session_id,run_id)
                  values('context-a','tenant-a','workspace-a','user-a','session-a','run-a');
                update runs set context_snapshot_id='context-a', input_json='{
                  "executor_type":"claude-agent-worker", "context_snapshot_id":"context-a",
                  "context_snapshot":{"context_snapshot_id":"context-a"}}' where id='run-a';
            """)
            spec = frozen_spec()
            attempt = await postgres.start_worker_run_attempt(
                conn, tenant_id="tenant-a", run_id="run-a", queue_attempt_id="qat-a",
                worker_id="worker-original", execution_spec=spec,
            )
            assert attempt["status"] == "running" and attempt["owner_generation"] == 4
            args = {**RESTORE, "attempt_id": attempt["id"]}
            await conn.execute("""
                insert into sandbox_leases(id,tenant_id,workspace_id,user_id,session_id,run_id,
                  attempt_id,sandbox_mode,provider,status,lease_payload_json,executor_reconciliation_context_json)
                values('lease-a','tenant-a','workspace-a','user-a','session-a','run-a',%s,
                  'ephemeral','fake','active','{"owner_generation":4}','{}')
            """, (attempt["id"],))
            await sandbox_leases.record_sandbox_executor_terminal(
                conn, tenant_id="tenant-a", run_id="run-a", attempt_id=attempt["id"], lease_id="lease-a",
                executor_status="completed", terminal_result={"run_id": "run-a", "status": "succeeded"},
            )
            claimed = await sandbox_leases.claim_sandbox_executor_reconciliations(
                conn, claim_token="claim-a", stale_after_seconds=300, limit=1,
            )
            assert [row["id"] for row in claimed] == ["lease-a"]
        yield connect, args, spec
    finally:
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_full_schema_actual_attempt_writer_round_trips_original_spec(full_receipt_db):
    connect, args, spec = full_receipt_db
    async with connect() as conn:
        restored = await restore(conn, **args)
        assert restored.execution_spec.canonical_json == spec.canonical_json
        assert restored.execution_spec.spec_sha256 == spec.spec_sha256
        assert restored.attempt["id"] == args["attempt_id"]
        assert (await (await conn.execute("select count(*) as n from run_attempts")).fetchone())["n"] == 1


@pytest.mark.asyncio
async def test_full_schema_real_cancellation_cas_rejects_old_success_and_rolls_back_message(full_receipt_db):
    from app.platform.postgres.errors import RepositoryConflictError
    connect, args, spec = full_receipt_db
    async with connect() as conn:
        original = await restore(conn, **args)
    # A different transaction performs the real legal cancellation transition.
    async with connect() as conn:
        await conn.execute("update runs set cancel_requested_at=now() where id='run-a'")
        cancelled = await postgres.request_run_attempt_cancel(
            conn, tenant_id="tenant-a", run_id="run-a", attempt_id=args["attempt_id"],
        )
        assert cancelled["status"] == "cancel_requested" and cancelled["owner_generation"] == 5
    with pytest.raises(RepositoryConflictError, match="transition_conflict"):
        async with connect() as conn:
            restored = await restore(conn, **args)
            # Cancellation recovery is allowed, but cannot confer success
            # authority on the captured pre-cancellation owner generation.
            assert restored.attempt["owner_generation"] == 5
            await conn.execute("""
                insert into messages(id,tenant_id,session_id,run_id,role,content)
                values('must-rollback','tenant-a','session-a','run-a','assistant','uncommitted')
            """)
            await postgres.transition_run_attempt(
                conn, tenant_id="tenant-a", run_id="run-a", attempt_id=args["attempt_id"],
                expected_status="running", requested_status="succeeded", expected_owner_kind="queue_worker",
                expected_owner_id=original.attempt["owner_id"], expected_owner_generation=args["expected_owner_generation"],
                terminal_reason="run_succeeded",
            )
    async with connect() as conn:
        assert await (await conn.execute("select id from messages where id='must-rollback'")).fetchall() == []
        assert (await (await conn.execute("select status from run_attempts")).fetchone())["status"] == "cancel_requested"
        assert (await restore(conn, **args)).execution_spec == spec


@pytest.mark.asyncio
async def test_full_schema_lost_claim_cannot_adopt_new_receipt_owner_or_write_terminal(full_receipt_db):
    from app.platform.postgres import sandbox_leases
    connect, args, _spec = full_receipt_db
    async with connect() as conn:
        await restore(conn, **args)
    async with connect() as conn:
        assert await sandbox_leases.retry_sandbox_executor_reconciliation(
            conn, lease_id="lease-a", claim_token="claim-a", error="test-owner-loss",
        )
        # Advance the explicit retry deadline; production SQL still owns the new claim.
        await conn.execute("update sandbox_leases set executor_reconciliation_retry_not_before=now()-interval '1 minute' where id='lease-a'")
        claimed = await sandbox_leases.claim_sandbox_executor_reconciliations(
            conn, claim_token="claim-new", stale_after_seconds=300, limit=1,
        )
        assert len(claimed) == 1
    with pytest.raises(ReconciliationAuthorityError, match="claim_stale"):
        async with connect() as conn:
            await restore(conn, **args)
            pytest.fail("a stale receipt cannot reach its terminal writer")
    async with connect() as conn:
        assert (await (await conn.execute("select status from runs")).fetchone())["status"] == "running"
        assert (await (await conn.execute("select status from run_attempts")).fetchone())["status"] == "running"
        assert (await restore(conn, **{**args, "claim_token": "claim-new"})).attempt["owner_generation"] == 4


@pytest.mark.asyncio
async def test_real_failure_settlement_and_frozen_restore_share_run_first_lock_order(full_receipt_db, monkeypatch):
    import logging
    from app import executor_reconciler
    connect, args, _spec = full_receipt_db
    failure_waiting_for_run = asyncio.Event()
    original_get_run = postgres.get_run

    async def observed_get_run(conn, **kwargs):
        failure_waiting_for_run.set()
        return await original_get_run(conn, **kwargs)

    monkeypatch.setattr(executor_reconciler, "transaction", connect)
    monkeypatch.setattr(postgres, "get_run", observed_get_run)
    task = None
    try:
        async with connect() as conn:
            # Hold Run before starting the actual failure handler on another
            # connection. If that handler takes lease first, restore deadlocks.
            await postgres.get_scoped_reconciliation_run(conn, tenant_id="tenant-a", run_id="run-a", scope=SCOPE)
            task = asyncio.create_task(executor_reconciler._terminalize_reconciliation_failure(
                {"id": "lease-a", "tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": args["attempt_id"]},
                claim_token="stale-claim", logger=logging.getLogger(__name__),
                v4_capabilities=None, attempt_lifecycle=None, lifecycle=None,
                run_diagnostics=None, reconciliation_error_code="synthetic-failure",
            ))
            await asyncio.wait_for(failure_waiting_for_run.wait(), 5)
            assert (await asyncio.wait_for(restore(conn, **args), 2)).attempt["id"] == args["attempt_id"]
        with pytest.raises(RuntimeError, match="claim_lost"):
            await asyncio.wait_for(task, 5)
        async with connect() as conn:
            assert (await (await conn.execute("select status from runs")).fetchone())["status"] == "running"
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import json
import subprocess
import time
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app import schema_migrations
from app.execution.application.run_delivery_intents import (
    FrozenQueueMessage,
    QueueDeliveryReceipt,
    RunDeliveryIntent,
    RunDeliveryIntents,
)
from app.execution.infrastructure import run_delivery_intents_postgres as store
from app.runs.application.queue_delivery_failure import QueueDeliveryFailureOwner
from app.runs.infrastructure import queue_delivery_failure_postgres


@pytest.fixture(scope="module")
async def delivery_db():
    """Run these SQL tests against a test-owned PostgreSQL 16 container."""
    name = f"run-delivery-{uuid4().hex[:12]}"
    started = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_USER=delivery_test",
            "-e",
            "POSTGRES_PASSWORD=delivery_test_only",
            "-e",
            "POSTGRES_DB=delivery_test",
            "-p",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    container_id = started.stdout.strip()
    try:
        published = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        port = published.rsplit(":", 1)[1]
        dsn = f"postgresql://delivery_test:delivery_test_only@127.0.0.1:{port}/delivery_test"
        admin = None
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                admin = await psycopg.AsyncConnection.connect(
                    dsn,
                    autocommit=True,
                    row_factory=dict_row,
                )
                break
            except psycopg.OperationalError:
                await asyncio.sleep(0.25)
        if admin is None:
            raise TimeoutError("test-owned PostgreSQL did not become ready")

        schema = f"delivery_{uuid4().hex}"
        await admin.execute(f'create schema "{schema}"')
        schema_name = f"delivery_actual_{uuid4().hex}"
        await admin.execute(f'create schema "{schema_name}"')

        @asynccontextmanager
        async def connect():
            conn = await psycopg.AsyncConnection.connect(
                dsn,
                options=f"-c search_path={schema}",
                row_factory=dict_row,
            )
            try:
                yield conn
            finally:
                await conn.close()

        async with connect() as conn:
            await conn.execute("""
                create table runs (
                  tenant_id text not null,
                  id text not null,
                  status text not null,
                  workspace_id text,
                  user_id text,
                  session_id text,
                  agent_id text,
                  execution_kind text,
                  skill_id text,
                  input_json jsonb,
                  context_snapshot_id text,
                  admitted_agent_profile_revision integer,
                  admitted_agent_profile_hash text,
                  copied_from_run_id text,
                  cancel_requested_at timestamptz,
                  terminalization_target text,
                  created_at timestamptz not null default now(),
                  primary key (tenant_id, id)
                );
                create table run_attempts (
                  tenant_id text not null,
                  run_id text not null,
                  primary key (tenant_id, run_id),
                  foreign key (tenant_id, run_id) references runs(tenant_id, id)
                );
                create table run_delivery_intents (
                  tenant_id text not null,
                  run_id text not null,
                  session_id text not null,
                  source text not null,
                  submission_id uuid,
                  user_message_id text,
                  queue_payload_json text not null,
                  queue_message_id text not null,
                  payload_sha256 text not null,
                  state text not null default 'pending',
                  claim_token uuid,
                  claimed_at timestamptz,
                  claim_expires_at timestamptz,
                  attempt_count integer not null default 0,
                  next_retry_at timestamptz not null default now(),
                  last_error_code text,
                  queue_position integer,
                  queue_admission_ordinal bigint,
                  queue_admission_source text,
                  delivered_at timestamptz,
                  suppressed_at timestamptz,
                  created_at timestamptz not null default now(),
                  updated_at timestamptz not null default now(),
                  primary key (tenant_id, run_id),
                  foreign key (tenant_id, run_id) references runs(tenant_id, id)
                );
                create index run_delivery_due on run_delivery_intents(state, next_retry_at, created_at, run_id);
                create table chat_submissions (
                  tenant_id text not null,
                  user_id text not null,
                      session_id text,
                  submission_id uuid not null,
                  run_id text,
                  state text not null,
                  queue_message_id text
                );
                create table messages (
                  id text not null,
                  tenant_id text not null,
                  session_id text not null,
                  run_id text not null,
                  role text not null,
                  created_at timestamptz not null default now()
                );
                create table run_events (
                  tenant_id text not null,
                  run_id text not null,
                  event_type text not null,
                  payload_json jsonb not null,
                  sequence bigint not null,
                  created_at timestamptz not null default now()
                );
            """)
            await conn.commit()

        @asynccontextmanager
        async def full_connect():
            conn = await psycopg.AsyncConnection.connect(
                dsn,
                options=f"-c search_path={schema_name}",
                row_factory=dict_row,
            )
            try:
                yield conn
            finally:
                await conn.close()

        @asynccontextmanager
        async def full_transaction():
            async with full_connect() as conn, conn.transaction():
                yield conn

        async def full_index_connection():
            return await psycopg.AsyncConnection.connect(
                dsn,
                autocommit=True,
                options=f"-c search_path={schema_name}",
                row_factory=dict_row,
            )

        async with full_transaction() as conn:
            await schema_migrations._ensure_ledger(conn)
            await conn.execute(
                "insert into schema_migrations(version, checksum_sha256) values (%s, %s)",
                (
                    schema_migrations.RUN_STATUS_SCHEMA_VERSION,
                    "5c62a5067c6d3554a600f1cdb34322888afd424c88c42ab4cafda5e4718691b1",
                ),
            )
        upgraded = await schema_migrations.apply_migrations(
            transaction_factory=full_transaction,
            index_connection_factory=full_index_connection,
        )
        repeated = await schema_migrations.apply_migrations(
            transaction_factory=full_transaction,
            index_connection_factory=full_index_connection,
        )
        assert upgraded["version"] == schema_migrations.TARGET_SCHEMA_VERSION
        assert repeated["status"] == "current"
        connect.full_schema = full_transaction
        connect.full_schema_name = schema_name

        yield connect
        await admin.execute(f'drop schema "{schema}" cascade')
        await admin.execute(f'drop schema "{schema_name}" cascade')
        await admin.close()
    finally:
        subprocess.run(
            ["docker", "stop", container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )


@pytest.fixture(autouse=True)
async def clean_delivery_tables(delivery_db):
    async with delivery_db() as conn, conn.transaction():
        await conn.execute("truncate run_delivery_intents, run_attempts, runs cascade")


async def seed(
    connect,
    run_id: str,
    *,
    status="queued",
    cancelled=False,
    attempt=False,
    target=None,
    submission_id=None,
    source="create_run",
    user_message_id=None,
    payload=None,
    digest=None,
):
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into runs(tenant_id,id,status,cancel_requested_at,terminalization_target) "
            "values ('tenant',%s,%s,case when %s then now() else null end,%s)",
            (run_id, status, cancelled, target),
        )
        if attempt:
            await conn.execute(
                "insert into run_attempts values ('tenant',%s)", (run_id,)
            )
        payload_json = payload or f'{{"run_id":"{run_id}"}}'
        digest = digest or sha256(run_id.encode()).hexdigest()
        return await store.insert(
            conn,
            tenant_id="tenant",
            run_id=run_id,
            session_id="session",
            source=source,
            submission_id=submission_id,
            user_message_id=user_message_id,
            payload_json=payload_json,
            message_id=digest,
            payload_sha256=digest,
        )


@pytest.mark.asyncio
async def test_insert_is_idempotent_across_native_uuid_and_string_values(delivery_db):
    connect = delivery_db
    submission_id = uuid4()
    payload = '{"frozen":"original"}'
    digest = "a" * 64
    await seed(
        connect,
        "insert-run",
        submission_id=str(submission_id).upper(),
        payload=payload,
        digest=digest,
    )
    async with connect() as conn, conn.transaction():
        existing = await store.insert(
            conn,
            tenant_id="tenant",
            run_id="insert-run",
            session_id="session",
            source="create_run",
            submission_id=str(submission_id),
            user_message_id=None,
            payload_json=payload,
            message_id=digest,
            payload_sha256=digest,
        )
    assert str(existing["submission_id"]) == str(submission_id)
    assert existing["queue_payload_json"] == payload


@pytest.mark.asyncio
async def test_claim_is_bounded_skips_locked_owner_and_claims_attempt_for_readback(
    delivery_db,
):
    connect = delivery_db
    await seed(connect, "blocked-cancel", cancelled=True)
    await seed(connect, "blocked-attempt", status="running", attempt=True)
    await seed(connect, "eligible-locked")
    await seed(connect, "eligible-next")
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_delivery_intents set next_retry_at=now()+interval '1 hour' "
            "where tenant_id='tenant' and run_id='blocked-attempt'"
        )

    async with connect() as locked, locked.transaction():
        await locked.execute(
            "select * from run_delivery_intents where tenant_id='tenant' and run_id='eligible-locked' for update"
        )
        async with connect() as scanner, scanner.transaction():
            claimed = await store.claim_due(scanner, limit=1, claim_ttl_seconds=30)
        assert [row["run_id"] for row in claimed] == ["eligible-next"]

    async with connect() as conn, conn.transaction():
        suppressed = await (
            await conn.execute(
                "select run_id from run_delivery_intents where state='suppressed'"
            )
        ).fetchall()
        assert [row["run_id"] for row in suppressed] == ["blocked-cancel"]
        row = await (
            await conn.execute(
                "select last_error_code from run_delivery_intents where run_id='eligible-next'"
            )
        ).fetchone()
        assert row["last_error_code"] is None
        claimed = await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
        assert [item["run_id"] for item in claimed] == ["eligible-locked"]

    async with connect() as conn, conn.transaction():
        claimed = await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
        assert [item["run_id"] for item in claimed] == []
        await conn.execute(
            "update run_delivery_intents set next_retry_at=now()-interval '1 second' "
            "where tenant_id='tenant' and run_id='blocked-attempt'"
        )
        attempted_claim = await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
        assert [item["run_id"] for item in attempted_claim] == ["blocked-attempt"]
        await conn.execute(
            "update run_delivery_intents set claim_expires_at=now()-interval '1 second', "
            "next_retry_at=now()-interval '1 second' where tenant_id='tenant' "
            "and run_id='blocked-attempt'"
        )
        reclaimed_attempt = await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
        assert [item["run_id"] for item in reclaimed_attempt] == ["blocked-attempt"]
        assert reclaimed_attempt[0]["claim_token"] != attempted_claim[0]["claim_token"]
    async with connect() as conn:
        rows = await (
            await conn.execute(
                "select run_id,last_error_code from run_delivery_intents "
                "where state='suppressed' order by run_id"
            )
        ).fetchall()
        assert [row["run_id"] for row in rows] == ["blocked-cancel"]
        assert rows[0]["last_error_code"] == "run_not_queued"


@pytest.mark.asyncio
async def test_cancelled_run_without_attempt_never_crosses_queue_boundary(delivery_db):
    connect = delivery_db
    await seed(connect, "cancelled-before-enqueue", cancelled=True)

    class Queue:
        def __init__(self):
            self.readbacks = 0
            self.enqueues = 0

        async def readback(self, _payload_json, *, message_id):
            self.readbacks += 1
            return None

        async def enqueue(self, _payload_json, *, message_id):
            self.enqueues += 1
            return QueueDeliveryReceipt(1, 1, message_id, "redis_enqueued")

    queue = Queue()

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    async def permanent_rejection(*_args, **_kwargs):
        return False

    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=None,
        queue=queue,
        transaction=transaction,
        scan_batch_size=10,
        claim_ttl_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=10,
        permanent_rejection_owner=permanent_rejection,
    )

    result = await manager.scan_once()

    assert result.claimed == 0
    assert result.suppressed == 0
    assert queue.readbacks == 0
    assert queue.enqueues == 0
    async with connect() as conn:
        row = await (
            await conn.execute(
                "select state,last_error_code from run_delivery_intents "
                "where tenant_id='tenant' and run_id='cancelled-before-enqueue'"
            )
        ).fetchone()
    assert row == {"state": "suppressed", "last_error_code": "run_not_queued"}


@pytest.mark.asyncio
async def test_two_scanners_never_receive_the_same_active_claim(delivery_db):
    connect = delivery_db
    await seed(connect, "contended")

    async def claim():
        async with connect() as conn, conn.transaction():
            return await store.claim_due(conn, limit=1, claim_ttl_seconds=30)

    first, second = await asyncio.gather(claim(), claim())
    claimed_ids = [row["run_id"] for row in [*first, *second]]
    assert claimed_ids == ["contended"]
    assert len([row for row in [*first, *second] if row["claim_token"]]) == 1


@pytest.mark.asyncio
async def test_history_candidate_scan_excludes_attempted_and_cancelled_runs_and_keeps_owner_facts(
    delivery_db,
):
    connect = delivery_db
    frozen_input = {
        "file_ids": [],
        "input": {"message": "saved"},
        "executor_type": "harness-chat",
        "execution_kind": "harness_chat",
        "skill_version": None,
        "release_decision": {},
        "skill_manifests": [],
        "context_snapshot_id": "ctx-chat",
        "context_snapshot": {
            "context_snapshot_id": "ctx-chat",
            "source": "chat_stream",
        },
        "model_id": "frozen-model",
        "model_value": "provider/frozen",
        "schema_version": "ai-platform.run-payload.v2",
    }
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into runs(tenant_id,id,status,workspace_id,user_id,session_id,"
            "agent_id,execution_kind,input_json,context_snapshot_id) values "
            "('tenant','history-chat','queued','workspace','user','session','agent',"
            "'harness_chat',%s::jsonb,'ctx-chat')",
            (json.dumps(frozen_input),),
        )
        await conn.execute(
            "insert into chat_submissions(tenant_id,user_id,session_id,submission_id,run_id,state) "
            "values ('tenant','user','session','123e4567-e89b-42d3-a456-426614174000',"
            "'history-chat','accepted_pending_enqueue')"
        )
        await conn.execute(
            "insert into messages(id,tenant_id,session_id,run_id,role) "
            "values ('message-chat','tenant','session','history-chat','user')"
        )
        await conn.execute(
            "insert into runs(tenant_id,id,status,cancel_requested_at) "
            "values ('tenant','history-cancelled','queued',now())"
        )
        await conn.execute(
            "insert into runs(tenant_id,id,status) values ('tenant','history-attempt','queued')"
        )
        await conn.execute(
            "insert into run_attempts(tenant_id,run_id) values ('tenant','history-attempt')"
        )

    async with connect() as conn, conn.transaction():
        rows = await store.list_historical_candidates(conn, limit=10)

    assert [row["id"] for row in rows] == ["history-chat"]
    assert rows[0]["input_json"] == frozen_input
    assert rows[0]["chat_submission_ids"] == ["123e4567-e89b-42d3-a456-426614174000"]
    assert rows[0]["user_message_ids"] == ["message-chat"]


@pytest.mark.asyncio
async def test_historical_preflight_maps_candidates_from_real_postgres_store(delivery_db):
    connect = delivery_db
    frozen_input = {
        "file_ids": [],
        "input": {"message": "saved"},
        "executor_type": "harness-chat",
        "execution_kind": "harness_chat",
        "skill_version": None,
        "release_decision": {},
        "skill_manifests": [],
        "context_snapshot_id": "ctx-preflight",
        "context_snapshot": {
            "context_snapshot_id": "ctx-preflight",
            "source": "chat_stream",
        },
        "model_id": "frozen-model",
        "model_value": "provider/frozen",
        "schema_version": "ai-platform.run-payload.v2",
    }
    submission_id = "123e4567-e89b-42d3-a456-426614174000"
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into runs(tenant_id,id,status,workspace_id,user_id,session_id,"
            "agent_id,execution_kind,input_json,context_snapshot_id) values "
            "('tenant','preflight-chat','queued','workspace','user','session','agent',"
            "'harness_chat',%s::jsonb,'ctx-preflight')",
            (json.dumps(frozen_input),),
        )
        await conn.execute(
            "insert into chat_submissions(tenant_id,user_id,session_id,submission_id,run_id,state) "
            "values ('tenant','user','session',%s,'preflight-chat','accepted_pending_enqueue')",
            (submission_id,),
        )
        await conn.execute(
            "insert into messages(id,tenant_id,session_id,run_id,role) "
            "values ('message-preflight','tenant','session','preflight-chat','user')"
        )

    class Codec:
        def freeze(self, payload):
            payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
            message_id = sha256(payload_json.encode("utf-8")).hexdigest()
            return FrozenQueueMessage(payload_json, message_id)

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    async def permanent_rejection(*_args, **_kwargs):
        return False

    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=Codec(),
        queue=None,
        transaction=transaction,
        scan_batch_size=10,
        claim_ttl_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=10,
        permanent_rejection_owner=permanent_rejection,
    )

    assert await manager.prepare_historical_delivery_intents() == 1
    async with connect() as conn:
        intent = await (
            await conn.execute(
                "select * from run_delivery_intents where tenant_id='tenant' "
                "and run_id='preflight-chat'"
            )
        ).fetchone()
    assert intent["source"] == "chat"
    assert str(intent["submission_id"]) == submission_id
    assert intent["user_message_id"] == "message-preflight"
    assert json.loads(intent["queue_payload_json"])["run_id"] == "preflight-chat"


@pytest.mark.asyncio
async def test_permanent_queue_rejection_owner_only_terminalizes_queued_runs_without_attempt(
    delivery_db, monkeypatch
):
    connect = delivery_db
    eligible = await seed(connect, "rejection-eligible")
    cancelled = await seed(connect, "rejection-cancelled", cancelled=True)
    attempted = await seed(connect, "rejection-attempted", attempt=True)
    terminalized: list[str] = []

    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(self, _conn, **_kwargs):
            return object()

    class Lifecycle:
        async def mark_run_enqueue_failed(self, _conn, *, tenant_id, user_id, run_id):
            assert tenant_id == "tenant"
            assert user_id is None
            terminalized.append(run_id)
            return type("Progress", (), {"did_transition": True})()

    class Capabilities:
        pending_admissions = PendingAdmissions()

    async def record_terminalization(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.runs.application.queue_delivery_failure.record_run_terminalization",
        record_terminalization,
    )
    owner = QueueDeliveryFailureOwner(
        persistence=queue_delivery_failure_postgres,
        lifecycle=Lifecycle(),
        capabilities=Capabilities(),
    )

    async with connect() as conn, conn.transaction():
        assert await owner(
            conn,
            intent=RunDeliveryIntent.from_record(eligible),
            reason="queue_payload_invalid",
        ) is True
        assert await owner(
            conn,
            intent=RunDeliveryIntent.from_record(cancelled),
            reason="queue_payload_invalid",
        ) is False
        assert await owner(
            conn,
            intent=RunDeliveryIntent.from_record(attempted),
            reason="queue_payload_invalid",
        ) is False

    assert terminalized == ["rejection-eligible"]


@pytest.mark.asyncio
async def test_stale_claim_cannot_write_receipt_after_reclaim(delivery_db):
    connect = delivery_db
    await seed(connect, "stale")
    async with connect() as conn, conn.transaction():
        first_claim = (await store.claim_due(conn, limit=1, claim_ttl_seconds=30))[0]
    stale = RunDeliveryIntent.from_record(first_claim)
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_delivery_intents set claim_expires_at=now()-interval '1 second' "
            "where tenant_id='tenant' and run_id='stale'"
        )
    async with connect() as conn, conn.transaction():
        replacement = (await store.claim_due(conn, limit=1, claim_ttl_seconds=30))[0]
    assert replacement["claim_token"] != first_claim["claim_token"]

    receipt = QueueDeliveryReceipt(1, 1, stale.message_id, "redis_readback_queued")
    async with connect() as conn, conn.transaction():
        recorded = await store.record_receipt(conn, intent=stale, receipt=receipt)
    assert recorded is False
    async with connect() as conn:
        row = await (
            await conn.execute(
                "select state,claim_token from run_delivery_intents where tenant_id='tenant' and run_id='stale'"
            )
        ).fetchone()
        assert row["state"] == "claimed"
        assert str(row["claim_token"]) == str(replacement["claim_token"])


@pytest.mark.asyncio
async def test_receipt_requires_the_claimed_persisted_payload_identity(delivery_db):
    connect = delivery_db
    payload = '{"tenant_id":"tenant","run_id":"identity","session_id":"session"}'
    digest = sha256(payload.encode("utf-8")).hexdigest()
    await seed(connect, "identity", payload=payload, digest=digest)
    async with connect() as conn, conn.transaction():
        claim = (await store.claim_due(conn, limit=1, claim_ttl_seconds=30))[0]
    intent = RunDeliveryIntent.from_record(claim)
    receipt = QueueDeliveryReceipt(1, 1, intent.message_id, "redis_readback_queued")
    tampered_intent = replace(intent, payload_json=payload + " ")

    async with connect() as conn, conn.transaction():
        assert await store.record_receipt(
            conn, intent=tampered_intent, receipt=receipt
        ) is False
    async with connect() as conn, conn.transaction():
        assert await store.record_receipt(
            conn,
            intent=intent,
            receipt=QueueDeliveryReceipt(1, 1, "f" * 64, "redis_readback_queued"),
        ) is False
    async with connect() as conn:
        row = await (
            await conn.execute(
                "select state,claim_token from run_delivery_intents "
                "where tenant_id='tenant' and run_id='identity'"
            )
        ).fetchone()
    assert row["state"] == "claimed"
    assert str(row["claim_token"]) == str(claim["claim_token"])


@pytest.mark.asyncio
async def test_queue_receipt_is_saved_after_attempt_creation_without_reenqueue(delivery_db):
    connect = delivery_db
    run_id = "receipt-after-attempt"
    payload = json.dumps(
        {"tenant_id": "tenant", "run_id": run_id, "session_id": "session"},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()
    await seed(connect, run_id, payload=payload, digest=digest)

    class Queue:
        def __init__(self):
            self.enqueues = 0
            self.manager = None

        async def readback(self, _payload_json, *, message_id):
            assert message_id == digest
            return None

        async def enqueue(self, payload_json, *, message_id):
            self.enqueues += 1
            assert payload_json == payload
            assert message_id == digest
            async with connect() as conn, conn.transaction():
                await conn.execute(
                    "insert into run_attempts(tenant_id,run_id) values ('tenant',%s)",
                    (run_id,),
                )
                await conn.execute(
                    "update runs set status='running' where tenant_id='tenant' and id=%s",
                    (run_id,),
                )
            assert self.manager is not None
            await self.manager.scan_once()
            return QueueDeliveryReceipt(3, 7, message_id, "redis_enqueued")

    queue = Queue()

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    async def permanent_rejection(*_args, **_kwargs):
        return False

    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=None,
        queue=queue,
        transaction=transaction,
        scan_batch_size=10,
        claim_ttl_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=10,
        permanent_rejection_owner=permanent_rejection,
    )
    queue.manager = manager

    first = await manager.deliver_one(tenant_id="tenant", run_id=run_id)
    second = await manager.deliver_one(tenant_id="tenant", run_id=run_id)
    assert first.state == "delivered"
    assert second.state == "delivered"
    assert first.receipt == QueueDeliveryReceipt(3, 7, digest, "redis_enqueued")
    assert queue.enqueues == 1
    async with connect() as conn:
        row = await (
            await conn.execute(
                "select state,queue_message_id,queue_position,queue_admission_ordinal "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        attempt_count = await (
            await conn.execute(
                "select count(*) as count from run_attempts "
                "where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert row == {
        "state": "delivered",
        "queue_message_id": digest,
        "queue_position": 3,
        "queue_admission_ordinal": 7,
    }
    assert attempt_count["count"] == 1


@pytest.mark.asyncio
async def test_scanner_recovers_receipt_after_enqueue_response_and_readback_fail(delivery_db):
    connect = delivery_db
    run_id = "lost-enqueue-ack-after-attempt"
    payload = json.dumps(
        {"tenant_id": "tenant", "run_id": run_id, "session_id": "session"},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()
    receipt = QueueDeliveryReceipt(4, 9, digest, "redis_enqueued")
    await seed(connect, run_id, payload=payload, digest=digest)

    class Queue:
        def __init__(self):
            self.readback_calls = 0
            self.enqueues = 0
            self.redis_receipt = None
            self.identities = []

        async def readback(self, payload_json, *, message_id):
            self.readback_calls += 1
            self.identities.append((payload_json, message_id))
            if self.readback_calls == 1:
                return None
            if self.readback_calls == 2:
                raise OSError("temporary readback failure")
            if self.readback_calls == 3:
                return None
            return self.redis_receipt

        async def enqueue(self, payload_json, *, message_id):
            self.enqueues += 1
            self.identities.append((payload_json, message_id))
            self.redis_receipt = receipt
            async with connect() as conn, conn.transaction():
                await conn.execute(
                    "insert into run_attempts(tenant_id,run_id) values ('tenant',%s)",
                    (run_id,),
                )
                await conn.execute(
                    "update runs set status='running' where tenant_id='tenant' and id=%s",
                    (run_id,),
                )
            raise OSError("enqueue response lost after acceptance")

    queue = Queue()

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    async def permanent_rejection(*_args, **_kwargs):
        return False

    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=None,
        queue=queue,
        transaction=transaction,
        scan_batch_size=10,
        claim_ttl_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=10,
        permanent_rejection_owner=permanent_rejection,
    )

    immediate = await manager.deliver_one(tenant_id="tenant", run_id=run_id)
    assert immediate.state == "pending"
    assert immediate.error_code == "queue_response_unknown"
    assert queue.enqueues == 1
    assert queue.readback_calls == 2
    async with connect() as conn:
        pending = await (
            await conn.execute(
                "select state,last_error_code,queue_payload_json,queue_message_id "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                "select count(*) as count from run_attempts "
                "where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert pending == {
        "state": "pending",
        "last_error_code": "queue_response_unknown",
        "queue_payload_json": payload,
        "queue_message_id": digest,
    }
    assert attempts["count"] == 1

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_delivery_intents set next_retry_at=now()-interval '1 second' "
            "where tenant_id='tenant' and run_id=%s",
            (run_id,),
        )

    waiting = await manager.scan_once()
    assert waiting.claimed == 1 and waiting.delivered == 0
    assert waiting.rescheduled == 1
    assert queue.enqueues == 1
    assert queue.readback_calls == 3
    async with connect() as conn:
        waiting_intent = await (
            await conn.execute(
                "select state,last_error_code,queue_payload_json,queue_message_id "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert waiting_intent == {
        "state": "pending",
        "last_error_code": "queue_receipt_not_yet_observed",
        "queue_payload_json": payload,
        "queue_message_id": digest,
    }

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_delivery_intents set next_retry_at=now()-interval '1 second' "
            "where tenant_id='tenant' and run_id=%s",
            (run_id,),
        )

    scanned = await manager.scan_once()
    assert scanned.claimed == 1 and scanned.delivered == 1
    assert queue.enqueues == 1
    assert queue.readback_calls == 4
    assert queue.identities == [(payload, digest)] * 5
    async with connect() as conn:
        delivered = await (
            await conn.execute(
                "select state,queue_payload_json,queue_message_id,queue_position,"
                "queue_admission_ordinal,queue_admission_source "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                "select count(*) as count from run_attempts "
                "where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert delivered == {
        "state": "delivered",
        "queue_payload_json": payload,
        "queue_message_id": digest,
        "queue_position": 4,
        "queue_admission_ordinal": 9,
        "queue_admission_source": "redis_enqueued",
    }
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_receipt_and_chat_projection_retry_after_owner_transaction_rollback(delivery_db):
    connect = delivery_db
    run_id = "chat-receipt-owner-rollback"
    submission_id = uuid4()
    user_message_id = "chat-user-message"
    payload = json.dumps(
        {"tenant_id": "tenant", "run_id": run_id, "session_id": "session"},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()
    receipt = QueueDeliveryReceipt(5, 12, digest, "redis_enqueued")
    await seed(
        connect,
        run_id,
        submission_id=submission_id,
        source="chat",
        user_message_id=user_message_id,
        payload=payload,
        digest=digest,
    )
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into chat_submissions(tenant_id,user_id,session_id,submission_id,"
            "run_id,state,queue_message_id) values ('tenant','user','session',%s,%s,"
            "'accepted_pending_enqueue',null)",
            (submission_id, run_id),
        )

    class Queue:
        def __init__(self):
            self.readback_calls = 0
            self.enqueues = 0
            self.redis_receipt = None
            self.identities = []

        async def readback(self, payload_json, *, message_id):
            self.readback_calls += 1
            self.identities.append((payload_json, message_id))
            return None if self.readback_calls == 1 else self.redis_receipt

        async def enqueue(self, payload_json, *, message_id):
            self.enqueues += 1
            self.identities.append((payload_json, message_id))
            self.redis_receipt = receipt
            async with connect() as conn, conn.transaction():
                await conn.execute(
                    "insert into run_attempts(tenant_id,run_id) values ('tenant',%s)",
                    (run_id,),
                )
                await conn.execute(
                    "update runs set status='running' where tenant_id='tenant' and id=%s",
                    (run_id,),
                )
            return receipt

    queue = Queue()
    chat_owner_calls = 0

    async def persist_chat_receipt(connection, *, intent, receipt):
        nonlocal chat_owner_calls
        chat_owner_calls += 1
        assert intent.submission_id == str(submission_id)
        assert receipt.message_id == digest
        updated = await connection.execute(
            "update chat_submissions set state='queued', queue_message_id=%s "
            "where tenant_id='tenant' and user_id='user' and session_id='session' "
            "and submission_id=%s and run_id=%s and state='accepted_pending_enqueue'",
            (receipt.message_id, submission_id, run_id),
        )
        assert updated.rowcount == 1
        if chat_owner_calls == 1:
            raise RuntimeError("chat_receipt_projection_failed")

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    async def permanent_rejection(*_args, **_kwargs):
        return False

    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=None,
        queue=queue,
        transaction=transaction,
        scan_batch_size=10,
        claim_ttl_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=10,
        permanent_rejection_owner=permanent_rejection,
        chat_receipt_owner=persist_chat_receipt,
    )

    with pytest.raises(RuntimeError, match="chat_receipt_projection_failed"):
        await manager.deliver_one(tenant_id="tenant", run_id=run_id)
    assert queue.enqueues == 1
    assert queue.readback_calls == 1
    async with connect() as conn:
        rolled_back_intent = await (
            await conn.execute(
                "select state,claim_token,queue_payload_json,queue_message_id,"
                "queue_position,queue_admission_ordinal,delivered_at "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        rolled_back_chat = await (
            await conn.execute(
                "select state,queue_message_id from chat_submissions "
                "where tenant_id='tenant' and submission_id=%s",
                (submission_id,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                "select count(*) as count from run_attempts "
                "where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert rolled_back_intent["state"] == "claimed"
    assert rolled_back_intent["claim_token"] is not None
    assert rolled_back_intent["queue_payload_json"] == payload
    assert rolled_back_intent["queue_message_id"] == digest
    assert rolled_back_intent["queue_position"] is None
    assert rolled_back_intent["queue_admission_ordinal"] is None
    assert rolled_back_intent["delivered_at"] is None
    assert rolled_back_chat == {
        "state": "accepted_pending_enqueue",
        "queue_message_id": None,
    }
    assert attempts["count"] == 1

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_delivery_intents set claim_expires_at=now()-interval '1 second', "
            "next_retry_at=now()-interval '1 second' where tenant_id='tenant' and run_id=%s",
            (run_id,),
        )

    scanned = await manager.scan_once()
    assert scanned.claimed == 1 and scanned.delivered == 1
    assert queue.enqueues == 1
    assert queue.readback_calls == 2
    assert queue.identities == [(payload, digest)] * 3
    assert chat_owner_calls == 2
    async with connect() as conn:
        delivered_intent = await (
            await conn.execute(
                "select state,submission_id,user_message_id,queue_payload_json,"
                "queue_message_id,queue_position,queue_admission_ordinal "
                "from run_delivery_intents where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        delivered_chat = await (
            await conn.execute(
                "select state,queue_message_id from chat_submissions "
                "where tenant_id='tenant' and submission_id=%s",
                (submission_id,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                "select count(*) as count from run_attempts "
                "where tenant_id='tenant' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
    assert delivered_intent == {
        "state": "delivered",
        "submission_id": submission_id,
        "user_message_id": user_message_id,
        "queue_payload_json": payload,
        "queue_message_id": digest,
        "queue_position": 5,
        "queue_admission_ordinal": 12,
    }
    assert delivered_chat == {"state": "queued", "queue_message_id": digest}
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_real_migration_upgrade_repeat_constraints_and_readiness(delivery_db):
    async with delivery_db.full_schema() as conn, conn.transaction():
        ledger = await (
            await conn.execute(
                "select version from schema_migrations where version = any(%s) order by version",
                ([
                    schema_migrations.RUN_STATUS_SCHEMA_VERSION,
                    schema_migrations.TARGET_SCHEMA_VERSION,
                ],),
            )
        ).fetchall()
        assert [row["version"] for row in ledger] == [
            schema_migrations.RUN_STATUS_SCHEMA_VERSION,
            schema_migrations.TARGET_SCHEMA_VERSION,
        ]
        status = await schema_migrations.schema_status(conn)
        assert status["ready"] is True
        assert status["constraint_definitions_current"] is True
        assert status["static_index_definitions_current"] is True

        await conn.execute(
            "insert into sessions(id, tenant_id, workspace_id, agent_id) "
            "values ('delivery-schema-session', 'default', 'default', 'general-agent')"
        )
        await conn.execute(
            "insert into runs(id, tenant_id, workspace_id, session_id, agent_id, "
            "execution_kind, skill_id, status) values "
            "('delivery-schema-run', 'default', 'default', 'delivery-schema-session', "
            "'general-agent', 'harness_chat', null, 'queued')"
        )

        async def invalid_insert(*, source="create_run", state="pending", session_id="session", message_id="a" * 64, attempts=0):
            await conn.execute(
                "insert into run_delivery_intents(tenant_id, run_id, session_id, source, "
                "queue_payload_json, queue_message_id, payload_sha256, state, attempt_count) "
                "values ('default', 'delivery-schema-run', %s, %s, '{}', %s, %s, %s, %s)",
                (session_id, source, message_id, "a" * 64, state, attempts),
            )

        invalid_rows = (
            ({"source": "other"}, "chk_run_delivery_intents_source"),
            ({"state": "other"}, "chk_run_delivery_intents_state"),
            ({"session_id": ""}, "chk_run_delivery_intents_identity"),
            ({"message_id": "invalid"}, "chk_run_delivery_intents_identity"),
            ({"attempts": -1}, "chk_run_delivery_intents_attempt_count"),
            ({"state": "claimed"}, "chk_run_delivery_intents_claim"),
            ({"state": "delivered"}, "chk_run_delivery_intents_receipt"),
            ({"state": "suppressed"}, "chk_run_delivery_intents_suppressed"),
        )
        for values, constraint_name in invalid_rows:
            with pytest.raises(psycopg.errors.CheckViolation) as raised:
                async with conn.transaction():
                    await invalid_insert(**values)
            assert raised.value.diag.constraint_name == constraint_name

        await invalid_insert()
        await conn.execute(
            "insert into sessions(id, tenant_id, workspace_id, agent_id) "
            "values ('delivery-schema-session-2', 'default', 'default', 'general-agent')"
        )
        await conn.execute(
            "insert into runs(id, tenant_id, workspace_id, session_id, agent_id, "
            "execution_kind, skill_id, status) values "
            "('delivery-schema-run-2', 'default', 'default', 'delivery-schema-session-2', "
            "'general-agent', 'harness_chat', null, 'queued')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.transaction():
                await conn.execute(
                    "insert into run_delivery_intents(tenant_id, run_id, session_id, source, "
                    "queue_payload_json, queue_message_id, payload_sha256) values "
                    "('default', 'delivery-schema-run-2', 'session-2', 'copy', '{}', %s, %s)",
                    ("a" * 64, "a" * 64),
                )

        await conn.execute("drop index idx_run_delivery_intents_due")
        not_ready = await schema_migrations.schema_status(conn)
        assert not_ready["ready"] is False
        assert not_ready["static_index_definitions_current"] is False

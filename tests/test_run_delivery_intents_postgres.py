from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import subprocess
import time
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.execution.application.run_delivery_intents import (
    QueueDeliveryReceipt,
    RunDeliveryIntent,
)
from app.execution.infrastructure import run_delivery_intents_postgres as store


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
                  cancel_requested_at timestamptz,
                  terminalization_target text,
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
            """)
            await conn.commit()

        yield connect
        await admin.execute(f'drop schema "{schema}" cascade')
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
            source="create_run",
            submission_id=submission_id,
            user_message_id=None,
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
async def test_claim_is_bounded_skips_a_locked_owner_and_suppression_makes_fair_progress(
    delivery_db,
):
    connect = delivery_db
    await seed(connect, "blocked-cancel", cancelled=True)
    await seed(connect, "blocked-attempt", attempt=True)
    await seed(connect, "eligible-locked")
    await seed(connect, "eligible-next")

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
        assert len(suppressed) == 1
        row = await (
            await conn.execute(
                "select last_error_code from run_delivery_intents where run_id='eligible-next'"
            )
        ).fetchone()
        assert row["last_error_code"] is None
        claimed = await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
        assert [item["run_id"] for item in claimed] == ["eligible-locked"]

    async with connect() as conn, conn.transaction():
        await store.claim_due(conn, limit=1, claim_ttl_seconds=30)
    async with connect() as conn:
        rows = await (
            await conn.execute(
                "select run_id,last_error_code from run_delivery_intents where state='suppressed' order by run_id"
            )
        ).fetchall()
        assert {row["run_id"] for row in rows} == {"blocked-attempt", "blocked-cancel"}
        assert {row["last_error_code"] for row in rows} == {
            "attempt_already_exists",
            "run_not_queued",
        }


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

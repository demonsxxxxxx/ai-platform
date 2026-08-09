"""Opt-in psycopg tests for the A1 adapter protocol, not app/schema.sql migrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.streaming import postgres


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


async def _connect(dsn: str, schema_name: str) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
    await _set_search_path(conn, schema_name)
    await conn.commit()
    return conn


@asynccontextmanager
async def _temporary_ledger_schema() -> AsyncIterator[tuple[str, str]]:
    """Create only A1 adapter tables in a unique temporary schema."""

    dsn = _postgres_dsn()
    schema_name = f"streaming_a1_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin, schema_name)
        await admin.execute(
            """
            create table run_events (
              id text primary key,
              tenant_id text not null,
              run_id text not null,
              trace_id text not null,
              schema_version text not null,
              sequence bigint not null,
              event_type text not null,
              stage text not null,
              message text not null,
              severity text not null,
              visible_to_user boolean not null,
              error_code text,
              latency_ms integer,
              input_token_count integer not null,
              output_token_count integer not null,
              total_token_count integer not null,
              estimated_cost_minor integer not null,
              payload_json jsonb not null,
              created_at timestamptz not null default now(),
              unique (tenant_id, run_id, sequence)
            );
            create table run_event_cursors (
              tenant_id text not null,
              run_id text not null,
              next_sequence bigint not null,
              updated_at timestamptz not null default now(),
              primary key (tenant_id, run_id)
            );
            create table run_event_batches (
              id text primary key,
              tenant_id text not null,
              run_id text not null,
              attempt_id text not null,
              batch_id text not null,
              event_ids_json jsonb not null default '[]'::jsonb,
              first_sequence bigint,
              through_sequence bigint,
              payload_digest text not null,
              projection_version text not null,
              item_count integer not null,
              first_source_sequence integer,
              through_source_sequence integer,
              callback_received_at timestamptz not null default now(),
              durable_committed_at timestamptz,
              unique (tenant_id, run_id, attempt_id, batch_id)
            );
            create table run_event_terminal_drains (
              tenant_id text not null,
              run_id text not null,
              attempt_id text not null,
              batch_id text not null,
              primary key (tenant_id, run_id, attempt_id)
            );
            """
        )
        yield dsn, schema_name
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


def _delta(value: str) -> postgres.LedgerEvent:
    return postgres.LedgerEvent(event_type="assistant_delta", stage="answer", payload={"delta": value})


@pytest.mark.asyncio
async def test_postgres_batch_receipt_replays_exactly_and_isolates_its_full_key():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        conn = await _connect(dsn, schema_name)
        try:
            async with conn.transaction():
                first = await postgres.append_batch(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="shared-batch",
                    events=[_delta("once")],
                )
            async with conn.transaction():
                replay = await postgres.append_batch(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="shared-batch",
                    events=[_delta("once")],
                )
            isolated = []
            for tenant_id, run_id, attempt_id in (
                ("tenant-b", "run-a", "attempt-a"),
                ("tenant-a", "run-b", "attempt-a"),
                ("tenant-a", "run-a", "attempt-b"),
            ):
                async with conn.transaction():
                    isolated.append(
                        await postgres.append_batch(
                            conn,
                            tenant_id=tenant_id,
                            run_id=run_id,
                            attempt_id=attempt_id,
                            batch_id="shared-batch",
                            events=[_delta(f"{tenant_id}-{run_id}-{attempt_id}")],
                        )
                    )

            assert first.duplicate is False
            assert replay.duplicate is True
            assert replay.event_ids == first.event_ids
            assert replay.payload_digest == first.payload_digest
            assert replay.item_count == 1
            assert all(receipt.duplicate is False for receipt in isolated)
            count = await conn.execute("select count(*) as count from run_event_batches")
            assert (await count.fetchone())["count"] == 4
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_batch_receipt_rejects_changed_content_for_same_identity():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        conn = await _connect(dsn, schema_name)
        try:
            async with conn.transaction():
                await postgres.append_batch(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-a",
                    events=[_delta("first")],
                )
            with pytest.raises(postgres.RunEventLedgerConflictError, match="run_event_batch_conflict"):
                async with conn.transaction():
                    await postgres.append_batch(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        batch_id="batch-a",
                        events=[_delta("changed")],
                    )
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_transaction_rollback_leaves_no_batch_event_or_cursor():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        conn = await _connect(dsn, schema_name)
        try:
            with pytest.raises(RuntimeError, match="rollback_requested"):
                async with conn.transaction():
                    await postgres.append_batch(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        batch_id="rollback-batch",
                        events=[_delta("discard")],
                    )
                    raise RuntimeError("rollback_requested")

            for table_name in ("run_event_batches", "run_events", "run_event_cursors"):
                count = await conn.execute(sql.SQL("select count(*) as count from {}").format(sql.Identifier(table_name)))
                assert (await count.fetchone())["count"] == 0
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_two_connections_allocate_unique_monotonic_cursors():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        first_conn = await _connect(dsn, schema_name)
        second_conn = await _connect(dsn, schema_name)
        gate = asyncio.Event()
        entered = 0
        lock = asyncio.Lock()

        async def append(conn: psycopg.AsyncConnection, value: str) -> postgres.EventReceipt:
            nonlocal entered
            async with conn.transaction():
                async with lock:
                    entered += 1
                    if entered == 2:
                        gate.set()
                await gate.wait()
                return await postgres.append_event(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    event=_delta(value),
                )

        try:
            receipts = await asyncio.gather(append(first_conn, "one"), append(second_conn, "two"))
            assert sorted(receipt.cursor.sequence for receipt in receipts) == [1, 2]
            cursor = await first_conn.execute(
                "select sequence from run_events where tenant_id = 'tenant-a' and run_id = 'run-a' order by sequence"
            )
            assert [row["sequence"] for row in await cursor.fetchall()] == [1, 2]
        finally:
            await first_conn.close()
            await second_conn.close()


@pytest.mark.asyncio
async def test_postgres_terminal_fence_rejects_concurrent_competitor_and_isolates_attempts():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        first_conn = await _connect(dsn, schema_name)
        second_conn = await _connect(dsn, schema_name)
        gate = asyncio.Event()
        entered = 0
        lock = asyncio.Lock()

        async def claim(conn: psycopg.AsyncConnection, *, attempt_id: str, batch_id: str) -> postgres.TerminalDrainReceipt:
            nonlocal entered
            async with conn.transaction():
                async with lock:
                    entered += 1
                    if entered == 2:
                        gate.set()
                await gate.wait()
                return await postgres.acquire_terminal_drain_fence(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id=attempt_id,
                    batch_id=batch_id,
                )

        try:
            results = await asyncio.gather(
                claim(first_conn, attempt_id="attempt-a", batch_id="batch-a"),
                claim(second_conn, attempt_id="attempt-a", batch_id="batch-b"),
                return_exceptions=True,
            )
            assert sum(result.duplicate is False for result in results if isinstance(result, postgres.TerminalDrainReceipt)) == 1
            assert any(
                isinstance(result, postgres.RunEventLedgerConflictError) and "terminal_drain_already_consumed" in str(result)
                for result in results
            )
            async with first_conn.transaction():
                isolated = await postgres.acquire_terminal_drain_fence(
                    first_conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-b",
                    batch_id="batch-b",
                )
            assert isolated.duplicate is False
        finally:
            await first_conn.close()
            await second_conn.close()


@pytest.mark.asyncio
async def test_postgres_read_event_rows_honors_cursor_limit_order_and_exact_scope():
    """Prove the adapter's replay contract in an isolated disposable schema."""

    async with _temporary_ledger_schema() as (dsn, schema_name):
        conn = await _connect(dsn, schema_name)
        try:
            async with conn.transaction():
                for value in ("one", "two", "three"):
                    await postgres.append_event(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        event=_delta(value),
                    )
                await postgres.append_event(
                    conn,
                    tenant_id="tenant-b",
                    run_id="run-a",
                    event=_delta("other-tenant"),
                )
                await postgres.append_event(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-b",
                    event=_delta("other-run"),
                )

            bounded = await postgres.read_event_rows(
                conn,
                tenant_id="tenant-a",
                cursor=postgres.RunCursor(run_id="run-a", sequence=0),
                limit=2,
            )
            unbounded = await postgres.read_event_rows(
                conn,
                tenant_id="tenant-a",
                cursor=postgres.RunCursor(run_id="run-a", sequence=1),
                limit=None,
            )

            assert [(row["sequence"], row["payload_json"]["delta"]) for row in bounded] == [
                (1, "one"),
                (2, "two"),
            ]
            assert [(row["sequence"], row["payload_json"]["delta"]) for row in unbounded] == [
                (2, "two"),
                (3, "three"),
            ]
        finally:
            await conn.close()

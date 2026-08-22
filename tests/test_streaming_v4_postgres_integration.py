from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from app.streaming import postgres as ledger
from app.streaming.redis import StreamAuthority
from app.streaming.v4 import (
    V4ProjectionError,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    recover_v4_rows,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _dsn() -> str:
    value = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not value:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return value


@asynccontextmanager
async def _connection_factory(dsn: str, schema_name: str):
    conn = await psycopg.AsyncConnection.connect(
        dsn,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )
    try:
        async with conn.transaction():
            yield conn
    finally:
        await conn.close()


async def _index_connection(dsn: str, schema_name: str):
    return await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )


async def _seed_run(conn: psycopg.AsyncConnection, suffix: str) -> tuple[str, str, str]:
    tenant = f"t_{suffix}"
    workspace = f"w_{suffix}"
    user = f"u_{suffix}"
    agent = f"a_{suffix}"
    skill = f"sk_{suffix}"
    session = f"s_{suffix}"
    run = f"r_{suffix}"
    attempt = f"att_{suffix}"
    await conn.execute("insert into tenants(id, name) values (%s, %s)", (tenant, tenant))
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
        (workspace, tenant, workspace),
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
        (user, tenant, user),
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values (%s, %s, %s, 'chat')",
        (agent, tenant, agent),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values (%s, %s, '1', 'fake')",
        (skill, skill),
    )
    await conn.execute(
        "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) values (%s, %s, %s, %s, %s, %s)",
        (session, tenant, workspace, user, agent, session),
    )
    await conn.execute(
        "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status) values (%s, %s, %s, %s, %s, %s, %s, 'running')",
        (run, tenant, workspace, session, user, agent, skill),
    )
    await conn.execute(
        """
        insert into sse_stream_authorities(
          tenant_id, run_id, attempt_id, design_id, projection_version,
          tenant_scope, stream_incarnation, state, open_event_id,
          open_payload_bytes, open_payload_digest, authorization_epoch,
          revocation_state
        ) values (%s, %s, %s, 'ai-platform.redis-streams-sse-event-channel.v4',
                  'public-stream-v4', %s, 2, 'confirmed', %s, '{}', 'digest', 4, 'active')
        """,
        (tenant, run, attempt, f"scope_{suffix}", f"open_{suffix}"),
    )
    return tenant, run, attempt


@asynccontextmanager
async def _schema():
    dsn = _dsn()
    schema_name = f"streaming_v4_evidence_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        def factory():
            return _connection_factory(dsn, schema_name)

        def index_factory():
            return _index_connection(dsn, schema_name)

        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        async with factory() as conn:
            async with conn.transaction():
                ids = await _seed_run(conn, uuid.uuid4().hex[:12])
        yield dsn, schema_name, ids
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


def _authority(tenant: str, run: str, attempt: str, *, incarnation: int = 2) -> StreamAuthority:
    return StreamAuthority(
        tenant_id=tenant,
        run_id=run,
        attempt_id=attempt,
        tenant_scope=f"scope_{tenant[2:]}",
        stream_incarnation=incarnation,
        state="confirmed",
        open_event_id="open",
        open_payload_bytes="{}",
        open_payload_digest="digest",
        authorization_epoch=4,
        revocation_state="active",
    )


def _metadata(tenant: str, run: str, attempt: str, *, incarnation: int = 2) -> dict[str, object]:
    return {
        "version": 1,
        "attempt_id": attempt,
        "stream_incarnation": incarnation,
        "authorization_epoch": 4,
        "message_id": opaque_message_id(tenant, run),
        "publication_state": "pending",
        "publication_attempts": 0,
        "execution_lease_id": "lease",
    }


async def _insert_v4_row(conn, *, tenant: str, run: str, attempt: str, sequence: int, event_id: str, delta: str, incarnation: int = 2, state: str = "pending") -> None:
    payload = {"delta": delta, "__stream_v4": _metadata(tenant, run, attempt, incarnation=incarnation)}
    await conn.execute(
        """
        insert into run_events(
          id, tenant_id, run_id, trace_id, schema_version, sequence,
          event_type, stage, message, severity, visible_to_user, payload_json,
          stream_publication_state, stream_publication_attempts,
          stream_publication_next_attempt_at
        ) values (%s, %s, %s, %s, 'ai-platform.event-envelope.v1', %s,
                  'message.delta', 'agent_kernel', '', 'info', true, %s::jsonb,
                  %s, 0, now())
        """,
        (event_id, tenant, run, f"trace_{run}", sequence, json.dumps(payload), state),
    )


@pytest.mark.asyncio
async def test_postgres_v4_transaction_rollback_and_duplicate_event_retry_converge():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            with pytest.raises(RuntimeError, match="rollback"):
                async with conn.transaction():
                    await ledger.append_event(
                        conn,
                        tenant_id=tenant,
                        run_id=run,
                        event=ledger.LedgerEvent(
                            event_type="message.delta",
                            stage="agent_kernel",
                            payload={"delta": "discard"},
                        ),
                    )
                    raise RuntimeError("rollback")
            async with conn.transaction():
                first = await ledger.append_batch(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    batch_id="retry-batch",
                    events=[ledger.LedgerEvent(event_type="message.delta", stage="agent_kernel", payload={"delta": "same"})],
                )
            async with conn.transaction():
                duplicate = await ledger.append_batch(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    batch_id="retry-batch",
                    events=[ledger.LedgerEvent(event_type="message.delta", stage="agent_kernel", payload={"delta": "same"})],
                )
            rows = await conn.execute("select count(*) as count from run_events where run_id = %s", (run,))
            assert (await rows.fetchone())["count"] == 1
            assert duplicate.duplicate is True
            assert duplicate.event_ids == first.event_ids


@pytest.mark.asyncio
async def test_postgres_v4_concurrent_publishers_allocate_one_ordered_run_sequence():
    async with _schema() as (dsn, schema_name, (tenant, run, _attempt)):
        first = await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema_name}", row_factory=dict_row)
        second = await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema_name}", row_factory=dict_row)
        gate = asyncio.Event()
        entered = 0
        mutex = asyncio.Lock()

        async def append(conn, value: str) -> int:
            nonlocal entered
            async with conn.transaction():
                async with mutex:
                    entered += 1
                    if entered == 2:
                        gate.set()
                await gate.wait()
                receipt = await ledger.append_event(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    event=ledger.LedgerEvent(event_type="message.delta", stage="agent_kernel", payload={"delta": value}),
                )
                return receipt.cursor.sequence

        try:
            sequences = await asyncio.gather(append(first, "one"), append(second, "two"))
            assert sorted(sequences) == [1, 2]
        finally:
            await first.close()
            await second.close()


@pytest.mark.asyncio
async def test_postgres_v4_recovery_rebinds_old_incarnation_and_strips_internal_fields():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=1, event_id="evt4_old", delta="old", incarnation=1, state="published")
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=2, event_id="evt4_trimmed", delta="trimmed", state="published")
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=3, event_id="evt4_latest", delta="latest", state="pending")
                authority = _authority(tenant, run, attempt)
                recovery = await recover_v4_rows(conn, tenant_id=tenant, run_id=run, authority=authority)
                assert [row["sequence"] for row in recovery.rows] == [1, 2, 3]
                trimmed = await recover_v4_rows(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    authority=authority,
                    after_sequence=1,
                    limit=1,
                )
                assert [row["sequence"] for row in trimmed.rows] == [2]
                internal = project_public_v4(recovery.rows[0], authority=authority)
                assert internal is not None
                public = project_public_envelope_v4(internal)
                assert public is not None
                assert public["seq"] == 1
                assert "tenant_scope" not in public
                assert "attempt_id" not in public
                assert "source" not in public

                with pytest.raises(V4ProjectionError, match="authority_scope"):
                    await recover_v4_rows(
                        conn,
                        tenant_id=tenant,
                        run_id=run,
                        authority=_authority(tenant, "other-run", attempt),
                    )


@pytest.mark.asyncio
async def test_postgres_v4_migration_index_constraint_and_rollback_preserve_event_facts():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=1, event_id="evt4_fact", delta="retained")
                index = await conn.execute(
                    """
                    select indexdef from pg_indexes
                    where schemaname = current_schema()
                      and indexname = 'idx_run_events_stream_publication_retry'
                    """
                )
                definition = (await index.fetchone())["indexdef"]
                assert "stream_publication_next_attempt_at" in definition
                assert "stream_publication_state = 'pending'" in definition
                constraint = await conn.execute(
                    """
                    select pg_get_constraintdef(oid) as definition
                    from pg_constraint
                    where conname = 'chk_run_events_stream_publication_state'
                    """
                )
                assert "pending" in (await constraint.fetchone())["definition"]
                await schema_migrations.rollback_v4_publication_migration(conn)
                columns = await conn.execute(
                    """
                    select column_name from information_schema.columns
                    where table_schema = current_schema() and table_name = 'run_events'
                      and column_name like 'stream_publication_%'
                    """
                )
                assert await columns.fetchall() == []
                fact = await conn.execute("select id, sequence from run_events where id = 'evt4_fact'")
                assert await fact.fetchone() == {"id": "evt4_fact", "sequence": 1}


@pytest.mark.asyncio
async def test_postgres_v4_terminal_row_needs_no_execution_lease_after_run_terminalizes():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await conn.execute("update runs set status = 'succeeded' where id = %s", (run,))
                await conn.execute("""
                    insert into sse_authority_leases(
                      id, tenant_id, run_id, api_instance_id, connection_id,
                      authorization_epoch, lease_not_after
                    ) values ('lease_evidence', %s, %s, 'api_evidence', 'conn_evidence', 4, now() + interval '15 seconds')
                """, (tenant, run))
                await conn.execute("""
                    update sse_authority_leases
                    set closed_at = now(), close_reason = 'worker_restart'
                    where id = 'lease_evidence'
                """)
                await conn.execute("update sse_stream_authorities set state = 'terminal' where tenant_id = %s and run_id = %s", (tenant, run))
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=1, event_id="evt4_terminal", delta="terminal")
                lease = await conn.execute("select count(*) as count from sse_authority_leases where run_id = %s and closed_at is null", (run,))
                assert (await lease.fetchone())["count"] == 0
                row = await conn.execute("select stream_publication_state from run_events where id = 'evt4_terminal'")
                assert (await row.fetchone())["stream_publication_state"] == "pending"

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql
import pytest

from app import schema_migrations
from tools.retire_legacy_sse_streams import cutoff, retire
from tests.support.db_transactions import event_loop_policy as event_loop_policy


class InventoryConnection:
    def __init__(self, *, legacy_schema=True):
        self.statements = []
        self.legacy_schema = legacy_schema

    async def execute(self, statement, params=()):
        self.statements.append(statement)
        return self

    async def fetchone(self):
        if "as legacy_schema" in self.statements[-1]:
            return {"legacy_schema": self.legacy_schema}
        return {"streams": 1, "active_runs": 1}


@asynccontextmanager
async def _legacy_schema():
    from tests.test_streaming_v4_postgres_integration import _connection_factory, _schema

    async with _schema() as context:
        dsn, schema, _ = context
        async with _connection_factory(dsn, schema) as conn:
            await conn.execute((Path(__file__).parent / "fixtures/legacy_sse_transport.sql").read_text(encoding="utf-8"))
        yield context


@pytest.mark.asyncio
async def test_legacy_retirement_is_read_only_by_default_and_refuses_active_runs():
    before = cutoff("2026-09-11T00:00:00Z")
    conn = InventoryConnection()
    report = await retire(conn, before=before, apply=False)
    assert report["applied"] is False and report["active_runs"] == 1
    assert conn.statements[0] == "set transaction read only"
    assert not any("update " in statement.lower() for statement in conn.statements)
    conn = InventoryConnection()
    with pytest.raises(RuntimeError, match="active_runs_must_finish_or_be_cancelled"):
        await retire(conn, before=before, apply=True)
    assert not any("update " in statement.lower() for statement in conn.statements)
    with pytest.raises(argparse.ArgumentTypeError):
        cutoff("2026-09-11T00:00:00")
    conn = InventoryConnection(legacy_schema=False)
    with pytest.raises(RuntimeError, match="pre_migration_schema_required"):
        await retire(conn, before=before, apply=True)
    assert not any("update " in statement.lower() for statement in conn.statements)


async def _insert_legacy_v4_row(conn, *, state="pending", **kwargs):
    from tests.test_streaming_v4_postgres_integration import _insert_v4_row

    await _insert_v4_row(conn, **kwargs)
    await conn.execute(
        """update run_events set stream_publication_state = %s,
           stream_publication_attempts = 0, stream_publication_next_attempt_at = now(),
           payload_json = jsonb_set(payload_json, '{__stream_v4}',
             (payload_json -> '__stream_v4') ||
             '{"publication_state":"pending","publication_attempts":0}'::jsonb)
           where id = %s""",
        (state, kwargs["event_id"]),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_version", ("v2.1", "v3", "v4"))
async def test_real_legacy_retirement_preserves_business_facts_answers_and_suppression(legacy_version):
    from tests.test_streaming_v4_postgres_integration import (
        _connection_factory, _index_connection,
    )

    async with _legacy_schema() as (dsn, schema, (tenant, run, attempt)):
        async def migrate():
            return await schema_migrations.apply_migrations(
                transaction_factory=lambda: _connection_factory(dsn, schema),
                index_connection_factory=lambda: _index_connection(dsn, schema),
            )

        before = datetime.now(timezone.utc) + timedelta(hours=1)
        digest = hashlib.sha256(b"{}").hexdigest()
        async with _connection_factory(dsn, schema) as conn:
            await conn.execute(
                "update runs set status = 'queued', result_json = '{\"message\":\"preserved\"}'::jsonb where tenant_id = %s and id = %s",
                (tenant, run),
            )
            await conn.execute("update sse_stream_authorities set design_id = %s", (f"ai-platform.redis-streams-sse-event-channel.{legacy_version}",))
            await _insert_legacy_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=1, event_id="evt4_preserved", payload={"delta": "preserved"})
            await _insert_legacy_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=2, event_id="evt4_suppressed", payload={"delta": "withheld"}, state="suppressed")
            await _insert_legacy_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=3, event_id="evt4_marker_only", payload={"delta": "marker-withheld"}, state=None)
            await conn.execute("update run_events set payload_json = jsonb_set(payload_json, '{__stream_v4}', ((payload_json -> '__stream_v4') - 'publication_state') || '{\"suppression_reason\":\"withheld\"}'::jsonb) where id = 'evt4_marker_only'")
            await conn.execute(
                "insert into sse_authority_leases(id, tenant_id, run_id, api_instance_id, connection_id, authorization_epoch, lease_not_after) values ('lease-cutover', %s, %s, 'api-test', 'connection-test', 4, now() + interval '15 seconds')",
                (tenant, run),
            )
            await conn.execute(
                """insert into sse_terminal_publication_intents(
                     id, tenant_id, run_id, attempt_id, stream_incarnation, schema_version, projection_version,
                     terminal_event_id, end_event_id, terminal_payload_bytes, terminal_payload_digest,
                     terminal_payload_size, end_payload_bytes, end_payload_digest, end_payload_size, emitted_at
                   ) values ('intent-cutover', %s, %s, %s, 2, 'ai-platform.stream-event.v4', 'public-stream-v4',
                     'sev_terminal', 'sev_end', '{}', %s, 2, '{}', %s, 2, '2026-09-11T00:00:00Z')""",
                (tenant, run, attempt, digest, digest),
            )
            await conn.execute(
                """insert into sse_stream_rebuilds(
                     id, tenant_id, run_id, attempt_id, source_incarnation, source_authorization_epoch,
                     origin_incarnation, origin_authorization_epoch, successor_incarnation, successor_authorization_epoch,
                     source_authority_fingerprint, source_cursor_sequence, source_through_sequence,
                     successor_open_event_id, successor_open_bytes, successor_open_digest,
                     claim_token_digest, claim_expires_at, item_count
                   ) values ('rebuild-cutover', %s, %s, %s, 2, 4, 2, 4, 3, 5, %s, 1, 1,
                     'open-rebuild', '{}', %s, %s, now() + interval '60 seconds', 1)""",
                (tenant, run, attempt, digest, digest, digest),
            )
            await conn.execute(
                "insert into sse_stream_rebuild_items(rebuild_id, sequence, event_id, event_type, canonical_envelope_bytes, envelope_digest) values ('rebuild-cutover', 1, 'evt4_preserved', 'message.delta', '{}', %s)",
                (digest,),
            )
            await conn.execute(
                """insert into run_event_batches(id, tenant_id, run_id, attempt_id, batch_id,
                     event_ids_json, first_sequence, through_sequence, payload_digest, projection_version, item_count)
                   values ('receipt-cutover', %s, %s, %s, 'batch-final', '["evt4_preserved","evt4_suppressed"]',
                     1, 2, %s, 'callback-receipt-v2.1', 2)""", (tenant, run, attempt, digest),
            )
            await conn.execute("insert into run_event_terminal_drains(tenant_id, run_id, attempt_id, batch_id) values (%s, %s, %s, 'batch-final')", (tenant, run, attempt))
            identity = await (await conn.execute("select workspace_id, user_id, session_id, agent_id, skill_id, execution_kind from runs where id = %s", (run,))).fetchone()
            spec = json.dumps({"schema_version": "ai-platform.execution-spec.v1", "tenant_id": tenant, "run_id": run, **identity}, sort_keys=True, separators=(",", ":"))
            await conn.execute(
                """insert into run_attempts(id, tenant_id, run_id, ordinal, status,
                   owner_kind, owner_id, queue_attempt_id, execution_spec_schema_version,
                   execution_spec_json, execution_spec_canonical_json, execution_spec_sha256)
                   values (%s, %s, %s, 1, 'created', 'queue_worker', 'worker-cutover',
                     'queue-cutover', 'ai-platform.execution-spec.v1', %s::jsonb, %s, %s)""",
                (attempt, tenant, run, spec, spec, hashlib.sha256(spec.encode()).hexdigest()),
            )
            for status in ("queued", "claimed", "running"):
                await conn.execute("update run_attempts set status = %s, owner_generation = owner_generation + 1, queue_message_id = 'queue-cutover' where id = %s", (status, attempt))
            await conn.execute("update run_attempts set status = 'succeeded', owner_generation = owner_generation + 1, started_at = now(), finished_at = now() where id = %s", (attempt,))
            receipts = await (await conn.execute("select * from run_event_batches")).fetchall()
            drains = await (await conn.execute("select * from run_event_terminal_drains")).fetchall()
            attempts = await (await conn.execute("select id, tenant_id, run_id, status from run_attempts")).fetchall()
            assert len(attempts) == 1

        with pytest.raises(psycopg.errors.RaiseException, match="legacy_sse_retirement_required"):
            await migrate()
        async with _connection_factory(dsn, schema) as conn:
            report = await retire(conn, before=before, apply=False)
            assert report["streams"] == 1 and report["active_runs"] == 0 and report["applied"] is False
            assert (await (await conn.execute("show transaction_read_only")).fetchone())["transaction_read_only"] == "on"

        async with _connection_factory(dsn, schema) as conn:
            assert (await retire(conn, before=before, apply=True))["applied"] is True
            rows = await (await conn.execute("select id, visible_to_user, payload_json, stream_publication_state from run_events order by sequence")).fetchall()
            assert [row["visible_to_user"] for row in rows] == [True, False, False]
            assert [row["payload_json"]["delta"] for row in rows] == ["preserved", "withheld", "marker-withheld"]
            assert all(row["stream_publication_state"] is None for row in rows)
            assert all("publication_state" not in row["payload_json"]["__stream_v4"] for row in rows)
            assert (await (await conn.execute("select state from sse_terminal_publication_intents")).fetchone())["state"] == "superseded"
            assert (await (await conn.execute("select state from sse_stream_rebuilds")).fetchone())["state"] == "aborted"
            assert (await (await conn.execute("select closed_at is not null as closed from sse_authority_leases")).fetchone())["closed"] is True
            await retire(conn, before=before, apply=True)
            assert await (await conn.execute("select authorization_epoch, revocation_state from sse_stream_authorities")).fetchone() == {"authorization_epoch": 5, "revocation_state": "effective"}
            facts = await (await conn.execute("select id, sequence, event_type, visible_to_user, payload_json, created_at from run_events order by sequence")).fetchall()

        assert (await migrate())["status"] == "applied"
        assert (await migrate())["status"] == "current"
        async with _connection_factory(dsn, schema) as conn:
            assert await (await conn.execute("select status, result_json from runs")).fetchone() == {"status": "succeeded", "result_json": {"message": "preserved"}}
            assert await (await conn.execute("select id, sequence, event_type, visible_to_user, payload_json, created_at from run_events order by sequence")).fetchall() == facts
            assert await (await conn.execute("select * from run_event_batches")).fetchall() == receipts
            assert await (await conn.execute("select * from run_event_terminal_drains")).fetchall() == drains
            assert await (await conn.execute("select id, tenant_id, run_id, status from run_attempts")).fetchall() == attempts
            from app.streaming.authority import RunCursor
            from app.streaming.postgres import read_event_rows
            from app.streaming.api import project_persisted_message_delta_v4

            history = await read_event_rows(conn, tenant_id=tenant, cursor=RunCursor(run, 0), limit=10)
            public = [project_persisted_message_delta_v4(row, tenant_id=tenant, run_id=run) for row in history]
            assert public[0]["payload"] == {"delta": "preserved"}
            assert public[1:] == [None, None]
            for relation in ("sse_terminal_publication_intents", "sse_stream_rebuilds", "sse_stream_rebuild_items"):
                assert (await (await conn.execute("select to_regclass(%s) as relation", (f"{schema}.{relation}",))).fetchone())["relation"] is None
            assert await (await conn.execute("select attname from pg_attribute where attrelid = 'run_events'::regclass and attname like 'stream_publication_%' and not attisdropped")).fetchall() == []
            assert await (await conn.execute("select index_name from schema_index_migrations where index_name in ('idx_run_events_stream_publication_retry','idx_run_events_stream_publication_claim','idx_run_events_v4_due_scope')")).fetchall() == []
            assert (await schema_migrations.schema_status(conn))["ready"] is True
        async with _connection_factory(dsn, schema) as conn:
            with pytest.raises(RuntimeError, match="pre_migration_schema_required"):
                await retire(conn, before=before, apply=False)


@pytest.mark.asyncio
async def test_retirement_requires_writers_to_quiesce_before_the_scope_is_checked():
    from tests.test_streaming_v4_postgres_integration import _connection_factory

    async with _legacy_schema() as (dsn, schema, _):
        async with _connection_factory(dsn, schema) as writer:
            await writer.execute("lock table runs in row exclusive mode")
            async with _connection_factory(dsn, schema) as conn:
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    await retire(conn, before=datetime.now(timezone.utc) + timedelta(hours=1), apply=True)


@pytest.mark.asyncio
async def test_retirement_ddl_never_falls_through_to_another_schema():
    from tests.test_streaming_v4_postgres_integration import _index_connection

    source = schema_migrations.schema_sql()
    start = source.index("-- Old producers must be stopped")
    retirement_ddl = source[start:source.index("do $$\ndeclare\n  unique_index_present", start)]
    async with _legacy_schema() as (dsn, legacy_schema, _):
        current_schema = legacy_schema + "_new"
        async with await _index_connection(dsn, legacy_schema) as conn:
            await conn.execute("update sse_stream_authorities set revocation_state = 'effective'")
            await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(current_schema)))
            try:
                await conn.execute(sql.SQL("set search_path to {}, {}, public").format(sql.Identifier(current_schema), sql.Identifier(legacy_schema)))
                await conn.execute("create table run_events(payload_json jsonb)")
                await conn.execute(retirement_ddl)
                assert (await (await conn.execute("select to_regclass(%s) as relation", (f"{legacy_schema}.sse_stream_rebuilds",))).fetchone())["relation"] is not None
                assert (await (await conn.execute("select to_regclass(%s) as relation", (f"{legacy_schema}.idx_sse_stream_authority_pending",))).fetchone())["relation"] is not None
            finally:
                await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(current_schema)))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("before_retirement", "commit", "none"))
async def test_cli_distinguishes_commit_uncertainty_from_a_refusal(monkeypatch, capsys, failure):
    from tools import retire_legacy_sse_streams as cli

    closed = []

    @asynccontextmanager
    async def transaction():
        yield object()
        if failure == "commit":
            raise ConnectionError("synthetic-private-diagnostic")

    async def operation(conn, **kwargs):
        if failure == "before_retirement":
            raise ValueError("synthetic-private-diagnostic")
        return {"applied": True}

    async def close():
        closed.append(True)

    monkeypatch.setattr(cli, "transaction", transaction)
    monkeypatch.setattr(cli, "retire", operation)
    monkeypatch.setattr(cli, "close_pool", close)
    code = await cli.main_async(argparse.Namespace(before=cutoff("2026-09-11T00:00:00Z"), apply=True))
    output = capsys.readouterr()
    report = json.loads(output.out if failure == "none" else output.err)
    assert report["applied"] is {"before_retirement": False, "commit": None, "none": True}[failure]
    assert code == (0 if failure == "none" else 1)
    if failure == "commit":
        assert report["error"] == "legacy_sse_commit_uncertain"
    assert "synthetic-private-diagnostic" not in output.out + output.err
    assert closed == [True]


@pytest.mark.asyncio
async def test_every_legacy_publication_column_requires_explicit_retirement():
    from tests.test_streaming_v4_postgres_integration import _connection_factory, _index_connection

    async with _legacy_schema() as (dsn, schema, (tenant, run, _)):
        before = datetime.now(timezone.utc) + timedelta(hours=1)
        async with _connection_factory(dsn, schema) as conn:
            await conn.execute("update runs set status = 'succeeded'")
            await retire(conn, before=before, apply=True)
            await conn.execute("insert into run_events(id, tenant_id, run_id, event_type, stage, sequence) values ('legacy-column', %s, %s, 'progress', 'execution', 1)", (tenant, run))
        cases = (
            {"stream_publication_state": "pending"},
            {"stream_publication_attempts": 1},
            {"stream_publication_redis_id": "1-0"},
            {"stream_publication_last_error": "legacy-error"},
            {"stream_publication_claim_token": "claim", "stream_publication_claim_expires_at": before},
            {"stream_publication_next_attempt_at": before},
        )
        columns = tuple(key for case in cases for key in case)
        for values in cases:
            async with _connection_factory(dsn, schema) as conn:
                assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values)
                await conn.execute(sql.SQL("update run_events set {} where id = 'legacy-column'").format(assignments), tuple(values.values()))
            with pytest.raises(psycopg.errors.RaiseException, match="legacy_sse_retirement_required"):
                await schema_migrations.apply_migrations(
                    transaction_factory=lambda: _connection_factory(dsn, schema),
                    index_connection_factory=lambda: _index_connection(dsn, schema),
                )
            async with _connection_factory(dsn, schema) as conn:
                await retire(conn, before=before, apply=True)
                row = await (await conn.execute("select * from run_events where id = 'legacy-column'")).fetchone()
                assert all(row[key] is None for key in columns)
                assert row["visible_to_user"] is False
        # A missing marker column must not hide a surviving legacy receipt.
        async with _connection_factory(dsn, schema) as conn:
            await conn.execute("update run_events set stream_publication_redis_id = '1-0' where id = 'legacy-column'")
            await conn.execute("alter table run_events drop column stream_publication_state")
        with pytest.raises(psycopg.errors.RaiseException, match="legacy_sse_retirement_required"):
            await schema_migrations.apply_migrations(
                transaction_factory=lambda: _connection_factory(dsn, schema),
                index_connection_factory=lambda: _index_connection(dsn, schema),
            )
        async with _connection_factory(dsn, schema) as conn:
            row = await (await conn.execute("select stream_publication_redis_id from run_events where id = 'legacy-column'")).fetchone()
            assert row["stream_publication_redis_id"] == "1-0"

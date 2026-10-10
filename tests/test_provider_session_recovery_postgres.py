"""Native transcript integrity and cancellation drain with real PostgreSQL."""

import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.context.infrastructure import provider_epochs
from app.context.domain.provider_sessions import ProviderSessionConflictError
from app.routes import runtime_callbacks
from app.runs.domain.attempt_lifecycle import decide_run_attempt_transition


PROVIDER_ID = "00000000-0000-4000-8000-000000000031"
INITIAL = [
    {"type": "user", "uuid": "user-1", "message": {"role": "user", "content": "OLD"}},
    {
        "type": "assistant",
        "uuid": "answer-1",
        "message": {"role": "assistant", "content": "ANSWER"},
    },
]


@pytest.fixture
async def recovery_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "provider_recovery_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema}",
            row_factory=dict_row,
        ) as conn:
            yield conn

    try:
        async with connect() as conn:
            await conn.execute("""
                create table sessions(tenant_id text, workspace_id text, user_id text,
                  id text, agent_id text, unique(tenant_id,workspace_id,user_id,id,agent_id));
                create table runs(tenant_id text, id text, status text,
                  cancel_requested_at timestamptz, primary key(tenant_id,id));
                create table run_attempts(tenant_id text, id text, run_id text, status text,
                  owner_kind text, owner_id text, owner_generation bigint,
                  execution_spec_sha256 text, execution_spec_schema_version text,
                  execution_spec_json jsonb, primary key(tenant_id,id));
                create table sandbox_leases(id text primary key, tenant_id text,
                  run_id text, attempt_id text, status text, released_at timestamptz,
                  expires_at timestamptz, created_at timestamptz default now(), lease_payload_json jsonb);
                insert into sessions values('tenant','workspace','user','session','agent');
                insert into runs values('tenant','run','running',null);
            """)
            source = (
                Path(__file__).resolve().parents[1] / "app/schema.sql"
            ).read_text()
            start = source.index("create table if not exists provider_session_heads")
            end = source.index("-- A populated pre-#511", start)
            await conn.execute(source[start:end])
            await conn.execute("""
                insert into provider_session_heads(tenant_id,workspace_id,user_id,session_id,
                  agent_id,engine,active_run_id) values('tenant','workspace','user','session','agent','claude','run');
            """)
            await conn.execute(
                """
                insert into provider_session_epochs(id,tenant_id,workspace_id,user_id,session_id,
                  agent_id,engine,epoch_number,provider_session_id,state)
                values('epoch','tenant','workspace','user','session','agent','claude',1,%s,'bootstrapping');
            """,
                (PROVIDER_ID,),
            )
            context = {
                "execution_mode": "empty_start",
                "provider_epoch_id": "epoch",
                "provider_session_id": PROVIDER_ID,
                "source_sha256": "a" * 64,
                "current_message_id": "current-user",
            }
            await conn.execute(
                """
                insert into run_attempts values('tenant','attempt','run','running','queue_worker',
                  'worker',2,%s,'ai-platform.execution-spec.v2',%s);
            """,
                ("d" * 64, Jsonb({"context_pack": {"conversation_context": context}})),
            )
            await conn.execute(
                """
                insert into sandbox_leases(id,tenant_id,run_id,attempt_id,status,expires_at,lease_payload_json)
                values('lease','tenant','run','attempt','active',now()+interval '5 minutes',%s);
            """,
                (
                    Jsonb(
                        {
                            "attempt_id": "attempt",
                            "owner_generation": 2,
                            "callback_token_id": "cbt",
                        }
                    ),
                ),
            )
            await operation(conn, "load")
            await operation(conn, "append", entries=INITIAL, expected_sequence=1)
        yield connect
    finally:
        await admin.execute(
            sql.SQL("drop schema {} cascade").format(sql.Identifier(schema))
        )
        await admin.close()


async def operation(conn, action, *, subpath=None, **kwargs):
    return await provider_epochs.callback_provider_epoch(
        conn,
        tenant_id="tenant",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        agent_id="agent",
        run_id="run",
        attempt_id="attempt",
        provider_session_id=PROVIDER_ID,
        action=action,
        subpath=subpath,
        entries=kwargs.get("entries", []),
        expected_sequence=kwargs.get("expected_sequence"),
    )


async def request_cancel(conn):
    # Use the real lifecycle decision, then persist that exact status/generation.
    decision = decide_run_attempt_transition(
        current_status="running",
        requested_status="cancel_requested",
        owner_generation=2,
        expected_owner_generation=2,
    )
    await conn.execute("update runs set cancel_requested_at=now() where id='run'")
    await conn.execute(
        "update run_attempts set status=%s,owner_generation=%s where id='attempt'",
        (decision.status, decision.owner_generation),
    )


async def test_native_history_and_child_paths_have_complete_receipt_coverage(
    recovery_db,
):
    async with recovery_db() as conn:
        child = [
            {
                "type": "assistant",
                "uuid": "child-1",
                "message": {"role": "assistant", "content": "CHILD"},
            }
        ]
        await operation(
            conn,
            "append",
            entries=child,
            expected_sequence=3,
            subpath="subagents/agent-a",
        )
        main = await operation(conn, "load")
        assert main["entries"] == INITIAL and main["next_sequence"] == 4
        assert (await operation(conn, "list_subkeys"))["subpaths"] == [
            "subagents/agent-a"
        ]
        assert (await operation(conn, "load", subpath="subagents/agent-a"))[
            "entries"
        ] == child


@pytest.mark.parametrize("value", [-0.0, 0.0, 1.0, 1e-7, 1e16, -1e21, 1e308, 5e-324])
async def test_original_numeric_representation_survives_jsonb_and_ack_retry(recovery_db, value):
    entries = [{"type": "assistant", "uuid": "numeric", "message": {
        "role": "assistant", "content": [{"type": "tool_use", "id": "tool", "name": "synthetic",
            "input": {"numbers": [value, 1000000000000000000001], "flag": True}}],
    }}]
    async with recovery_db() as conn:
        await operation(conn, "append", entries=entries, expected_sequence=3)
        retry = await operation(conn, "append", entries=entries, expected_sequence=3)
        assert retry["next_sequence"] == 4
        restored = (await operation(conn, "load"))["entries"][-1]
        assert json.dumps(restored, sort_keys=True) == json.dumps(entries[0], sort_keys=True)
        recovered = restored["message"]["content"][0]["input"]["numbers"][0]
        assert type(recovered) is float and math.copysign(1, recovered) == math.copysign(1, value)
        assert (await operation(conn, "list_subkeys"))["next_sequence"] == 4


@pytest.mark.parametrize("value,unavailable", [
    (-0.0, True), (1e16, True), (-1e21, True), (1e308, True),
    (0.0, False), (1.0, False), (1e-7, False), (5e-324, False),
    (1000000000000000000001, False),
])
async def test_legacy_numeric_bytes_are_verified_or_explicitly_unavailable(recovery_db, value, unavailable):
    entry = {"type": "assistant", "uuid": "legacy-numeric", "value": value}
    async with recovery_db() as conn:
        await operation(conn, "append", entries=[entry], expected_sequence=3)
        # An old writer stored only JSONB and the original append receipt.
        await conn.execute("update provider_session_entries set entry_canonical_json=null")
        for action in ("load", "list_subkeys"):
            if unavailable:
                with pytest.raises(ProviderSessionConflictError, match="provider_session_integrity_unavailable"):
                    await operation(conn, action)
            else:
                result = await operation(conn, action)
                assert result["next_sequence"] == 4
                if action == "load":
                    assert result["entries"][-1] == entry


@pytest.mark.parametrize("change", [
    "update provider_session_entries set entry_canonical_json='{}'::json where sequence=2",
    "update provider_session_entries set entry_canonical_json='[]'::json where sequence=2",
    "update provider_session_entries set entry_canonical_json='null'::json where sequence=2",
    "update provider_session_entries set entry_canonical_json=' {\"type\":\"assistant\"} '::json where sequence=2",
    "update provider_session_entries set entry_canonical_json=jsonb_set(entry_json,'{message,content}','\"CHANGED\"')::json, entry_json=jsonb_set(entry_json,'{message,content}','\"CHANGED\"') where sequence=2",
])
async def test_canonical_original_must_match_jsonb_shape_and_receipt(recovery_db, change):
    async with recovery_db() as conn:
        await conn.execute(change)
        for action in ("load", "list_subkeys"):
            with pytest.raises(ProviderSessionConflictError, match="provider_session_integrity_mismatch"):
                await operation(conn, action)


async def test_consistently_changed_original_and_jsonb_still_fail_receipt(recovery_db):
    changed = json.dumps({**INITIAL[1], "message": {"role": "assistant", "content": "CHANGED"}},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    async with recovery_db() as conn:
        await conn.execute("update provider_session_entries set entry_canonical_json=%s::json, "
                           "entry_json=%s::jsonb where sequence=2", (changed, changed))
        with pytest.raises(ProviderSessionConflictError, match="provider_session_integrity_mismatch"):
            await operation(conn, "load")


async def test_invalid_original_json_is_rejected_without_poisoning_valid_history(recovery_db):
    async with recovery_db() as conn:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            async with conn.transaction():
                await conn.execute("update provider_session_entries set entry_canonical_json='invalid'::json")
        assert (await operation(conn, "load"))["entries"] == INITIAL


async def test_representation_migration_preserves_legacy_rows_without_backfill(recovery_db):
    async with recovery_db() as conn:
        await conn.execute("alter table provider_session_entries drop column entry_canonical_json")
        source = (Path(__file__).resolve().parents[1] / "app/schema.sql").read_text()
        migration = next(line for line in source.splitlines() if line.startswith(
            "alter table provider_session_entries add column if not exists entry_canonical_json"))
        await conn.execute(migration)
        await conn.execute(migration)
        rows = await (await conn.execute(
            "select entry_canonical_json from provider_session_entries order by sequence"
        )).fetchall()
        assert rows == [{"entry_canonical_json": None}, {"entry_canonical_json": None}]
        assert (await operation(conn, "load"))["entries"] == INITIAL
        assert (await operation(conn, "list_subkeys"))["next_sequence"] == 3


@pytest.mark.parametrize(
    "corrupt",
    [
        "delete from provider_session_entries where sequence=2",
        "update provider_session_entries set entry_json=jsonb_set(entry_json,'{message,content}','\"CHANGED\"') where sequence=2",
        "update provider_session_entries set sequence=3 where sequence=2",
        "update provider_session_epochs set next_sequence=2",
        "update provider_session_epochs set entry_count=1",
        "update provider_session_epochs set transcript_bytes=transcript_bytes+1",
        "update provider_session_entries set subpath='other-child' where sequence=2",
        "delete from provider_session_append_receipts",
        "update provider_session_append_receipts set batch_sha256=repeat('f',64)",
    ],
)
async def test_corrupt_or_unverifiable_history_is_not_resumed(recovery_db, corrupt):
    async with recovery_db() as conn:
        await conn.execute(corrupt)
        for action in ("load", "list_subkeys"):
            code = (
                "provider_session_integrity_unavailable"
                if corrupt == "delete from provider_session_append_receipts"
                else "provider_session_integrity_mismatch"
            )
            with pytest.raises(ProviderSessionConflictError, match=code):
                await operation(conn, action)


async def test_identical_append_ack_retry_does_not_duplicate_entries(recovery_db):
    async with recovery_db() as conn:
        repeated = await operation(conn, "append", entries=INITIAL, expected_sequence=1)
        assert repeated["last_sequence"] == 2 and repeated["next_sequence"] == 3
        assert (await operation(conn, "load"))["entries"] == INITIAL
        with pytest.raises(ProviderSessionConflictError, match="append_conflict"):
            await operation(
                conn,
                "append",
                entries=[{"type": "user", "content": "different"}],
                expected_sequence=1,
            )


async def test_cancel_then_tail_saves_only_original_writer_without_tool_authority(
    recovery_db,
):
    cancelled = asyncio.Event()

    async def cancel():
        async with recovery_db() as conn:
            await request_cancel(conn)
        cancelled.set()

    async def drain():
        await cancelled.wait()
        async with recovery_db() as conn:
            # Ordinary tool/input callback authority must stay revoked.
            with pytest.raises(HTTPException, match="sandbox_runtime_attempt_inactive"):
                await runtime_callbacks._require_current_runtime_attempt(
                    conn,
                    tenant_id="tenant",
                    run_id="run",
                    attempt_id="attempt",
                    callback_token_id="cbt",
                )
            tail = await operation(
                conn,
                "append",
                entries=[{"type": "last-prompt", "lastPrompt": "TAIL"}],
                expected_sequence=3,
            )
            assert tail["next_sequence"] == 4
            await runtime_callbacks._require_current_runtime_attempt(
                conn,
                tenant_id="tenant",
                run_id="run",
                attempt_id="attempt",
                callback_token_id="cbt",
                allow_provider_tail=True,
            )
            with pytest.raises(
                HTTPException, match="sandbox_runtime_owner_generation_stale"
            ):
                await runtime_callbacks._require_current_runtime_attempt(
                    conn,
                    tenant_id="tenant",
                    run_id="run",
                    attempt_id="attempt",
                    callback_token_id="stale",
                    allow_provider_tail=True,
                )
            retry = await operation(
                conn,
                "append",
                entries=[{"type": "last-prompt", "lastPrompt": "TAIL"}],
                expected_sequence=3,
            )
            assert retry["next_sequence"] == 4
            for action in ("load", "list_subkeys"):
                with pytest.raises(ProviderSessionConflictError):
                    await operation(conn, action)

    await asyncio.gather(cancel(), drain())


@pytest.mark.parametrize(
    "revoke",
    [
        "update runs set status='cancelled'",
        "update run_attempts set status='cancelled'",
        "update run_attempts set owner_kind='reconciler',owner_generation=4",
        "update run_attempts set owner_generation=4",
        "update sandbox_leases set expires_at=now()-interval '1 second'",
        "update sandbox_leases set status='released',released_at=now()",
        "update provider_session_epochs set writer_attempt_id='other-attempt'",
        "update provider_session_epochs set writer_run_id=null,writer_attempt_id=null,writer_owner_generation=null",
        "update provider_session_heads set active_attempt_id='other-attempt'",
    ],
)
async def test_cancel_tail_cannot_claim_new_writer_or_use_stale_authority(
    recovery_db, revoke
):
    async with recovery_db() as conn:
        await request_cancel(conn)
        await conn.execute(revoke)
        with pytest.raises(ProviderSessionConflictError):
            await operation(
                conn, "append", entries=[{"type": "last-prompt"}], expected_sequence=3
            )
        cursor = await conn.execute(
            "select count(*) as n from provider_session_entries"
        )
        assert (await cursor.fetchone())["n"] == 2

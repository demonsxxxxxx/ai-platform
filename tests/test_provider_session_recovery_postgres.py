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
from tests.support.db_transactions import event_loop_policy as event_loop_policy
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
                  id text, agent_id text, status text not null default 'active',
                  unique(tenant_id,workspace_id,user_id,id,agent_id));
                create table runs(tenant_id text, id text, status text,
                  cancel_requested_at timestamptz, primary key(tenant_id,id));
                create table run_attempts(tenant_id text, id text, run_id text, status text,
                  owner_kind text, owner_id text, owner_generation bigint,
                  execution_spec_sha256 text, execution_spec_schema_version text,
                  execution_spec_json jsonb, started_at timestamptz, primary key(tenant_id,id));
                create table sandbox_leases(id text primary key, tenant_id text,
                  run_id text, attempt_id text, status text, released_at timestamptz,
                  expires_at timestamptz, created_at timestamptz default now(), lease_payload_json jsonb);
                insert into sessions(tenant_id,workspace_id,user_id,id,agent_id)
                  values('tenant','workspace','user','session','agent');
                insert into runs values('tenant','run','running',null);
            """)
            source = (
                Path(__file__).resolve().parents[1] / "app/schema.sql"
            ).read_text()
            start = source.index("create table if not exists provider_session_heads")
            end = source.index("-- A populated pre-#511", start)
            await conn.execute(source[start:end])
            await conn.execute((Path(__file__).resolve().parents[1] /
                                "app/context/infrastructure/conversation_recovery_schema.sql").read_text())
            # Match production prepare: epoch 1 has already consumed its number.
            await conn.execute("""
                insert into provider_session_heads(tenant_id,workspace_id,user_id,session_id,
                  agent_id,engine,active_run_id,next_epoch_number)
                values('tenant','workspace','user','session','agent','claude','run',2);
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
                insert into run_attempts(tenant_id,id,run_id,status,owner_kind,owner_id,owner_generation,
                  execution_spec_sha256,execution_spec_schema_version,execution_spec_json)
                values('tenant','attempt','run','running','queue_worker',
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
        include_child_entries=kwargs.get("include_child_entries", False),
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
                await runtime_callbacks._recheck_callback_authority(
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
            await runtime_callbacks._recheck_callback_authority(
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
                await runtime_callbacks._recheck_callback_authority(
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


@pytest.mark.parametrize("lost_sequence", [3, 131, 259])
async def test_chunked_sdk_append_reuses_actual_pg_receipts_after_ack_loss(recovery_db, lost_sequence):
    """Actual host ledger SQL/constraints; transport alone simulates a lost ACK."""
    from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter, ClaudeSessionStoreTransportError
    adapter = ClaudeSessionStoreAdapter(
        callback_url="https://callback.example.test", callback_token="synthetic",
        callback_token_id="synthetic", run_id="run", attempt_id="attempt", provider_session_id=PROVIDER_ID,
    )
    lost = False
    sequences = []

    async def request(payload):
        nonlocal lost
        async with recovery_db() as conn:
            response = await operation(conn, payload["action"], subpath=payload["subpath"],
                                       entries=payload["entries"], expected_sequence=payload["expected_sequence"])
        if payload["action"] == "append":
            sequences.append(payload["expected_sequence"])
            if not lost and payload["expected_sequence"] == lost_sequence:
                lost = True
                raise ClaudeSessionStoreTransportError("provider_session_callback_timeout")
        return response

    adapter._request = request
    key = {"session_id": PROVIDER_ID}
    await adapter.load(key)
    entries = [{"type": "assistant", "uuid": f"chunk-{i}"} for i in range(257)]
    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_timeout"):
        await adapter.append(key, entries)
    assert adapter.accepted_final_sequence == 2
    await adapter.append(key, entries)
    assert adapter.accepted_final_sequence == 259
    assert sequences == [s for n in (3, 131, 259) for s in ([n, n] if n == lost_sequence else [n])]
    async with recovery_db() as conn:
        # This load verifies the entire contiguous ledger and every batch hash.
        loaded = await operation(conn, "load")
        assert loaded["entries"] == INITIAL + entries
        assert loaded["next_sequence"] == 260
        receipts = await (await conn.execute(
            "select expected_sequence,entry_count,last_sequence from provider_session_append_receipts order by expected_sequence"
        )).fetchall()
        assert [(r["expected_sequence"], r["entry_count"], r["last_sequence"]) for r in receipts] == [(1, 2, 2), (3, 128, 130), (131, 128, 258), (259, 1, 259)]


async def test_chunked_append_legal_cancel_tail_and_restart_cannot_adopt_dirty_epoch(recovery_db):
    from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter

    def make_adapter():
        return ClaudeSessionStoreAdapter(
            callback_url="https://callback.example.test", callback_token="synthetic",
            callback_token_id="synthetic", run_id="run", attempt_id="attempt", provider_session_id=PROVIDER_ID,
        )

    adapter = make_adapter()
    cancelled = False

    async def request(payload):
        nonlocal cancelled
        async with recovery_db() as conn:
            response = await operation(conn, payload["action"], subpath=payload["subpath"],
                                       entries=payload["entries"], expected_sequence=payload["expected_sequence"])
        if payload["action"] == "append" and not cancelled:
            async with recovery_db() as conn:
                await request_cancel(conn)
            cancelled = True
        return response

    adapter._request = request
    key = {"session_id": PROVIDER_ID}
    await adapter.load(key)
    await adapter.append(key, [{"type": "last-prompt", "n": i} for i in range(129)])
    assert adapter.accepted_final_sequence == 131
    async with recovery_db() as conn:
        rows = await (await conn.execute(
            "select expected_sequence,owner_generation from provider_session_append_receipts order by expected_sequence"
        )).fetchall()
        assert [(r["expected_sequence"], r["owner_generation"]) for r in rows] == [(1, 2), (3, 2), (131, 2)]
        # Model the existing host recovery disposition after an uncommitted
        # writer disappears. A fresh adapter must not bypass that disposition.
        await conn.execute("update provider_session_epochs set state='dirty'")
    restarted = make_adapter()
    restarted._request = request
    with pytest.raises(ProviderSessionConflictError, match="epoch_unavailable"):
        await restarted.load(key)
    assert restarted.accepted_final_sequence is None


async def test_child_read_view_is_opt_in_complete_and_uses_one_verified_scan(
    recovery_db, monkeypatch
):
    child_a_first = [{"type": "assistant", "uuid": "child-a-first", "value": 1}]
    child_b = [{"type": "assistant", "uuid": "child-b", "value": 2}]
    child_a_last = [{"type": "agent_metadata", "agentType": "synthetic"}]
    main_tail = [{"type": "assistant", "uuid": "main-tail", "value": 3}]
    async with recovery_db() as conn:
        for sequence, path, entries in (
            (3, "subagents/agent-a", child_a_first),
            (4, "", main_tail),
            (5, "subagents/agent-b", child_b),
            (6, "subagents/agent-a", child_a_last),
        ):
            await operation(
                conn, "append", subpath=path, entries=entries,
                expected_sequence=sequence,
            )
        plain = await operation(conn, "list_subkeys")
        assert "child_entries" not in plain
        main = await operation(conn, "load", include_child_entries=True)
        assert "child_entries" not in main
        assert main["entries"] == INITIAL + main_tail
        verified = provider_epochs._verified_provider_entries
        scans = 0

        async def count_verified(*args, **kwargs):
            nonlocal scans
            scans += 1
            return await verified(*args, **kwargs)

        monkeypatch.setattr(provider_epochs, "_verified_provider_entries", count_verified)
        view = await operation(conn, "list_subkeys", include_child_entries=True)
        assert scans == 1
        assert view["next_sequence"] == 7
        assert view["subpaths"] == ["subagents/agent-a", "subagents/agent-b"]
        assert set(view["child_entries"]) == set(view["subpaths"])
        assert "" not in view["child_entries"]
        assert view["child_entries"] == {
            "subagents/agent-a": child_a_first + child_a_last,
            "subagents/agent-b": child_b,
        }


async def test_opted_in_empty_child_read_view_is_complete(recovery_db):
    async with recovery_db() as conn:
        result = await operation(conn, "list_subkeys", include_child_entries=True)
        assert result["subpaths"] == []
        assert result["child_entries"] == {}
        assert result["next_sequence"] == 3


@pytest.mark.parametrize("corrupt,error", [
    ("delete from provider_session_entries where sequence=3",
     "provider_session_integrity_mismatch"),
    ("update provider_session_entries set entry_json='{}'::jsonb where sequence=3",
     "provider_session_integrity_mismatch"),
    ("update provider_session_entries set subpath='subagents/changed' where sequence=3",
     "provider_session_integrity_mismatch"),
    ("delete from provider_session_append_receipts where expected_sequence=3",
     "provider_session_integrity_unavailable"),
    ("update provider_session_append_receipts set batch_sha256=repeat('f',64) where expected_sequence=3",
     "provider_session_integrity_mismatch"),
    ("update provider_session_epochs set next_sequence=3",
     "provider_session_integrity_mismatch"),
])
async def test_child_read_view_does_not_bypass_child_or_receipt_integrity(
    recovery_db, corrupt, error
):
    async with recovery_db() as conn:
        await operation(
            conn, "append", subpath="subagents/agent-a",
            entries=[{"type": "assistant", "uuid": "child-integrity"}],
            expected_sequence=3,
        )
        await conn.execute(corrupt)
        with pytest.raises(ProviderSessionConflictError, match=error):
            await operation(conn, "list_subkeys", include_child_entries=True)


@pytest.mark.parametrize("revoke", [
    "update provider_session_epochs set state='dirty'",
    "update provider_session_heads set active_attempt_id='other-attempt'",
    "update run_attempts set owner_generation=3",
    "update sandbox_leases set expires_at=now()-interval '1 second'",
    "update sandbox_leases set status='released',released_at=now()",
    "update runs set status='cancelled'",
])
async def test_child_read_view_retains_current_epoch_and_attempt_fences(
    recovery_db, revoke
):
    async with recovery_db() as conn:
        await conn.execute(revoke)
        with pytest.raises(ProviderSessionConflictError):
            await operation(conn, "list_subkeys", include_child_entries=True)


async def test_child_read_view_cannot_use_cancel_tail_authority(recovery_db):
    async with recovery_db() as conn:
        await request_cancel(conn)
        with pytest.raises(ProviderSessionConflictError):
            await operation(conn, "list_subkeys", include_child_entries=True)
        tail = await operation(
            conn, "append", entries=[{"type": "last-prompt", "lastPrompt": "TAIL"}],
            expected_sequence=3,
        )
        assert tail["next_sequence"] == 4


class _ProviderReadHttpBridge:
    """Replace only HTTP with real callback SQL over this test's PostgreSQL schema."""

    def __init__(self, connect):
        self.connect = connect
        self.requests = []
        self.scans = {"entries": 0, "receipts": 0}

    def client(self, **_kwargs):
        bridge = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            @asynccontextmanager
            async def stream(self, method, _url, *, json, headers):
                assert method == "POST"
                payload = json
                bridge.requests.append((payload["action"], dict(headers)))
                opted_in = headers.get("X-AI-Platform-Provider-Read-View") == "children-v1"
                async with bridge.connect() as conn:
                    class RecordingConnection:
                        async def execute(self, query, *args, **kwargs):
                            query_text = str(query)
                            if "from provider_session_entries " in query_text:
                                bridge.scans["entries"] += 1
                            if "from provider_session_append_receipts " in query_text:
                                bridge.scans["receipts"] += 1
                            return await conn.execute(query, *args, **kwargs)

                    response = await operation(
                        RecordingConnection(), payload["action"],
                        subpath=payload["subpath"], entries=payload["entries"],
                        expected_sequence=payload["expected_sequence"],
                        include_child_entries=opted_in,
                    )
                yield _ProviderReadHttpResponse(response)

        return Client()


class _ProviderReadHttpResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    async def aiter_bytes(self):
        yield json.dumps(self.payload, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("child_count", [0, 8, 64])
async def test_real_sdk_child_materialization_uses_two_complete_pg_scans(
    recovery_db, monkeypatch, tmp_path, child_count
):
    """Use real SDK restore functions and adapter, without CLI/auth-file materialization."""
    from claude_agent_sdk._internal.session_resume import _load_candidate, _materialize_subkeys
    from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter

    expected = {}
    async with recovery_db() as conn:
        sequence = 3
        for i in range(child_count):
            path = f"subagents/agent-{i}"
            entries = [
                {"type": "assistant", "uuid": f"child-{i}", "content": f"CHILD-{i}"},
                {"type": "agent_metadata", "agentType": f"synthetic-{i}"},
            ]
            expected[path] = entries
            await operation(conn, "append", subpath=path, entries=entries,
                            expected_sequence=sequence)
            sequence += len(entries)
    bridge = _ProviderReadHttpBridge(recovery_db)
    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        bridge.client,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="https://callback.example.test", callback_token="synthetic",
        callback_token_id="synthetic", run_id="run", attempt_id="attempt",
        provider_session_id=PROVIDER_ID,
    )
    assert await adapter.load(PROVIDER_ID) == INITIAL
    assert await _load_candidate(adapter, "synthetic-project", PROVIDER_ID, 60) == (PROVIDER_ID, INITIAL)
    project = tmp_path / "p"  # Keep SDK child filenames below Windows MAX_PATH.
    project.mkdir(parents=True)
    await _materialize_subkeys(adapter, tmp_path, project, "synthetic-project", PROVIDER_ID, 60)
    assert [action for action, _headers in bridge.requests] == ["load", "list_subkeys"]
    assert "X-AI-Platform-Provider-Read-View" not in bridge.requests[0][1]
    assert bridge.requests[1][1]["X-AI-Platform-Provider-Read-View"] == "children-v1"
    assert bridge.scans == {"entries": 2, "receipts": 2}
    assert adapter.accepted_final_sequence == 2 + child_count * 2
    for path, entries in expected.items():
        target = project / PROVIDER_ID / path
        restored = [json.loads(line) for line in target.with_suffix(".jsonl").read_text().splitlines()]
        assert restored == entries[:1]
        assert json.loads(target.with_suffix(".meta.json").read_text()) == {
            "agentType": entries[1]["agentType"]
        }
    assert len(list(project.rglob("*.jsonl"))) == child_count


async def test_real_pg_sequence_change_before_child_view_is_not_adopted(
    recovery_db, monkeypatch
):
    from app.execution.infrastructure.harness.claude.session_store import (
        ClaudeSessionStoreAdapter, ClaudeSessionStoreTransportError,
    )

    bridge = _ProviderReadHttpBridge(recovery_db)
    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        bridge.client,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="https://callback.example.test", callback_token="synthetic",
        callback_token_id="synthetic", run_id="run", attempt_id="attempt",
        provider_session_id=PROVIDER_ID,
    )
    assert await adapter.load(PROVIDER_ID) == INITIAL
    async with recovery_db() as conn:
        await operation(
            conn, "append", subpath="subagents/agent-a",
            entries=[{"type": "assistant", "uuid": "changed-after-preload"}],
            expected_sequence=3,
        )
    with pytest.raises(ClaudeSessionStoreTransportError, match="sequence_conflict"):
        await adapter.list_subkeys({"session_id": PROVIDER_ID})
    assert adapter.accepted_final_sequence == 2
    assert (PROVIDER_ID, "subagents/agent-a") not in adapter._load_cache


@pytest.mark.parametrize("schema_version", [
    "ai-platform.execution-spec.v2", "ai-platform.execution-spec.v3",
])
async def test_real_pg_callback_load_binds_both_frozen_spec_versions(
    recovery_db, schema_version
):
    """The callback authority accepts a v3 attempt, not only the frozen v2 one."""

    async with recovery_db() as conn:
        await conn.execute(
            "update run_attempts set execution_spec_schema_version=%s where id='attempt'",
            (schema_version,),
        )
        loaded = await operation(conn, "load")
        assert loaded["entries"] == INITIAL
        assert loaded["next_sequence"] == 3

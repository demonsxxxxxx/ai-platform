"""Stop-before-first-callback regression through actual prepare and isolated PostgreSQL."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from app.context.domain.conversation_recovery import make_provider_coverage_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionConflictError, ProviderSessionScope
from app.context.infrastructure import provider_epochs
from app.context.infrastructure.provider_continuation import settle_provider_continuation

SCOPE = ProviderSessionScope("tenant", "workspace", "user", "session", "agent")
SCOPE_MAP = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session", agent_id="agent")
EXECUTION_SPEC_V4 = "ai-platform.execution-spec.v4"


def _production_table_definition(schema: str, table: str) -> str:
    start = schema.index(f"create table if not exists {table} (")
    end = schema.index("\n);", start) + len("\n);")
    return schema[start:end]


async def _create_attempt_settlement_support(conn, schema: str) -> None:
    for table in (
        "run_attempt_settlement_retirement",
        "run_attempt_settlements",
        "run_attempt_settlement_storage",
        "run_attempt_settlement_writer",
        "run_stale_reconciliation_claims",
        "sandbox_execution_dispatches",
    ):
        await conn.execute(_production_table_definition(schema, table))
    await conn.execute("""
        insert into run_attempt_settlement_retirement(singleton, state)
        values (true, 'expanded');
        insert into run_attempt_settlement_storage(singleton, storage_version)
        values (true, 'sandbox_lease.v1');
        insert into run_attempt_settlement_writer(singleton, writer_version)
        values (true, 'sandbox_lease.v1');
    """)


@pytest.fixture
async def unclaimed_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "unclaimed_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema}", row_factory=dict_row) as conn:
            yield conn

    try:
        async with connect() as conn:
            await conn.execute("""
                create table sessions(tenant_id text, workspace_id text, user_id text, id text,
                  agent_id text, status text default 'active', unique(tenant_id,workspace_id,user_id,id,agent_id));
                create table runs(tenant_id text, id text, workspace_id text, user_id text,
                  session_id text, agent_id text, session_generation bigint, status text,
                  context_snapshot_id text, started_at timestamptz default now(), cancel_requested_at timestamptz,
                  result_json jsonb, error_code text, finished_at timestamptz,
                  primary key(tenant_id,id));
                create table run_attempts(id text primary key, tenant_id text, run_id text, status text,
                  owner_kind text, owner_generation bigint, execution_spec_sha256 text,
                  execution_spec_schema_version text, execution_spec_json jsonb, ordinal int default 1,
                  started_at timestamptz default now(), owner_id text default 'worker',
                  queue_message_id text, queue_attempt_id text, lease_expires_at timestamptz,
                  last_heartbeat_at timestamptz, finished_at timestamptz, terminal_reason text,
                  error_code text, created_at timestamptz default now(), updated_at timestamptz default now(),
                  unique(tenant_id,run_id,id));
                create table run_context_snapshots(id text, tenant_id text, workspace_id text, user_id text,
                  session_id text, run_id text, context_kind text, conversation_authority_json jsonb);
                create table messages(id text, tenant_id text, session_id text, run_id text, role text, content text);
                create table sandbox_leases(id text primary key, tenant_id text, run_id text, attempt_id text, status text,
                  released_at timestamptz, expires_at timestamptz, lease_payload_json jsonb,
                  executor_status text, executor_heartbeat_at timestamptz, executor_terminal_json jsonb,
                  executor_terminal_received_at timestamptz, executor_reconciliation_context_json jsonb,
                  executor_reconciliation_intent_context_json jsonb, executor_reconciliation_status text,
                  executor_reconciliation_claim_token text, executor_reconciliation_claimed_at timestamptz,
                  executor_reconciliation_retry_not_before timestamptz,
                  executor_terminal_reconciliation_attempt_count integer, executor_reconciled_at timestamptz,
                  release_reason text, created_at timestamptz default now(), updated_at timestamptz default now());
                insert into sessions(tenant_id,workspace_id,user_id,id,agent_id)
                  values('tenant','workspace','user','session','agent');
            """)
            source = Path("app/schema.sql").read_text()
            await _create_attempt_settlement_support(conn, source)
            await conn.execute(source[source.index("create table if not exists provider_session_heads"):source.index("-- A populated pre-#511")])
            await conn.execute(Path("app/context/infrastructure/conversation_recovery_schema.sql").read_text())
        yield connect
    finally:
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await admin.close()


async def prepare(conn, name, generation, *, execution_spec_schema_version="ai-platform.execution-spec.v2"):
    await conn.execute("insert into runs(tenant_id,id,workspace_id,user_id,session_id,agent_id,session_generation,status,context_snapshot_id) values('tenant',%s,'workspace','user','session','agent',%s,'running',%s)", (name, generation, "snapshot-" + name))
    await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id=name)
    coverage = await provider_epochs.read_provider_coverage(conn, scope=SCOPE, run_id=name, session_generation=generation)
    authority = make_provider_coverage_authority_receipt(coverage=coverage, scope=SCOPE_MAP,
                                                       through_session_generation=generation, current_message_id="user-" + name)
    await conn.execute("insert into run_context_snapshots values(%s,'tenant','workspace','user','session',%s,'executor',%s)", ("snapshot-" + name, name, Jsonb(authority)))
    await conn.execute("insert into messages values(%s,'tenant','session',%s,'user','synthetic input')", ("user-" + name, name))
    frozen = await provider_epochs.prepare_provider_epoch(
        conn, scope=SCOPE, run_id=name, conversation_context=dict(
            source_sha256=coverage["source_sha256"], message_count=coverage["message_count"],
            authority_schema_version=coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
            current_message_id="user-" + name, messages=[], selected_message_count=0, selected_turn_count=0,
        ),
    )
    spec_json = {
        "schema_version": execution_spec_schema_version,
        "context_pack": {"conversation_context": frozen},
    }
    if execution_spec_schema_version == EXECUTION_SPEC_V4:
        spec_json.update({
            "model_group_id": "group-a", "model_group_code": "group-a",
            "model_group_type": "chat", "model_group_revision": 7,
            "model_selection_mode": "pinned", "usage_scene": "conversation",
            "model_policy_version": "policy-v1",
        })
    await conn.execute("insert into run_attempts(tenant_id,id,run_id,status,owner_kind,owner_generation,execution_spec_sha256,execution_spec_schema_version,execution_spec_json) values('tenant',%s,%s,'running','queue_worker',2,%s,%s,%s)", ("attempt-" + name, name, "d" * 64, execution_spec_schema_version, Jsonb(spec_json)))
    await conn.execute(
        "insert into sandbox_leases(id,tenant_id,run_id,attempt_id,status,released_at,expires_at,lease_payload_json) "
        "values(%s,'tenant',%s,%s,'active',null,now()+interval '5 minutes',%s)",
        ("lease-" + name, name, "attempt-" + name, Jsonb(dict(owner_generation=2))),
    )
    return frozen


async def callback(conn, name, frozen, action, entries=(), sequence=None):
    return await provider_epochs.callback_provider_epoch(
        conn, **SCOPE_MAP, run_id=name, attempt_id="attempt-" + name,
        provider_session_id=frozen["provider_session_id"], action=action,
        entries=list(entries), expected_sequence=sequence, subpath=None,
    )


async def seed_source(conn):
    frozen = await prepare(conn, "source", 1)
    await callback(conn, "source", frozen, "load")
    await callback(conn, "source", frozen, "append", [{"type": "user", "uuid": "source-user"}], 1)
    receipt = dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="synthetic-version",
                   scope={**{key: value for key, value in SCOPE_MAP.items() if key != "agent_id"}, "run_id": "source", "attempt_id": "attempt-source"},
                   file_count=0, total_bytes=0, source_stop_evidence="provider_stopped")
    await settle_provider_continuation(conn, tenant_id="tenant", run_id="source", attempt_id="attempt-source",
                                       final_sequence=1, workspace_receipt=receipt, stop_evidence="provider_stopped",
                                       sdk_settlement=dict(owner_generation=2, input_accepted=True, mirror_complete=True))
    await conn.execute("update runs set status='failed' where id='source'")
    await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="source")
    return frozen


async def close_unclaimed(conn, **overrides):
    kwargs = dict(tenant_id="tenant", run_id="target", attempt_id="attempt-target", final_sequence=None,
                  workspace_receipt=None, stop_evidence="provider_stopped",
                  sdk_settlement=dict(owner_generation=2, input_accepted=False, mirror_complete=False, ack_unknown=False))
    kwargs.update(overrides)
    return await settle_provider_continuation(conn, **kwargs)


@pytest.mark.parametrize("with_source", [False, True])
@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
async def test_prepared_started_attempt_before_first_callback_can_close_and_continue(
    unclaimed_db, monkeypatch, with_source, terminal,
):
    async with unclaimed_db() as conn:
        source = await seed_source(conn) if with_source else None
        frozen = await prepare(conn, "target", 2 if with_source else 1)
        before = await (await conn.execute("select current_epoch_id,recovery_json from provider_session_heads")).fetchone()
        target = await (await conn.execute("select * from provider_session_epochs where id=%s", (frozen["provider_epoch_id"],))).fetchone()
        receipts_before = await (await conn.execute("select * from provider_session_append_receipts where epoch_id=%s order by expected_sequence", (target["id"],))).fetchall()
        source_before = (await (await conn.execute("select * from provider_session_epochs where id=%s", (source["provider_epoch_id"],))).fetchone()) if source else None
        assert target["writer_run_id"] is None and target["writer_attempt_id"] is None and target["writer_owner_generation"] is None
        attempt = await (await conn.execute("select status,started_at from run_attempts where id='attempt-target'")).fetchone()
        assert attempt["status"] == "running" and attempt["started_at"] is not None
        assert (await (await conn.execute("select active_attempt_id from provider_session_heads")).fetchone())["active_attempt_id"] is None
        assert (await (await conn.execute("select count(*) as n from provider_turn_receipts where epoch_id=%s", (target["id"],))).fetchone())["n"] == 0
        if terminal == "cancelled":
            await conn.execute("update runs set cancel_requested_at=now() where id='target'")
            await conn.execute("update run_attempts set status='cancel_requested',owner_generation=3 where id='attempt-target'")
        sdk = dict(owner_generation=3 if terminal == "cancelled" else 2, input_accepted=False,
                   mirror_complete=False, ack_unknown=False)
        result = await close_unclaimed(conn, sdk_settlement=sdk)
        assert result["settlement_kind"] == "unclaimed_unaccepted" and result["input_accepted"] is False
        assert await close_unclaimed(conn, sdk_settlement=sdk) == result
        with pytest.raises(ProviderSessionConflictError, match="epoch_unavailable"):
            await callback(conn, "target", frozen, "load")
        assert (await (await conn.execute("select current_epoch_id,recovery_json from provider_session_heads")).fetchone()) == before
        assert (await (await conn.execute("select count(*) as n from provider_turn_receipts where epoch_id=%s", (target["id"],))).fetchone())["n"] == 0
        counts = await (await conn.execute("select entry_count,next_sequence,transcript_bytes from provider_session_epochs where id=%s", (target["id"],))).fetchone()
        assert counts == {key: target[key] for key in counts}
        assert (await (await conn.execute("select * from provider_session_append_receipts where epoch_id=%s order by expected_sequence", (target["id"],))).fetchall()) == receipts_before
        if source:
            assert (await (await conn.execute("select * from provider_session_epochs where id=%s", (source["provider_epoch_id"],))).fetchone()) == source_before
        await conn.execute("update runs set status=%s where id='target'", (terminal,))
        from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
        from app.bootstrap.context import configure_context_services
        monkeypatch.setattr(
            "app.bootstrap.run_attempt_lifecycle.get_settings",
            lambda: SimpleNamespace(run_attempt_settlement_shadow_enabled=True),
        )
        configure_context_services()
        transitioned = await build_run_attempt_lifecycle_service().terminalize(
            conn, tenant_id="tenant", run_id="target", attempt_id="attempt-target",
            status=terminal, terminal_reason="synthetic_stopped",
        )
        assert transitioned["owner_generation"] == sdk["owner_generation"] + 1
        settlement = await (await conn.execute(
            "select mapping_state,attempt_status_snapshot,attempt_owner_generation_snapshot,run_status_snapshot "
            "from run_attempt_settlements where tenant_id='tenant' and run_id='target' "
            "and attempt_id='attempt-target'"
        )).fetchone()
        assert settlement == dict(
            mapping_state="unknown",
            attempt_status_snapshot=terminal,
            attempt_owner_generation_snapshot=transitioned["owner_generation"],
            run_status_snapshot=terminal,
        )
        closing = await (await conn.execute("select recovery_json from provider_session_epochs where id=%s", (target["id"],))).fetchone()
        assert closing["recovery_json"]["owner_generation"] == transitioned["owner_generation"]
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        assert (await (await conn.execute("select active_run_id,active_attempt_id from provider_session_heads")).fetchone()) == dict(active_run_id=None, active_attempt_id=None)
        next_frozen = await prepare(conn, "next", 3 if with_source else 2)
        assert next_frozen["execution_mode"] == ("native_resume" if with_source else "empty_start")
        if source:
            assert next_frozen["provider_session_id"] == source["provider_session_id"]
            assert (await callback(conn, "next", next_frozen, "load"))["entries"] == [{"type": "user", "uuid": "source-user"}]


async def test_v4_model_group_unclaimed_attempt_can_close_with_real_postgres_scope_checks(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1, execution_spec_schema_version=EXECUTION_SPEC_V4)
        result = await close_unclaimed(conn)
        assert result["settlement_kind"] == "unclaimed_unaccepted"
        assert result["input_accepted"] is False


async def test_v4_model_group_unclaimed_attempt_rejects_foreign_frozen_authority_scope(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1, execution_spec_schema_version=EXECUTION_SPEC_V4)
        await conn.execute(
            "update run_context_snapshots set conversation_authority_json=jsonb_set(conversation_authority_json,'{scope,user_id}',to_jsonb('foreign-user'::text)) where run_id='target'"
        )
        with pytest.raises(ProviderSessionConflictError, match="terminal_coverage_invalid"):
            await close_unclaimed(conn)


async def test_unknown_execution_spec_version_stays_rejected_by_unclaimed_settlement(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1, execution_spec_schema_version="ai-platform.execution-spec.v5")
        with pytest.raises(ProviderSessionConflictError, match="terminal_writer_invalid"):
            await close_unclaimed(conn)


@pytest.mark.parametrize("with_source", [False, True])
@pytest.mark.parametrize("fault", ["generation", "scope", "provider", "unverified", "accepted", "unknown_input", "ack", "missing_ack", "other_writer", "modified_epoch", "external_effect"])
async def test_unclaimed_close_rejects_uncertain_or_mismatched_evidence_keeps_gate(unclaimed_db, with_source, fault):
    async with unclaimed_db() as conn:
        if with_source:
            await seed_source(conn)
        frozen = await prepare(conn, "target", 2 if with_source else 1)
        kwargs = {}
        sdk = dict(owner_generation=2, input_accepted=False, mirror_complete=False, ack_unknown=False)
        if fault == "generation":
            sdk["owner_generation"] = 1
        elif fault == "scope":
            await conn.execute("update run_context_snapshots set user_id='other' where run_id='target'")
        elif fault == "provider":
            await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{context_pack,conversation_context,provider_session_id}',%s) where id='attempt-target'", (Jsonb(str(uuid4())),))
        elif fault == "unverified":
            kwargs["stop_evidence"] = "unverified"
        elif fault == "accepted":
            sdk["input_accepted"] = True
        elif fault == "unknown_input":
            sdk.pop("input_accepted")
        elif fault == "ack":
            sdk["ack_unknown"] = True
        elif fault == "missing_ack":
            sdk.pop("ack_unknown")
        elif fault == "other_writer":
            await conn.execute("update provider_session_epochs set writer_run_id='other',writer_attempt_id='other',writer_owner_generation=2 where id=%s", (frozen["provider_epoch_id"],))
        elif fault == "modified_epoch":
            await conn.execute("update provider_session_epochs set entry_count=entry_count+1 where id=%s", (frozen["provider_epoch_id"],))
        else:
            kwargs["external_effect_unknown"] = True
        with pytest.raises(ProviderSessionConflictError):
            await close_unclaimed(conn, sdk_settlement=sdk, **kwargs)
        await conn.execute("update runs set status='failed' where id='target'")
        await conn.execute("update run_attempts set status='failed' where id='attempt-target'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        assert (await (await conn.execute("select active_run_id from provider_session_heads")).fetchone())["active_run_id"] == "target"
        with pytest.raises(ProviderSessionConflictError, match="lineage_busy"):
            await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="next")


@pytest.mark.parametrize("change", ["generation", "spec", "other_started_attempt"])
async def test_unclaimed_terminal_release_requires_current_proof_for_every_started_attempt(unclaimed_db, change):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await close_unclaimed(conn)
        if change == "generation":
            await conn.execute("update run_attempts set owner_generation=3 where id='attempt-target'")
        elif change == "spec":
            await conn.execute("update run_attempts set execution_spec_sha256=%s where id='attempt-target'", ("a" * 64,))
        else:
            await conn.execute("insert into run_attempts(tenant_id,id,run_id,status,owner_generation) values('tenant','other-attempt','target','failed',1)")
        await conn.execute("update runs set status='failed' where id='target'")
        await conn.execute("update run_attempts set status='failed' where id='attempt-target'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        assert (await (await conn.execute("select active_run_id from provider_session_heads")).fetchone())["active_run_id"] == "target"

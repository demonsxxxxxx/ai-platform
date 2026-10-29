"""Real-PG epoch forks, terminal coverage and writer isolation (synthetic receipts)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from app.context.domain.conversation_recovery import (
    AUTHORITY_V3, make_provider_coverage_authority_receipt,
)
from app.context.domain.provider_sessions import ProviderSessionConflictError, ProviderSessionScope
from app.context.infrastructure import provider_epochs
from app.context.infrastructure.provider_continuation import get_session_continuation, settle_provider_continuation
from tests.test_provider_session_recovery_postgres import (
    recovery_db as provider_recovery_db, operation, request_cancel, PROVIDER_ID, INITIAL,
)

SCOPE = ProviderSessionScope("tenant", "workspace", "user", "session", "agent")
SCOPE_MAP = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session", agent_id="agent")
EXECUTION_SPEC_V4 = "ai-platform.execution-spec.v4"


recovery_db = provider_recovery_db


@pytest.fixture
async def lifecycle_db(recovery_db):
    async with recovery_db() as conn:
        await conn.execute("""
            alter table sandbox_leases add column if not exists executor_terminal_json jsonb;
        """)
    return recovery_db


def workspace(*, run="run", attempt="attempt"):
    return dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="version-" + run,
                scope={**{k: v for k, v in SCOPE_MAP.items() if k != "agent_id"}, "run_id": run, "attempt_id": attempt},
                file_count=0, total_bytes=0, source_stop_evidence="provider_stopped")


def settlement(**changes):
    return dict(owner_generation=2, input_accepted=True, mirror_complete=True, **changes)


async def settle(conn, **changes):
    kwargs = dict(tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=2,
                  workspace_receipt=workspace(), stop_evidence="provider_stopped", sdk_settlement=settlement())
    kwargs.update(changes)
    return await settle_provider_continuation(conn, **kwargs)


async def mark_attempt_v4_model_group(conn):
    await conn.execute(
        "update run_attempts set execution_spec_schema_version=%s, execution_spec_json=execution_spec_json || %s::jsonb where id='attempt'",
        (EXECUTION_SPEC_V4, Jsonb({
            "schema_version": EXECUTION_SPEC_V4,
            "model_group_id": "group-a", "model_group_code": "group-a",
            "model_group_type": "chat", "model_group_revision": 7,
            "model_selection_mode": "pinned", "usage_scene": "conversation",
            "model_policy_version": "policy-v1",
        })),
    )


async def stage_run(conn, name, *, generation=3):
    await conn.execute(
        "insert into runs(tenant_id,id,workspace_id,user_id,session_id,agent_id,status,"
        "session_generation,context_snapshot_id,started_at) "
        "values('tenant',%s,'workspace','user','session','agent','running',%s,%s,now())",
        (name, generation, "snapshot-" + name),
    )
    await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id=name)
    coverage = await provider_epochs.read_provider_coverage(conn, scope=SCOPE, run_id=name, session_generation=generation)
    context = dict(source_sha256=coverage["source_sha256"], message_count=coverage["message_count"],
                   authority_schema_version=coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
                   messages=[], current_message_id="user-" + name, selected_message_count=0, selected_turn_count=0)
    receipt = make_provider_coverage_authority_receipt(
        coverage=coverage, scope=SCOPE_MAP, through_session_generation=generation,
        current_message_id="user-" + name,
    )
    await conn.execute("insert into run_context_snapshots values(%s,'tenant','workspace','user','session',%s,'executor',%s)",
                       ("snapshot-" + name, name, Jsonb(receipt)))
    await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content) values(%s,'tenant','session',%s,'user','new input')", ("user-" + name, name))
    frozen = await provider_epochs.prepare_provider_epoch(conn, scope=SCOPE, run_id=name, conversation_context=context)
    await conn.execute(
        "insert into run_attempts(tenant_id,id,run_id,ordinal,status,owner_kind,owner_id,"
        "owner_generation,execution_spec_sha256,execution_spec_schema_version,execution_spec_json,started_at) "
        "values('tenant',%s,%s,1,'running','queue_worker','worker',2,%s,"
        "'ai-platform.execution-spec.v2',%s,now())",
        ("attempt-" + name, name, "d" * 64, Jsonb({"context_pack": {"conversation_context": frozen}})),
    )
    await conn.execute("insert into sandbox_leases(id,tenant_id,run_id,attempt_id,status,expires_at,lease_payload_json) values(%s,'tenant',%s,%s,'active',now()+interval '5 minutes',%s)",
                       ("lease-" + name, name, "attempt-" + name, Jsonb(dict(owner_generation=2))))
    return frozen


async def callback(conn, name, action, **changes):
    kwargs = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                  agent_id="agent", run_id=name, attempt_id="attempt-" + name, provider_session_id=PROVIDER_ID,
                  action=action, subpath=None, entries=[], expected_sequence=None)
    kwargs.update(changes)
    return await provider_epochs.callback_provider_epoch(conn, **kwargs)


async def test_interrupted_first_turn_publishes_v3_then_resumes_same_uuid_new_epoch(lifecycle_db):
    async with lifecycle_db() as conn:
        nul_entry = {"type": "assistant", "uuid": "nul-native", "text": "real\x00nul and literal \\u0000"}
        await operation(conn, "append", entries=[nul_entry], expected_sequence=3)
        await request_cancel(conn)
        await conn.execute("update runs set status='cancelled' where id='run'")
        recovery = await settle(conn, final_sequence=3,
                                sdk_settlement={**settlement(), "owner_generation": 3,
                                                "writer_owner_generation": 2})
        assert recovery["state"] == "resumable" and recovery["reason_code"] == "interrupted"
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        public = await get_session_continuation(conn, tenant_id="tenant", user_id="user", session_id="session")
        assert public == dict(state="resumable", reason_code="interrupted", action="continue", workspace_saved=True)
        source = await (await conn.execute("select * from provider_session_epochs where id='epoch'")).fetchone()
        assert source["coverage_schema_version"] == AUTHORITY_V3 and source["coverage_message_count"] == 1
        source_entries = await (await conn.execute(
            "select sequence,subpath,entry_json,entry_canonical_json::text as canonical,image_format,image_references "
            "from provider_session_entries where epoch_id='epoch' order by sequence"
        )).fetchall()
        source_receipts = await (await conn.execute(
            "select expected_sequence,batch_sha256,entry_count,last_sequence,run_id,attempt_id,owner_generation "
            "from provider_session_append_receipts where epoch_id='epoch' order by expected_sequence"
        )).fetchall()
        assert len(source_entries) == 3 and all(row["entry_json"] is None for row in source_entries)
        assert json.loads(source_entries[-1]["canonical"]) == nul_entry
        assert r"\u0000" in source_entries[-1]["canonical"]
        assert r"\\u0000" in source_entries[-1]["canonical"]
        frozen = await stage_run(conn, "next")
        assert frozen["provider_session_id"] == PROVIDER_ID and frozen["execution_mode"] == "native_resume"
        assert frozen["provider_epoch_id"] != "epoch" and frozen["source_epoch_id"] == "epoch"
        assert frozen["source_final_sequence"] == 3
        assert (await callback(conn, "next", "load"))["entries"] == INITIAL + [nul_entry]
        copied_entries = await (await conn.execute(
            "select sequence,subpath,entry_json,entry_canonical_json::text as canonical,image_format,image_references "
            "from provider_session_entries where epoch_id=%s order by sequence", (frozen["provider_epoch_id"],)
        )).fetchall()
        copied_receipts = await (await conn.execute(
            "select expected_sequence,batch_sha256,entry_count,last_sequence,run_id,attempt_id,owner_generation "
            "from provider_session_append_receipts where epoch_id=%s order by expected_sequence",
            (frozen["provider_epoch_id"],),
        )).fetchall()
        assert copied_entries == source_entries
        assert copied_receipts == source_receipts
        next_entry = {"type": "user", "uuid": "next-epoch-ack", "text": "copied receipt"}
        appended = await callback(conn, "next", "append", entries=[next_entry], expected_sequence=4)
        retried = await callback(conn, "next", "append", entries=[next_entry], expected_sequence=4)
        assert appended == retried and appended["last_sequence"] == 4
        assert (await (await conn.execute(
            "select count(*) as n from provider_session_append_receipts "
            "where epoch_id=%s and expected_sequence=4", (frozen["provider_epoch_id"],)
        )).fetchone())["n"] == 1
        # A changed lease generation cannot acquire the frozen new epoch.
        await conn.execute("update run_attempts set owner_generation=3 where id='attempt-next'")
        with pytest.raises(ProviderSessionConflictError):
            await callback(conn, "next", "load")


async def test_native_resume_rejects_source_epoch_from_another_scope(lifecycle_db):
    async with lifecycle_db() as conn:
        await settle(conn)
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        await conn.execute("""
            insert into sessions(tenant_id,workspace_id,user_id,id,agent_id)
            values('tenant','other-workspace','other-user','other-session','other-agent');
            insert into provider_session_heads(tenant_id,workspace_id,user_id,session_id,agent_id,engine)
            values('tenant','other-workspace','other-user','other-session','other-agent','claude');
            insert into provider_session_epochs(id,tenant_id,workspace_id,user_id,session_id,agent_id,
                engine,epoch_number,provider_session_id,state)
            values('off-scope-epoch','tenant','other-workspace','other-user','other-session','other-agent',
                'claude',1,'00000000-0000-4000-8000-000000000032','ready');
            insert into runs(tenant_id,id,workspace_id,user_id,session_id,agent_id,status,
                session_generation,context_snapshot_id,started_at)
            values('tenant','scope-check','workspace','user','session','agent','running',3,'scope-snapshot',now());
        """)
        await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="scope-check")
        coverage = await provider_epochs.read_provider_coverage(
            conn, scope=SCOPE, run_id="scope-check", session_generation=3,
        )
        context = dict(source_sha256=coverage["source_sha256"], message_count=coverage["message_count"],
                       authority_schema_version=coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
                       messages=[], current_message_id="scope-user", selected_message_count=0,
                       selected_turn_count=0, source_epoch_id="off-scope-epoch")

        with pytest.raises(ProviderSessionConflictError, match="provider_session_spec_mismatch"):
            await provider_epochs.prepare_provider_epoch(
                conn, scope=SCOPE, run_id="scope-check", conversation_context=context,
            )

        assert (await (await conn.execute(
            "select count(*) as n from provider_session_epochs where session_id='session'"
        )).fetchone())["n"] == 1
        assert (await (await conn.execute(
            "select next_epoch_number from provider_session_heads where session_id='session'"
        )).fetchone())["next_epoch_number"] == 2


async def test_source_a_to_failed_b_to_explicit_original_a_c_has_no_failed_tail(lifecycle_db):
    async with lifecycle_db() as conn:
        child = [{"type": "agent_metadata", "agentType": "opaque"}]
        await operation(conn, "append", subpath="subagents/child", entries=child, expected_sequence=3)
        await settle(conn, final_sequence=3)
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        a = await (await conn.execute("select * from provider_session_epochs where id='epoch'")).fetchone()
        original_entries = await (await conn.execute("select sequence,subpath,entry_canonical_json::text from provider_session_entries where epoch_id='epoch' order by sequence")).fetchall()
        original_receipts = await (await conn.execute("select expected_sequence,last_sequence,batch_sha256,run_id,attempt_id,owner_generation from provider_session_append_receipts where epoch_id='epoch' order by expected_sequence")).fetchall()
        b = await stage_run(conn, "b")
        assert (await callback(conn, "b", "load"))["entries"] == INITIAL
        assert (await callback(conn, "b", "load", subpath="subagents/child"))["entries"] == child
        await callback(conn, "b", "append", entries=[{"type": "user", "uuid": "b-tail", "text": "FAILED B"}], expected_sequence=4)
        await conn.execute("update runs set status='failed' where id='b'")
        # Failed B has no stopping evidence: normal release must keep the gate.
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="b")
        with pytest.raises(ProviderSessionConflictError, match="lineage_busy"):
            await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="c")
        # Explicit trusted test disposition: stopped B, choosing original candidate A.
        # This is deliberately not an automatic replay/product entry point.
        await conn.execute("update provider_session_epochs set state='dirty',writer_run_id=null,writer_attempt_id=null,writer_owner_generation=null where id=%s", (b["provider_epoch_id"],))
        await conn.execute("update provider_session_heads set active_run_id=null,active_attempt_id=null")
        c = await stage_run(conn, "c", generation=4)
        assert c["source_epoch_id"] == "epoch" and c["provider_epoch_id"] != b["provider_epoch_id"]
        assert (await callback(conn, "c", "load"))["entries"] == INITIAL
        c_entries = await (await conn.execute("select sequence,subpath,entry_canonical_json::text from provider_session_entries where epoch_id=%s order by sequence", (c["provider_epoch_id"],))).fetchall()
        c_receipts = await (await conn.execute("select expected_sequence,last_sequence,batch_sha256,run_id,attempt_id,owner_generation from provider_session_append_receipts where epoch_id=%s order by expected_sequence", (c["provider_epoch_id"],))).fetchall()
        assert c_entries == original_entries and c_receipts == original_receipts
        assert (await (await conn.execute("select * from provider_session_epochs where id='epoch'")).fetchone()) == a
        # Neither an explicit other epoch nor a copied historical ACK can target C.
        with pytest.raises(ProviderSessionConflictError, match="spec_mismatch"):
            await callback(conn, "c", "load", provider_epoch_id=b["provider_epoch_id"])
        with pytest.raises(ProviderSessionConflictError, match="append_conflict"):
            await callback(conn, "c", "append", entries=INITIAL, expected_sequence=1)


@pytest.mark.parametrize("override,reason,public_workspace_saved", [
    ({"stop_evidence": "unverified"}, "writer_unverified", False),
    ({"workspace_receipt": None}, "workspace_unsaved", False),
    ({"sdk_settlement": settlement(ack_unknown=True)}, "native_unavailable", True),
])
async def test_blocked_settlement_never_publishes_resume_or_silently_empty_starts(
    lifecycle_db, override, reason, public_workspace_saved,
):
    async with lifecycle_db() as conn:
        recovery = await settle(conn, **override)
        assert recovery["state"] == "blocked" and recovery["reason_code"] == reason
        assert recovery["workspace_saved"] is (reason != "workspace_unsaved")
        assert (await (await conn.execute("select current_epoch_id from provider_session_heads")).fetchone())["current_epoch_id"] is None
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        head = await (await conn.execute("select active_run_id from provider_session_heads")).fetchone()
        assert (head["active_run_id"] == "run") is (reason == "writer_unverified")
        projection = await get_session_continuation(conn, tenant_id="tenant", user_id="user", session_id="session")
        assert projection == dict(state="blocked", action="new_conversation", reason_code=reason,
                                  workspace_saved=public_workspace_saved)


@pytest.mark.parametrize("sequence", [1, 3, True])
async def test_settlement_rejects_non_boundary_or_unknown_final_ack(lifecycle_db, sequence):
    async with lifecycle_db() as conn:
        with pytest.raises(ProviderSessionConflictError, match="terminal_receipt_invalid"):
            await settle(conn, final_sequence=sequence)
        assert (await (await conn.execute("select recovery_json from provider_session_heads")).fetchone())["recovery_json"] is None


@pytest.mark.parametrize("change", ["tenant", "workspace", "generation", "provider", "snapshot"])
async def test_settlement_rejects_wrong_scope_frozen_identity_or_generation(lifecycle_db, change):
    async with lifecycle_db() as conn:
        kwargs = {}
        if change == "tenant":
            kwargs["tenant_id"] = "other-tenant"
        elif change == "workspace":
            kwargs["workspace_receipt"] = {**workspace(), "scope": {**workspace()["scope"], "user_id": "other-user"}}
        elif change == "generation":
            kwargs["sdk_settlement"] = {**settlement(), "owner_generation": 1}
        elif change == "provider":
            await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{context_pack,conversation_context,provider_session_id}',%s) where id='attempt'", (Jsonb("00000000-0000-4000-8000-000000000099"),))
        else:
            await conn.execute("update run_context_snapshots set user_id='other-user'")
        with pytest.raises(ProviderSessionConflictError):
            await settle(conn, **kwargs)
        assert (await (await conn.execute("select recovery_json from provider_session_heads")).fetchone())["recovery_json"] is None


async def test_v4_model_group_attempt_settles_stopped_context_in_real_postgres(lifecycle_db):
    async with lifecycle_db() as conn:
        await mark_attempt_v4_model_group(conn)
        recovery = await settle(conn)
        assert recovery["state"] == "resumable"
        assert recovery["source_attempt_id"] == "attempt"


async def test_v4_model_group_attempt_keeps_frozen_authority_scope_fence(lifecycle_db):
    async with lifecycle_db() as conn:
        await mark_attempt_v4_model_group(conn)
        await conn.execute(
            "update run_context_snapshots set conversation_authority_json=jsonb_set(conversation_authority_json,'{scope,user_id}',to_jsonb('foreign-user'::text)) where id='snapshot'"
        )
        with pytest.raises(ProviderSessionConflictError, match="terminal_coverage_invalid"):
            await settle(conn)
        assert (await (await conn.execute("select recovery_json from provider_session_heads")).fetchone())["recovery_json"] is None


async def test_hard_fault_verified_prefix_is_candidate_and_does_not_invent_assistant(lifecycle_db):
    async with lifecycle_db() as conn:
        result = await settle(conn, sdk_settlement={**settlement(), "mirror_complete": False}, external_effect_unknown=True)
        assert result["state"] == "recovery_candidate" and result["reason_code"] == "external_effect_unknown"
        assert (await (await conn.execute("select count(*) as n from messages where role='assistant'")).fetchone())["n"] == 0
        assert (await (await conn.execute("select state from provider_turn_receipts")).fetchone())["state"] == "failed"
        # Same settlement is idempotent; it cannot change the selected source or workspace.
        assert await settle(conn, sdk_settlement={**settlement(), "mirror_complete": False}, external_effect_unknown=True) == result


async def test_success_v2_coverage_waits_for_workspace_before_head_publication(lifecycle_db):
    async with lifecycle_db() as conn:
        await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content,created_at) values('assistant','tenant','session','run','assistant','ANSWER',now()+interval '1 second')")
        await provider_epochs.commit_provider_turn(conn, tenant_id="tenant", run_id="run", attempt_id="attempt", assistant_message_id="assistant", final_sequence=2)
        before = await (await conn.execute("select current_epoch_id from provider_session_heads")).fetchone()
        assert before["current_epoch_id"] is None
        recovery = await settle(conn)
        assert recovery["state"] == "resumable"
        epoch = await (await conn.execute("select coverage_schema_version,coverage_message_count from provider_session_epochs where id='epoch'")).fetchone()
        assert epoch == dict(coverage_schema_version="ai-platform.conversation-authority.v2", coverage_message_count=2)


async def test_schema_fragment_reapplies_without_rewriting_entries_or_receipts(lifecycle_db):
    async with lifecycle_db() as conn:
        fragment = Path("app/context/infrastructure/conversation_recovery_schema.sql").read_text()
        await conn.execute(fragment)
        await conn.execute(fragment)
        assert (await operation(conn, "load"))["entries"] == INITIAL
        # UUID reuse is legal across versions; source FK prevents cross-scope provenance.
        await conn.execute("insert into provider_session_epochs(id,tenant_id,workspace_id,user_id,session_id,agent_id,engine,epoch_number,provider_session_id,state,source_epoch_id,source_final_sequence) values('copy','tenant','workspace','user','session','agent','claude',2,%s,'ready','epoch',2)", (PROVIDER_ID,))


async def test_fault_candidate_copies_only_verified_complete_ack_prefix(lifecycle_db):
    async with lifecycle_db() as conn:
        await operation(conn, "append", entries=[{"type": "last-prompt", "text": "unselected tail"}], expected_sequence=3)
        recovery = await settle(conn, sdk_settlement={**settlement(), "mirror_complete": False})
        assert recovery["state"] == "recovery_candidate" and recovery["confirmed_entry_count"] == 2
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        frozen = await stage_run(conn, "prefix")
        assert frozen["source_final_sequence"] == 2
        assert (await callback(conn, "prefix", "load"))["entries"] == INITIAL
        receipts = await (await conn.execute("select expected_sequence,last_sequence from provider_session_append_receipts where epoch_id=%s", (frozen["provider_epoch_id"],))).fetchall()
        assert receipts == [dict(expected_sequence=1, last_sequence=2)]
        assert (await (await conn.execute("select entry_count from provider_session_epochs where id='epoch'")).fetchone())["entry_count"] == 3


async def test_proven_unaccepted_first_input_does_not_poison_initial_admission(lifecycle_db):
    async with lifecycle_db() as conn:
        # Synthetic first writer loaded the empty store but SDK never accepted input.
        await conn.execute("delete from provider_session_entries; delete from provider_session_append_receipts")
        await conn.execute("update provider_session_epochs set entry_count=0,next_sequence=1,transcript_bytes=0,structured_bytes=0")
        await settle(conn, final_sequence=0, workspace_receipt=None,
                     sdk_settlement={**settlement(), "input_accepted": False})
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        assert (await (await conn.execute("select recovery_json,current_epoch_id from provider_session_heads")).fetchone()) == dict(recovery_json=None, current_epoch_id=None)
        frozen = await stage_run(conn, "unaccepted-next")
        assert frozen["execution_mode"] == "empty_start"


async def test_proven_unaccepted_next_input_preserves_committed_source(lifecycle_db):
    async with lifecycle_db() as conn:
        await settle(conn)
        await conn.execute("update runs set status='failed' where id='run'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
        a_head = await (await conn.execute("select current_epoch_id,recovery_json from provider_session_heads")).fetchone()
        await stage_run(conn, "unaccepted-b")
        await callback(conn, "unaccepted-b", "load")
        result = await settle_provider_continuation(
            conn, tenant_id="tenant", run_id="unaccepted-b", attempt_id="attempt-unaccepted-b",
            final_sequence=2, workspace_receipt=None, stop_evidence="provider_stopped",
            sdk_settlement={**settlement(), "input_accepted": False},
        )
        assert result["input_accepted"] is False
        await conn.execute("update runs set status='failed' where id='unaccepted-b'")
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="unaccepted-b")
        assert (await (await conn.execute("select current_epoch_id,recovery_json from provider_session_heads")).fetchone()) == a_head
        c = await stage_run(conn, "unaccepted-c", generation=4)
        assert c["source_epoch_id"] == "epoch"
        assert (await callback(conn, "unaccepted-c", "load"))["entries"] == INITIAL


async def test_production_prepare_advances_epoch_counter_and_frozen_read_does_not(lifecycle_db):
    async with lifecycle_db() as conn:
        scope = ProviderSessionScope("tenant", "workspace", "user", "allocation-session", "agent")
        await conn.execute("insert into sessions(tenant_id,workspace_id,user_id,id,agent_id) values('tenant','workspace','user','allocation-session','agent')")
        await conn.execute("insert into runs(tenant_id,id,workspace_id,user_id,session_id,agent_id,status,session_generation,context_snapshot_id) values('tenant','allocation-run','workspace','user','allocation-session','agent','running',1,'allocation-snapshot')")
        await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="allocation-run")
        coverage = await provider_epochs.read_provider_coverage(
            conn, scope=scope, run_id="allocation-run", session_generation=1,
        )
        authority = make_provider_coverage_authority_receipt(
            coverage=coverage, scope={**SCOPE_MAP, "session_id": "allocation-session"},
            through_session_generation=1, current_message_id=None,
        )
        await conn.execute(
            "insert into run_context_snapshots(id,tenant_id,workspace_id,user_id,session_id,run_id,context_kind,conversation_authority_json) "
            "values('allocation-snapshot','tenant','workspace','user','allocation-session','allocation-run','executor',%s)",
            (Jsonb(authority),),
        )
        context = dict(source_sha256=coverage["source_sha256"], message_count=0,
                       authority_schema_version=coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
                       messages=[], current_message_id=None, selected_message_count=0, selected_turn_count=0)
        first = await provider_epochs.prepare_provider_epoch(
            conn, scope=scope, run_id="allocation-run", conversation_context=context,
        )
        assert (await (await conn.execute("select next_epoch_number from provider_session_heads where session_id='allocation-session'")).fetchone())["next_epoch_number"] == 2
        await conn.execute("insert into run_attempts(tenant_id,id,run_id,status,owner_generation,execution_spec_sha256,execution_spec_schema_version,execution_spec_json,started_at) values('tenant','allocation-attempt','allocation-run','running',1,%s,'ai-platform.execution-spec.v2',%s,now())",
                           ("d" * 64, Jsonb({"context_pack": {"conversation_context": first}})))
        assert await provider_epochs.prepare_provider_epoch(
            conn, scope=scope, run_id="allocation-run", conversation_context=context,
        ) == first
        assert (await (await conn.execute("select next_epoch_number from provider_session_heads where session_id='allocation-session'")).fetchone())["next_epoch_number"] == 2
        # Close the prepared but unclaimed epoch with explicit unaccepted input
        # and stop evidence before applying the terminal owner-generation CAS.
        closed = await settle_provider_continuation(
            conn, tenant_id="tenant", run_id="allocation-run", attempt_id="allocation-attempt",
            final_sequence=None, workspace_receipt=None, stop_evidence="provider_not_created",
            sdk_settlement=dict(owner_generation=1, input_accepted=False, mirror_complete=False,
                                ack_unknown=False, terminal_reason="predispatch_failure"),
        )
        assert closed["settlement_kind"] == "unclaimed_unaccepted"
        assert closed["input_accepted"] is False and closed["stop_evidence"] == "provider_not_created"
        await conn.execute("update run_attempts set status='failed',owner_generation=2 where id='allocation-attempt'")
        await conn.execute("update runs set status='failed' where id='allocation-run'")
        await provider_epochs.advance_unclaimed_terminal_generation(
            conn,
            previous=dict(tenant_id="tenant", run_id="allocation-run", id="allocation-attempt",
                          status="running", owner_generation=1, execution_spec_sha256="d" * 64),
            terminal=dict(tenant_id="tenant", run_id="allocation-run", id="allocation-attempt",
                          status="failed", owner_generation=2, execution_spec_sha256="d" * 64),
        )
        assert (await (await conn.execute("select active_run_id,active_attempt_id from provider_session_heads where session_id='allocation-session'")).fetchone()) == dict(active_run_id=None, active_attempt_id=None)
        closed_epoch = await (await conn.execute("select state,recovery_json from provider_session_epochs where id=%s", (first["provider_epoch_id"],))).fetchone()
        assert closed_epoch["state"] == "closed" and closed_epoch["recovery_json"]["owner_generation"] == 2

        await conn.execute("insert into runs(tenant_id,id,workspace_id,user_id,session_id,agent_id,status,session_generation) values('tenant','allocation-run-next','workspace','user','allocation-session','agent','running',2)")
        await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="allocation-run-next")
        next_coverage = await provider_epochs.read_provider_coverage(
            conn, scope=scope, run_id="allocation-run-next", session_generation=2,
        )
        next_context = dict(source_sha256=next_coverage["source_sha256"], message_count=0,
                            authority_schema_version=next_coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
                            messages=[], current_message_id=None, selected_message_count=0, selected_turn_count=0)
        second = await provider_epochs.prepare_provider_epoch(
            conn, scope=scope, run_id="allocation-run-next", conversation_context=next_context,
        )
        assert second["provider_epoch_id"] != first["provider_epoch_id"]
        numbers = await (await conn.execute("select epoch_number from provider_session_epochs where session_id='allocation-session' order by epoch_number")).fetchall()
        assert numbers == [dict(epoch_number=1), dict(epoch_number=2)]
        assert (await (await conn.execute("select next_epoch_number from provider_session_heads where session_id='allocation-session'")).fetchone())["next_epoch_number"] == 3

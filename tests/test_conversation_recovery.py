"""Context recovery receipt versions, safe projection and native ACK boundaries."""
from __future__ import annotations

import copy

import pytest

from app.context.domain.conversation_authority import (
    initial_source_digest, make_authority_receipt, validate_authority_receipt,
)
from app.context.domain.conversation_recovery import (
    AUTHORITY_V3, RECOVERY_SCHEMA_VERSION, make_provider_coverage_authority_receipt,
    public_continuation, validate_provider_authority_receipt,
)
from app.context.domain.provider_sessions import ProviderSessionConflictError
from app.context.infrastructure import provider_epochs
from app.execution.infrastructure.harness.claude.session_store import (
    ClaudeSessionStoreAdapter, ClaudeSessionStoreTransportError,
)

SCOPE = dict(tenant_id="tenant", workspace_id="workspace", user_id="user",
             session_id="session", agent_id="agent")


def test_v3_names_accepted_messages_without_reinterpreting_v2_success_counts():
    kwargs = dict(scope=SCOPE, through_session_generation=3, current_message_id="new-user")
    coverage = dict(message_count=1, source_sha256=initial_source_digest(SCOPE))
    legacy = make_provider_coverage_authority_receipt(coverage=coverage, **kwargs)
    assert legacy == make_authority_receipt(**coverage, **kwargs)
    assert validate_authority_receipt(legacy)["message_count"] == 1
    v3 = make_provider_coverage_authority_receipt(
        coverage={**coverage, "authority_schema_version": AUTHORITY_V3}, **kwargs,
    )
    assert v3["accepted_message_count"] == 1 and "message_count" not in v3
    assert v3["coverage_kind"] == "native_accepted_messages"
    assert validate_provider_authority_receipt(v3)["message_count"] == 1
    with pytest.raises(ValueError, match="schema_invalid"):
        validate_authority_receipt(v3)
    with pytest.raises(ValueError, match="coverage_invalid"):
        validate_provider_authority_receipt({**v3, "message_count": 2})


@pytest.mark.parametrize("state,reason,action", [
    ("resumable", "interrupted", "continue"),
    ("recovery_candidate", "fault_candidate", "continue"),
    ("blocked", "writer_unverified", "new_conversation"),
    ("blocked", "workspace_unsaved", "new_conversation"),
])
def test_public_continuation_is_an_allowlisted_four_field_projection(state, reason, action):
    private = dict(schema_version=RECOVERY_SCHEMA_VERSION, state=state, reason_code=reason,
                   workspace_saved=True, provider_epoch_id="private", provider_session_id="private",
                   workspace_version_id="private", workspace_receipt={"path": "/private"})
    projected = public_continuation(private)
    assert projected == dict(state=state, reason_code=reason, action=action, workspace_saved=True)
    assert "private" not in str(projected)
    assert public_continuation({**private, "reason_code": "/private"}) == dict(
        state="blocked", reason_code="native_unavailable", action="new_conversation", workspace_saved=False,
    )


def test_capacity_and_settling_do_not_claim_restorable_missing_history():
    assert public_continuation(None)["state"] == "unavailable"
    assert public_continuation(None, settling=True)["action"] == "wait"
    assert public_continuation(None, capacity_exhausted=True)["state"] == "capacity_exhausted"


class Cursor:
    def __init__(self, rows=(), row=None):
        self.rows, self.row = rows, row

    async def fetchall(self):
        return copy.deepcopy(self.rows)

    async def fetchone(self):
        return copy.deepcopy(self.row)


class PrefixConnection:
    def __init__(self):
        self.entries = [dict(sequence=n, subpath=path, entry_json=entry, entry_canonical_json=None, image_references=[], image_format=None)
                        for n, path, entry in [(1, "", {"type": "user"}),
                                              (2, "", {"type": "assistant"}),
                                              (3, "subagents/child", {"type": "opaque", "value": 7})]]
        self.receipts = [dict(expected_sequence=1, entry_count=2, last_sequence=2,
                              batch_sha256=provider_epochs.batch_digest("", [r["entry_json"] for r in self.entries[:2]])),
                         dict(expected_sequence=3, entry_count=1, last_sequence=3,
                              batch_sha256=provider_epochs.batch_digest("subagents/child", [self.entries[2]["entry_json"]]))]
        self.epoch = dict(id="epoch", entry_count=3, next_sequence=4,
                          transcript_bytes=sum(len(provider_epochs._canonical(r["entry_json"])) for r in self.entries),
                          structured_bytes=sum(len(provider_epochs._canonical(r["entry_json"])) for r in self.entries))

    async def execute(self, query, values):
        assert query.count("%s") == len(values)
        if "from provider_session_entries " in query:
            return Cursor(rows=self.entries)
        if "select last_sequence" in query:
            return Cursor(row=next((r for r in self.receipts if r["last_sequence"] == values[1]), None))
        return Cursor(rows=self.receipts)


@pytest.mark.asyncio
async def test_prefix_validates_main_children_full_bytes_and_whole_ack_boundary():
    conn = PrefixConnection()
    prefix, total = await provider_epochs.verified_provider_prefix(conn, epoch=conn.epoch, final_sequence=2)
    assert [row["sequence"] for row in prefix] == [1, 2]
    assert total == sum(len(provider_epochs._canonical(row["entry_json"])) for row in prefix)
    with pytest.raises(ProviderSessionConflictError, match="terminal_receipt_invalid"):
        await provider_epochs.verified_provider_prefix(conn, epoch=conn.epoch, final_sequence=1)
    with pytest.raises(ProviderSessionConflictError, match="terminal_receipt_invalid"):
        await provider_epochs.verified_provider_prefix(conn, epoch=conn.epoch, final_sequence=4)
    conn.receipts.pop()
    with pytest.raises(ProviderSessionConflictError, match="integrity_unavailable"):
        await provider_epochs.verified_provider_prefix(conn, epoch=conn.epoch, final_sequence=2)


@pytest.mark.asyncio
async def test_session_store_preserves_frozen_epoch_and_reports_unknown_ack_without_advancing_final():
    adapter = ClaudeSessionStoreAdapter(
        callback_url="https://callback.invalid", callback_token="synthetic", callback_token_id="synthetic",
        run_id="run", attempt_id="attempt", provider_session_id="provider", provider_epoch_id="epoch-new",
    )
    calls = []
    failed = False

    async def request(payload):
        nonlocal failed
        calls.append(copy.deepcopy(payload))
        assert payload["provider_epoch_id"] == "epoch-new"
        if payload["action"] == "load":
            return dict(entries=[], next_sequence=1)
        if not failed:
            failed = True
            raise ClaudeSessionStoreTransportError("provider_session_callback_timeout")
        return dict(next_sequence=2)

    adapter._request = request
    await adapter.load("provider")
    with pytest.raises(ClaudeSessionStoreTransportError):
        await adapter.append("provider", [{"type": "user"}])
    assert adapter.continuation_receipt == dict(final_sequence=0, last_acknowledged_sequence=0, ack_unknown=True)
    await adapter.append("provider", [{"type": "user"}])
    assert adapter.continuation_receipt == dict(final_sequence=1, last_acknowledged_sequence=1, ack_unknown=False)
    assert calls[1] == calls[2]


class SettlementConnection(PrefixConnection):
    def __init__(self):
        super().__init__()
        self.entries = self.entries[:2]
        self.receipts = self.receipts[:1]
        self.epoch.update(entry_count=2, next_sequence=3,
                          transcript_bytes=sum(len(provider_epochs._canonical(r["entry_json"])) for r in self.entries),
                          structured_bytes=sum(len(provider_epochs._canonical(r["entry_json"])) for r in self.entries))
        authority = make_authority_receipt(scope=SCOPE, through_session_generation=2,
                                           current_message_id="user", message_count=0, source_sha256="a" * 64)
        self.row = dict(
            **self.epoch, **SCOPE, session_generation=2, run_status="cancelled", cancel_requested_at="synthetic",
            owner_generation=3, writer_owner_generation=2, execution_spec_sha256="d" * 64,
            frozen_context=dict(provider_epoch_id="epoch", provider_session_id="provider", source_sha256="a" * 64,
                                current_message_id="user"), conversation_authority_json=authority,
            provider_session_id="provider", user_message_id="user", turn_spec_sha256="d" * 64,
            turn_state="writing", start_sequence=1, turn_id="turn", recovery_json=None,
        )
        self.updates = []

    async def execute(self, query, values=()):
        assert query.count("%s") == len(values)
        if "from runs owning_run" in query:
            return Cursor(row=self.row)
        if "select id, run_id, role, content from messages" in query:
            return Cursor(row=dict(id="user", run_id="run", role="user", content="accepted input"))
        if query.lstrip().startswith("update"):
            self.updates.append((query, values))
            return Cursor()
        return await super().execute(query, values)


def saved_workspace():
    return dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="private-version",
                scope={**{key: value for key, value in SCOPE.items() if key != "agent_id"},
                       "run_id": "run", "attempt_id": "attempt"}, file_count=0, total_bytes=0,
                source_stop_evidence="provider_stopped")


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror,stop,saved,unknown,expected", [
    (True, "provider_stopped", True, False, "resumable"),
    (False, "provider_not_found", True, False, "recovery_candidate"),
    (True, "unverified", True, False, "blocked"),
    (True, "provider_stopped", False, False, "blocked"),
    (True, "provider_stopped", True, True, "blocked"),
])
async def test_settlement_combines_verified_ack_stop_workspace_and_cancel_generation(mirror, stop, saved, unknown, expected):
    from app.context.infrastructure.provider_continuation import settle_provider_continuation

    conn = SettlementConnection()
    result = await settle_provider_continuation(
        conn, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=2,
        workspace_receipt=saved_workspace() if saved else None, stop_evidence=stop,
        sdk_settlement=dict(owner_generation=3, writer_owner_generation=2,
                            mirror_complete=mirror, input_accepted=True, ack_unknown=unknown),
    )
    assert result["state"] == expected
    publication = conn.updates[-1][1]
    assert publication[2] is (expected != "blocked")
    coverage_updates = [(sql, args) for sql, args in conn.updates if "coverage_schema_version = %s" in sql]
    assert bool(coverage_updates) is (expected != "blocked")
    if coverage_updates:
        assert coverage_updates[0][1][1:4] == (1, 2, AUTHORITY_V3)
    assert all("assistant_message_id" not in sql for sql, _args in conn.updates)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "writer_generation", "workspace_scope", "authority_scope"])
async def test_settlement_rejects_stale_or_cross_scope_receipts_before_publication(change):
    from app.context.infrastructure.provider_continuation import settle_provider_continuation

    conn = SettlementConnection()
    sdk = dict(owner_generation=3, writer_owner_generation=2, mirror_complete=True, input_accepted=True)
    work = saved_workspace()
    if change == "generation":
        sdk["owner_generation"] = 2
    elif change == "writer_generation":
        sdk["writer_owner_generation"] = 1
    elif change == "workspace_scope":
        work["scope"]["session_id"] = "other-session"
    else:
        conn.row["conversation_authority_json"]["scope"]["user_id"] = "other-user"
    with pytest.raises(ProviderSessionConflictError):
        await settle_provider_continuation(
            conn, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=2,
            workspace_receipt=work, stop_evidence="provider_stopped", sdk_settlement=sdk,
        )
    assert not conn.updates


@pytest.mark.asyncio
async def test_worker_materializes_v3_accepted_coverage_without_reconstructing_messages():
    from app.context.application.worker_snapshot import materialize_worker_context_snapshot

    receipt = make_provider_coverage_authority_receipt(
        coverage=dict(authority_schema_version=AUTHORITY_V3, message_count=1, source_sha256="a" * 64),
        scope=SCOPE, through_session_generation=3, current_message_id="new-user",
    )

    async def load(_conn, **scope):
        assert scope["run_id"] == "next-run" and scope["session_id"] == "session"
        return dict(included_message_ids=["new-user"], included_file_ids=[], conversation_authority_json=receipt)

    result = await materialize_worker_context_snapshot(
        None, identity={**SCOPE, "run_id": "next-run", "engine": "claude"},
        context_snapshot_id="snapshot", snapshot_loader=load,
        context_projector=lambda _row: {"context_snapshot_id": "snapshot"},
    )
    assert result["conversation_context"]["messages"] == []
    assert result["conversation_context"]["message_count"] == 1
    assert result["conversation_context"]["authority_schema_version"] == AUTHORITY_V3


@pytest.mark.asyncio
async def test_api_recovery_ports_delegate_without_widening_identity(monkeypatch):
    from app.context import api
    from app.context.application import provider_sessions

    calls = []

    class Repository:
        async def settle_continuation(self, conn, **kwargs):
            calls.append((conn, kwargs))
            return {"state": "blocked"}

        async def get_continuation(self, conn, **kwargs):
            calls.append((conn, kwargs))
            return public_continuation(None)

    monkeypatch.setattr(provider_sessions, "_use_cases", provider_sessions.ProviderSessionUseCases(Repository()))
    marker = object()
    assert await api.settle_provider_continuation(
        marker, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=2,
        workspace_receipt=None, stop_evidence="unverified", sdk_settlement={"owner_generation": 3},
    ) == {"state": "blocked"}
    assert calls[0][0] is marker and calls[0][1]["attempt_id"] == "attempt"
    assert calls[0][1]["external_effect_unknown"] is False
    assert await api.get_session_continuation(marker, tenant_id="tenant", user_id="user", session_id="session") == public_continuation(None)
    assert calls[1][1] == dict(tenant_id="tenant", user_id="user", session_id="session")


@pytest.mark.asyncio
@pytest.mark.parametrize("has_private_saved_receipt", [False, True])
async def test_terminal_run_with_unverified_writer_projects_blocked_instead_of_wait_forever(has_private_saved_receipt):
    from app.context.infrastructure.provider_continuation import get_session_continuation

    private = (dict(schema_version=RECOVERY_SCHEMA_VERSION, state="blocked", reason_code="writer_unverified",
                    source_run_id="failed-run", stop_evidence="unverified", workspace_saved=True)
               if has_private_saved_receipt else None)

    class Connection:
        async def execute(self, query, values):
            assert values == ("tenant", "user", "session")
            assert "owner_run.workspace_id = head.workspace_id" in query
            return Cursor(row=dict(active_run_id="failed-run", active_run_status="failed",
                                   recovery_json=private, entry_count=2, transcript_bytes=20))

    result = await get_session_continuation(Connection(), tenant_id="tenant", user_id="user", session_id="session")
    assert result == dict(state="blocked", reason_code="writer_unverified", workspace_saved=False,
                          action="new_conversation")


def test_recovery_capacity_uses_verified_prefix_while_legacy_uses_whole_epoch():
    from app.context.domain.conversation_recovery import provider_restore_size

    epoch = dict(entry_count=4096, transcript_bytes=8 * 1024 * 1024)
    assert provider_restore_size(epoch, None) == (4096, 8 * 1024 * 1024)
    candidate = dict(state="recovery_candidate", confirmed_entry_count=2, confirmed_transcript_bytes=35)
    assert provider_restore_size(epoch, candidate) == (2, 35)


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_unknown", [False, True])
async def test_only_proven_unaccepted_untouched_input_preserves_previous_head(ack_unknown):
    from app.context.infrastructure.provider_continuation import settle_provider_continuation

    conn = SettlementConnection()
    conn.entries = []
    conn.receipts = []
    conn.row.update(entry_count=0, next_sequence=1, transcript_bytes=0, structured_bytes=0)
    result = await settle_provider_continuation(
        conn, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=0,
        workspace_receipt=None, stop_evidence="provider_stopped",
        sdk_settlement=dict(owner_generation=3, writer_owner_generation=2, mirror_complete=True,
                            input_accepted=False, ack_unknown=ack_unknown),
    )
    assert result["input_accepted"] is False
    assert conn.updates[-1][1][0] is (not ack_unknown)
    assert conn.updates[-1][1][2] is False


@pytest.mark.asyncio
async def test_missing_terminal_boundary_still_verifies_full_ledger_before_recording_disposition():
    from app.context.infrastructure.provider_continuation import settle_provider_continuation

    conn = SettlementConnection()
    conn.receipts.clear()
    with pytest.raises(ProviderSessionConflictError, match="integrity_unavailable"):
        await settle_provider_continuation(
            conn, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=None,
            workspace_receipt=None, stop_evidence="unverified",
            sdk_settlement=dict(owner_generation=3, writer_owner_generation=2,
                                mirror_complete=False, input_accepted=True),
        )
    assert not conn.updates

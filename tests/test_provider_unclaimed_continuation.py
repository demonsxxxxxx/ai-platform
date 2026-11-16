"""Synthetic regression of the unclaimed close port; PG owns SQL integration proof."""
import copy
import json

import pytest

from app.context.domain.conversation_authority import initial_source_digest, make_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionConflictError
from app.context.infrastructure.provider_continuation import settle_provider_continuation
from app.context.infrastructure.unclaimed_provider_continuation import unclaimed_lineage_release_proven
from tests.test_conversation_recovery import Cursor, PrefixConnection, SCOPE


class UnclaimedConnection(PrefixConnection):
    def __init__(self, *, native=False):
        super().__init__()
        if not native:
            self.entries, self.receipts = [], []
            self.epoch.update(entry_count=0, next_sequence=1, transcript_bytes=0, structured_bytes=0)
        digest = "a" * 64 if native else initial_source_digest(SCOPE)
        authority = make_authority_receipt(scope=SCOPE, through_session_generation=2, current_message_id="user",
                                           message_count=1 if native else 0, source_sha256=digest)
        frozen = dict(provider_epoch_id="epoch", provider_session_id="provider", source_sha256=digest,
                      message_count=authority["message_count"], current_message_id="user",
                      execution_mode="native_resume" if native else "empty_start")
        if native:
            frozen.update(source_epoch_id="source", source_final_sequence=3)
        self.row = dict(**self.epoch, **SCOPE, owner_generation=3, session_generation=2,
                        execution_spec_sha256="d" * 64, conversation_authority_json=authority,
                        frozen_context=frozen, current_epoch_id="source" if native else None,
                        provider_session_id="provider", state="ready" if native else "bootstrapping",
                        source_epoch_id="source" if native else None, source_final_sequence=3 if native else None,
                        coverage_source_sha256=digest if native else None, coverage_message_count=authority["message_count"],
                        writer_run_id=None, writer_attempt_id=None, writer_owner_generation=None, recovery_json=None)
        self.updates = []

    async def execute(self, query, values=()):
        assert query.count("%s") == len(values)
        if "from runs owning_run" in query:
            if "head.active_attempt_id is null" not in query:
                return Cursor()
            assert "epoch.writer_run_id is null" in query and "for update of owning_run, attempt, snapshot, head, epoch" in query
            return Cursor(row=copy.deepcopy(self.row))
        if "select * from provider_session_epochs" in query:
            return Cursor(row={**self.row, "id": "source", "state": "ready"})
        if "from run_attempts attempt" in query:
            return Cursor(rows=[{**self.row, "attempt_id": "attempt", "attempt_status": "failed"}])
        if query.startswith("update provider_session_epochs"):
            self.updates.append((query, values))
            self.row.update(state="closed", recovery_json=json.loads(values[0]))
            return Cursor()
        return await super().execute(query, values)


async def close(conn, **overrides):
    kwargs = dict(tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=None,
                  workspace_receipt=None, stop_evidence="provider_stopped",
                  sdk_settlement=dict(owner_generation=3, input_accepted=False, mirror_complete=False, ack_unknown=False))
    kwargs.update(overrides)
    return await settle_provider_continuation(conn, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("stop_evidence", ["provider_stopped", "provider_not_created"])
async def test_unclaimed_close_preserves_source_and_never_claims_writer_or_invents_ack(native, stop_evidence):
    conn = UnclaimedConnection(native=native)
    result = await close(conn, stop_evidence=stop_evidence)
    assert result["settlement_kind"] == "unclaimed_unaccepted" and result["input_accepted"] is False
    assert result["prepared_final_sequence"] == (3 if native else 0) and result["final_sequence"] is None
    assert len(conn.updates) == 1 and conn.row["state"] == "closed"
    assert all("provider_session_heads" not in query and "provider_turn_receipts" not in query for query, _ in conn.updates)
    assert await close(conn, stop_evidence=stop_evidence) == result and len(conn.updates) == 1
    head = {**SCOPE, "engine": "claude", "active_attempt_id": None}
    assert await unclaimed_lineage_release_proven(conn, head=head, run_id="run")
    conn.row["owner_generation"] += 1
    assert not await unclaimed_lineage_release_proven(conn, head=head, run_id="run")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["generation", "snapshot_scope", "provider", "unverified", "input", "unknown_input", "ack", "missing_ack", "bytes", "external"])
async def test_unclaimed_close_rejects_uncertain_receipts_without_mutation(fault):
    conn = UnclaimedConnection()
    sdk = dict(owner_generation=3, input_accepted=False, mirror_complete=False, ack_unknown=False)
    kwargs = {}
    if fault == "generation":
        sdk["owner_generation"] = 2
    elif fault == "snapshot_scope":
        conn.row["conversation_authority_json"]["scope"]["user_id"] = "other"
    elif fault == "provider":
        conn.row["frozen_context"]["provider_session_id"] = "other"
    elif fault == "unverified":
        kwargs["stop_evidence"] = "unverified"
    elif fault == "input":
        sdk["input_accepted"] = True
    elif fault == "unknown_input":
        sdk.pop("input_accepted")
    elif fault == "ack":
        sdk["ack_unknown"] = True
    elif fault == "missing_ack":
        sdk.pop("ack_unknown")
    elif fault == "bytes":
        conn.row["transcript_bytes"] = 1
    else:
        kwargs["external_effect_unknown"] = True
    with pytest.raises(ProviderSessionConflictError):
        await close(conn, sdk_settlement=sdk, **kwargs)
    assert not conn.updates


@pytest.mark.asyncio
async def test_unclaimed_copy_must_match_source_bytes_and_receipt_provenance():
    conn = UnclaimedConnection(native=True)
    original = conn.execute

    async def changed_receipt(query, values=()):
        if "created_at from provider_session_append_receipts" in query and values[0] == "epoch":
            return Cursor(rows=[{**receipt, "run_id": "forged"} for receipt in conn.receipts])
        return await original(query, values)

    conn.execute = changed_receipt
    with pytest.raises(ProviderSessionConflictError, match="integrity_mismatch"):
        await close(conn)
    assert not conn.updates


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_evidence", ["provider_not_created", "provider_stopped"])
async def test_predispatch_receipt_cannot_settle_a_claimed_writer(stop_evidence):
    class WriterConnection:
        async def execute(self, query, values):
            return Cursor(row={"id": "claimed-epoch"})
    with pytest.raises(ProviderSessionConflictError, match="terminal_writer_invalid"):
        await close(WriterConnection(), stop_evidence=stop_evidence,
                    sdk_settlement=dict(owner_generation=3, input_accepted=False, ack_unknown=False,
                                        mirror_complete=False, terminal_reason="predispatch_failure"))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "scope", "generation", "accepted", "ack", "workspace"])
async def test_predispatch_composition_preserves_runtime_authority(monkeypatch, fault):
    from app.bootstrap import conversation_continuation as composition
    from app.runtime.sandbox.runtime import SandboxRuntime
    from types import SimpleNamespace
    conn = UnclaimedConnection()
    req = SimpleNamespace(tenant_id="tenant", run_id="run", attempt_id="attempt", owner_generation=3)
    receipt = SandboxRuntime._predispatch_settlement(req, "provider_not_created")
    if fault == "scope":
        receipt["attempt_id"] = "other"
    elif fault == "generation":
        receipt["sdk_settlement"]["owner_generation"] = 2
    elif fault == "accepted":
        receipt["sdk_settlement"]["input_accepted"] = True
    elif fault == "ack":
        receipt["sdk_settlement"]["ack_unknown"] = True
    elif fault == "workspace":
        receipt["workspace_receipt"] = {"state": "saved"}
    monkeypatch.setattr(composition.context_api, "settle_provider_continuation", settle_provider_continuation)
    async def execute():
        return await composition.settle_predispatch_continuation(
            conn, tenant_id="tenant", run_id="run", attempt_id="attempt", settlement=receipt,
        )
    if fault:
        with pytest.raises((ValueError, ProviderSessionConflictError)):
            await execute()
        assert conn.updates == []
    else:
        result = await execute()
        assert result["stop_evidence"] == "provider_not_created"
        assert result["owner_generation"] == 3

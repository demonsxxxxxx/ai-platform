"""Executable synthetic checks for Context's epoch and terminal authorities."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.context.domain.conversation_authority import (
    ConversationSourceChain,
    extend_source_digest,
)
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_ENTRIES,
    MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    ProviderSessionConflictError,
    ProviderSessionScope,
    normalize_provider_entry_batch,
)
from app.context.infrastructure import provider_epochs

SCOPE = ProviderSessionScope("tenant-a", "workspace-a", "user-a", "session-a", "agent-a")


class Cursor:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, *, head=None, existing_attempt=None):
        self.head = head
        self.existing_attempt = existing_attempt
        self.epoch_next = 1
        self.receipts: dict[int, dict[str, Any]] = {}
        self.entries: list[tuple[Any, ...]] = []
        self.calls: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.rows: list[dict[str, Any]] = []
        self.terminal_row: dict[str, Any] | None = None
        self.release_row: dict[str, Any] | None = None
        self.provider_entries: list[dict[str, Any]] = []

    async def execute(self, sql, params=()):
        values = tuple(params)
        assert sql.count("%s") == len(values), sql
        self.calls.append(sql.strip().lower())
        self.params.append(values)
        if "select active_run_id from provider_session_heads" in sql:
            return Cursor({"active_run_id": self.head} if self.head is not False else None)
        if "select head.current_epoch_id, head.next_epoch_number" in sql:
            return Cursor(self.head)
        if "select execution_spec_json->'context_pack'" in sql:
            return Cursor(self.existing_attempt)
        if "select batch_sha256, entry_count, last_sequence" in sql:
            return Cursor(self.receipts.get(values[1]))
        if "insert into provider_session_entries" in sql:
            self.entries.append(values)
        if "insert into provider_session_append_receipts" in sql:
            self.receipts[values[1]] = dict(zip(
                ("epoch_id", "expected_sequence", "batch_sha256", "entry_count",
                 "last_sequence", "run_id", "attempt_id", "owner_generation"), values,
            ))
        if "update provider_session_epochs set next_sequence" in sql:
            self.epoch_next = values[0]
        if "select runs.status, head.tenant_id" in sql:
            return Cursor(self.release_row)
        if "select runs.tenant_id" in sql:
            return Cursor(self.terminal_row)
        if "select id, run_id, role, content, created_at from messages" in sql:
            return Cursor(rows=self.rows)
        if "select count(*) as main_entry_count" in sql:
            epoch_id, start_sequence = values
            main_entry_count = sum(
                1
                for entry in self.provider_entries
                if entry.get("epoch_id") == epoch_id
                and entry.get("sequence", 0) >= start_sequence
                and entry.get("subpath") == ""
            )
            return Cursor({"main_entry_count": main_entry_count})
        return Cursor()


def source_receipt():
    chain = ConversationSourceChain(
        scope={"tenant_id": SCOPE.tenant_id, "workspace_id": SCOPE.workspace_id,
               "user_id": SCOPE.user_id, "session_id": SCOPE.session_id,
               "agent_id": SCOPE.agent_id},
        through_session_generation=2, current_run_id="run-current",
        current_message_id="msg-user",
    )
    return chain.receipt()


@pytest.mark.asyncio
async def test_lineage_claim_rejects_a_different_active_run():
    conn = Connection(head="run-a")
    with pytest.raises(ProviderSessionConflictError, match="lineage_busy"):
        await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="run-b")
    assert not any("update provider_session_heads set active_run_id" in sql for sql in conn.calls)
    conn.calls.clear()
    await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="run-a")
    assert any("for update" in sql for sql in conn.calls)


@pytest.mark.asyncio
async def test_empty_bootstrap_without_persisted_mutation_releases_cleanly_after_failure():
    for next_sequence, expected in ((1, "ready"), (2, "dirty")):
        conn = Connection()
        conn.release_row = {"status": "failed", "tenant_id": SCOPE.tenant_id,
                            "workspace_id": SCOPE.workspace_id, "user_id": SCOPE.user_id,
                            "session_id": SCOPE.session_id, "agent_id": SCOPE.agent_id,
                            "engine": "claude", "active_attempt_id": "attempt-a",
                            "epoch_id": "pe-new", "epoch_state": "active",
                            "next_sequence": next_sequence, "coverage_source_sha256": None,
                            "start_sequence": 1, "turn_state": "writing"}
        await provider_epochs.release_provider_lineage(conn, tenant_id=SCOPE.tenant_id,
                                                      run_id="run-current")
        states = [params[0] for sql, params in zip(conn.calls, conn.params)
                  if "update provider_session_epochs set state = %s" in sql]
        assert states == [expected]
        assert any("update provider_session_heads set active_run_id = null" in sql
                   for sql in conn.calls)


@pytest.mark.asyncio
async def test_ready_epoch_requires_exact_source_digest_and_count_before_resume():
    receipt = source_receipt()
    context = {"source_sha256": receipt["source_sha256"], "message_count": 0,
               "current_message_id": "msg-user", "messages": [],
               "selected_message_count": 0, "selected_turn_count": 0}
    head = {"active_run_id": "run-current", "current_epoch_id": "pe-old",
            "next_epoch_number": 2, "state": "ready", "coverage_source_sha256": receipt["source_sha256"],
            "coverage_message_count": 0, "entry_count": 1, "transcript_bytes": 100,
            "provider_session_id": "b4f6b554-ef0a-45c3-8db0-2710293a1685"}
    result = await provider_epochs.prepare_provider_epoch(
        Connection(head=head), scope=SCOPE, run_id="run-current", conversation_context=context,
    )
    assert result["execution_mode"] == "native_resume"
    assert result["provider_epoch_id"] == "pe-old" and result["messages"] == []
    changed = copy.deepcopy(head)
    changed["coverage_message_count"] = 1
    conn = Connection(head=changed)
    rotated = await provider_epochs.prepare_provider_epoch(
        conn, scope=SCOPE, run_id="run-current", conversation_context=context,
    )
    assert rotated["execution_mode"] == "empty_start"
    assert rotated["provider_epoch_id"] != "pe-old"
    assert rotated["provider_session_id"] != head["provider_session_id"]
    assert any("insert into provider_session_epochs" in sql for sql in conn.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head_change",
    [
        {"state": "dirty"},
        {"state": "closed"},
        {"coverage_source_sha256": "b" * 64},
        {"coverage_message_count": 0},
        {"entry_count": MAX_PROVIDER_SESSION_ENTRIES},
        {"transcript_bytes": MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES},
    ],
)
async def test_existing_conversation_requires_new_conversation_when_native_epoch_is_unusable(
    head_change,
):
    receipt = source_receipt()
    context = {
        "source_sha256": receipt["source_sha256"],
        "message_count": 1,
        "current_message_id": "msg-user",
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
    }
    head = {
        "active_run_id": "run-current",
        "current_epoch_id": "pe-old",
        "next_epoch_number": 2,
        "state": "ready",
        "coverage_source_sha256": receipt["source_sha256"],
        "coverage_message_count": 1,
        "entry_count": 1,
        "transcript_bytes": 100,
        "provider_session_id": "b4f6b554-ef0a-45c3-8db0-2710293a1685",
        **head_change,
    }

    with pytest.raises(
        ProviderSessionConflictError,
        match="provider_session_requires_new_conversation",
    ):
        await provider_epochs.prepare_provider_epoch(
            Connection(head=head),
            scope=SCOPE,
            run_id="run-current",
            conversation_context=context,
        )


@pytest.mark.asyncio
async def test_running_attempt_restores_frozen_native_resume_before_epoch_readiness_check():
    receipt = source_receipt()
    context = {
        "source_sha256": receipt["source_sha256"],
        "message_count": 1,
        "current_message_id": "msg-user",
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
        "native_source_verified": False,
    }
    frozen = {
        **context,
        "execution_mode": "native_resume",
        "provider_epoch_id": "pe-old",
        "provider_session_id": "b4f6b554-ef0a-45c3-8db0-2710293a1685",
    }
    head = {
        "active_run_id": "run-current",
        "current_epoch_id": "pe-old",
        "next_epoch_number": 2,
        "state": "active",
        "coverage_source_sha256": receipt["source_sha256"],
        "coverage_message_count": 0,
        "entry_count": 10,
        "transcript_bytes": 1_000,
        "provider_session_id": frozen["provider_session_id"],
    }

    result = await provider_epochs.prepare_provider_epoch(
        Connection(head=head, existing_attempt={"frozen_context": frozen}),
        scope=SCOPE,
        run_id="run-current",
        conversation_context=context,
    )

    assert result == frozen


@pytest.mark.asyncio
async def test_uuidless_batch_response_loss_reuses_db_receipt_not_uuid(monkeypatch):
    conn = Connection()

    async def locked(_conn, **_kwargs):
        return (
            {"id": "pe-a", "next_sequence": conn.epoch_next, "entry_count": len(conn.entries),
             "transcript_bytes": 100},
            {"owner_generation": 1},
            {"source_sha256": source_receipt()["source_sha256"]},
        )

    monkeypatch.setattr(provider_epochs, "_locked_callback_epoch", locked)
    base = dict(tenant_id=SCOPE.tenant_id, workspace_id=SCOPE.workspace_id,
                user_id=SCOPE.user_id, session_id=SCOPE.session_id, agent_id=SCOPE.agent_id,
                run_id="run-current", attempt_id="attempt-a",
                provider_session_id="b4f6b554-ef0a-45c3-8db0-2710293a1685",
                action="append", subpath=None, expected_sequence=1)
    batch = [{"type": "assistant", "content": "opaque entry without UUID"}]
    first = await provider_epochs.callback_provider_epoch(conn, entries=batch, **base)
    assert first["next_sequence"] == 2 and len(conn.entries) == 1
    retry = await provider_epochs.callback_provider_epoch(conn, entries=batch, **base)
    assert retry == first and len(conn.entries) == 1
    with pytest.raises(ProviderSessionConflictError, match="append_conflict"):
        await provider_epochs.callback_provider_epoch(conn, entries=[{"type": "assistant", "content": "changed"}], **base)
    with pytest.raises(ProviderSessionConflictError, match="append_conflict"):
        await provider_epochs.callback_provider_epoch(conn, entries=batch, **{**base, "expected_sequence": 3})
    assert normalize_provider_entry_batch(batch)[0][0].sdk_entry_uuid is None


@pytest.mark.asyncio
async def test_assistant_coverage_is_verified_before_turn_epoch_and_head_updates():
    receipt = source_receipt()
    conn = Connection()
    now = datetime(2026, 9, 15, tzinfo=UTC)
    conn.rows = [
        {"id": "msg-user", "run_id": "run-current", "role": "user", "content": "hi", "created_at": now},
        {"id": "msg-assistant", "run_id": "run-current", "role": "assistant", "content": "hello", "created_at": now + timedelta(microseconds=1)},
    ]
    conn.terminal_row = {
        **receipt["scope"], "session_generation": 2,
        "conversation_authority_json": receipt,
        "frozen_context": {"execution_mode": "empty_start", "provider_epoch_id": "pe-a",
                           "source_sha256": receipt["source_sha256"], "current_message_id": "msg-user"},
        "current_epoch_id": None, "active_run_id": "run-current", "active_attempt_id": "attempt-a",
        "epoch_id": "pe-a", "epoch_state": "active", "next_sequence": 2,
        "coverage_source_sha256": None, "writer_owner_generation": 1, "owner_generation": 1,
        "turn_id": "ptr-a", "turn_state": "writing", "turn_spec_sha256": "d" * 64,
        "execution_spec_sha256": "d" * 64, "prior_coverage_sha256": None,
        "bootstrap_source_sha256": receipt["source_sha256"], "start_sequence": 1,
        "user_message_id": "msg-user",
    }
    with pytest.raises(ProviderSessionConflictError, match="terminal_coverage_invalid"):
        await provider_epochs.commit_provider_turn(
            conn, tenant_id="tenant-a", run_id="run-current", attempt_id="attempt-a",
            assistant_message_id="msg-assistant", final_sequence=2,
        )
    assert not any(sql.startswith("update provider_turn_receipts") for sql in conn.calls)
    conn.provider_entries = [
        {"epoch_id": "pe-old", "sequence": 99, "subpath": ""},
        {"epoch_id": "pe-a", "sequence": 0, "subpath": ""},
        {"epoch_id": "pe-a", "sequence": 1, "subpath": "child-agent"},
        {"epoch_id": "pe-a", "sequence": 1, "subpath": ""},
    ]
    conn.calls.clear()
    conn.params.clear()
    await provider_epochs.commit_provider_turn(
        conn, tenant_id="tenant-a", run_id="run-current", attempt_id="attempt-a",
        assistant_message_id="msg-assistant", final_sequence=None,
    )
    updates = [(sql, params) for sql, params in zip(conn.calls, conn.params) if sql.startswith("update")]
    assert updates[0][1][0] == 1

    conn.calls.clear()
    conn.params.clear()
    await provider_epochs.commit_provider_turn(
        conn, tenant_id="tenant-a", run_id="run-current", attempt_id="attempt-a",
        assistant_message_id="msg-assistant", final_sequence=1,
    )
    updates = [(sql, params) for sql, params in zip(conn.calls, conn.params) if sql.startswith("update")]
    assert updates[0][0].startswith("update provider_turn_receipts")
    assert updates[1][0].startswith("update provider_session_epochs")
    assert updates[2][0].startswith("update provider_session_heads")
    expected = extend_source_digest(receipt["source_sha256"], conn.rows)
    assert updates[0][1][2] == updates[1][1][0] == expected
    assert updates[1][1][2] == 2

    conn.provider_entries = [
        {"epoch_id": "pe-old", "sequence": 99, "subpath": ""},
        {"epoch_id": "pe-a", "sequence": 0, "subpath": ""},
        {"epoch_id": "pe-a", "sequence": 1, "subpath": "child-agent"},
    ]
    with pytest.raises(
        ProviderSessionConflictError,
        match="provider_session_terminal_receipt_invalid",
    ):
        await provider_epochs.commit_provider_turn(
            conn, tenant_id="tenant-a", run_id="run-current", attempt_id="attempt-a",
            assistant_message_id="msg-assistant", final_sequence=None,
        )

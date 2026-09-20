"""Owning checks for immutable conversation source range receipts."""

import hashlib

import pytest

from app.executors.claude.prompts import conversation_history_prompt_section

from app.context.application.worker_snapshot import materialize_worker_context_snapshot
from app.context.domain.conversation_authority import (
    ConversationSourceChain,
    canonical_message,
    source_chain_digest,
    source_digest_scope,
    validate_authority_receipt,
)


_SCOPE = {
    "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
    "session_id": "session-a", "agent_id": "agent-a",
}


def test_conversation_source_chain_authorizes_all_pages_without_a_message_candidate_cap():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": f"earlier constraint {index}: " + "x" * 128,
         "created_at": f"2026-09-15T00:{index // 60:02d}:{index % 60:02d}Z",
         "session_generation": index + 1}
        for index in range(82)
    ]
    assert sum(len(row["content"]) for row in rows) > 8192
    chain = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
    for start in range(0, len(rows), 4):
        chain.add_page(rows[start:start + 4])
    receipt = validate_authority_receipt(chain.receipt())
    assert receipt["message_count"] == receipt["tail_message_count"] == 82
    assert receipt["current_message_id"] == "msg-current"
    assert receipt["through_session_generation"] == 84
    assert receipt["range_start"]["id"] == "msg-000"
    assert receipt["range_end"]["id"] == "msg-081"
    assert receipt["source_sha256"] == source_chain_digest(scope=source_digest_scope(**_SCOPE), rows=rows)
    assert receipt["tail_sha256"] != receipt["source_sha256"]
    with pytest.raises(ValueError, match="conversation_authority_range_invalid"):
        chain.add_page([rows[-1]])
    with pytest.raises(ValueError, match="conversation_authority_message_invalid"):
        canonical_message({**rows[0], "id": ""})
    with pytest.raises(ValueError, match="conversation_authority_digest_invalid"):
        validate_authority_receipt({**receipt, "source_sha256": "0" * 63 + "X"})


def test_conversation_source_rejects_current_run_or_unproven_generation():
    for change in ({"run_id": "run-current"}, {"session_generation": 84}, {"role": "system"}):
        chain = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
        with pytest.raises(ValueError, match="conversation_authority_(range|message)_invalid"):
            chain.add_page([{"id": "msg-old", "run_id": "run-old", "role": "user",
                             "content": "old", "created_at": "2026-09-15T00:00:00Z",
                             "session_generation": 1, **change}])


@pytest.mark.asyncio
async def test_worker_verifies_frozen_range_and_materializes_every_prior_turn():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": "constraint " + "x" * 128, "created_at": f"2026-09-15T00:{index // 60:02d}:{index % 60:02d}Z",
         "session_generation": index + 1}
        for index in range(82)
    ]
    chain = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
    chain.add_page(rows)
    receipt = chain.receipt()
    identity = {**_SCOPE, "run_id": "run-current", "engine": "claude"}
    calls = []

    async def snapshot(_conn, **_kwargs):
        return {"id": "ctx-current", "included_message_ids": ["msg-current"],
                "included_file_ids": [], "conversation_authority_json": receipt}

    async def page(_conn, **kwargs):
        calls.append(kwargs)
        after = kwargs["after_id"]
        remaining = [row for row in rows if after is None or row["id"] > after]
        return remaining[:kwargs["limit"]]

    async def forbidden_explicit_loader(*_args, **_kwargs):
        raise AssertionError("prior history must not be materialized from explicit IDs")

    common = dict(identity=identity, context_snapshot_id="ctx-current", snapshot_loader=snapshot,
                  message_loader=forbidden_explicit_loader, history_page_loader=page,
                  context_projector=lambda row: {"context_snapshot_id": row["id"]})
    result = await materialize_worker_context_snapshot(object(), **common)
    assert result is not None
    assert result["conversation_context"]["selected_message_count"] == 82
    assert result["conversation_context"]["selected_turn_count"] == 82
    assert result["conversation_context"]["dropped_turn_count"] == 0
    assert all(item["message_id"] != "msg-current" for item in result["conversation_context"]["messages"])
    assert len(calls) == 21 and calls[-1]["after_id"] == "msg-079"
    assert "conversation_authority_json" not in str(result["context_snapshot"])

    rows[0] = {**rows[0], "content": "tampered"}
    assert await materialize_worker_context_snapshot(object(), **common) is None
    rows[0] = {**rows[0], "content": "constraint " + "x" * 128}


@pytest.mark.asyncio
async def test_ready_checkpoint_preserves_full_source_digest_and_only_materializes_tail():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": f"earlier constraint {index}: " + "x" * 128,
         "created_at": f"2026-09-15T00:{index // 60:02d}:{index % 60:02d}+00:00",
         "session_generation": index + 1}
        for index in range(82)
    ]
    previous = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
    previous.add_page(rows[:74])
    base = {"id": "ccp-old", "range_start": previous.range_start, "range_end": previous.range_end,
            "message_count": 74, "source_sha256": previous.receipt()["source_sha256"],
            "summary_text": "prior goal and explicit constraints", "through_session_generation": 84}
    base["summary_sha256"] = hashlib.sha256(base["summary_text"].encode()).hexdigest()
    chain = ConversationSourceChain(
        _SCOPE, 84, "run-current", "msg-current",
        predecessor_digest=base["source_sha256"], base_checkpoint_id=base["id"],
        base_checkpoint_summary_sha256=base["summary_sha256"],
        predecessor_message_count=74, predecessor_range_start=base["range_start"],
        predecessor_range_end=base["range_end"],
    )
    chain.add_page(rows[74:])
    receipt = validate_authority_receipt(chain.receipt())
    assert receipt["message_count"] == 82 and receipt["tail_message_count"] == 8
    assert receipt["source_sha256"] == source_chain_digest(scope=source_digest_scope(**_SCOPE), rows=rows)
    calls = []

    async def page(_conn, **kwargs):
        calls.append(kwargs)
        return [row for row in rows if kwargs["after_id"] is None or row["id"] > kwargs["after_id"]][:4]

    async def checkpoint(_conn, **kwargs):
        assert kwargs["checkpoint_id"] == "ccp-old"
        return base

    async def snapshot(_conn, **_kwargs):
        return {"id": "ctx-current", "included_message_ids": ["msg-current"],
                "included_file_ids": [], "conversation_authority_json": receipt}

    common = dict(identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
                  context_snapshot_id="ctx-current", snapshot_loader=snapshot,
                  message_loader=lambda *_args, **_kwargs: None, history_page_loader=page,
                  checkpoint_loader=checkpoint,
                  context_projector=lambda row: {"context_snapshot_id": row["id"]})
    result = await materialize_worker_context_snapshot(object(), **common)
    assert result is not None
    context = result["conversation_context"]
    assert context["message_count"] == 82 and context["selected_message_count"] == 8
    assert [row["message_id"] for row in context["messages"]] == [row["id"] for row in rows[74:]]
    assert calls[0]["after_id"] == "msg-073" and len(calls) == 3
    rendered = conversation_history_prompt_section(context)
    assert "prior goal and explicit constraints" in rendered
    assert "earlier constraint 74" in rendered and "earlier constraint 0" not in rendered
    assert conversation_history_prompt_section({**context, "execution_mode": "native_resume"}) == ""
    rows[74] = {**rows[74], "content": "tampered"}
    assert await materialize_worker_context_snapshot(object(), **common) is None
    rows[74] = {**rows[74], "content": "earlier constraint 74: " + "x" * 128}
    base["summary_sha256"] = "0" * 64
    assert await materialize_worker_context_snapshot(object(), **common) is None


@pytest.mark.asyncio
async def test_ready_native_epoch_verifies_over_16_mib_without_materializing_old_bodies():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": f"earlier constraint {index}: " + "x" * 205000,
         "created_at": f"2026-09-15T00:{index // 60:02d}:{index % 60:02d}+00:00",
         "session_generation": index + 1}
        for index in range(82)
    ]
    chain = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
    chain.add_page(rows)
    receipt = chain.receipt()
    assert sum(len(row["content"]) for row in rows) > 16 * 1024 * 1024

    async def page(_conn, **kwargs):
        return [row for row in rows if kwargs["after_id"] is None or row["id"] > kwargs["after_id"]][:4]

    async def snapshot(_conn, **_kwargs):
        return {"id": "ctx-current", "included_message_ids": ["msg-current"],
                "included_file_ids": [], "conversation_authority_json": receipt}

    async def matches(_conn, *, scope, run_id, source_sha256, message_count):
        assert scope.session_id == "session-a" and run_id == "run-current"
        assert source_sha256 == receipt["source_sha256"] and message_count == 82
        return True

    common = dict(identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
                  context_snapshot_id="ctx-current", snapshot_loader=snapshot,
                  message_loader=lambda *_args, **_kwargs: None, history_page_loader=page,
                  context_projector=lambda row: {"context_snapshot_id": row["id"]})
    result = await materialize_worker_context_snapshot(object(), provider_epoch_matcher=matches, **common)
    assert result is not None
    context = result["conversation_context"]
    assert context["message_count"] == 82 and context["native_source_verified"] is True
    assert context["messages"] == [] and context["selected_message_count"] == 0
    assert conversation_history_prompt_section({**context, "execution_mode": "native_resume"}) == ""
    rows[0] = {**rows[0], "content": "tampered"}
    assert await materialize_worker_context_snapshot(object(), provider_epoch_matcher=matches, **common) is None

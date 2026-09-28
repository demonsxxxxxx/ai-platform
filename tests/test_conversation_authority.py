"""Checks for scoped conversation authority receipts."""

import pytest

from app.context.application.worker_snapshot import materialize_worker_context_snapshot
from app.context.domain.conversation_authority import (
    initial_source_digest,
    make_authority_receipt,
    validate_authority_receipt,
)


_SCOPE = {
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "user_id": "user-a",
    "session_id": "session-a",
    "agent_id": "agent-a",
}


def _receipt(*, current_message_id="msg-current", source_sha256=None):
    return make_authority_receipt(
        scope=_SCOPE,
        through_session_generation=4,
        message_count=3,
        source_sha256=source_sha256 or initial_source_digest(_SCOPE),
        current_message_id=current_message_id,
    )


def test_authority_receipt_binds_scope_current_message_and_digest():
    digest = initial_source_digest(_SCOPE)
    assert len(digest) == 64
    assert initial_source_digest(dict(_SCOPE)) == digest
    assert initial_source_digest({**_SCOPE, "session_id": "session-b"}) != digest

    receipt = validate_authority_receipt(_receipt(source_sha256=digest))
    assert receipt["scope"] == _SCOPE
    assert receipt["current_message_id"] == "msg-current"
    assert receipt["source_sha256"] == digest

    with pytest.raises(ValueError, match="conversation_authority_scope_invalid"):
        make_authority_receipt(
            scope={**_SCOPE, "agent_id": ""},
            through_session_generation=4,
            message_count=3,
            source_sha256=digest,
            current_message_id="msg-current",
        )
    with pytest.raises(ValueError, match="conversation_authority_current_message_invalid"):
        validate_authority_receipt({**receipt, "current_message_id": "../msg"})
    with pytest.raises(ValueError, match="conversation_authority_digest_invalid"):
        validate_authority_receipt({**receipt, "source_sha256": "not-a-digest"})


@pytest.mark.asyncio
async def test_worker_snapshot_uses_receipt_metadata_without_reading_message_bodies():
    receipt = _receipt()
    snapshot_calls = []

    class NoMessageBodyReads:
        async def execute(self, *_args, **_kwargs):
            pytest.fail("worker snapshot must not query message bodies")

    async def snapshot_loader(_conn, **kwargs):
        snapshot_calls.append(kwargs)
        return {
            "id": "ctx-current",
            "included_message_ids": ["msg-current"],
            "included_file_ids": ["file-current"],
            "conversation_authority_json": receipt,
        }

    result = await materialize_worker_context_snapshot(
        NoMessageBodyReads(),
        identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
        context_snapshot_id="ctx-current",
        snapshot_loader=snapshot_loader,
        context_projector=lambda row: {"context_snapshot_id": row["id"]},
    )

    assert snapshot_calls == [{
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-current",
        "context_snapshot_id": "ctx-current",
    }]
    assert result is not None
    context = result["conversation_context"]
    assert context["current_message_id"] == "msg-current"
    assert context["source_sha256"] == receipt["source_sha256"]
    assert context["message_count"] == 3
    assert context["messages"] == []
    assert result["file_ids"] == ["file-current"]


@pytest.mark.asyncio
async def test_worker_snapshot_rejects_receipt_scope_or_current_message_mismatch():
    valid_receipt = _receipt()
    invalid_receipts = (
        {**valid_receipt, "scope": {**_SCOPE, "user_id": "user-other"}},
        {**valid_receipt, "current_message_id": "msg-other"},
        {**valid_receipt, "source_sha256": "x" * 64},
    )

    for receipt in invalid_receipts:
        async def snapshot_loader(_conn, **_kwargs):
            return {
                "id": "ctx-current",
                "included_message_ids": ["msg-current"],
                "included_file_ids": [],
                "conversation_authority_json": receipt,
            }

        result = await materialize_worker_context_snapshot(
            object(),
            identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
            context_snapshot_id="ctx-current",
            snapshot_loader=snapshot_loader,
            context_projector=lambda row: {"context_snapshot_id": row["id"]},
        )
        assert result is None

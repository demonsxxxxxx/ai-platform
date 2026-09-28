from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.context.domain.conversation import empty_executor_conversation_context
from app.context.domain.conversation_authority import validate_authority_receipt
from app.context.domain.provider_sessions import (
    PROVIDER_SESSION_RESUME_CONTEXT_KEY,
    ProviderSessionContinuityError,
)

_SAFE_SNAPSHOT_MEMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

SnapshotLoader = Callable[..., Awaitable[dict[str, Any] | None]]
ContextProjector = Callable[[dict[str, Any]], dict[str, Any]]


async def materialize_worker_context_snapshot(
    conn: Any,
    *,
    identity: dict[str, str],
    context_snapshot_id: str,
    snapshot_loader: SnapshotLoader,
    context_projector: ContextProjector,
) -> dict[str, Any] | None:
    scoped_snapshot = await snapshot_loader(
        conn,
        tenant_id=identity["tenant_id"],
        workspace_id=identity["workspace_id"],
        user_id=identity["user_id"],
        session_id=identity["session_id"],
        run_id=identity["run_id"],
        context_snapshot_id=context_snapshot_id,
    )
    if scoped_snapshot is None:
        return None

    raw_message_ids = scoped_snapshot.get("included_message_ids")
    raw_file_ids = scoped_snapshot.get("included_file_ids")
    if not isinstance(raw_message_ids, list) or not isinstance(raw_file_ids, list):
        return None
    if any(
        not isinstance(member_id, str) or not _SAFE_SNAPSHOT_MEMBER_ID.fullmatch(member_id)
        for member_id in (*raw_message_ids, *raw_file_ids)
    ):
        return None
    selected_message_ids = list(raw_message_ids)
    selected_file_ids = list(raw_file_ids)
    if len(selected_message_ids) != len(set(selected_message_ids)) or len(selected_file_ids) != len(set(selected_file_ids)):
        return None

    conversation_context = empty_executor_conversation_context()
    raw_receipt = scoped_snapshot.get("conversation_authority_json")
    if raw_receipt is None:
        if selected_message_ids:
            return None
        if identity.get("engine") == "claude":
            raise ProviderSessionContinuityError("provider_session_authority_missing")
    else:
        if not isinstance(raw_receipt, dict):
            return None
        try:
            receipt = validate_authority_receipt(raw_receipt)
        except (ValueError, TypeError):
            return None
        expected_scope = {
            field: identity.get(field, "")
            for field in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")
        }
        if receipt["scope"] != expected_scope:
            return None
        current_message_id = receipt["current_message_id"]
        expected_ids = [current_message_id] if current_message_id is not None else []
        if selected_message_ids != expected_ids:
            return None
        conversation_context.update({
            "source_sha256": receipt["source_sha256"],
            "through_session_generation": receipt["through_session_generation"],
            "current_message_id": current_message_id,
            "message_count": receipt["message_count"],
        })

    context_ref = context_projector(scoped_snapshot)
    return {
        "context_snapshot_id": str(context_ref["context_snapshot_id"]),
        "context_snapshot": context_ref,
        "conversation_context": {
            **conversation_context,
            PROVIDER_SESSION_RESUME_CONTEXT_KEY: False,
        },
        "file_ids": selected_file_ids,
    }

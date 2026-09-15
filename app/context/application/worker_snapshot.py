from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.context.domain.conversation import (
    ConversationContextError,
    build_executor_conversation_context,
    empty_executor_conversation_context,
)
from app.context.domain.provider_sessions import PROVIDER_SESSION_RESUME_CONTEXT_KEY

_SAFE_SNAPSHOT_MEMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

SnapshotLoader = Callable[..., Awaitable[dict[str, Any] | None]]
MessageLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
ProviderTranscriptLoader = Callable[..., Awaitable[bool | dict[str, Any] | None]]
ContextProjector = Callable[[dict[str, Any]], dict[str, Any]]


async def materialize_worker_context_snapshot(
    conn: Any,
    *,
    identity: dict[str, str],
    context_snapshot_id: str,
    snapshot_loader: SnapshotLoader,
    message_loader: MessageLoader,
    context_projector: ContextProjector,
    provider_transcript_loader: ProviderTranscriptLoader | None = None,
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
    if (
        any(
            not isinstance(member_id, str)
            or not _SAFE_SNAPSHOT_MEMBER_ID.fullmatch(member_id)
            for member_id in (*raw_message_ids, *raw_file_ids)
        )
    ):
        return None
    selected_message_ids = list(raw_message_ids)
    selected_file_ids = list(raw_file_ids)
    if len(selected_message_ids) != len(set(selected_message_ids)) or len(
        selected_file_ids
    ) != len(set(selected_file_ids)):
        return None
    has_provider_transcript = False
    if provider_transcript_loader is not None and identity.get("engine") == "claude":
        provider_state = await provider_transcript_loader(
            conn,
            tenant_id=identity["tenant_id"],
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            session_id=identity["session_id"],
            run_id=identity["run_id"],
            agent_id=identity.get("agent_id", ""),
            engine=identity["engine"],
        )
        has_provider_transcript = (
            bool(provider_state)
            if not isinstance(provider_state, dict)
            else bool(
                provider_state.get("has_main_transcript")
                or provider_state.get("main_transcript_exists")
            )
        )

    if has_provider_transcript:
        conversation_context = empty_executor_conversation_context()
    elif selected_message_ids:
        materialized_messages = await message_loader(
            conn,
            tenant_id=identity["tenant_id"],
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            session_id=identity["session_id"],
            run_id=identity["run_id"],
            limit=len(selected_message_ids),
        )
        try:
            conversation_context = build_executor_conversation_context(
                materialized_messages,
                selected_message_ids=selected_message_ids,
                current_run_id=identity["run_id"],
            )
        except ConversationContextError:
            return None
    else:
        conversation_context = empty_executor_conversation_context()

    context_ref = context_projector(scoped_snapshot)
    return {
        "context_snapshot_id": str(context_ref["context_snapshot_id"]),
        "context_snapshot": context_ref,
        "conversation_context": {
            **conversation_context,
            PROVIDER_SESSION_RESUME_CONTEXT_KEY: has_provider_transcript,
        },
        "file_ids": selected_file_ids,
    }

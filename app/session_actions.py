"""Tenant-safe mutations for active Session compatibility actions."""

from typing import Any

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin


class SessionActionNotFoundError(Exception):
    """Raised when a session action cannot reveal a resource to the caller."""


class SessionActionValidationError(Exception):
    """Raised when a supported action has an invalid public input."""


def _session_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "agent_id": row["agent_id"],
        "title": row.get("title") or "",
        "status": row.get("status") or "active",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def _authorized_session(
    conn: Any,
    *,
    principal: AuthPrincipal,
    session_id: str,
    allow_deleted: bool = False,
) -> dict[str, Any]:
    row = await repositories.get_session_for_action(
        conn,
        tenant_id=principal.tenant_id,
        session_id=session_id,
    )
    if row is None:
        raise SessionActionNotFoundError("session_not_found")
    if row.get("user_id") != principal.user_id and not is_ai_admin(principal):
        raise SessionActionNotFoundError("session_not_found")
    if row.get("status") != "active" and not (allow_deleted and row.get("status") == "deleted"):
        raise SessionActionNotFoundError("session_not_found")
    return row


async def rename_session(
    conn: Any,
    *,
    principal: AuthPrincipal,
    session_id: str,
    title: str,
) -> dict[str, Any]:
    """Rename one active same-tenant session visible to the principal."""

    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 200:
        raise SessionActionValidationError("invalid_session_title")
    await _authorized_session(conn, principal=principal, session_id=session_id)
    updated = await repositories.update_session_title(
        conn,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        title=normalized_title,
    )
    if updated is None:
        raise SessionActionNotFoundError("session_not_found")
    return _session_payload(updated)


async def delete_session(
    conn: Any,
    *,
    principal: AuthPrincipal,
    session_id: str,
) -> dict[str, Any]:
    """Soft-delete a session once while making the owned terminal state idempotent."""

    row = await _authorized_session(
        conn,
        principal=principal,
        session_id=session_id,
        allow_deleted=True,
    )
    if row.get("status") == "deleted":
        return {"session": _session_payload(row), "already_deleted": True}
    updated = await repositories.mark_session_deleted(
        conn,
        tenant_id=principal.tenant_id,
        session_id=session_id,
    )
    if updated is None:
        raise SessionActionNotFoundError("session_not_found")
    return {"session": _session_payload(updated), "already_deleted": False}


async def fork_session_message(
    conn: Any,
    *,
    principal: AuthPrincipal,
    session_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Fork the authorized source-session prefix ending at one source message."""

    source = await _authorized_session(conn, principal=principal, session_id=session_id)
    messages = await repositories.list_session_messages_for_fork(
        conn,
        tenant_id=principal.tenant_id,
        session_id=session_id,
    )
    selected_index = next((index for index, message in enumerate(messages) if message["id"] == message_id), None)
    if selected_index is None:
        raise SessionActionNotFoundError("session_not_found")

    await repositories.ensure_user(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        display_name=principal.display_name,
    )
    fork_id = await repositories.create_session(
        conn,
        tenant_id=principal.tenant_id,
        workspace_id=source["workspace_id"],
        user_id=principal.user_id,
        agent_id=source["agent_id"],
        title=f"{source.get('title') or '新会话'} (fork)",
    )
    for message in messages[: selected_index + 1]:
        await repositories.append_message(
            conn,
            tenant_id=principal.tenant_id,
            session_id=fork_id,
            run_id=None,
            role=message["role"],
            content=message["content"],
            metadata_json=message.get("metadata_json") or {},
        )
    return {
        "source_session_id": session_id,
        "session": {
            "id": fork_id,
            "workspace_id": source["workspace_id"],
            "agent_id": source["agent_id"],
            "title": f"{source.get('title') or '新会话'} (fork)",
            "status": "active",
        },
    }

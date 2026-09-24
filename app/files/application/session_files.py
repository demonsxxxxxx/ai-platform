from __future__ import annotations

from typing import Any


async def list_owned_session_files(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return active files owned by one exact active session, including imports awaiting a Run."""

    cursor = await conn.execute(
        """
        select files.id, files.run_id, files.original_name, files.content_type,
               files.size_bytes, files.created_at
        from files
        join sessions on sessions.id = files.session_id
          and sessions.tenant_id = files.tenant_id
          and sessions.workspace_id = files.workspace_id
          and sessions.user_id = files.user_id
          and sessions.status = 'active'
        where files.tenant_id = %s
          and files.workspace_id = %s
          and files.user_id = %s
          and files.session_id = %s
          and files.lifecycle_state = 'active'
        order by files.created_at asc, files.id asc
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    return list(await cursor.fetchall())


async def get_owned_unbound_file(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    """Resolve one active file owned by the user before it is bound to a session."""

    cursor = await conn.execute(
        """
        select *
        from files
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and id = %s
          and session_id is null
          and run_id is null
          and lifecycle_state = 'active'
        """,
        (tenant_id, workspace_id, user_id, file_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def get_owned_session_file(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    """Resolve one active file through exact tenant/workspace/user/session ownership."""

    cursor = await conn.execute(
        """
        select files.*
        from files
        join sessions on sessions.id = files.session_id
          and sessions.tenant_id = files.tenant_id
          and sessions.workspace_id = files.workspace_id
          and sessions.user_id = files.user_id
          and sessions.status = 'active'
        where files.tenant_id = %s
          and files.workspace_id = %s
          and files.user_id = %s
          and files.session_id = %s
          and files.id = %s
          and files.lifecycle_state = 'active'
        """,
        (tenant_id, workspace_id, user_id, session_id, file_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None

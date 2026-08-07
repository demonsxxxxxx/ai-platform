from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def list_authorized_session_input_files(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return files authorized by each source run's persisted immutable snapshot ID."""

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
        join runs on runs.id = files.run_id
          and runs.tenant_id = files.tenant_id
          and runs.workspace_id = files.workspace_id
          and runs.user_id = files.user_id
          and runs.session_id = files.session_id
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        join run_context_snapshots authorized_snapshot
          on authorized_snapshot.id = runs.context_snapshot_id
          and authorized_snapshot.tenant_id = files.tenant_id
          and authorized_snapshot.workspace_id = files.workspace_id
          and authorized_snapshot.user_id = files.user_id
          and authorized_snapshot.session_id = files.session_id
          and authorized_snapshot.run_id = files.run_id
          and authorized_snapshot.context_kind = 'executor'
          and authorized_snapshot.included_file_ids ? files.id
        where files.tenant_id = %s
          and files.workspace_id = %s
          and files.user_id = %s
          and files.session_id = %s
        order by files.created_at asc, files.id asc
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    return list(await cursor.fetchall())

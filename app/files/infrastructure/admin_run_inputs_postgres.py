"""Content-free input file references from the admitted Run context snapshot."""
from typing import Any


async def read_admin_run_input_files(conn: Any, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute("""
        select f.id as file_id, f.original_name, f.content_type, f.size_bytes, f.lifecycle_state
        from runs r join run_context_snapshots c on c.id=r.context_snapshot_id
          and c.run_id=r.id and c.tenant_id=r.tenant_id and c.workspace_id=r.workspace_id
          and c.user_id=r.user_id and c.session_id=r.session_id and c.context_kind='executor'
        join files f on c.included_file_ids ? f.id and f.tenant_id=r.tenant_id
          and f.workspace_id=r.workspace_id and f.user_id=r.user_id and f.session_id=r.session_id
        where r.tenant_id=%s and r.id=%s order by f.created_at, f.id limit 101
        """, (tenant_id, run_id))
    return list(await cursor.fetchall())

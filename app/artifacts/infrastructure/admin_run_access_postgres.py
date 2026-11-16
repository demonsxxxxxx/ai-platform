"""Exact tenant, Run, artifact and active owning-session binding."""
from typing import Any


async def get_admin_run_artifact(conn: Any, *, tenant_id: str, run_id: str, artifact_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute("""
        select a.*, r.user_id as target_user_id
        from artifacts a
        join runs r on r.tenant_id = a.tenant_id and r.id = a.run_id
        join sessions s on s.tenant_id = r.tenant_id and s.id = r.session_id
          and s.workspace_id = r.workspace_id and s.user_id = r.user_id and s.agent_id = r.agent_id
        where a.tenant_id = %s and a.run_id = %s and a.id = %s and s.status = 'active'
        """, (tenant_id, run_id, artifact_id))
    return await cursor.fetchone()

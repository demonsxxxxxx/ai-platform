"""File overlays persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.values import new_id
from psycopg import AsyncConnection
from typing import Any


async def list_user_skill_file_overlays(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_ids: list[str],
    include_content: bool = False,
) -> list[dict[str, Any]]:
    """Return tenant/user file overlays for public Skill file projections."""

    if not skill_ids:
        return []
    content_projection = "content_base64" if include_content else "'' as content_base64"
    cursor = await conn.execute(
        f"""
        select skill_id, file_path, {content_projection}
          , size_bytes, status, updated_at
        from user_skill_files
        where tenant_id = %s
          and user_id = %s
          and skill_id = any(%s)
          and status in ('active', 'deleted')
        order by skill_id asc, file_path asc
        """,
        (tenant_id, user_id, skill_ids),
    )
    return [dict(row) for row in list(await cursor.fetchall())]


async def upsert_user_skill_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_id: str,
    file_path: str,
    content_base64: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Persist a tenant/user Skill file overlay and mark it active."""

    cursor = await conn.execute(
        """
        insert into user_skill_files(
          id, tenant_id, user_id, skill_id, file_path, content_base64, size_bytes, status
        )
        values (%s, %s, %s, %s, %s, %s, %s, 'active')
        on conflict (tenant_id, user_id, skill_id, file_path)
        do update set
          content_base64 = excluded.content_base64,
          size_bytes = excluded.size_bytes,
          status = 'active',
          updated_at = now()
        returning skill_id, file_path, content_base64, size_bytes, status, updated_at
        """,
        (
            new_id("usf"),
            tenant_id,
            user_id,
            skill_id,
            file_path,
            content_base64,
            size_bytes,
        ),
    )
    row = await cursor.fetchone()
    return dict(row)


async def delete_user_skill_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Persist a tenant/user Skill file tombstone for public projections."""

    cursor = await conn.execute(
        """
        insert into user_skill_files(
          id, tenant_id, user_id, skill_id, file_path, content_base64, size_bytes, status
        )
        values (%s, %s, %s, %s, %s, '', 0, 'deleted')
        on conflict (tenant_id, user_id, skill_id, file_path)
        do update set
          content_base64 = '',
          size_bytes = 0,
          status = 'deleted',
          updated_at = now()
        returning skill_id, file_path, content_base64, size_bytes, status, updated_at
        """,
        (new_id("usf"), tenant_id, user_id, skill_id, file_path),
    )
    row = await cursor.fetchone()
    return dict(row)

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def get_file_storage_usage(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
) -> dict[str, int]:
    lock_scope = f"file-storage:{tenant_id}:{workspace_id}:{user_id}"
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )
    cursor = await conn.execute(
        """
        select
            coalesce((select sum(size_bytes) from files
                where tenant_id = %s and workspace_id = %s and user_id = %s
                  and lifecycle_state = 'active'), 0) as stored_bytes,
            coalesce((select sum(expected_size_bytes) from file_upload_sessions
                where tenant_id = %s and workspace_id = %s and user_id = %s
                  and state in ('pending', 'completing') and expires_at > now()), 0) as reserved_bytes,
            coalesce((select count(*) from file_upload_sessions
                where tenant_id = %s and workspace_id = %s and user_id = %s
                  and state in ('pending', 'completing') and expires_at > now()), 0) as active_uploads
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            tenant_id,
            workspace_id,
            user_id,
            tenant_id,
            workspace_id,
            user_id,
        ),
    )
    row = await cursor.fetchone() or {}
    return {
        "stored_bytes": int(row.get("stored_bytes") or 0),
        "reserved_bytes": int(row.get("reserved_bytes") or 0),
        "active_uploads": int(row.get("active_uploads") or 0),
    }


async def create_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    file_id: str,
    original_name: str,
    content_type: str,
    expected_size_bytes: int,
    part_size_bytes: int,
    part_count: int,
    storage_key: str,
    upload_id: str,
) -> None:
    await conn.execute(
        """
        insert into file_upload_sessions(
            id, tenant_id, workspace_id, user_id, session_id, file_id,
            original_name, content_type, expected_size_bytes, part_size_bytes,
            part_count, storage_key, upload_id, expires_at
        ) values (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            now() + interval '3 hours'
        )
        """,
        (
            upload_session_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            file_id,
            original_name,
            content_type,
            expected_size_bytes,
            part_size_bytes,
            part_count,
            storage_key,
            upload_id,
        ),
    )


async def get_authorized_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
    tenant_id: str,
    user_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        f"""
        select id, tenant_id, workspace_id, user_id, session_id, file_id,
               original_name, content_type, expected_size_bytes, part_size_bytes,
               part_count, storage_key, upload_id, state, expires_at, completed_at
        from file_upload_sessions
        where id = %s and tenant_id = %s and user_id = %s
        {"for update" if for_update else ""}
        """,
        (upload_session_id, tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def claim_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
) -> bool:
    cursor = await conn.execute(
        """
        update file_upload_sessions
        set state = 'completing'
        where id = %s and state = 'pending'
        returning id
        """,
        (upload_session_id,),
    )
    return await cursor.fetchone() is not None


async def complete_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
) -> None:
    await conn.execute(
        """
        update file_upload_sessions
        set state = 'completed', completed_at = now()
        where id = %s and state = 'completing'
        """,
        (upload_session_id,),
    )


async def expire_file_upload_sessions(
    conn: AsyncConnection,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        update file_upload_sessions
        set state = 'expired'
        where id in (
            select id from file_upload_sessions
            where state in ('pending', 'completing') and expires_at <= now()
            order by expires_at asc
            limit %s
            for update skip locked
        )
        returning id, storage_key, upload_id
        """,
        (limit,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def retry_expired_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
) -> None:
    await conn.execute(
        """
        update file_upload_sessions
        set state = 'pending'
        where id = %s and state = 'expired'
        """,
        (upload_session_id,),
    )


async def delete_expired_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
) -> None:
    await conn.execute(
        """
        delete from file_upload_sessions
        where id = %s and state = 'expired'
        """,
        (upload_session_id,),
    )


async def abort_file_upload_session(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
    state: str = "aborted",
) -> None:
    if state not in {"aborted", "expired"}:
        raise ValueError("invalid upload session terminal state")
    await conn.execute(
        """
        update file_upload_sessions
        set state = %s
        where id = %s and state in ('pending', 'completing')
        """,
        (state, upload_session_id),
    )

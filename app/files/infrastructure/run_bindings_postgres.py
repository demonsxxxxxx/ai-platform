"""Run bindings persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.context.file_continuity import compatible_reusable_file_ids
from app.context.file_continuity import has_file_input_mode
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from psycopg import AsyncConnection
from typing import Any


async def create_file(
    conn: AsyncConnection,
    *,
    file_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    original_name: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    sha256: str,
) -> None:
    await conn.execute(
        """
        insert into files(id, tenant_id, workspace_id, user_id, session_id, original_name, content_type, size_bytes, storage_key, sha256)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            file_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            original_name,
            content_type,
            size_bytes,
            storage_key,
            sha256,
        ),
    )


async def authorize_files_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
    reusable_file_ids: list[str] | None = None,
    input_modes: list[object] | None = None,
) -> list[dict[str, Any]]:
    """Lock and validate run input files before any run creation side effect."""

    rows: list[dict[str, Any]] = []
    reusable_ids = set(reusable_file_ids or [])
    if not reusable_ids.issubset(file_ids):
        raise RepositoryConflictError("file_scope_mismatch")
    for file_id in file_ids:
        if file_id in reusable_ids:
            cursor = await conn.execute(
                """
                select files.id, files.tenant_id, files.workspace_id, files.user_id,
                       files.session_id, files.run_id, files.original_name,
                       files.content_type, files.size_bytes, files.sha256
                from files
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
                where files.id = %s and files.lifecycle_state = 'active'
                for update of files
                """,
                (file_id,),
            )
        else:
            cursor = await conn.execute(
                """
            select id, tenant_id, workspace_id, user_id, session_id, run_id,
                   original_name, content_type, size_bytes, sha256
            from files
            where id = %s and lifecycle_state = 'active'
            for update
            """,
                (file_id,),
            )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryNotFoundError("file_not_found")
        if row["tenant_id"] != tenant_id or row["workspace_id"] != workspace_id:
            raise RepositoryConflictError("file_scope_mismatch")
        if row["user_id"] != user_id:
            raise RepositoryConflictError("file_user_mismatch")
        if file_id in reusable_ids:
            if row["session_id"] != session_id or not row["run_id"]:
                raise RepositoryConflictError("file_session_mismatch")
        else:
            if row["session_id"] and row["session_id"] != session_id:
                raise RepositoryConflictError("file_session_mismatch")
            if row["run_id"] and row["run_id"] != run_id:
                raise RepositoryConflictError("file_already_bound")
        rows.append(dict(row))
    if input_modes is not None and has_file_input_mode(input_modes):
        compatible_ids = compatible_reusable_file_ids(rows, input_modes=input_modes)
        if len(compatible_ids) != len(rows):
            raise RepositoryConflictError("file_required_for_skill")
    return rows


async def bind_files_to_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
) -> None:
    await authorize_files_for_run(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        file_ids=file_ids,
    )
    for file_id in file_ids:
        await conn.execute(
            """
            update files
            set session_id = %s, run_id = %s
            where id = %s and lifecycle_state = 'active'
            """,
            (session_id, run_id, file_id),
        )


async def get_file(conn: AsyncConnection, *, tenant_id: str, file_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from files
        where tenant_id = %s and id = %s
        """,
        (tenant_id, file_id),
    )
    return await cursor.fetchone()

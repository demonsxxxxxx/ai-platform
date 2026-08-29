from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def list_scoped_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select messages.id, messages.session_id, messages.run_id, messages.role, messages.content,
               messages.metadata_json, messages.created_at
        from messages
        join runs source_runs on source_runs.id = messages.run_id and source_runs.tenant_id = messages.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = messages.tenant_id
        join sessions on sessions.id = current_run.session_id and sessions.tenant_id = current_run.tenant_id
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where messages.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and messages.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and source_runs.workspace_id = current_run.workspace_id
          and source_runs.user_id = current_run.user_id
          and source_runs.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_message_ids ? messages.id
        order by messages.created_at asc
        limit %s offset %s
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, max(1, int(limit)), max(0, int(offset))),
    )
    return list(await cursor.fetchall())


async def get_scoped_context_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select files.*
        from files
        join runs source_run on source_run.id = files.run_id and source_run.tenant_id = files.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = files.tenant_id
        join sessions on sessions.id = current_run.session_id
          and sessions.tenant_id = current_run.tenant_id
          and sessions.status = 'active'
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where files.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and current_run.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and files.workspace_id = current_run.workspace_id
          and files.user_id = current_run.user_id
          and files.session_id = current_run.session_id
          and source_run.workspace_id = current_run.workspace_id
          and source_run.user_id = current_run.user_id
          and source_run.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_file_ids ? files.id
          and files.id = %s
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, file_id),
    )
    return await cursor.fetchone()


async def get_scoped_context_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs source_run on source_run.id = artifacts.run_id and source_run.tenant_id = artifacts.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = current_run.session_id and sessions.tenant_id = current_run.tenant_id
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where artifacts.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and current_run.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and source_run.workspace_id = current_run.workspace_id
          and source_run.user_id = current_run.user_id
          and source_run.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_artifact_ids ? artifacts.id
          and artifacts.id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, artifact_id),
    )
    return await cursor.fetchone()


async def list_session_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a bounded ordered message tail for one exact owned session."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select *
        from (
          select messages.id, messages.run_id, messages.role, messages.content,
                 messages.metadata_json, messages.created_at, runs.session_generation
          from messages
          join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
          join runs on runs.id = messages.run_id and runs.tenant_id = messages.tenant_id
          where messages.tenant_id = %s
            and messages.session_id = %s
            and sessions.workspace_id = %s
            and sessions.user_id = %s
            and sessions.status = 'active'
            and runs.workspace_id = sessions.workspace_id
            and runs.user_id = sessions.user_id
            and runs.session_id = sessions.id
            and runs.session_generation is not null
            and runs.session_generation < (select session_generation from current_run)
          order by runs.session_generation desc, messages.created_at desc, messages.id desc
          limit %s
        ) recent_messages
        order by session_generation asc, created_at asc, id asc
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            session_id,
            workspace_id,
            user_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def count_session_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> int:
    """Count eligible ordered session history without loading its content."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select count(*) as context_message_count
        from messages
        join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
        join runs on runs.id = messages.run_id and runs.tenant_id = messages.tenant_id
        where messages.tenant_id = %s
          and messages.session_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.status = 'active'
          and runs.workspace_id = sessions.workspace_id
          and runs.user_id = sessions.user_id
          and runs.session_id = sessions.id
          and runs.session_generation is not null
          and runs.session_generation < (select session_generation from current_run)
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            session_id,
            workspace_id,
            user_id,
        ),
    )
    row = await cursor.fetchone()
    try:
        return max(0, int((row or {}).get("context_message_count") or 0))
    except (AttributeError, TypeError, ValueError):
        return 0


async def list_session_context_files(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a bounded recent file tail for one exact owned session."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select *
        from (
          select files.id, files.run_id, files.original_name, files.content_type,
                 files.size_bytes, files.sha256, files.created_at, runs.session_generation
          from files
          join sessions on sessions.id = files.session_id and sessions.tenant_id = files.tenant_id
          join runs on runs.id = files.run_id and runs.tenant_id = files.tenant_id
          join run_context_snapshots authorized_snapshot
            on authorized_snapshot.id = runs.context_snapshot_id
            and authorized_snapshot.tenant_id = runs.tenant_id
            and authorized_snapshot.workspace_id = runs.workspace_id
            and authorized_snapshot.user_id = runs.user_id
            and authorized_snapshot.session_id = runs.session_id
            and authorized_snapshot.run_id = runs.id
            and authorized_snapshot.context_kind = 'executor'
            and authorized_snapshot.included_file_ids ? files.id
          where files.tenant_id = %s
            and files.workspace_id = %s
            and files.user_id = %s
            and files.session_id = %s
            and sessions.workspace_id = files.workspace_id
            and sessions.user_id = files.user_id
            and sessions.status = 'active'
            and runs.workspace_id = files.workspace_id
            and runs.user_id = files.user_id
            and runs.session_id = files.session_id
            and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
            and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
            and runs.session_generation is not null
            and runs.session_generation < (select session_generation from current_run)
          order by runs.session_generation desc, files.created_at desc, files.id desc
          limit %s
        ) recent_files
        order by session_generation asc, created_at asc, id asc
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def list_authorized_context_file_rows(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    file_ids: list[str],
) -> list[dict[str, Any]]:
    """Return only scoped file display metadata for an already-authorized context set."""

    normalized_ids = [str(file_id) for file_id in file_ids if isinstance(file_id, str) and file_id]
    if not normalized_ids:
        return []
    cursor = await conn.execute(
        """
        select id, original_name
        from files
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and id = any(%s::text[])
        order by id asc
        """,
        (tenant_id, workspace_id, user_id, session_id, normalized_ids),
    )
    return list(await cursor.fetchall())


async def list_session_context_artifacts(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    exclude_run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return artifacts from the latest successful prior run in one owned session."""

    cursor = await conn.execute(
        """
        with latest_source_run as (
          select runs.id
          from runs
          join sessions on sessions.id = runs.session_id and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id <> %s
            and runs.session_generation is not null
            and runs.session_generation < (
              select session_generation
              from runs
              where tenant_id = %s
                and workspace_id = %s
                and user_id = %s
                and session_id = %s
                and id = %s
                and session_generation is not null
            )
            and runs.status = 'succeeded'
            and sessions.workspace_id = runs.workspace_id
            and sessions.user_id = runs.user_id
            and sessions.status = 'active'
            and exists (
              select 1 from artifacts
              where artifacts.tenant_id = runs.tenant_id
                and artifacts.run_id = runs.id
                and artifacts.lifecycle_state = 'active'
                and (artifacts.expires_at is null or artifacts.expires_at > now())
            )
          order by runs.session_generation desc
          limit 1
        )
        select artifacts.id, artifacts.run_id, artifacts.trace_id, artifacts.artifact_type,
               artifacts.label, artifacts.content_type, artifacts.size_bytes,
               artifacts.manifest_version, artifacts.manifest_json, artifacts.created_at
        from artifacts
        join latest_source_run on latest_source_run.id = artifacts.run_id
        where artifacts.tenant_id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        order by artifacts.created_at asc, artifacts.id asc
        limit %s
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            exclude_run_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            exclude_run_id,
            tenant_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def session_has_legacy_run_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> bool:
    """Report only whether pre-generation same-session history was excluded."""

    cursor = await conn.execute(
        """
        select exists (
          select 1
          from runs legacy_run
          join sessions on sessions.id = legacy_run.session_id
            and sessions.tenant_id = legacy_run.tenant_id
          where legacy_run.tenant_id = %s
            and legacy_run.workspace_id = %s
            and legacy_run.user_id = %s
            and legacy_run.session_id = %s
            and legacy_run.id <> %s
            and legacy_run.session_generation is null
            and sessions.status = 'active'
        ) as legacy_history_excluded
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("legacy_history_excluded"))

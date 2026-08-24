"""PostgreSQL persistence for Sessions, Messages, and Agent Conversation history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import (
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from app.platform.postgres.limits import (
    MESSAGE_CONTENT_MAX_BYTES,
    MESSAGE_METADATA_MAX_BYTES,
    PersistenceSizeLimitError,
    ensure_json_size,
    ensure_text_size,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _require_json_size(value: Any, *, max_bytes: int, code: str) -> None:
    try:
        ensure_json_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


def _require_text_size(value: str, *, max_bytes: int, code: str) -> None:
    try:
        ensure_text_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


async def ensure_workspace_belongs_to_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, tenant_id, status
        from workspaces
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, workspace_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("workspace_not_found")
    return dict(row)


async def create_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    user_id: str | None,
    title: str,
    title_source: str = "initial",
    session_id: str | None = None,
    admitted_agent_profile_revision: int | None = None,
    admitted_agent_profile_hash: str | None = None,
    purpose: str = "conversation",
    return_created: bool = False,
) -> str | tuple[str, bool]:
    resolved_id = session_id or _new_id("ses")
    await ensure_workspace_belongs_to_tenant(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    cursor = await conn.execute(
        """
        insert into sessions(
          id, tenant_id, workspace_id, user_id, agent_id, title, title_source,
          admitted_agent_profile_revision, admitted_agent_profile_hash, purpose
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update
        set id = excluded.id
        where sessions.tenant_id = excluded.tenant_id
          and sessions.workspace_id = excluded.workspace_id
          and sessions.user_id is not distinct from excluded.user_id
          and sessions.agent_id = excluded.agent_id
          and sessions.title = excluded.title
          and sessions.title_source = excluded.title_source
          and sessions.admitted_agent_profile_revision is not distinct from excluded.admitted_agent_profile_revision
          and sessions.admitted_agent_profile_hash is not distinct from excluded.admitted_agent_profile_hash
          and sessions.purpose = excluded.purpose
        returning sessions.id, (xmax = 0) as created
        """,
        (
            resolved_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            title,
            title_source,
            admitted_agent_profile_revision,
            admitted_agent_profile_hash,
            purpose,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("session_scope_mismatch")
    if return_created:
        return resolved_id, bool(row.get("created"))
    return resolved_id


async def list_authorized_sessions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select sessions.id, sessions.workspace_id, sessions.agent_id, sessions.title, sessions.purpose,
               sessions.admitted_agent_profile_revision, sessions.admitted_agent_profile_hash,
               sessions.created_at, sessions.updated_at,
               profile.name as agent_profile_name,
               profile.description as agent_profile_description,
               profile.welcome_message as agent_profile_welcome_message,
               profile.starter_prompts as agent_profile_starter_prompts,
               profile.capability_summary as agent_profile_capability_summary,
               profile.recommended_tasks as agent_profile_recommended_tasks,
               profile.supported_input_types as agent_profile_supported_input_types,
               profile.expected_outputs as agent_profile_expected_outputs,
               profile.permissions_and_data_access_notice as agent_profile_permissions_and_data_access_notice,
               profile.avatar_ref as agent_profile_avatar_ref,
               profile.avatar_seed as agent_profile_avatar_seed,
               profile.category as agent_profile_category,
               profile.published_at as agent_profile_published_at
        from sessions
        left join agent_profile_revisions profile
          on profile.tenant_id = sessions.tenant_id
         and profile.agent_id = sessions.agent_id
         and profile.revision = sessions.admitted_agent_profile_revision
         and profile.content_hash = sessions.admitted_agent_profile_hash
        where sessions.tenant_id = %s
          and sessions.user_id = %s
          and sessions.status = 'active'
          and sessions.admitted_agent_profile_revision is null
        order by sessions.updated_at desc, sessions.created_at desc
        limit 100
        """,
        (tenant_id, user_id),
    )
    return list(await cursor.fetchall())


async def get_authorized_session_projection(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Load one owned Session with only the safe immutable Agent Conversation identity."""

    cursor = await conn.execute(
        """
        select sessions.id, sessions.workspace_id, sessions.agent_id, sessions.title, sessions.purpose,
               sessions.admitted_agent_profile_revision, sessions.admitted_agent_profile_hash,
               sessions.created_at, sessions.updated_at,
               profile.name as agent_profile_name,
               profile.description as agent_profile_description,
               profile.welcome_message as agent_profile_welcome_message,
               profile.starter_prompts as agent_profile_starter_prompts,
               profile.capability_summary as agent_profile_capability_summary,
               profile.recommended_tasks as agent_profile_recommended_tasks,
               profile.supported_input_types as agent_profile_supported_input_types,
               profile.expected_outputs as agent_profile_expected_outputs,
               profile.permissions_and_data_access_notice as agent_profile_permissions_and_data_access_notice,
               profile.avatar_ref as agent_profile_avatar_ref,
               profile.avatar_seed as agent_profile_avatar_seed,
               profile.category as agent_profile_category,
               profile.published_at as agent_profile_published_at
        from sessions
        left join agent_profile_revisions profile
          on profile.tenant_id = sessions.tenant_id
         and profile.agent_id = sessions.agent_id
         and profile.revision = sessions.admitted_agent_profile_revision
         and profile.content_hash = sessions.admitted_agent_profile_hash
        where sessions.tenant_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.status = 'active'
        """,
        (tenant_id, user_id, session_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def get_authorized_lambchat_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, workspace_id, agent_id, title, title_source, status, created_at, updated_at
        from sessions
        where tenant_id = %s
          and id = %s
          and user_id = %s
          and status = 'active'
        """,
        (tenant_id, session_id, user_id),
    )
    return await cursor.fetchone()


async def get_session_for_action(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Load one tenant session for an application service to authorize."""

    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, title, title_source, status, created_at, updated_at
        from sessions
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, session_id),
    )
    return await cursor.fetchone()


async def update_session_title(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    title: str,
    title_source: str = "user",
    expected_title_source: str | None = None,
) -> dict[str, Any] | None:
    """Rename an active tenant session after application-layer authorization."""

    expected_source_clause = "" if expected_title_source is None else " and title_source = %s"
    params: tuple[Any, ...] = (title, title_source, tenant_id, session_id)
    if expected_title_source is not None:
        params += (expected_title_source,)
    cursor = await conn.execute(
        """
        update sessions
        set title = %s, title_source = %s, updated_at = now()
        where tenant_id = %s and id = %s and status = 'active'
        """
        + expected_source_clause
        + """
        returning id, tenant_id, workspace_id, user_id, agent_id, title, title_source, status, created_at, updated_at
        """,
        params,
    )
    return await cursor.fetchone()


async def mark_session_deleted(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Soft-delete an active tenant session after application-layer authorization."""

    cursor = await conn.execute(
        """
        update sessions
        set status = 'deleted', updated_at = now()
        where tenant_id = %s and id = %s and status = 'active'
        returning id, tenant_id, workspace_id, user_id, agent_id, title, status, created_at, updated_at
        """,
        (tenant_id, session_id),
    )
    return await cursor.fetchone()


async def list_session_messages_for_fork(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    limit: int = 201,
) -> list[dict[str, Any]]:
    """Load one authorized source session's ordered message prefix candidates."""

    cursor = await conn.execute(
        """
        select id, run_id, role, content, metadata_json, created_at
        from messages
        where tenant_id = %s and session_id = %s
        order by created_at asc, id asc
        limit %s
        """,
        (tenant_id, session_id, max(1, min(int(limit), 201))),
    )
    return list(await cursor.fetchall())


async def append_message(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str | None,
    role: str,
    content: str,
    metadata_json: dict[str, Any] | None = None,
) -> str:
    resolved_metadata = metadata_json or {}
    _require_text_size(
        content,
        max_bytes=MESSAGE_CONTENT_MAX_BYTES,
        code="message_content_too_large",
    )
    _require_json_size(
        resolved_metadata,
        max_bytes=MESSAGE_METADATA_MAX_BYTES,
        code="message_metadata_too_large",
    )
    message_id = _new_id("msg")
    await conn.execute(
        """
        insert into messages(id, tenant_id, session_id, run_id, role, content, metadata_json)
        values (%s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            message_id,
            tenant_id,
            session_id,
            run_id,
            role,
            content,
            _dumps_json(resolved_metadata),
        ),
    )
    await conn.execute(
        "update sessions set updated_at = now() where tenant_id = %s and id = %s",
        (tenant_id, session_id),
    )
    return message_id


async def list_authorized_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    cursor: tuple[Any, str] | None = None,
    limit: int = 101,
) -> list[dict[str, Any]]:
    cursor_filter = ""
    params: list[Any] = [tenant_id, session_id, user_id]
    if cursor is not None:
        cursor_filter = "and (messages.created_at, messages.id) > (%s, %s)"
        params.extend(cursor)
    params.append(max(1, min(int(limit), 201)))
    cursor = await conn.execute(
        f"""
        select messages.id, messages.session_id, messages.run_id, messages.role, messages.content,
               messages.metadata_json, messages.created_at
        from messages
        join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
        where messages.tenant_id = %s
          and messages.session_id = %s
          and sessions.user_id = %s
          {cursor_filter}
        order by messages.created_at asc, messages.id asc
        limit %s
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def list_authorized_user_messages_for_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    run_ids: list[str],
) -> list[dict[str, Any]]:
    """Project minimal persisted user turns for authorized target runs."""

    target_run_ids = list(
        dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip())
    )
    if not target_run_ids:
        return []
    cursor = await conn.execute(
        """
        select messages.id, messages.run_id, messages.content, messages.metadata_json,
               messages.created_at
        from messages
        join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
        where messages.tenant_id = %s
          and messages.session_id = %s
          and sessions.user_id = %s
          and messages.role = 'user'
          and messages.run_id = any(%s::text[])
        order by messages.created_at asc, messages.id asc
        """,
        (tenant_id, session_id, user_id, target_run_ids),
    )
    return list(await cursor.fetchall())


async def list_authorized_agent_conversations(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    revision: int,
    cursor: tuple[datetime, datetime, str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """List one principal-owned immutable Agent/revision history page."""

    boundary_sql = ""
    params: list[Any] = [tenant_id, user_id, agent_id, revision]
    if cursor is not None:
        updated_at, created_at, session_id = cursor
        boundary_sql = """
          and (
            sessions.updated_at < %s
            or (sessions.updated_at = %s and sessions.created_at < %s)
            or (
              sessions.updated_at = %s
              and sessions.created_at = %s
              and sessions.id < %s
            )
          )
        """
        params.extend(
            [updated_at, updated_at, created_at, updated_at, created_at, session_id]
        )
    params.append(limit)
    result = await conn.execute(
        f"""
        select sessions.id, sessions.workspace_id, sessions.agent_id,
               coalesce(legacy_first_user.title, sessions.title) as title, sessions.purpose,
               sessions.admitted_agent_profile_revision, sessions.admitted_agent_profile_hash,
               sessions.created_at, sessions.updated_at,
               profile.name as agent_profile_name,
               profile.description as agent_profile_description,
               profile.welcome_message as agent_profile_welcome_message,
               profile.starter_prompts as agent_profile_starter_prompts,
               profile.capability_summary as agent_profile_capability_summary,
               profile.recommended_tasks as agent_profile_recommended_tasks,
               profile.supported_input_types as agent_profile_supported_input_types,
               profile.expected_outputs as agent_profile_expected_outputs,
               profile.permissions_and_data_access_notice as agent_profile_permissions_and_data_access_notice,
               profile.avatar_ref as agent_profile_avatar_ref,
               profile.avatar_seed as agent_profile_avatar_seed,
               profile.category as agent_profile_category,
               profile.published_at as agent_profile_published_at
        from sessions
        join agent_profile_revisions profile
          on profile.tenant_id = sessions.tenant_id
         and profile.agent_id = sessions.agent_id
         and profile.revision = sessions.admitted_agent_profile_revision
         and profile.content_hash = sessions.admitted_agent_profile_hash
        left join lateral (
          select left(
            btrim(
              translate(
                messages.content,
                chr(13) || chr(10) || chr(9) || chr(11) || chr(12),
                '     '
              )
            ),
            32
          ) as title
          from messages
          where sessions.title_source = 'initial'
            and sessions.title = profile.name
            and messages.tenant_id = sessions.tenant_id
            and messages.session_id = sessions.id
            and messages.role = 'user'
            and btrim(
              translate(
                messages.content,
                chr(13) || chr(10) || chr(9) || chr(11) || chr(12),
                '     '
              )
            ) <> ''
          order by messages.created_at asc, messages.id asc
          limit 1
        ) legacy_first_user on true
        where sessions.tenant_id = %s
          and sessions.user_id = %s
          and sessions.agent_id = %s
          and sessions.admitted_agent_profile_revision = %s
          and sessions.status = 'active'
          and sessions.purpose = 'conversation'
          {boundary_sql}
        order by sessions.updated_at desc, sessions.created_at desc, sessions.id desc
        limit %s
        """,
        tuple(params),
    )
    return list(await result.fetchall())

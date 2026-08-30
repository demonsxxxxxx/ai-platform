from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.kernel.memory_redaction import (
    normalize_memory_redaction_mode,
    redact_memory_metadata,
    redact_memory_text,
)
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError


MEMORY_RETENTION_CLEANUP_CURSOR_KEY = "memory_retention_cleanup"


def memory_policy_id(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> str:
    raw = "\x1f".join([tenant_id, workspace_id, user_id, agent_id or ""])
    return f"mempol_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


async def list_scoped_context_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    query_pattern = f"%{query}%" if query else ""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, content, metadata_json, status, deleted_at, created_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and agent_id = %s
          and session_id = %s
          and status = 'active'
          and deleted_at is null
          and (%s = '' or content ilike %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, agent_id, session_id, query_pattern, query_pattern, max(1, int(limit))),
    )
    return list(await cursor.fetchall())


def _default_memory_policy(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "standard",
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


def _stored_memory_redaction_mode(value: object) -> str:
    if value is None or str(value).strip() == "":
        return "strict"
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError:
        return "strict"


def _validated_memory_redaction_mode(value: object) -> str:
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError as exc:
        raise RepositoryConflictError(str(exc)) from exc


def _memory_policy_from_row(row: dict[str, Any], *, source: str = "stored") -> dict[str, Any]:
    return {
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": row.get("agent_id"),
        "memory_enabled": bool(row.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(row.get("retention_days") or 90),
        "redaction_mode": _stored_memory_redaction_mode(row.get("redaction_mode")),
        "source": source,
        "reason": str(row.get("reason") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "updated_at": row.get("updated_at"),
    }


async def get_effective_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               redaction_mode, reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and (agent_id = %s or agent_id is null)
        order by case when agent_id = %s then 0 else 1 end, updated_at desc
        limit 1
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return _default_memory_policy(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    return _memory_policy_from_row(dict(row))


async def set_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    memory_enabled: bool,
    long_term_memory_enabled: bool,
    retention_days: int,
    redaction_mode: str,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    if long_term_memory_enabled:
        raise RepositoryConflictError("long_term_memory_not_available")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    policy_id = memory_policy_id(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    cursor = await conn.execute(
        """
        insert into memory_policies(
          id, tenant_id, workspace_id, user_id, agent_id,
          memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
          reason, updated_by
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update
        set memory_enabled = excluded.memory_enabled,
            long_term_memory_enabled = excluded.long_term_memory_enabled,
            retention_days = excluded.retention_days,
            redaction_mode = excluded.redaction_mode,
            reason = excluded.reason,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, workspace_id, user_id, agent_id,
                  memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
                  reason, updated_by, updated_at
        """,
        (
            policy_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            bool(memory_enabled),
            bool(long_term_memory_enabled),
            int(retention_days),
            redaction_mode,
            reason,
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    return _memory_policy_from_row(dict(row))


async def list_admin_memory_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return stored memory policies for an admin same-tenant operational view."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               redaction_mode, reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or agent_id = %s)
        order by updated_at desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, agent_id, agent_id, limit),
    )
    return [_memory_policy_from_row(dict(row)) for row in await cursor.fetchall()]


async def create_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    session_id: str | None,
    record_type: str,
    content: str,
    metadata_json: dict[str, Any],
    retention_days: int = 90,
    redaction_mode: str = "standard",
) -> dict[str, Any]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    if not agent_id:
        raise RepositoryConflictError("memory_agent_id_required")
    retention_days = int(retention_days)
    if retention_days <= 0:
        raise RepositoryConflictError("memory_retention_days_invalid")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    record_id = f"mem_{uuid.uuid4().hex}"
    redacted_content = redact_memory_text(content, mode=redaction_mode)
    redacted_metadata = redact_memory_metadata(metadata_json, mode=redaction_mode)
    cursor = await conn.execute(
        """
        insert into memory_records(
          id, tenant_id, workspace_id, user_id, agent_id, session_id,
          record_type, content, metadata_json, expires_at
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now() + (%s * interval '1 day')
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, content, metadata_json, status, expires_at,
                  deleted_at, created_at, updated_at
        """,
        (
            record_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            session_id,
            record_type,
            redacted_content,
            json.dumps(redacted_metadata, ensure_ascii=False),
            retention_days,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            agent_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("session_not_found")
    return dict(row)


async def list_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, content, metadata_json, status, expires_at,
               deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and status = 'active'
          and deleted_at is null
          and (%s::text is null or agent_id = %s)
          and (%s::text is null or session_id = %s)
          and (expires_at is null or expires_at > now())
        order by created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id, session_id, session_id, limit),
    )
    return list(await cursor.fetchall())


async def list_admin_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    status: str = "active",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status not in {"active", "deleted", "all"}:
        raise RepositoryConflictError("memory_status_invalid")
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, status, expires_at, deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s = 'all' or status = %s)
        order by coalesce(deleted_at, expires_at, created_at) desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and agent_id = %s
          and session_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, user_id, agent_id, session_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def admin_delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def cleanup_expired_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise RepositoryConflictError("memory_workspace_id_required")
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (
          select id
          from memory_records
          where tenant_id = %s
            and workspace_id = %s
            and status = 'active'
            and deleted_at is null
            and expires_at is not null
            and expires_at <= now()
          order by expires_at asc, created_at asc
          limit %s
          for update skip locked
        )
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, limit),
    )
    return list(await cursor.fetchall())


async def cleanup_expired_memory_records_across_scopes(
    conn: AsyncConnection,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Soft-delete expired memory records using a bounded rotating scope cursor."""
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from worker_maintenance_cursors
        where cursor_key = %s
        for update
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY,),
    )
    cursor_row = await cursor.fetchone()
    last_tenant_id = str(cursor_row["tenant_id"]) if cursor_row and cursor_row.get("tenant_id") else None
    last_workspace_id = str(cursor_row["workspace_id"]) if cursor_row and cursor_row.get("workspace_id") else None

    scope_rows: list[dict[str, Any]] = []
    if last_tenant_id is not None and last_workspace_id is not None:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=last_tenant_id,
                after_workspace_id=last_workspace_id,
                limit=limit,
            )
        )
    else:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=None,
                after_workspace_id=None,
                limit=limit,
            )
        )
    if last_tenant_id is not None and last_workspace_id is not None and len(scope_rows) < limit:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                before_or_at_tenant_id=last_tenant_id,
                before_or_at_workspace_id=last_workspace_id,
                limit=limit - len(scope_rows),
            )
        )
    if not scope_rows:
        return []

    last_scope = scope_rows[-1]
    await conn.execute(
        """
        insert into worker_maintenance_cursors(cursor_key, tenant_id, workspace_id)
        values (%s, %s, %s)
        on conflict (cursor_key) do update
        set tenant_id = excluded.tenant_id,
            workspace_id = excluded.workspace_id,
            updated_at = now()
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY, str(last_scope["tenant_id"]), str(last_scope["workspace_id"])),
    )
    per_scope_limit = max(1, (limit + len(scope_rows) - 1) // len(scope_rows))
    tenant_ids = [str(row["tenant_id"]) for row in scope_rows]
    workspace_ids = [str(row["workspace_id"]) for row in scope_rows]
    cursor = await conn.execute(
        """
        with candidate_scopes as (
          select *
          from unnest(%s::text[], %s::text[]) as scope(tenant_id, workspace_id)
        ),
        candidate_rows as (
          select selected.id,
                 selected.expires_at,
                 selected.created_at,
                 selected.tenant_id,
                 selected.workspace_id,
                 selected.scope_rank
          from candidate_scopes scope
          cross join lateral (
            select locked_rows.id,
                   locked_rows.expires_at,
                   locked_rows.created_at,
                   locked_rows.tenant_id,
                   locked_rows.workspace_id,
                   row_number() over (
                     order by locked_rows.expires_at asc, locked_rows.created_at asc, locked_rows.id asc
                   ) as scope_rank
            from (
              select id, expires_at, created_at, tenant_id, workspace_id
              from memory_records
              where memory_records.tenant_id = scope.tenant_id
                and memory_records.workspace_id = scope.workspace_id
                and status = 'active'
                and deleted_at is null
                and expires_at is not null
                and expires_at <= now()
              order by expires_at asc, created_at asc, id asc
              limit %s
              for update skip locked
            ) locked_rows
          ) selected
          order by case when selected.scope_rank = 1 then 0 else 1 end,
                   selected.expires_at asc,
                   selected.created_at asc,
                   selected.tenant_id asc,
                   selected.workspace_id asc,
                   selected.id asc
          limit %s
        )
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (select id from candidate_rows)
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_ids, workspace_ids, per_scope_limit, limit),
    )
    return list(await cursor.fetchall())


async def _list_expired_memory_cleanup_scopes(
    conn: AsyncConnection,
    *,
    after_tenant_id: str | None = None,
    after_workspace_id: str | None = None,
    before_or_at_tenant_id: str | None = None,
    before_or_at_workspace_id: str | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from memory_records
        where status = 'active'
          and deleted_at is null
          and expires_at is not null
          and expires_at <= now()
          and (
            %s::text is null
            or (tenant_id, workspace_id) > (%s, %s)
          )
          and (
            %s::text is null
            or (tenant_id, workspace_id) <= (%s, %s)
          )
        group by tenant_id, workspace_id
        order by tenant_id asc, workspace_id asc
        limit %s
        """,
        (
            after_tenant_id,
            after_tenant_id,
            after_workspace_id,
            before_or_at_tenant_id,
            before_or_at_tenant_id,
            before_or_at_workspace_id,
            int(limit),
        ),
    )
    return list(await cursor.fetchall())

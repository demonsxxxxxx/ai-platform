"""Capability distributions persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.capability_distribution import CapabilityAccessContext
from app.capability_distribution import CapabilityAccessDecision
from app.capability_distribution import CapabilityAuthorizationDenial
from app.capability_distribution import has_valid_capability_distribution_archive_evidence
from app.capability_distribution import is_capability_distribution_archived as shared_capability_distribution_archived
from app.capability_distribution import is_valid_archive_actor
from app.platform.postgres.errors import RepositoryAuthorizationError
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from app.skills.dependencies import PUBLIC_WORKBENCH_SKILL_IDS
from psycopg import AsyncConnection
from typing import Any
import json


def _capability_distribution_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise RepositoryConflictError("capability_distribution_scope_invalid")
    if isinstance(value, (list, tuple)):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise RepositoryConflictError("capability_distribution_scope_invalid")
        return list(value)
    raise RepositoryConflictError("capability_distribution_scope_invalid")


def _capability_distribution_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _capability_distribution_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "tenant_id": str(row.get("tenant_id") or ""),
        "capability_kind": str(row.get("capability_kind") or ""),
        "capability_id": str(row.get("capability_id") or ""),
        "status": str(row.get("status") or "disabled"),
        "visible_to_user": bool(row.get("visible_to_user")),
        "scope_mode": str(row.get("scope_mode") or "allowlist"),
        "department_ids": _capability_distribution_string_list(row.get("department_ids")),
        "allowed_roles": _capability_distribution_string_list(row.get("allowed_roles")),
        "metadata_json": _capability_distribution_json(row.get("metadata_json")),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def is_capability_distribution_archived(row: dict[str, Any] | None) -> bool:
    """Return whether a tenant capability binding has been archived."""

    return shared_capability_distribution_archived(row)


async def _acquire_capability_distribution_lifecycle_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> None:
    """Serialize one distribution lifecycle key, including writes for currently missing rows."""

    lock_scope = json.dumps(
        {
            "capability_id": capability_id,
            "capability_kind": capability_kind,
            "tenant_id": tenant_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


async def acquire_capability_distribution_lifecycle_locks(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_ids: list[str],
) -> None:
    """Finish tenant backfill before pre-acquiring ordered lifecycle keys for one batch."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    for capability_id in sorted(set(capability_ids)):
        await _acquire_capability_distribution_lifecycle_lock(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )


async def _lock_capability_distribution_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any] | None:
    """Lock one binding and return its parsed metadata for archive/write lifecycle decisions."""

    cursor = await conn.execute(
        """
        select metadata_json
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        for update
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    return _capability_distribution_json(row.get("metadata_json")) if row is not None else None


async def _require_unarchived_capability_distribution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    allow_missing: bool,
) -> None:
    """Reject valid archive markers before a distribution mutation while holding the row lock."""

    metadata_json = await _lock_capability_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    if metadata_json is None:
        if not allow_missing:
            raise RepositoryNotFoundError("capability_distribution_not_found")
        return
    if shared_capability_distribution_archived({"metadata_json": metadata_json}):
        raise RepositoryConflictError("capability_distribution_archived")


async def _raise_distribution_update_failure(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> None:
    """Distinguish an archived binding from a missing row after a guarded write."""

    cursor = await conn.execute(
        """
        select metadata_json
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    if row is not None and is_capability_distribution_archived(dict(row)):
        raise RepositoryConflictError("capability_distribution_archived")
    raise RepositoryNotFoundError("capability_distribution_not_found")


async def ensure_tenant_capability_distribution_backfill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> None:
    """Backfill one tenant exactly once; insert-only conflicts cannot overwrite lifecycle state."""

    completion_cursor = await conn.execute(
        """
        select completed_at
        from tenant_capability_distribution_backfills
        where tenant_id = %s
        """,
        (tenant_id,),
    )
    completion = await completion_cursor.fetchone()
    if completion is not None and completion.get("completed_at") is not None:
        return

    await conn.execute(
        """
        insert into tenant_capability_distribution_backfills(tenant_id)
        values (%s)
        on conflict (tenant_id) do nothing
        """,
        (tenant_id,),
    )
    completion_cursor = await conn.execute(
        """
        select completed_at
        from tenant_capability_distribution_backfills
        where tenant_id = %s
        for update
        """,
        (tenant_id,),
    )
    completion = await completion_cursor.fetchone()
    if completion is not None and completion.get("completed_at") is not None:
        return

    await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json
        )
        select
          source_rows.id, source_rows.tenant_id, source_rows.capability_kind,
          source_rows.capability_id, source_rows.status, source_rows.visible_to_user,
          source_rows.scope_mode, source_rows.department_ids, source_rows.allowed_roles,
          source_rows.metadata_json
        from (
          select
            'capdist_' || substr(md5(tenant_workbench_skills.tenant_id || ':skill:' || tenant_workbench_skills.skill_id), 1, 24),
            tenant_workbench_skills.tenant_id,
            'skill',
            tenant_workbench_skills.skill_id,
            tenant_workbench_skills.status,
            tenant_workbench_skills.visible_to_user,
            'allowlist',
            array[]::text[],
            '[]'::jsonb,
            '{"legacy_source":"tenant_workbench_skills"}'::jsonb
          from tenant_workbench_skills
          join skills on skills.id = tenant_workbench_skills.skill_id
          where tenant_workbench_skills.tenant_id = %s
            and skills.status = 'active'
          union all
          select
            'capdist_' || substr(md5(%s || ':skill:' || skills.id), 1, 24),
            %s,
            'skill',
            skills.id,
            'active',
            true,
            'allowlist',
            array[]::text[],
            '[]'::jsonb,
            '{"legacy_source":"builtin_public_skill"}'::jsonb
          from skills
          left join tenant_workbench_skills
            on tenant_workbench_skills.tenant_id = %s
           and tenant_workbench_skills.skill_id = skills.id
          where skills.id = any(%s)
            and skills.status = 'active'
            and tenant_workbench_skills.skill_id is null
        ) as source_rows(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json
        )
        on conflict (tenant_id, capability_kind, capability_id) do nothing
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, sorted(PUBLIC_WORKBENCH_SKILL_IDS)),
    )
    await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by
        )
        select
          'capdist_' || substr(md5(mcp_servers.tenant_id || ':mcp_server:' || mcp_servers.name), 1, 24),
          mcp_servers.tenant_id,
          'mcp_server',
          mcp_servers.name,
          case
            when not role_validation.scope_valid or not department_validation.scope_valid then 'disabled'
            when mcp_servers.status = 'active' then 'active'
            else 'disabled'
          end,
          true,
          'allowlist',
          case
            when department_validation.scope_valid then mcp_servers.department_ids
            else array[]::text[]
          end,
          role_scope.normalized_allowed_roles,
          jsonb_build_object(
            'legacy_source', 'mcp_servers',
            'legacy_scope_invalid',
            not role_validation.scope_valid or not department_validation.scope_valid
          ),
          mcp_servers.updated_by
        from mcp_servers
        cross join lateral (
          select case
            when jsonb_typeof(mcp_servers.allowed_roles) is distinct from 'array' then false
            when exists (
              select 1
              from jsonb_array_elements(mcp_servers.allowed_roles) as role_items(role_value)
              where jsonb_typeof(role_value) is distinct from 'string'
                 or btrim(role_value #>> '{}') = ''
            ) then false
            else true
          end as scope_valid
        ) as role_validation
        cross join lateral (
          select coalesce(
            bool_and(department_id is not null and btrim(department_id) <> ''),
            true
          ) as scope_valid
          from unnest(mcp_servers.department_ids) as department_items(department_id)
        ) as department_validation
        cross join lateral (
          select case
            when role_validation.scope_valid then coalesce(
              (
                select jsonb_agg(normalized_role order by normalized_role)
                from (
                  select distinct lower(btrim(role_value #>> '{}')) as normalized_role
                  from jsonb_array_elements(mcp_servers.allowed_roles) as role_items(role_value)
                ) as normalized_roles
              ),
              '[]'::jsonb
            )
            else '[]'::jsonb
          end as normalized_allowed_roles
        ) as role_scope
        where mcp_servers.tenant_id = %s
          and mcp_servers.status <> 'deleted'
        on conflict (tenant_id, capability_kind, capability_id) do nothing
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        update tenant_capability_distribution_backfills
        set completed_at = now()
        where tenant_id = %s
        """,
        (tenant_id,),
    )


async def list_capability_distribution_rows(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str | None = None,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """List the authoritative distribution rows for one tenant."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    filters = ["tenant_id = %s", "(%s or status = 'active')"]
    params: list[Any] = [tenant_id, include_disabled]
    if capability_kind is not None:
        filters.insert(1, "capability_kind = %s")
        params.insert(1, capability_kind)
    cursor = await conn.execute(
        f"""
        select id, tenant_id, capability_kind, capability_id, status, visible_to_user,
               scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
               created_at, updated_at
        from tenant_capability_distributions
        where {' and '.join(filters)}
        order by capability_kind asc, capability_id asc
        """,
        tuple(params),
    )
    return [_capability_distribution_projection(dict(row)) for row in await cursor.fetchall()]


async def get_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any] | None:
    """Fetch one authoritative distribution row after insert-only backfill."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select id, tenant_id, capability_kind, capability_id, status, visible_to_user,
               scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
               created_at, updated_at
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    return _capability_distribution_projection(dict(row)) if row is not None else None


async def upsert_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    status: str,
    visible_to_user: bool,
    scope_mode: str,
    department_ids: list[str],
    allowed_roles: list[str],
    metadata_json: dict[str, Any],
    updated_by: str | None,
) -> dict[str, Any]:
    """Create or update one authoritative capability distribution row."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=True,
    )
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
        on conflict (tenant_id, capability_kind, capability_id) do update
        set status = excluded.status,
            visible_to_user = excluded.visible_to_user,
            scope_mode = excluded.scope_mode,
            department_ids = excluded.department_ids,
            allowed_roles = excluded.allowed_roles,
            metadata_json = excluded.metadata_json,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (
            new_id("capdist"),
            tenant_id,
            capability_kind,
            capability_id,
            status,
            visible_to_user,
            scope_mode,
            department_ids,
            json.dumps(allowed_roles, ensure_ascii=False),
            dumps_json(metadata_json),
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    projected = _capability_distribution_projection(dict(row))
    if capability_kind == "mcp_server":
        distribution_enabled = projected["status"] == "active"
        catalog_cursor = await conn.execute(
            """
            update mcp_servers
            set catalog_generation = catalog_generation + 1,
                catalog_status = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_unavailable_reason = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_discovered_count = 0,
                catalog_selectable_count = 0,
                catalog_sync_lease_expires_at = null,
                updated_at = now()
            where tenant_id = %s
              and name = %s
              and status <> 'deleted'
            returning name
            """,
            (distribution_enabled, distribution_enabled, tenant_id, capability_id),
        )
        if await catalog_cursor.fetchone() is None:
            raise RepositoryNotFoundError("mcp_server_not_found")
    return projected


async def archive_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    archived_by: str | None,
) -> dict[str, Any]:
    """Archive one tenant capability binding without mutating global Skill evidence."""

    if not is_valid_archive_actor(archived_by):
        raise RepositoryConflictError("capability_distribution_archive_actor_invalid")
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    metadata_json = await _lock_capability_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    if metadata_json is None:
        raise RepositoryNotFoundError("capability_distribution_not_found")
    preserve_existing_evidence = has_valid_capability_distribution_archive_evidence(
        {"metadata_json": metadata_json}
    )
    cursor = await conn.execute(
        """
        update tenant_capability_distributions
        set status = 'disabled',
            visible_to_user = false,
            metadata_json = case
              when jsonb_typeof(metadata_json) = 'object' then metadata_json
              else '{}'::jsonb
            end || jsonb_build_object(
              'archived_at', case
                when %s::boolean then metadata_json -> 'archived_at'
                else to_jsonb(to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
              end,
              'archived_by', case
                when %s::boolean then metadata_json -> 'archived_by'
                else to_jsonb(left(coalesce(%s, ''), 255))
              end
            ),
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (
            preserve_existing_evidence,
            preserve_existing_evidence,
            archived_by,
            archived_by,
            tenant_id,
            capability_kind,
            capability_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("capability_distribution_not_found")
    return _capability_distribution_projection(dict(row))


async def toggle_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    enabled: bool | None,
    updated_by: str | None,
) -> dict[str, Any]:
    """Toggle or set the status of one authoritative distribution row."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=False,
    )
    cursor = await conn.execute(
        """
        update tenant_capability_distributions
        set status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'active' end
              when %s::boolean then 'active'
              else 'disabled'
            end,
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (enabled, enabled, updated_by, tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    projected = _capability_distribution_projection(dict(row))
    if capability_kind == "mcp_server":
        distribution_enabled = projected["status"] == "active"
        catalog_cursor = await conn.execute(
            """
            update mcp_servers
            set catalog_generation = catalog_generation + 1,
                catalog_status = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_unavailable_reason = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_discovered_count = 0,
                catalog_selectable_count = 0,
                catalog_sync_lease_expires_at = null,
                updated_at = now()
            where tenant_id = %s
              and name = %s
              and status <> 'deleted'
            returning name
            """,
            (distribution_enabled, distribution_enabled, tenant_id, capability_id),
        )
        if await catalog_cursor.fetchone() is None:
            raise RepositoryNotFoundError("mcp_server_not_found")
    return projected


async def set_capability_distribution_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    status: str,
    updated_by: str | None,
) -> dict[str, Any]:
    """Set authoritative status while preserving existing distribution scope."""

    if status not in {"active", "disabled"}:
        raise RepositoryConflictError("invalid_capability_distribution_status")
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=True,
    )
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, true, 'allowlist', array[]::text[], '[]'::jsonb, '{}'::jsonb, %s, now())
        on conflict (tenant_id, capability_kind, capability_id) do update
        set status = excluded.status,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (new_id("capdist"), tenant_id, capability_kind, capability_id, status, updated_by),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    return _capability_distribution_projection(dict(row))


def _capability_not_authorized(
    *,
    context: CapabilityAccessContext | None = None,
    capability_kind: str = "",
    capability_id: str = "",
    decision: CapabilityAccessDecision | None = None,
) -> RepositoryAuthorizationError:
    denial = None
    if context is not None and capability_kind and capability_id:
        denial_decision = decision or CapabilityAccessDecision(
            visible=False,
            usable=False,
            manageable=False,
            admin_bypass=False,
            decision_reason="capability_not_authorized",
        )
        denial = CapabilityAuthorizationDenial.from_decision(
            decision=denial_decision,
            actor_department_id=context.department_id,
            actor_roles=context.roles,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    return RepositoryAuthorizationError("capability_not_authorized", denial=denial)

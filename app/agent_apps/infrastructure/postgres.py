"""Agent Profile identity, revision, and aggregate persistence."""

from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


_KNOWLEDGE_FRESHNESS_SELECT = """
(
  select case
           when bool_and(connections.last_complete_sync_at is not null)
             then min(connections.last_complete_sync_at)
           else null
         end
  from jsonb_array_elements_text(
    agent_profile_revisions.knowledge_source_ids
  ) as bound_source(source_id)
  join knowledge_sources sources
    on sources.tenant_id = agent_profile_revisions.tenant_id
   and sources.id = bound_source.source_id
   and sources.status = 'active'
  join knowledge_connections connections
    on connections.tenant_id = sources.tenant_id
   and connections.id = sources.connection_id
   and connections.status = 'active'
) as knowledge_freshness_at
"""


async def ensure_agent_profile_identity(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    name: str,
    default_skill_id: str,
) -> None:
    """Create exactly one profile identity without mutating existing Agent definitions."""

    cursor = await conn.execute(
        """
        select id, tenant_id, agent_type, status
        from agents
        where id = %s
        """,
        (agent_id,),
    )
    existing = await cursor.fetchone()
    if existing is not None:
        if (
            str(existing.get("tenant_id") or "") != tenant_id
            or str(existing.get("agent_type") or "") != "profile"
        ):
            raise RepositoryConflictError("agent_profile_identity_conflict")
        if str(existing.get("status") or "") != "active":
            raise RepositoryConflictError("agent_inactive")
        return
    await conn.execute(
        """
        insert into agents(id, tenant_id, name, agent_type, description, default_skill_id, status)
        values (%s, %s, %s, 'profile', '', %s, 'active')
        """,
        (agent_id, tenant_id, name, default_skill_id),
    )


async def acquire_agent_profile_lifecycle_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
) -> None:
    """Serialize every lifecycle writer before it reads or mutates profile state."""

    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"agent-profile:{tenant_id}:{agent_id}",),
    )


async def create_agent_profile_revision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    status: str,
    name: str,
    description: str,
    instructions: str,
    legacy_model_id: str,
    skill_id: str,
    skill_version: str,
    mcp_tool_ids: list[str],
    content_hash: str,
    created_by: str,
    skill_set: list[dict[str, str]] | None = None,
    published_by: str | None = None,
    expected_previous_revision: int | None = None,
    published_from_revision: int | None = None,
    avatar_ref: str = "builtin:agent",
    avatar_seed: str = "",
    category: str = "general",
    visibility: str = "tenant",
    allowed_department_ids: list[str] | None = None,
    allowed_roles: list[str] | None = None,
    allowed_user_ids: list[str] | None = None,
    withdrawn_from_revision: int | None = None,
    welcome_message: str = "",
    starter_prompts: list[str] | None = None,
    capability_summary: str = "",
    recommended_tasks: list[str] | None = None,
    supported_input_types: list[str] | None = None,
    legacy_supported_file_types: list[str] | None = None,
    expected_outputs: list[str] | None = None,
    permissions_and_data_access_notice: str = "",
    avatar_asset_id: str | None = None,
    knowledge_source_ids: list[str] | None = None,
    retrieval_profile_id: str | None = None,
    knowledge_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append one revision under an optimistic fence and transaction advisory lock."""

    await acquire_agent_profile_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )
    cursor = await conn.execute(
        """
        select coalesce(max(revision), 0) as current_revision
        from agent_profile_revisions
        where tenant_id = %s and agent_id = %s
        """,
        (tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    current_revision = int(row["current_revision"] if row else 0)
    if expected_previous_revision is not None and current_revision != expected_previous_revision:
        raise RepositoryConflictError("agent_profile_revision_stale")
    revision = current_revision + 1
    legacy_status = "published" if status == "published" and visibility == "tenant" else "draft"
    cursor = await conn.execute(
        """
        insert into agent_profile_revisions(
          tenant_id, agent_id, revision, status, revision_status, name, description, instructions,
          model_id, skill_id, skill_version, skill_set, mcp_tool_ids,
          knowledge_source_ids, retrieval_profile_id, knowledge_bindings, content_hash,
          avatar_ref, avatar_seed, category, visibility, allowed_department_ids, allowed_roles,
          allowed_user_ids, welcome_message, starter_prompts, capability_summary,
          recommended_tasks, supported_input_types, supported_file_types, expected_outputs,
          permissions_and_data_access_notice, avatar_asset_id,
          created_by, published_by, published_at,
          published_from_revision, withdrawn_from_revision
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s, %s::jsonb, %s,
                %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb,
                %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s,
                %s, %s, %s, case when %s::text is null then null else now() end, %s, %s)
        returning tenant_id, agent_id, revision, revision_status as status, name, description, instructions,
                  model_id, skill_id, skill_version, skill_set, mcp_tool_ids,
                  knowledge_source_ids, retrieval_profile_id, knowledge_bindings, content_hash,
                  avatar_ref, avatar_seed, category, visibility, allowed_department_ids, allowed_roles,
                  allowed_user_ids, welcome_message, starter_prompts, capability_summary,
                  recommended_tasks, supported_input_types,
                  supported_file_types as legacy_supported_file_types, expected_outputs,
                  permissions_and_data_access_notice, avatar_asset_id,
                  created_at, published_at
        """,
        (
            tenant_id,
            agent_id,
            revision,
            legacy_status,
            status,
            name,
            description,
            instructions,
            legacy_model_id,
            skill_id,
            skill_version,
            _dumps_json(
                skill_set
                or [{"skill_id": skill_id, "expected_version": skill_version}]
            ),
            _dumps_json(mcp_tool_ids),
            _dumps_json(knowledge_source_ids or []),
            retrieval_profile_id,
            _dumps_json(knowledge_bindings or []),
            content_hash,
            avatar_ref,
            avatar_seed,
            category,
            visibility,
            _dumps_json(allowed_department_ids or []),
            _dumps_json(allowed_roles or []),
            _dumps_json(allowed_user_ids or []),
            welcome_message,
            _dumps_json(starter_prompts or []),
            capability_summary,
            _dumps_json(recommended_tasks or []),
            _dumps_json(supported_input_types or ["text"]),
            _dumps_json(legacy_supported_file_types or []),
            _dumps_json(expected_outputs or []),
            permissions_and_data_access_notice,
            avatar_asset_id,
            created_by,
            published_by,
            published_by,
            published_from_revision,
            withdrawn_from_revision,
        ),
    )
    saved = await cursor.fetchone()
    if saved is None:
        raise RepositoryConflictError("agent_profile_revision_write_failed")
    return dict(saved)


async def get_agent_profile_revision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    revision: int,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Read one tenant-scoped immutable revision, optionally by lifecycle state."""

    status_filter = "and agent_profile_revisions.revision_status = %s" if status else ""
    params: list[Any] = [tenant_id, agent_id, revision]
    if status:
        params.append(status)
    cursor = await conn.execute(
        f"""
        select agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision, agent_profile_revisions.revision_status as status,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message, agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary, agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions, agent_profile_revisions.model_id,
               agent_profile_revisions.skill_id, agent_profile_revisions.skill_version,
               agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed,
               agent_profile_revisions.category,
               agent_profile_revisions.visibility, agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profile_revisions
        join agents on agents.id = agent_profile_revisions.agent_id
          and agents.tenant_id = agent_profile_revisions.tenant_id
        where agent_profile_revisions.tenant_id = %s
          and agent_profile_revisions.agent_id = %s
          and agent_profile_revisions.revision = %s
          and agents.agent_type = 'profile'
          and agents.status = 'active'
          {status_filter}
        """,
        tuple(params),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def list_latest_agent_profile_revisions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List only each profile's latest revision for the calling tenant."""

    status_filter = "and agent_profile_revisions.revision_status = %s" if status else ""
    params: tuple[Any, ...] = (tenant_id, status) if status else (tenant_id,)
    cursor = await conn.execute(
        f"""
        select distinct on (agent_profile_revisions.agent_id)
               agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision, agent_profile_revisions.revision_status as status,
               agent_profiles.published_revision,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message, agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary, agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions, agent_profile_revisions.model_id,
               agent_profile_revisions.skill_id, agent_profile_revisions.skill_version,
               agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed,
               agent_profile_revisions.category,
               agent_profile_revisions.visibility, agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profile_revisions
        join agent_profiles on agent_profiles.tenant_id = agent_profile_revisions.tenant_id
          and agent_profiles.agent_id = agent_profile_revisions.agent_id
        join agents on agents.id = agent_profile_revisions.agent_id
          and agents.tenant_id = agent_profile_revisions.tenant_id
        where agent_profile_revisions.tenant_id = %s
          and agents.agent_type = 'profile'
          and agents.status = 'active'
          {status_filter}
        order by agent_profile_revisions.agent_id, agent_profile_revisions.revision desc
        """,
        params,
    )
    return [dict(row) for row in await cursor.fetchall()]


async def record_agent_profile_draft(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    revision: int,
) -> dict[str, Any] | None:
    """Advance aggregate draft history without replacing an already live publication."""

    cursor = await conn.execute(
        """
        insert into agent_profiles(tenant_id, agent_id, lifecycle_status, latest_revision)
        values (%s, %s, 'draft', %s)
        on conflict (tenant_id, agent_id) do update
        set latest_revision = excluded.latest_revision,
            lifecycle_status = case
              when agent_profiles.lifecycle_status = 'withdrawn' then 'withdrawn'
              when agent_profiles.published_revision is null then 'draft'
              else 'published'
            end,
            updated_at = now()
        returning published_revision
        """,
        (tenant_id, agent_id, revision),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def record_agent_profile_publication(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    revision: int,
    content_hash: str,
) -> None:
    """Move the sole current-publication pointer after a locked immutable append."""

    cursor = await conn.execute(
        """
        update agent_profiles
        set lifecycle_status = 'published', latest_revision = %s,
            published_revision = %s, published_hash = %s,
            published_status = 'published', updated_at = now()
        where tenant_id = %s and agent_id = %s
        returning agent_id
        """,
        (revision, revision, content_hash, tenant_id, agent_id),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("agent_profile_aggregate_missing")
    await conn.execute(
        """
        update agent_profile_revisions
        set status = case
          when revision = %s and visibility = 'tenant' then 'published'
          else 'draft'
        end
        where tenant_id = %s and agent_id = %s and revision_status = 'published'
        """,
        (revision, tenant_id, agent_id),
    )


async def record_agent_profile_withdrawal(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    revision: int,
) -> None:
    """Remove admission authority while retaining all historical revision rows."""

    cursor = await conn.execute(
        """
        update agent_profiles
        set lifecycle_status = 'withdrawn', latest_revision = %s,
            published_revision = null, published_hash = null,
            published_status = null, updated_at = now()
        where tenant_id = %s and agent_id = %s and lifecycle_status = 'published'
        returning agent_id
        """,
        (revision, tenant_id, agent_id),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("agent_profile_revision_stale")
    await conn.execute(
        """
        update agent_profile_revisions
        set status = 'draft'
        where tenant_id = %s and agent_id = %s and revision_status = 'published'
        """,
        (tenant_id, agent_id),
    )


async def get_agent_profile_aggregate(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Load one authoritative profile aggregate in its tenant scope."""

    cursor = await conn.execute(
        f"""
        select tenant_id, agent_id, lifecycle_status, latest_revision, published_revision,
               published_hash, published_status, created_at, updated_at
        from agent_profiles
        where tenant_id = %s and agent_id = %s
        {"for update" if for_update else ""}
        """,
        (tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def get_current_published_agent_profile(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    expected_revision: int | None = None,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Read the one aggregate-selected publication, never a superseded historical row."""

    expected_filter = "and agent_profiles.published_revision = %s" if expected_revision is not None else ""
    params: list[Any] = [tenant_id, agent_id]
    if expected_revision is not None:
        params.append(expected_revision)
    cursor = await conn.execute(
        f"""
        select agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision, agent_profile_revisions.revision_status as status,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message, agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary, agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions, agent_profile_revisions.model_id,
               agent_profile_revisions.skill_id, agent_profile_revisions.skill_version,
               agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               {_KNOWLEDGE_FRESHNESS_SELECT},
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed,
               agent_profile_revisions.category,
               agent_profile_revisions.visibility, agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profiles
        join agent_profile_revisions
          on agent_profile_revisions.tenant_id = agent_profiles.tenant_id
         and agent_profile_revisions.agent_id = agent_profiles.agent_id
         and agent_profile_revisions.revision = agent_profiles.published_revision
         and agent_profile_revisions.content_hash = agent_profiles.published_hash
         and agent_profile_revisions.revision_status = agent_profiles.published_status
        join agents on agents.id = agent_profiles.agent_id
          and agents.tenant_id = agent_profiles.tenant_id
        where agent_profiles.tenant_id = %s
          and agent_profiles.agent_id = %s
          and agent_profiles.lifecycle_status = 'published'
          and agent_profiles.published_status = 'published'
          and agents.agent_type = 'profile'
          and agents.status = 'active'
          {expected_filter}
        {"for update of agent_profiles" if for_update else ""}
        """,
        tuple(params),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def get_bound_published_agent_profile(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    revision: int,
    content_hash: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Load a session-pinned publication while requiring the Agent to remain live."""

    cursor = await conn.execute(
        f"""
        select agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision, agent_profile_revisions.revision_status as status,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message, agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary, agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions, agent_profile_revisions.model_id,
               agent_profile_revisions.skill_id, agent_profile_revisions.skill_version,
               agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed,
               agent_profile_revisions.category,
               agent_profile_revisions.visibility, agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               current_revision.visibility as current_visibility,
               current_revision.allowed_department_ids as current_allowed_department_ids,
               current_revision.allowed_roles as current_allowed_roles,
               current_revision.allowed_user_ids as current_allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profiles
        join agent_profile_revisions
         on agent_profile_revisions.tenant_id = agent_profiles.tenant_id
         and agent_profile_revisions.agent_id = agent_profiles.agent_id
        join agent_profile_revisions current_revision
          on current_revision.tenant_id = agent_profiles.tenant_id
         and current_revision.agent_id = agent_profiles.agent_id
         and current_revision.revision = agent_profiles.published_revision
         and current_revision.content_hash = agent_profiles.published_hash
         and current_revision.revision_status = agent_profiles.published_status
        join agents on agents.id = agent_profiles.agent_id
          and agents.tenant_id = agent_profiles.tenant_id
        where agent_profiles.tenant_id = %s
          and agent_profiles.agent_id = %s
          and agent_profiles.lifecycle_status = 'published'
          and agent_profiles.published_status = 'published'
          and agent_profile_revisions.revision = %s
          and agent_profile_revisions.content_hash = %s
          and agent_profile_revisions.revision_status = 'published'
          and agents.agent_type = 'profile'
          and agents.status = 'active'
        {"for update of agent_profiles" if for_update else ""}
        """,
        (tenant_id, agent_id, revision, content_hash),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def list_current_published_agent_profiles(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    query: str | None = None,
    category: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List aggregate-selected published profiles with bounded server-side search/filtering."""

    query_filter = ""
    category_filter = ""
    params: list[Any] = [tenant_id]
    if query:
        query_filter = """
        and (
          normalize(agent_profile_revisions.name, NFKC) ilike %s escape E'\\\\'
          or normalize(agent_profile_revisions.description, NFKC) ilike %s escape E'\\\\'
          or normalize(agent_profile_revisions.capability_summary, NFKC) ilike %s escape E'\\\\'
          or exists (
            select 1
            from jsonb_array_elements_text(
              case
                when jsonb_typeof(agent_profile_revisions.recommended_tasks) = 'array'
                  then agent_profile_revisions.recommended_tasks
                else '[]'::jsonb
              end
            ) as recommended_task(value)
            where normalize(recommended_task.value, NFKC) ilike %s escape E'\\\\'
          )
        )
        """
        escaped_query = (
            query.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_query}%"
        params.extend([pattern, pattern, pattern, pattern])
    if category:
        category_filter = "and agent_profile_revisions.category = %s"
        params.append(category)
    params.append(max(1, min(int(limit), 200)))
    cursor = await conn.execute(
        f"""
        select agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision, agent_profile_revisions.revision_status as status,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message, agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary, agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions, agent_profile_revisions.model_id,
               agent_profile_revisions.skill_id, agent_profile_revisions.skill_version,
               agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               {_KNOWLEDGE_FRESHNESS_SELECT},
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed,
               agent_profile_revisions.category,
               agent_profile_revisions.visibility, agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profiles
        join agent_profile_revisions
          on agent_profile_revisions.tenant_id = agent_profiles.tenant_id
         and agent_profile_revisions.agent_id = agent_profiles.agent_id
         and agent_profile_revisions.revision = agent_profiles.published_revision
         and agent_profile_revisions.content_hash = agent_profiles.published_hash
         and agent_profile_revisions.revision_status = agent_profiles.published_status
        join agents on agents.id = agent_profiles.agent_id
          and agents.tenant_id = agent_profiles.tenant_id
        where agent_profiles.tenant_id = %s
          and agent_profiles.lifecycle_status = 'published'
          and agent_profiles.published_status = 'published'
          and agents.agent_type = 'profile'
          and agents.status = 'active'
          {query_filter}
          {category_filter}
        order by agent_profile_revisions.name asc, agent_profile_revisions.agent_id asc
        limit %s
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def list_agent_profile_revision_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Return all immutable revisions for a tenant-scoped profile identity."""

    cursor = await conn.execute(
        """
        select agent_profile_revisions.tenant_id, agent_profile_revisions.agent_id,
               agent_profile_revisions.revision,
               agent_profile_revisions.revision_status as status,
               agent_profiles.published_revision,
               agent_profile_revisions.name, agent_profile_revisions.description,
               agent_profile_revisions.welcome_message,
               agent_profile_revisions.starter_prompts,
               agent_profile_revisions.capability_summary,
               agent_profile_revisions.recommended_tasks,
               agent_profile_revisions.supported_input_types,
               agent_profile_revisions.supported_file_types as legacy_supported_file_types,
               agent_profile_revisions.expected_outputs,
               agent_profile_revisions.permissions_and_data_access_notice,
               agent_profile_revisions.instructions,
               agent_profile_revisions.model_id, agent_profile_revisions.skill_id,
               agent_profile_revisions.skill_version, agent_profile_revisions.skill_set,
               agent_profile_revisions.mcp_tool_ids,
               agent_profile_revisions.knowledge_source_ids,
               agent_profile_revisions.retrieval_profile_id,
               agent_profile_revisions.knowledge_bindings,
               agent_profile_revisions.content_hash,
               agent_profile_revisions.avatar_ref, agent_profile_revisions.avatar_asset_id,
               agent_profile_revisions.avatar_seed, agent_profile_revisions.category,
               agent_profile_revisions.visibility,
               agent_profile_revisions.allowed_department_ids,
               agent_profile_revisions.allowed_roles, agent_profile_revisions.allowed_user_ids,
               agent_profile_revisions.created_at, agent_profile_revisions.published_at
        from agent_profile_revisions
        join agent_profiles on agent_profiles.tenant_id = agent_profile_revisions.tenant_id
          and agent_profiles.agent_id = agent_profile_revisions.agent_id
        where agent_profile_revisions.tenant_id = %s
          and agent_profile_revisions.agent_id = %s
        order by agent_profile_revisions.revision desc
        """,
        (tenant_id, agent_id),
    )
    return [dict(row) for row in await cursor.fetchall()]

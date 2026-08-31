from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def get_agent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, name, agent_type, default_skill_id, status, created_at
        from agents
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_tenant_profile_validation_agent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> str | None:
    """Find a same-tenant active agent for capability-only unsaved draft validation."""

    cursor = await conn.execute(
        """
        select id
        from agents
        where tenant_id = %s and status = 'active'
        order by case when id = 'general-agent' then 0 else 1 end, id asc
        limit 1
        """,
        (tenant_id,),
    )
    row = await cursor.fetchone()
    return str(row["id"]) if row is not None else None


async def list_lambchat_agents(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          agents.id,
          agents.name,
          agents.description,
          agents.agent_type,
          agents.default_skill_id,
          agents.status,
          coalesce(skill_release_policies.current_version, skills.version) as skill_version,
          coalesce(skill_versions.status, 'active') as skill_version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          previous_skill_versions.status as release_policy_previous_version_status,
          skills.input_modes,
          skills.output_modes
        from agents
        left join skills on skills.id = agents.default_skill_id
        left join skill_release_policies
          on skill_release_policies.tenant_id = agents.tenant_id
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join skill_versions as previous_skill_versions
          on previous_skill_versions.skill_id = skills.id
         and previous_skill_versions.version = skill_release_policies.previous_version
        where agents.tenant_id = %s
          and agents.id in ('general-agent', 'baoyu-translate', 'qa-word-review')
          and agents.status = 'active'
          and (agents.default_skill_id is null or skills.status = 'active')
        order by case agents.id
          when 'general-agent' then 1
          when 'baoyu-translate' then 2
          when 'qa-word-review' then 3
          else 99
        end, agents.id asc
        """,
        (tenant_id,),
    )
    return list(await cursor.fetchall())

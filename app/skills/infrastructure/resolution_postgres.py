"""Resolution persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.skills.lifecycle import is_user_runnable_status
from psycopg import AsyncConnection
from typing import Any


DEFAULT_RUN_EXECUTOR_TYPES = {"claude-agent-worker"}


async def _resolve_executable_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    require_default_skill: bool,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          agents.id as agent_id,
          agents.status as agent_status,
          agents.default_skill_id,
          skills.id as skill_id,
          skills.name as skill_display_label,
          skills.status as skill_status,
          coalesce(skill_release_policies.current_version, skills.version) as skill_version,
          coalesce(skill_versions.content_hash, coalesce(skill_release_policies.current_version, skills.version)) as skill_content_hash,
          coalesce(skill_versions.status, 'active') as skill_version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          skills.executor_type,
          mcp_tools.id as backing_mcp_tool_id,
          mcp_tools.status as mcp_tool_status,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          mcp_tools.server_id as server_id,
          skills.input_modes
        from agents
        join skills on skills.id = %s
        left join skill_release_policies
          on skill_release_policies.tenant_id = agents.tenant_id
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join mcp_tools on mcp_tools.id = skills.id
        left join tool_policies
          on tool_policies.tenant_id = agents.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where agents.tenant_id = %s and agents.id = %s
        """,
        (skill_id, tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("agent_or_skill_not_found")
    row = dict(row)
    if row["agent_status"] != "active":
        raise RepositoryConflictError("agent_inactive")
    if row["skill_status"] != "active":
        raise RepositoryConflictError("skill_inactive")
    if not is_user_runnable_status(row.get("skill_version_status", "active")):
        raise RepositoryConflictError("skill_version_not_released")
    skill_version = str(row.get("skill_version") or "")
    skill_content_hash = str(row.get("skill_content_hash") or skill_version)
    if not skill_version or skill_content_hash != skill_version:
        raise RepositoryConflictError("skill_version_not_materializable")
    if row["executor_type"] not in DEFAULT_RUN_EXECUTOR_TYPES:
        raise RepositoryConflictError("executor_type_not_allowed")
    if require_default_skill and row["default_skill_id"] != skill_id:
        raise RepositoryConflictError("agent_skill_mismatch")
    return row


async def resolve_agent_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
) -> dict[str, Any]:
    """Resolve an active fixed capability Skill bound as the Agent default."""

    return await _resolve_executable_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        require_default_skill=True,
    )


async def resolve_selected_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
) -> dict[str, Any]:
    """Resolve an active ordinary-user selected Skill without default binding."""

    return await _resolve_executable_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        require_default_skill=False,
    )

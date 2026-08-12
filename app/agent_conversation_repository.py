from datetime import datetime
from typing import Any

from psycopg import AsyncConnection


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
               profile.supported_file_types as agent_profile_supported_file_types,
               profile.expected_outputs as agent_profile_expected_outputs,
               profile.permissions_and_data_access_notice as agent_profile_permissions_and_data_access_notice,
               profile.avatar_ref as agent_profile_avatar_ref,
               profile.category as agent_profile_category,
               profile.published_at as agent_profile_published_at
        from sessions
        join agent_profile_revisions profile
          on profile.tenant_id = sessions.tenant_id
         and profile.agent_id = sessions.agent_id
         and profile.revision = sessions.admitted_agent_profile_revision
         and profile.content_hash = sessions.admitted_agent_profile_hash
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

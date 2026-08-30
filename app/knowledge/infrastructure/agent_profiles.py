"""Knowledge-source authorization used by the Agent Profile authority."""

from __future__ import annotations

from typing import Any

from app.knowledge.domain import (
    DEFAULT_RETRIEVAL_PROFILE_ID,
    DEFAULT_RETRIEVAL_PROFILE_REVISION,
    KnowledgeAcl,
)
from app.platform.postgres.errors import (
    RepositoryAuthorizationError,
    RepositoryConflictError,
)


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if isinstance(item, str) and item.strip()}


def _source_acl_allows(
    row: dict[str, Any],
    *,
    principal_user_id: str,
    principal_department_id: str,
    principal_roles: list[str],
    is_admin: bool,
) -> bool:
    return KnowledgeAcl.create(
        visibility=str(row.get("visibility") or ""),
        department_ids=_string_set(row.get("allowed_department_ids")),
        roles=_string_set(row.get("allowed_roles")),
        user_ids=_string_set(row.get("allowed_user_ids")),
    ).allows(
        user_id=principal_user_id,
        department_id=principal_department_id,
        roles=principal_roles,
        is_admin=is_admin,
    )


async def authorize_agent_profile_knowledge_sources(
    conn: Any,
    *,
    tenant_id: str,
    source_ids: list[str],
    retrieval_profile_id: str | None,
    principal_user_id: str,
    principal_department_id: str,
    principal_roles: list[str],
    is_admin: bool,
    agent_visibility: str,
    agent_department_ids: list[str],
    agent_roles: list[str],
    agent_user_ids: list[str],
) -> tuple[dict[str, Any], ...]:
    """Resolve exact active logical sources and fail closed on ACL/readiness drift."""

    if not source_ids:
        if retrieval_profile_id is not None:
            raise RepositoryConflictError("agent_profile_knowledge_selection_invalid")
        return ()
    if retrieval_profile_id != DEFAULT_RETRIEVAL_PROFILE_ID:
        raise RepositoryConflictError("agent_profile_retrieval_profile_unavailable")
    if len(source_ids) > 8 or len(source_ids) != len(set(source_ids)):
        raise RepositoryConflictError("agent_profile_knowledge_selection_invalid")

    cursor = await conn.execute(
        """
        select sources.id, sources.authorization_version, sources.status,
               connections.status as connection_status, acl.visibility,
               coalesce(array(
                 select department_id from knowledge_source_acl_departments departments
                 where departments.tenant_id = sources.tenant_id
                   and departments.source_id = sources.id
                   and departments.authorization_version = sources.authorization_version
               ), array[]::text[]) as allowed_department_ids,
               coalesce(array(
                 select role_id from knowledge_source_acl_roles roles
                 where roles.tenant_id = sources.tenant_id
                   and roles.source_id = sources.id
                   and roles.authorization_version = sources.authorization_version
               ), array[]::text[]) as allowed_roles,
               coalesce(array(
                 select user_id from knowledge_source_acl_users users_acl
                 where users_acl.tenant_id = sources.tenant_id
                   and users_acl.source_id = sources.id
                   and users_acl.authorization_version = sources.authorization_version
               ), array[]::text[]) as allowed_user_ids
        from knowledge_sources sources
        join knowledge_connections connections
          on connections.tenant_id = sources.tenant_id
         and connections.id = sources.connection_id
        join knowledge_source_acl_versions acl
          on acl.tenant_id = sources.tenant_id
         and acl.source_id = sources.id
         and acl.authorization_version = sources.authorization_version
        where sources.tenant_id = %s and sources.id = any(%s)
        """,
        (tenant_id, source_ids),
    )
    rows = {str(row["id"]): dict(row) for row in await cursor.fetchall()}
    if set(rows) != set(source_ids):
        raise RepositoryConflictError("agent_profile_knowledge_source_unavailable")

    bindings: list[dict[str, Any]] = []
    agent_acl = KnowledgeAcl.create(
        visibility=agent_visibility,
        department_ids=agent_department_ids,
        roles=agent_roles,
        user_ids=agent_user_ids,
    )
    for ordinal, source_id in enumerate(source_ids):
        row = rows[source_id]
        if str(row.get("status") or "") != "active" or str(
            row.get("connection_status") or ""
        ) != "active":
            raise RepositoryConflictError("agent_profile_knowledge_source_unavailable")
        if not _source_acl_allows(
            row,
            principal_user_id=principal_user_id,
            principal_department_id=principal_department_id,
            principal_roles=principal_roles,
            is_admin=is_admin,
        ):
            raise RepositoryAuthorizationError("agent_profile_knowledge_source_not_authorized")
        source_acl = KnowledgeAcl.create(
            visibility=str(row.get("visibility") or ""),
            department_ids=_string_set(row.get("allowed_department_ids")),
            roles=_string_set(row.get("allowed_roles")),
            user_ids=_string_set(row.get("allowed_user_ids")),
        )
        if not source_acl.contains(agent_acl):
            raise RepositoryConflictError("agent_profile_knowledge_scope_incompatible")
        bindings.append(
            {
                "source_id": source_id,
                "source_authorization_version": int(row["authorization_version"]),
                "ordinal": ordinal,
                "required": True,
                "retrieval_profile_id": DEFAULT_RETRIEVAL_PROFILE_ID,
                "retrieval_profile_revision": DEFAULT_RETRIEVAL_PROFILE_REVISION,
            }
        )
    return tuple(bindings)


class PostgresAgentProfileKnowledgeAuthorizationRepository:
    """PostgreSQL adapter for immutable Agent Knowledge binding resolution."""

    async def authorize(self, conn: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
        return await authorize_agent_profile_knowledge_sources(conn, **kwargs)

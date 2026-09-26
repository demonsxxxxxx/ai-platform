"""Catalog persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.control_plane_contracts import LEGACY_SYNTHETIC_CHAT_SKILL_ID
from app.identity.infrastructure.capability_distributions_postgres import ensure_tenant_capability_distribution_backfill
from app.identity.infrastructure.capability_distributions_postgres import get_capability_distribution_row
from app.identity.infrastructure.capability_distributions_postgres import is_capability_distribution_archived
from app.identity.infrastructure.capability_distributions_postgres import set_capability_distribution_status
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.skills.dependencies import PUBLIC_WORKBENCH_SKILL_IDS
from app.skills.dependencies import is_workbench_skill_public
from app.skills.infrastructure.versions_postgres import _json_dict
from app.skills.infrastructure.versions_postgres import _json_list
from app.skills.infrastructure.versions_postgres import _principal_skill_release_decision
from app.skills.lifecycle import is_user_runnable_status
from psycopg import AsyncConnection
from typing import Any


async def get_skill(conn: AsyncConnection, *, skill_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id as skill_id, version, status
        from skills
        where id = %s
        """,
        (skill_id,),
    )
    return await cursor.fetchone()


async def list_skill_ids(conn: AsyncConnection) -> list[str]:
    cursor = await conn.execute(
        """
        select id
        from skills
        order by id asc
        """,
        (),
    )
    return [str(row["id"]) for row in list(await cursor.fetchall())]


async def _upsert_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    await set_capability_distribution_status(
        conn,
        tenant_id=tenant_id,
        capability_kind="skill",
        capability_id=skill_id,
        status=status,
        updated_by=None,
    )
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          skills.status as lifecycle_status,
          tenant_capability_distributions.status,
          tenant_capability_distributions.visible_to_user
        from skills
        join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("skill_not_found")
    return row


async def set_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    if not is_workbench_skill_public(skill_id):
        raise RepositoryNotFoundError("workbench_skill_not_found")
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )


async def set_uploaded_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    """Enable a governed uploaded Skill for one tenant after catalog creation."""
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )


async def list_public_skill_catalog(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = False,
    rollout_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return tenant-visible public Skills/Marketplace catalog rows."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          coalesce(skill_release_policies.current_version, skills.version) as version,
          skill_versions.content_hash as expected_version,
          coalesce(skill_versions.description, skills.description) as description,
          skills.input_modes,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.department_ids, array[]::text[]) as department_ids,
          coalesce(tenant_capability_distributions.allowed_roles, '[]'::jsonb) as allowed_roles,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json,
          coalesce(skill_versions.status, 'active') as version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          previous_skill_versions.status as release_policy_previous_version_status,
          previous_skill_versions.content_hash as release_policy_previous_content_hash,
          previous_skill_versions.description as release_policy_previous_description,
          previous_skill_versions.source_json as release_policy_previous_source_json,
          previous_skill_versions.dependency_ids as release_policy_previous_dependency_ids,
          previous_skill_versions.created_by as release_policy_previous_created_by,
          previous_skill_versions.created_at as release_policy_previous_created_at,
          coalesce(skill_versions.source_json, '{}'::jsonb) as source_json,
          coalesce(skill_versions.dependency_ids, '[]'::jsonb) as dependency_ids,
          skill_versions.created_by,
          skill_versions.created_at,
          skills.created_at as updated_at
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        left join skill_release_policies
          on skill_release_policies.tenant_id = %s
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join skill_versions as previous_skill_versions
          on previous_skill_versions.skill_id = skills.id
         and previous_skill_versions.version = skill_release_policies.previous_version
        where (skills.id = any(%s) or tenant_capability_distributions.capability_id is not null)
          and skills.id <> %s
          and skills.status = 'active'
        order by skills.name asc, skills.id asc
        """,
        (
            tenant_id,
            tenant_id,
            sorted(PUBLIC_WORKBENCH_SKILL_IDS),
            LEGACY_SYNTHETIC_CHAT_SKILL_ID,
        ),
    )
    rows = []
    for row in list(await cursor.fetchall()):
        projected = dict(row)
        if is_capability_distribution_archived({"metadata_json": projected.get("distribution_metadata_json")}):
            continue
        projected.pop("distribution_metadata_json", None)
        if rollout_key is not None:
            release_decision = _principal_skill_release_decision(
                projected,
                tenant_id=tenant_id,
                skill_id=str(projected.get("skill_id") or ""),
                rollout_key=rollout_key,
                fallback_version_field="version",
            )
            if release_decision.selected_track == "previous":
                projected["version"] = release_decision.selected_version
                projected["expected_version"] = projected.get("release_policy_previous_content_hash")
                projected["version_status"] = projected.get("release_policy_previous_version_status")
                projected["description"] = projected.get("release_policy_previous_description") or ""
                projected["source_json"] = projected.get("release_policy_previous_source_json") or {}
                projected["dependency_ids"] = projected.get("release_policy_previous_dependency_ids") or []
                projected["created_by"] = projected.get("release_policy_previous_created_by")
                projected["created_at"] = projected.get("release_policy_previous_created_at")
        for field in (
            "release_policy_version",
            "release_policy_previous_version",
            "release_policy_rollout_percent",
            "release_policy_previous_version_status",
            "release_policy_previous_content_hash",
            "release_policy_previous_description",
            "release_policy_previous_source_json",
            "release_policy_previous_dependency_ids",
            "release_policy_previous_created_by",
            "release_policy_previous_created_at",
        ):
            projected.pop(field, None)
        selected_version = str(projected.get("version") or "")
        expected_version = str(projected.get("expected_version") or "")
        if not selected_version or expected_version != selected_version:
            continue
        projected["expected_version"] = expected_version
        if not include_disabled and not is_user_runnable_status(projected.get("version_status")):
            continue
        projected["source"] = _json_dict(projected.pop("source_json", {}))
        projected["dependency_ids"] = _json_list(projected.get("dependency_ids"))
        projected["input_modes"] = _json_list(projected.get("input_modes"))
        rows.append(projected)
    return rows


async def set_public_skill_enabled(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    """Set tenant availability for a user-facing public skill."""

    if status not in {"active", "disabled"}:
        raise RepositoryConflictError("invalid_skill_status")
    if not is_workbench_skill_public(skill_id):
        distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="skill",
            capability_id=skill_id,
        )
        if distribution is None:
            raise RepositoryNotFoundError("workbench_skill_not_found")
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )

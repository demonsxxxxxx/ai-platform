"""Versions persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.identity.infrastructure.capability_distributions_postgres import ensure_tenant_capability_distribution_backfill
from app.identity.infrastructure.capability_distributions_postgres import is_capability_distribution_archived
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from app.skills.infrastructure.postgres import get_skill_version
from app.skills.infrastructure.run_snapshots_postgres import _sanitize_skill_snapshot_source
from app.skills.release_policy import resolve_rollout_skill_decision
from psycopg import AsyncConnection
from typing import Any
import json


def _principal_skill_release_decision(
    row: dict[str, Any],
    *,
    tenant_id: str,
    skill_id: str,
    rollout_key: str,
    fallback_version_field: str,
):
    return resolve_rollout_skill_decision(
        {
            "skill_version": row.get(fallback_version_field),
            "release_policy_version": row.get("release_policy_version"),
            "release_policy_previous_version": row.get("release_policy_previous_version"),
            "release_policy_rollout_percent": row.get("release_policy_rollout_percent"),
        },
        tenant_id=tenant_id,
        skill_id=skill_id,
        rollout_key=rollout_key,
    )


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _project_skill_version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": row["skill_id"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "description": row.get("description") or "",
        "source": _json_dict(row.get("source_json")),
        "dependency_ids": _json_list(row.get("dependency_ids")),
        "status": row.get("status") or "active",
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
    }


def _project_skill_release_policy(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "skill_id": row["skill_id"],
        "channel": row.get("channel") or "stable",
        "current_version": row["current_version"],
        "previous_version": row.get("previous_version"),
        "rollout_percent": int(row.get("rollout_percent") or 0),
        "status": row.get("status") or "active",
        "promoted_by": row.get("promoted_by"),
        "promoted_at": row.get("promoted_at"),
    }


async def upsert_skill_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    content_hash: str,
    description: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    status: str = "active",
    created_by: str | None = None,
) -> bool:
    cursor = await conn.execute(
        """
        insert into skill_versions(
          id, skill_id, version, content_hash, description, source_json,
          dependency_ids, status, created_by
        )
        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        on conflict (skill_id, version)
        do nothing
        returning skill_id
        """,
        (
            new_id("skv"),
            skill_id,
            version,
            content_hash,
            description,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            status,
            created_by,
        ),
    )
    return await cursor.fetchone() is not None


async def create_skill_catalog(
    conn: AsyncConnection,
    *,
    skill_id: str,
    name: str,
    version: str,
    description: str,
    input_modes: list[str],
    output_modes: list[str],
    executor_type: str,
    status: str = "active",
) -> None:
    cursor = await conn.execute(
        """
        insert into skills(
          id, name, version, description, input_modes, output_modes, executor_type, status
        )
        values (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        on conflict (id) do nothing
        returning id
        """,
        (
            skill_id,
            name,
            version,
            description,
            json.dumps(input_modes, ensure_ascii=False),
            json.dumps(output_modes, ensure_ascii=False),
            executor_type,
            status,
        ),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("skill_catalog_already_exists")


async def update_skill_catalog_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    description: str,
) -> None:
    await conn.execute(
        """
        update skills
        set version = %s,
            description = %s
        where id = %s
        """,
        (version, description, skill_id),
    )


async def backfill_builtin_skill_version_snapshot(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    description: str,
) -> None:
    serialized_source_json = dumps_json(source_json)
    serialized_dependency_ids = json.dumps(dependency_ids, ensure_ascii=False)
    await conn.execute(
        """
        update skill_versions
        set source_json = %s::jsonb,
            dependency_ids = %s::jsonb,
            description = %s
        where skill_id = %s
          and version = %s
          and source_json->>'kind' = 'builtin'
          and (
            not (source_json ? 'files')
            or source_json->'files' is distinct from (%s::jsonb->'files')
            or dependency_ids <> %s::jsonb
            or source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')
          )
        """,
        (
            serialized_source_json,
            serialized_dependency_ids,
            description,
            skill_id,
            version,
            serialized_source_json,
            serialized_dependency_ids,
            serialized_source_json,
        ),
    )


async def update_skill_version_status(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        update skill_versions
        set status = %s
        where skill_id = %s and version = %s
        returning
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        """,
        (status, skill_id, version),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("skill_version_not_found")
    return _project_skill_version(row)


async def get_effective_skill_version_for_policy(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
) -> dict[str, Any] | None:
    return await get_skill_version(conn, skill_id=skill_id, version=version)


async def list_skill_versions(conn: AsyncConnection, *, skill_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        from skill_versions
        where skill_id = %s
        order by created_at desc, version desc
        """,
        (skill_id,),
    )
    return [_project_skill_version(row) for row in list(await cursor.fetchall())]


async def get_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skill_id,
          channel,
          current_version,
          previous_version,
          rollout_percent,
          status,
          promoted_by,
          promoted_at
        from skill_release_policies
        where tenant_id = %s and skill_id = %s and channel = %s and status = 'active'
        """,
        (tenant_id, skill_id, channel),
    )
    return _project_skill_release_policy(await cursor.fetchone())


async def set_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    version: str,
    previous_version: str | None,
    promoted_by: str | None,
    channel: str = "stable",
    rollout_percent: int = 100,
    status: str = "active",
) -> None:
    await conn.execute(
        """
        insert into skill_release_policies(
          id, tenant_id, skill_id, channel, current_version, previous_version,
          rollout_percent, status, promoted_by, promoted_at, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        on conflict (tenant_id, skill_id, channel)
        do update set
          current_version = excluded.current_version,
          previous_version = excluded.previous_version,
          rollout_percent = excluded.rollout_percent,
          status = excluded.status,
          promoted_by = excluded.promoted_by,
          promoted_at = now(),
          updated_at = now()
        """,
        (
            new_id("skr"),
            tenant_id,
            skill_id,
            channel,
            version,
            previous_version,
            rollout_percent,
            status,
            promoted_by,
        ),
    )


async def diff_skill_versions(
    conn: AsyncConnection,
    *,
    skill_id: str,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    source = await get_skill_version(conn, skill_id=skill_id, version=from_version)
    target = await get_skill_version(conn, skill_id=skill_id, version=to_version)
    if source is None or target is None:
        raise RepositoryNotFoundError("skill_version_not_found")
    source_dependencies = set(_json_list(source.get("dependency_ids")))
    target_dependencies = set(_json_list(target.get("dependency_ids")))
    return {
        "skill_id": skill_id,
        "from_version": from_version,
        "to_version": to_version,
        "content_hash_changed": source.get("content_hash") != target.get("content_hash"),
        "description_changed": source.get("description") != target.get("description"),
        "source_changed": source.get("source") != target.get("source"),
        "dependency_added": sorted(target_dependencies - source_dependencies),
        "dependency_removed": sorted(source_dependencies - target_dependencies),
    }


async def list_admin_skill_summaries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Return lifecycle summaries without exposing package or runtime-private source data."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.description,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as distribution_status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json,
          latest_version.version as latest_version,
          latest_version.status as latest_version_status,
          skill_release_policies.current_version,
          skill_release_policies.rollout_percent
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        left join lateral (
          select skill_versions.version, skill_versions.status
          from skill_versions
          where skill_versions.skill_id = skills.id
          order by skill_versions.created_at desc, skill_versions.version desc
          limit 1
        ) as latest_version on true
        left join skill_release_policies
          on skill_release_policies.tenant_id = %s
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        order by skills.name asc, skills.id asc
        """,
        (tenant_id, tenant_id),
    )
    rows = []
    for raw_row in list(await cursor.fetchall()):
        row = dict(raw_row)
        distribution_metadata_json = row.pop("distribution_metadata_json", {})
        if is_capability_distribution_archived(
            {"metadata_json": distribution_metadata_json}
        ):
            continue
        rows.append(row)
    return rows


async def get_admin_skill_detail(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
) -> dict[str, Any] | None:
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
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
          coalesce(tenant_capability_distributions.status, 'disabled') as status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    raw_skill = await cursor.fetchone()
    if raw_skill is None:
        return None
    skill = dict(raw_skill)
    distribution_metadata_json = skill.pop("distribution_metadata_json", {})
    if is_capability_distribution_archived(
        {"metadata_json": distribution_metadata_json}
    ):
        return None

    versions = await list_skill_versions(conn, skill_id=skill_id)
    release_policy = await get_skill_release_policy(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
    )
    snapshots_cursor = await conn.execute(
        """
        select
          run_id,
          skill_id,
          skill_version,
          content_hash,
          source_json,
          dependency_ids,
          allowed,
          staged,
          used,
          used_skills_source,
          inferred_used,
          created_at
        from run_skill_snapshots
        where tenant_id = %s and skill_id = %s
        order by created_at desc
        limit 20
        """,
        (tenant_id, skill_id),
    )
    snapshots = []
    for row in list(await snapshots_cursor.fetchall()):
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        snapshot = {
            "run_id": row["run_id"],
            "skill_id": row["skill_id"],
            "source": _sanitize_skill_snapshot_source(row.get("source_json")),
            "dependency_ids": _json_list(row.get("dependency_ids")),
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row.get("created_at"),
        }
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = used_skills_source
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)

    return {
        "skill": skill,
        "release_policy": release_policy,
        "versions": versions,
        "recent_snapshots": snapshots,
    }

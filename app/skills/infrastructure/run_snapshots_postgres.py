"""Run snapshots persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from app.platform.public_payload import sanitize_public_payload
from app.platform.public_payload import sanitize_public_text
from app.skills.infrastructure.postgres import run_skill_snapshot_source_json
from app.skills.pinning import SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V1
from app.skills.pinning import SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2
from app.skills.pinning import SkillVersionMaterializationError
from app.skills.pinning import build_skill_manifest_ref
from app.skills.pinning import build_skill_manifest_refs
from app.skills.pinning import skill_manifest_materialization_sha256
from app.skills.pinning import validate_skill_manifest_refs
from psycopg import AsyncConnection
from typing import Any
import json


async def upsert_run_skill_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_id: str,
    skill_version: str,
    content_hash: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    allowed: bool,
    staged: bool,
    used: bool,
    used_skills_source: str = "",
    inferred_used: bool = False,
) -> None:
    cursor = await conn.execute(
        """
        insert into run_skill_snapshots(
          id, tenant_id, run_id, skill_id, skill_version, content_hash,
          source_json, dependency_ids, allowed, staged, used, used_skills_source, inferred_used
        )
        values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
        on conflict (tenant_id, run_id, skill_id)
        do update set
          allowed = run_skill_snapshots.allowed or excluded.allowed,
          staged = run_skill_snapshots.staged or excluded.staged,
          used = run_skill_snapshots.used or excluded.used,
          used_skills_source = case
            when excluded.used then excluded.used_skills_source
            when run_skill_snapshots.used then run_skill_snapshots.used_skills_source
            when excluded.used_skills_source <> '' then excluded.used_skills_source
            else run_skill_snapshots.used_skills_source
          end,
          inferred_used = case
            when run_skill_snapshots.used or excluded.used then false
            else run_skill_snapshots.inferred_used or excluded.inferred_used
          end
        where run_skill_snapshots.skill_version = excluded.skill_version
          and run_skill_snapshots.content_hash = excluded.content_hash
          and run_skill_snapshots.source_json = excluded.source_json
          and run_skill_snapshots.dependency_ids = excluded.dependency_ids
        returning id
        """,
        (
            new_id("rss"),
            tenant_id,
            run_id,
            skill_id,
            skill_version,
            content_hash,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            allowed,
            staged,
            used,
            used_skills_source,
            inferred_used,
        ),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")


def pin_primary_skill_mcp_tool_ids(
    skill_manifests: list[dict[str, Any]],
    *,
    skill_id: str,
    mcp_tool_ids: list[str],
) -> list[dict[str, Any]]:
    """Attach the authorized MCP execution set to the primary immutable Skill pin."""

    normalized_tool_ids = list(dict.fromkeys(str(item) for item in mcp_tool_ids if str(item)))
    pinned: list[dict[str, Any]] = []
    primary_found = False
    for manifest in skill_manifests:
        item = dict(manifest)
        if str(item.get("skill_id") or "") == skill_id:
            item["mcp_tool_ids"] = normalized_tool_ids
            primary_found = True
        pinned.append(item)
    if not primary_found:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
    return pinned


async def insert_run_skill_snapshots_at_creation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifests: list[dict[str, Any]],
    release_decision: dict[str, Any],
) -> None:
    """Insert exact run Skill provenance before any execution-side mutation."""

    for manifest in skill_manifests:
        skill_id = str(manifest.get("skill_id") or "")
        skill_version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        dependency_ids = manifest.get("dependency_ids")
        if (
            not skill_id
            or not skill_version
            or skill_version != content_hash
            or not isinstance(dependency_ids, list)
            or any(not isinstance(item, str) or not item for item in dependency_ids)
        ):
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        cursor = await conn.execute(
            """
            insert into run_skill_snapshots(
              id, tenant_id, run_id, skill_id, skill_version, content_hash,
              source_json, dependency_ids, allowed, staged, used, used_skills_source, inferred_used
            )
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, true, false, false, '', false)
            on conflict (tenant_id, run_id, skill_id) do nothing
            returning id
            """,
            (
                new_id("rss"),
                tenant_id,
                run_id,
                skill_id,
                skill_version,
                content_hash,
                dumps_json(run_skill_snapshot_source_json(manifest, release_decision=release_decision)),
                json.dumps(dependency_ids, ensure_ascii=False),
            ),
        )
        if await cursor.fetchone() is None:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        materialization_sha256 = skill_manifest_materialization_sha256(manifest)
        materialized = await conn.execute(
            """
            insert into run_skill_materializations(
              tenant_id, run_id, skill_id, materialization_sha256, manifest_json
            )
            values (%s, %s, %s, %s, %s::jsonb)
            on conflict (tenant_id, run_id, skill_id) do nothing
            returning skill_id
            """,
            (
                tenant_id,
                run_id,
                skill_id,
                materialization_sha256,
                dumps_json(manifest),
            ),
        )
        if await materialized.fetchone() is None:
            raise RepositoryConflictError("run_skill_materialization_identity_mismatch")


def skill_manifest_refs(skill_manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the only Skill package form permitted in run input and Redis."""

    try:
        return build_skill_manifest_refs(skill_manifests)
    except SkillVersionMaterializationError as exc:
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch") from exc


async def materialize_run_skill_manifests(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifest_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load exact private packages for bounded, digest-bound references."""

    try:
        exact_refs = validate_skill_manifest_refs(skill_manifest_refs)
    except SkillVersionMaterializationError:
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    if not exact_refs:
        return []
    cursor = await conn.execute(
        """
        select skill_id, materialization_sha256, manifest_json
        from run_skill_materializations
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    manifests_by_id: dict[str, dict[str, Any]] = {}
    verified_refs_by_id: dict[str, dict[str, Any]] = {}
    for row in await cursor.fetchall():
        manifest = row.get("manifest_json")
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except json.JSONDecodeError as exc:
                raise RepositoryConflictError(
                    "run_skill_materialization_identity_mismatch"
                ) from exc
        row_skill_id = str(row.get("skill_id") or "")
        try:
            verified_ref = (
                build_skill_manifest_ref(manifest)
                if isinstance(manifest, dict)
                else None
            )
        except SkillVersionMaterializationError as exc:
            raise RepositoryConflictError(
                "run_skill_materialization_identity_mismatch"
            ) from exc
        if (
            verified_ref is None
            or verified_ref["skill_id"] != row_skill_id
            or verified_ref["materialization_sha256"] != str(row.get("materialization_sha256") or "")
            or row_skill_id in manifests_by_id
        ):
            raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
        manifests_by_id[row_skill_id] = dict(manifest)
        verified_refs_by_id[row_skill_id] = verified_ref
    manifests = [
        manifests_by_id.get(str(ref.get("skill_id") or ""))
        for ref in exact_refs
    ]
    if any(manifest is None for manifest in manifests):
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    exact_manifests = [manifest for manifest in manifests if manifest is not None]
    if [verified_refs_by_id[ref["skill_id"]] for ref in exact_refs] != exact_refs:
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    return exact_manifests


async def list_run_skill_snapshots(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
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
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    rows = list(await cursor.fetchall())
    snapshots = []
    for row in rows:
        source = _sanitize_skill_snapshot_source(row.get("source_json"))
        dependency_ids = row.get("dependency_ids") if isinstance(row.get("dependency_ids"), list) else []
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = sanitize_public_text(used_skills_source)
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        snapshot = {
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "content_hash": row["content_hash"],
            "source": source,
            "dependency_ids": [str(item) for item in dependency_ids],
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row["created_at"],
        }
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)
    return snapshots


async def validate_run_skill_snapshots_for_dispatch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifests: list[dict[str, Any]],
    release_decision: dict[str, Any],
) -> None:
    """Require locked manifests to match immutable creation-time snapshot rows."""

    cursor = await conn.execute(
        """
        select skill_id, skill_version, content_hash, source_json, dependency_ids
        from run_skill_snapshots
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    expected: dict[str, dict[str, Any]] = {}
    for manifest in skill_manifests:
        skill_id = str(manifest.get("skill_id") or "")
        version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        dependency_ids = manifest.get("dependency_ids")
        if (
            not skill_id
            or skill_id in expected
            or not version
            or version != content_hash
            or not isinstance(dependency_ids, list)
        ):
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        expected[skill_id] = {
            "skill_version": version,
            "content_hash": content_hash,
            "source_json": run_skill_snapshot_source_json(manifest, release_decision=release_decision),
            "dependency_ids": dependency_ids,
        }
    if len(rows) != len(expected):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
    for row in rows:
        skill_id = str(row.get("skill_id") or "")
        locked = expected.get(skill_id)
        source_json = row.get("source_json")
        dependency_ids = row.get("dependency_ids")
        if isinstance(source_json, str):
            try:
                source_json = json.loads(source_json)
            except json.JSONDecodeError:
                source_json = None
        if isinstance(dependency_ids, str):
            try:
                dependency_ids = json.loads(dependency_ids)
            except json.JSONDecodeError:
                dependency_ids = None
        if locked is None or {
            "skill_version": str(row.get("skill_version") or ""),
            "content_hash": str(row.get("content_hash") or ""),
            "source_json": source_json,
            "dependency_ids": dependency_ids,
        } != locked:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")


def _sanitize_skill_snapshot_source(source_json: object) -> dict[str, Any]:
    source = sanitize_public_payload(source_json if isinstance(source_json, dict) else {})
    if not isinstance(source, dict):
        return {}
    source.pop("version", None)
    governance = source.get("snapshot_governance")
    if isinstance(governance, dict):
        governance_schema = governance.get("schema_version")
        if governance_schema == SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V1:
            legacy_boundary = governance.pop("does_not_close_b4_or_211", None)
            if isinstance(legacy_boundary, bool):
                governance["does_not_close_b4_or_deployed_runtime_acceptance"] = (
                    legacy_boundary
                )
        elif governance_schema == SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2:
            governance.pop("does_not_close_b4_or_211", None)
        manifest = governance.get("manifest")
        if isinstance(manifest, dict):
            manifest.pop("digest", None)
        release_lock = governance.get("release_lock")
        if isinstance(release_lock, dict):
            release_lock.pop("track", None)
            release_lock.pop("rollout", None)
    return source


def _sanitize_skill_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = _sanitize_skill_snapshot_source(snapshot.get("source"))
    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
    sanitized_usage: dict[str, Any] = {}
    for key, value in usage.items():
        if isinstance(value, str):
            sanitized_text = sanitize_public_text(value)
            if sanitized_text:
                sanitized_usage[key] = sanitized_text
        elif isinstance(value, list):
            cleaned = [sanitize_public_text(item) for item in value]
            sanitized_usage[key] = [item for item in cleaned if item]
        elif isinstance(value, (int, bool)):
            sanitized_usage[key] = value
    sanitized = {
        "skill_id": str(snapshot.get("skill_id") or ""),
        "skill_version": str(snapshot.get("skill_version") or ""),
        "content_hash": str(snapshot.get("content_hash") or ""),
        "source": source,
        "dependency_ids": [str(item) for item in snapshot.get("dependency_ids") or []],
        "allowed": bool(snapshot.get("allowed")),
        "staged": bool(snapshot.get("staged")),
        "used": bool(snapshot.get("used")),
        "created_at": snapshot.get("created_at"),
    }
    if sanitized_usage:
        sanitized["usage"] = sanitized_usage
    return sanitized


def _skill_usage_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usage_by_skill: dict[str, dict[str, Any]] = {}
    ordered_events = sorted(events, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    for item in ordered_events:
        if item.get("event_type") != "skill_used":
            continue
        if bool(item.get("visible_to_user", True)):
            continue
        payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
        skill_id = str(payload.get("skill_id") or payload.get("skill_name") or "").strip()
        if not skill_id:
            continue
        usage = usage_by_skill.setdefault(
            skill_id,
            {
                "event_source": "",
                "event_count": 0,
                "tool_use_ids": [],
            },
        )
        usage["event_count"] += 1
        event_source = sanitize_public_text(payload.get("source")).strip()
        if event_source and not usage["event_source"]:
            usage["event_source"] = event_source
        tool_use_id = sanitize_public_text(payload.get("tool_use_id")).strip()
        if tool_use_id and tool_use_id not in usage["tool_use_ids"]:
            usage["tool_use_ids"].append(tool_use_id)
    for usage in usage_by_skill.values():
        usage["tool_use_ids"].sort()
    return usage_by_skill


def _attach_skill_usage(
    skill_snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    usage_by_skill = _skill_usage_from_events(events)
    if not usage_by_skill:
        return skill_snapshots
    enriched: list[dict[str, Any]] = []
    for snapshot in skill_snapshots:
        usage = usage_by_skill.get(str(snapshot.get("skill_id") or ""))
        if usage:
            persisted_usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
            enriched.append({**snapshot, "usage": {**persisted_usage, **usage}})
        else:
            enriched.append(snapshot)
    return enriched

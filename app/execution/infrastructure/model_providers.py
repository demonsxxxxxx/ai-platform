"""Independent model suppliers and safe configuration retirement."""
from __future__ import annotations

from typing import Any

from app.platform.postgres.values import new_id

CONNECTION_LOCK_KEY = 749_120_009
GROUP_LOCK_KEY = 749_120_017


async def require_provider(conn: Any, *, provider_id: str) -> dict[str, Any]:
    row = await (await conn.execute(
        "select id, name from model_providers where id = %s and archived_at is null", (provider_id,),
    )).fetchone()
    if row is None:
        raise ValueError("model_provider_not_found")
    return dict(row)


async def list_providers(conn: Any) -> list[dict[str, Any]]:
    rows = await (await conn.execute("""
        select p.id, p.name, gw.revision,
          (select count(*) from model_catalog_entries c where c.provider_id = p.id and c.archived_at is null) as model_count
        from model_providers p
        left join model_gateway_revisions gw on gw.provider_id = p.id and gw.active
        where p.archived_at is null order by p.created_at, p.id
    """)).fetchall()
    return [dict(row) for row in rows]


async def create_provider(conn: Any, *, name: str) -> dict[str, Any]:
    name = name.strip()
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise ValueError("model_provider_name_invalid")
    row = await (await conn.execute(
        "insert into model_providers(id, name) values (%s, %s) returning id, name",
        (new_id("mp"), name),
    )).fetchone()
    return dict(row)


async def provider_impact(conn: Any, *, provider_id: str, missing_only: bool = False, tenant_id: str | None = None) -> dict[str, Any]:
    provider = await require_provider(conn, provider_id=provider_id)
    rows = await (await conn.execute("select model_id from model_catalog_entries where provider_id = %s and archived_at is null and (not %s or not upstream_available)", (provider_id, missing_only))).fetchall()
    model_ids = [str(row["model_id"]) for row in rows]
    row = await (await conn.execute("""
        select
          (select revision from model_gateway_revisions where provider_id = %s and active) as revision,
          (select count(*) from model_catalog_entries where model_id = any(%s)) as model_count,
          (select count(distinct gm.group_id) from model_group_models gm join model_catalog_entries c on c.model_id = gm.model_id
            where c.model_id = any(%s)) as group_count,
          (select count(*) from model_groups g join model_catalog_entries c on c.model_id = g.default_model_id
            where c.model_id = any(%s)) as default_group_count,
          (select count(*) from runs r join model_gateway_revisions gw on gw.revision = r.model_gateway_revision
            where (case when %s then r.model_id = any(%s) else gw.provider_id = %s end)
              and r.status not in ('succeeded','failed','cancelled','canceled','dead-letter','dead_letter','dead-lettered')) as active_run_count
    """, (provider_id, model_ids, model_ids, model_ids, missing_only, model_ids, provider_id))).fetchone()
    candidates = await (await conn.execute("""
        select c.model_id as id, c.display_name as label,
          (select count(*) from model_group_models gm where gm.model_id = c.model_id) as group_count,
          (select count(*) from model_groups g where g.default_model_id = c.model_id) as default_group_count,
          (select count(*) from runs r where r.model_id = c.model_id
            and r.status not in ('succeeded','failed','cancelled','canceled','dead-letter','dead_letter','dead-lettered')) as active_run_count
        from model_catalog_entries c where c.model_id = any(%s) order by c.model_id
    """, (model_ids,))).fetchall()
    blocked = []
    for item in candidates:
        if item["group_count"] or item["default_group_count"] or item["active_run_count"]:
            groups = await (await conn.execute("""
                select g.name from model_groups g where g.tenant_id = %s
                  and (g.default_model_id = %s or exists
                    (select 1 from model_group_models gm where gm.group_id = g.id and gm.model_id = %s))
                order by g.name, g.id
            """, (tenant_id, item["id"], item["id"]))).fetchall()
            blocked.append({**dict(item), "groups": [dict(group) for group in groups]})
    return {**provider, **dict(row), "cleanable_count": len(candidates) - len(blocked), "blocked_models": blocked}


async def retire_provider(conn: Any, *, provider_id: str, expected_revision: int | None, confirm: bool, missing_only: bool = False) -> dict[str, Any]:
    # Same lock order as admission: catalog before group policy.
    await conn.execute("select pg_advisory_xact_lock(%s)", (CONNECTION_LOCK_KEY,))
    await conn.execute("select pg_advisory_xact_lock(%s)", (GROUP_LOCK_KEY,))
    impact = await provider_impact(conn, provider_id=provider_id, missing_only=missing_only)
    if impact["revision"] != expected_revision:
        raise ValueError("model_catalog_revision_conflict")
    if not confirm:
        raise ValueError("model_provider_confirmation_required")
    if missing_only:
        if impact["model_count"] and not impact["cleanable_count"]:
            raise ValueError("model_provider_in_use")
        blocked_ids = [item["id"] for item in impact["blocked_models"]]
        await conn.execute("""update model_catalog_entries set enabled = false, upstream_available = false, archived_at = now()
            where provider_id = %s and archived_at is null and not upstream_available and not (model_id = any(%s))""",
            (provider_id, blocked_ids))
        return {"id": provider_id, "deleted": True, "model_count": impact["cleanable_count"], "retained_count": len(blocked_ids)}
    if impact["group_count"] or impact["default_group_count"] or impact["active_run_count"]:
        raise ValueError("model_provider_in_use")
    await conn.execute("update model_catalog_entries set enabled = false, upstream_available = false, archived_at = now() where provider_id = %s and archived_at is null", (provider_id,))
    await conn.execute("update model_gateway_revisions set active = false where provider_id = %s and active", (provider_id,))
    await conn.execute("update model_providers set archived_at = now() where id = %s", (provider_id,))
    return {"id": provider_id, "deleted": True}

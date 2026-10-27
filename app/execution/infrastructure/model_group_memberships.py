"""Atomic user/group membership edits with a shared revision boundary."""
from __future__ import annotations

from typing import Any

from app.execution.domain.model_groups import ModelGroupPolicyError

GROUP_LOCK_KEY = 749_120_017


async def get_user_group_memberships(conn: Any, *, tenant_id: str, user_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """select u.id as user_id, u.model_group_membership_revision as revision,
          coalesce(jsonb_agg(jsonb_build_object(
            'id', g.id, 'code', g.code, 'name', g.name, 'enabled', g.enabled,
            'revision', g.revision, 'is_primary', m.is_primary
          ) order by m.is_primary desc, g.sort_order, g.id)
          filter (where g.id is not null), '[]'::jsonb) as groups
        from users u
        left join model_group_members m on m.tenant_id = u.tenant_id and m.user_id = u.id
        left join model_groups g on g.id = m.group_id and g.type = 'business'
        where u.tenant_id = %s and u.id = %s and u.status = 'active'
        group by u.id""", (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    groups = row["groups"] or []
    return {
        "user_id": str(row["user_id"]), "revision": int(row["revision"]),
        "groups": groups,
        "primary_group_id": next((g["id"] for g in groups if g["is_primary"]), None),
        "default_implicit": not groups,
    }


async def set_user_group_memberships(
    conn: Any, *, tenant_id: str, user_id: str, group_ids: list[str],
    primary_group_id: str | None, expected_revision: int | None = None,
    confirm_default_fallback: bool = False,
) -> dict[str, Any]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (GROUP_LOCK_KEY,))
    current = await get_user_group_memberships(conn, tenant_id=tenant_id, user_id=user_id)
    if current is None:
        raise ModelGroupPolicyError("model_group_member_unknown_user")
    if type(expected_revision) is not int or expected_revision != current["revision"]:
        raise ModelGroupPolicyError("model_group_revision_conflict")
    if any(not isinstance(g, str) or not g or g != g.strip() for g in group_ids):
        raise ModelGroupPolicyError("group_not_available")
    if len(set(group_ids)) != len(group_ids):
        raise ModelGroupPolicyError("model_group_duplicate")
    previous = {g["id"]: bool(g["is_primary"]) for g in current["groups"]}
    primary = primary_group_id
    if primary is None and current["primary_group_id"] in group_ids:
        primary = current["primary_group_id"]
    elif primary is None and not previous and len(group_ids) == 1:
        primary = group_ids[0]
    if (group_ids and primary not in group_ids) or (not group_ids and primary is not None):
        raise ModelGroupPolicyError("model_group_primary_required")
    desired = {g: g == primary for g in group_ids}
    if desired == previous:
        return {"user_id": user_id, "group_ids": group_ids, "primary_group_id": primary, "revision": current["revision"]}
    if not group_ids and previous and not confirm_default_fallback:
        raise ModelGroupPolicyError("model_group_fallback_confirmation_required")
    if group_ids:
        cursor = await conn.execute(
            """select id, enabled, archived_at from model_groups
               where tenant_id = %s and type = 'business' and id = any(%s) for update""",
            (tenant_id, group_ids),
        )
        groups = {g["id"]: g for g in await cursor.fetchall()}
        if set(groups) != set(group_ids) or any(
            g["archived_at"] is not None or (not g["enabled"] and gid not in previous)
            for gid, g in groups.items()
        ):
            raise ModelGroupPolicyError("group_not_available")
    await conn.execute("delete from model_group_members where tenant_id = %s and user_id = %s", (tenant_id, user_id))
    for group_id, is_primary in desired.items():
        await conn.execute(
            "insert into model_group_members(tenant_id, group_id, user_id, is_primary) values (%s,%s,%s,%s)",
            (tenant_id, group_id, user_id, is_primary),
        )
    changed = [g for g in previous.keys() | desired.keys() if previous.get(g) != desired.get(g)]
    await conn.execute(
        "update model_groups set revision = revision + 1, updated_at = now() where tenant_id = %s and id = any(%s)",
        (tenant_id, changed),
    )
    await conn.execute(
        "update users set model_group_membership_revision = model_group_membership_revision + 1 where tenant_id = %s and id = %s",
        (tenant_id, user_id),
    )
    return {"user_id": user_id, "group_ids": group_ids, "primary_group_id": primary, "revision": current["revision"] + 1}


async def set_group_members(
    conn: Any, *, tenant_id: str, group_id: str, revision: int,
    members: list[dict[str, Any]], confirm_default_fallback: bool = False,
) -> dict[str, Any]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (GROUP_LOCK_KEY,))
    group = await (await conn.execute(
        "select type, enabled, archived_at, revision from model_groups where tenant_id = %s and id = %s for update",
        (tenant_id, group_id),
    )).fetchone()
    if group is None or group["type"] != "business" or group["archived_at"] is not None:
        raise ModelGroupPolicyError("group_not_available")
    if int(group["revision"]) != revision:
        raise ModelGroupPolicyError("model_policy_changed")
    wanted = {m["user_id"]: m for m in members}
    if len(wanted) != len(members):
        raise ModelGroupPolicyError("model_group_member_duplicate")
    existing = await (await conn.execute(
        "select user_id from model_group_members where tenant_id = %s and group_id = %s", (tenant_id, group_id),
    )).fetchall()
    old_users = {r["user_id"] for r in existing}
    if not group["enabled"] and set(wanted) - old_users:
        raise ModelGroupPolicyError("group_disabled")
    states = {uid: await get_user_group_memberships(conn, tenant_id=tenant_id, user_id=uid)
              for uid in old_users | wanted.keys()}
    if any(value is None for value in states.values()):
        raise ModelGroupPolicyError("model_group_member_unknown_user")
    # The surrounding control-plane transaction owns rollback across every member.
    for user_id in sorted(states):
        current = states[user_id]
        ids = [g["id"] for g in current["groups"] if g["id"] != group_id]
        primary = current["primary_group_id"]
        if user_id in wanted:
            ids.append(group_id)
            if wanted[user_id].get("is_primary"):
                primary = group_id
        if primary == group_id and user_id not in wanted:
            primary = None
            if ids:
                raise ModelGroupPolicyError("model_group_primary_required")
        await set_user_group_memberships(
            conn, tenant_id=tenant_id, user_id=user_id, group_ids=ids,
            primary_group_id=primary, expected_revision=current["revision"],
            confirm_default_fallback=confirm_default_fallback,
        )
    result = await (await conn.execute("select revision from model_groups where id = %s", (group_id,))).fetchone()
    return {"group_id": group_id, "revision": int(result["revision"])}


async def set_users_group_memberships_batch(conn: Any, *, tenant_id: str, updates: list[dict[str, Any]], confirm_default_fallback: bool = False) -> dict[str, Any]:
    if not updates or len({u["user_id"] for u in updates}) != len(updates):
        raise ModelGroupPolicyError("model_group_batch_invalid")
    await conn.execute("select pg_advisory_xact_lock(%s)", (GROUP_LOCK_KEY,))
    before = {u["user_id"]: await get_user_group_memberships(conn, tenant_id=tenant_id, user_id=u["user_id"]) for u in updates}
    results = [await set_user_group_memberships(conn, tenant_id=tenant_id, confirm_default_fallback=confirm_default_fallback, **u) for u in updates]
    after = {u["user_id"]: await get_user_group_memberships(conn, tenant_id=tenant_id, user_id=u["user_id"]) for u in updates}
    return {"tenant_id": tenant_id, "before": before, "users": results, "after": after}

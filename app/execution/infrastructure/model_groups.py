"""PostgreSQL authority for tenant model groups, membership, and routing."""

from __future__ import annotations

from typing import Any

from app.execution.domain.model_groups import (
    BUSINESS_GROUP_TYPE,
    DEFAULT_GROUP_CODE,
    DEFAULT_GROUP_TYPE,
    ModelGroupPolicyError,
    ModelGroupSelection,
    SELECTION_AUTO,
    SELECTION_EXPLICIT,
    USAGE_SCENE_CHAT,
    USAGE_SCENE_EXPERT,
    normalize_group_code,
    normalize_group_name,
)

from app.execution.infrastructure.model_group_memberships import (
    GROUP_LOCK_KEY as _GROUP_LOCK_KEY,
    get_user_group_memberships,
    set_group_members,
    set_user_group_memberships,
    set_users_group_memberships_batch,
)

__all__ = [
    "get_user_group_memberships", "set_group_members",
    "set_user_group_memberships", "set_users_group_memberships_batch",
]


async def resolve_company_employee_user(
    conn: Any, *, tenant_id: str, work_id: str,
) -> dict[str, str]:
    cursor = await conn.execute(
        """
        select id, display_name, auth_source, coalesce(external_id, '') as external_id
        from users
        where tenant_id = %s and status = 'active' and auth_source = 'company-login'
          and external_id = %s
        """,
        (tenant_id, work_id),
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise ModelGroupPolicyError(
            "company_identity_ambiguous" if len(rows) > 1 else "company_identity_not_mapped"
        )
    row = rows[0]
    return {
        "user_id": str(row["id"]),
        "work_id": work_id,
        "display_name": str(row["display_name"]),
        "auth_source": str(row["auth_source"]),
        "tenant_id": tenant_id,
    }


async def _effective_group(conn: Any, *, tenant_id: str, user_id: str, group_id: str | None, usage_scene: str) -> dict[str, Any]:
    if usage_scene not in {USAGE_SCENE_CHAT, USAGE_SCENE_EXPERT, "independent", "skill"}:
        raise ModelGroupPolicyError("model_policy_unavailable")
    await conn.execute("select pg_advisory_xact_lock_shared(%s)", (_GROUP_LOCK_KEY,))
    cursor = await conn.execute(
        """select g.*, m.is_primary from model_group_members m
           join model_groups g on g.id = m.group_id and g.tenant_id = m.tenant_id
           where m.tenant_id = %s and m.user_id = %s""", (tenant_id, user_id),
    )
    memberships = [dict(row) for row in await cursor.fetchall()]
    if memberships:
        group = next((g for g in memberships if g["id"] == group_id), None) if group_id else next((g for g in memberships if g["is_primary"]), None)
        if group is None:
            raise ModelGroupPolicyError("group_not_available")
    else:
        default_id = await ensure_default_group(conn, tenant_id=tenant_id)
        if group_id is not None and group_id != default_id:
            raise ModelGroupPolicyError("group_not_available")
        group = dict(await (await conn.execute("select * from model_groups where tenant_id = %s and id = %s", (tenant_id, default_id))).fetchone())
    if not group["enabled"] or group.get("archived_at") is not None:
        raise ModelGroupPolicyError("group_disabled")
    if group["type"] == DEFAULT_GROUP_TYPE and usage_scene in {USAGE_SCENE_CHAT, USAGE_SCENE_EXPERT}:
        raise ModelGroupPolicyError("group_not_available")
    return group


async def _available_group_models(conn: Any, group_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """select c.model_id, c.upstream_model_id, c.display_name, c.provider,
             c.auto_compact_window, c.max_output_tokens, gw.revision as gateway_revision,
             gm.priority, gm.weight, gm.display_order
           from model_group_models gm
           join model_catalog_entries c on c.model_id = gm.model_id
           join model_gateway_revisions gw on gw.active and gw.revision = c.last_seen_revision
           where gm.group_id = %s and gm.enabled and c.enabled and c.upstream_available
             and c.auto_compact_window is not null and c.max_output_tokens is not null
           order by gm.display_order, c.model_id""", (group_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def list_public_group_models(
    conn: Any, *, tenant_id: str, user_id: str, usage_scene: str,
    group_id: str | None = None,
) -> dict[str, Any]:
    group = await _effective_group(conn, tenant_id=tenant_id, user_id=user_id, group_id=group_id, usage_scene=usage_scene)
    rows = await _available_group_models(conn, str(group["id"]))
    default_model_id = str(group.get("default_model_id") or "")
    available_ids = {str(row["model_id"]) for row in rows}
    if default_model_id not in available_ids:
        default_model_id = ""
    return {
        "models": [{"id": r["model_id"], "value": r["upstream_model_id"],
                    "label": r["display_name"], "provider": r["provider"],
                    "profile": {"auto_compact_window": r["auto_compact_window"],
                                "max_output_tokens": r["max_output_tokens"]}} for r in rows],
        "count": len(rows), "enabled_count": len(rows),
        "default_model_id": default_model_id or None,
    }


async def ensure_default_group(conn: Any, *, tenant_id: str) -> str:
    existing = await (await conn.execute(
        "select id from model_groups where tenant_id = %s and code = 'default'", (tenant_id,),
    )).fetchone()
    if existing is not None:
        return str(existing["id"])
    cursor = await conn.execute(
        """
        insert into model_groups(tenant_id, code, name, type, enabled)
        values (%s, 'default', '普通默认组', 'ordinary_default', true)
        on conflict (tenant_id, code) do update set enabled = true
        returning id
        """,
        (tenant_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ModelGroupPolicyError("model_policy_unavailable")
    return str(row["id"])


async def list_groups_for_user(conn: Any, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    await ensure_default_group(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select g.id, g.code, g.name, g.type, g.enabled, g.revision,
               g.default_model_id, m.is_primary,
               case when g.type = 'ordinary_default' then '[]'::jsonb else
                 coalesce((select jsonb_agg(jsonb_build_object(
                   'model_id', gm.model_id, 'model_value', c.upstream_model_id,
                   'display_name', c.display_name, 'enabled', gm.enabled
                 ) order by gm.priority desc, gm.display_order, gm.model_id)
                 from model_group_models gm
                 join model_catalog_entries c on c.model_id = gm.model_id
                   and c.enabled and c.upstream_available
                   and c.auto_compact_window is not null and c.max_output_tokens is not null
                 join model_gateway_revisions gw on gw.active and gw.revision = c.last_seen_revision
                 where gm.group_id = g.id and gm.enabled and g.enabled), '[]'::jsonb) end as models,
               case when not g.enabled then 'group_disabled'
                    when g.type = 'ordinary_default' then 'default_not_for_chat'
                    when not exists (select 1 from model_group_models x where x.group_id = g.id and x.enabled) then 'group_has_no_models'
                    when not exists (select 1 from model_group_models x join model_catalog_entries c on c.model_id = x.model_id
                       join model_gateway_revisions gw on gw.active and gw.revision = c.last_seen_revision
                       where x.group_id = g.id and x.enabled and c.enabled and c.upstream_available
                         and c.auto_compact_window is not null and c.max_output_tokens is not null) then 'group_models_unavailable'
                    else 'available' end as availability
        from model_groups g
        left join model_group_members m on m.group_id = g.id and m.user_id = %s
        where g.tenant_id = %s and g.archived_at is null and (
          (g.type = 'business' and m.user_id is not null)
          or (g.type = 'ordinary_default' and not exists (
            select 1 from model_group_members any_member
            join model_groups business_group on business_group.id = any_member.group_id
            where any_member.tenant_id = %s and any_member.user_id = %s
              and business_group.type = 'business'
          ))
        )
        group by g.id, m.is_primary
        order by (g.type = 'ordinary_default') desc, g.sort_order, g.id
        """,
        (user_id, tenant_id, tenant_id, user_id),
    )
    groups: list[dict[str, Any]] = []
    for row in await cursor.fetchall():
        models = row.get("models") or []
        groups.append({
            "id": str(row["id"]),
            "code": str(row["code"]),
            "name": str(row["name"]),
            "type": str(row["type"]),
            "enabled": bool(row["enabled"]),
            "revision": int(row["revision"]),
            "is_primary": bool(row.get("is_primary")),
            "default_model_id": row.get("default_model_id"),
            "models": [
                {
                    "id": str(model["model_id"]),
                    "value": str(model["model_value"]),
                    "label": str(model.get("display_name") or model["model_value"]),
                    "enabled": bool(model["enabled"]),
                }
                for model in models if isinstance(model, dict)
            ],
            "availability": str(row.get("availability") or "group_models_unavailable"),
        })
    return groups


async def get_admin_group_details(
    conn: Any, *, tenant_id: str, group_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select g.id, g.code, g.name, g.type, g.enabled, g.revision, g.remark, g.sort_order,
          g.default_model_id,
          (g.type = 'ordinary_default') as members_are_implicit,
          case when g.type = 'ordinary_default' then
            (select count(*) from users u where u.tenant_id = g.tenant_id and u.status = 'active'
              and not exists (select 1 from model_group_members bm join model_groups bg on bg.id = bm.group_id
                where bm.tenant_id = u.tenant_id and bm.user_id = u.id and bg.type = 'business'))
            else (select count(*) from model_group_members m where m.group_id = g.id) end as member_count,
          (select count(*) from model_group_models gm where gm.group_id = g.id) as model_count,
          coalesce((select jsonb_agg(jsonb_build_object(
            'user_id', u.id, 'work_id', case when u.auth_source = 'company-login' then u.external_id else null end,
            'display_name', u.display_name, 'auth_source', u.auth_source, 'is_primary', m.is_primary
          ) order by u.id) from model_group_members m join users u on u.id = m.user_id and u.tenant_id = g.tenant_id
             where m.group_id = g.id), '[]'::jsonb) as members,
          coalesce((select jsonb_agg(jsonb_build_object(
            'model_id', gm.model_id, 'model_value', c.upstream_model_id, 'label', c.display_name,
            'enabled', gm.enabled, 'display_order', gm.display_order, 'priority', gm.priority, 'weight', gm.weight
          ) order by gm.display_order, gm.model_id) from model_group_models gm join model_catalog_entries c on c.model_id = gm.model_id
             where gm.group_id = g.id), '[]'::jsonb) as models
        from model_groups g where g.tenant_id = %s and g.id = %s
        """,
        (tenant_id, group_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def list_admin_groups(conn: Any, *, tenant_id: str) -> list[dict[str, Any]]:
    await ensure_default_group(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select g.id, g.code, g.name, g.type, g.enabled, g.revision,
               g.remark, g.sort_order, g.default_model_id,
               (g.type = 'ordinary_default') as members_are_implicit,
               case when g.type = 'ordinary_default' then
                 (select count(*) from users u where u.tenant_id = g.tenant_id and u.status = 'active'
                   and not exists (select 1 from model_group_members bm join model_groups bg on bg.id = bm.group_id
                     where bm.tenant_id = u.tenant_id and bm.user_id = u.id and bg.type = 'business'))
                 else (select count(*) from model_group_members m where m.group_id = g.id) end as member_count,
               (select count(*) from model_group_models gm where gm.group_id = g.id) as model_count
        from model_groups g where g.tenant_id = %s
        order by (g.type = 'ordinary_default') desc, g.sort_order, g.id
        """,
        (tenant_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def create_group(conn: Any, *, tenant_id: str, code: str, name: str, remark: str = "", sort_order: int = 0) -> dict[str, Any]:
    code = normalize_group_code(code)
    name = normalize_group_name(name)
    if code == DEFAULT_GROUP_CODE:
        raise ModelGroupPolicyError("model_group_code_reserved")
    duplicate = await conn.execute(
        "select 1 from model_groups where tenant_id = %s and lower(btrim(name)) = lower(btrim(%s)) and archived_at is null",
        (tenant_id, name),
    )
    if await duplicate.fetchone() is not None:
        raise ModelGroupPolicyError("model_group_name_duplicate")
    cursor = await conn.execute(
        """
        insert into model_groups(tenant_id, code, name, type, remark, sort_order)
        values (%s, %s, %s, 'business', %s, %s)
        returning id, code, name, type, enabled, revision, remark, sort_order
        """,
        (tenant_id, code, name, str(remark or "").strip(), int(sort_order)),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ModelGroupPolicyError("model_group_create_failed")
    return dict(row)


async def update_group(conn: Any, *, tenant_id: str, group_id: str, name: str | None, enabled: bool | None, remark: str | None, sort_order: int | None, revision: int, confirm_impact: bool = False) -> dict[str, Any]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_GROUP_LOCK_KEY,))
    group_cursor = await conn.execute(
        "select type, enabled from model_groups where tenant_id = %s and id = %s and archived_at is null",
        (tenant_id, group_id),
    )
    group = await group_cursor.fetchone()
    if group is None:
        raise ModelGroupPolicyError("group_not_available")
    if group["type"] == DEFAULT_GROUP_TYPE and (
        name is not None or enabled is not None or sort_order is not None
    ):
        raise ModelGroupPolicyError("model_default_group_immutable")
    if group["type"] == BUSINESS_GROUP_TYPE and enabled is False and bool(group["enabled"]):
        member_cursor = await conn.execute(
            "select exists(select 1 from model_group_members where tenant_id = %s and group_id = %s) as has_members",
            (tenant_id, group_id),
        )
        if bool((await member_cursor.fetchone() or {}).get("has_members")) and not confirm_impact:
            raise ModelGroupPolicyError("model_group_impact_confirmation_required")
    changes: list[str] = []
    params: list[Any] = []
    if name is not None:
        normalized_name = normalize_group_name(name)
        duplicate = await conn.execute(
            "select 1 from model_groups where tenant_id = %s and id <> %s and lower(btrim(name)) = lower(btrim(%s)) and archived_at is null",
            (tenant_id, group_id, normalized_name),
        )
        if await duplicate.fetchone() is not None:
            raise ModelGroupPolicyError("model_group_name_duplicate")
        changes.append("name = %s")
        params.append(normalized_name)
    if enabled is not None:
        changes.append("enabled = %s")
        params.append(bool(enabled))
    if remark is not None:
        changes.append("remark = %s")
        params.append(str(remark).strip())
    if sort_order is not None:
        changes.append("sort_order = %s")
        params.append(int(sort_order))
    if not changes:
        raise ModelGroupPolicyError("model_group_update_empty")
    changes.extend(("revision = revision + 1", "updated_at = now()"))
    params.extend((tenant_id, group_id, revision))
    cursor = await conn.execute(
        f"""update model_groups set {', '.join(changes)}
            where tenant_id = %s and id = %s and type in ('business', 'ordinary_default') and revision = %s
            returning id, code, name, type, enabled, revision, remark, sort_order""",
        tuple(params),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ModelGroupPolicyError("model_group_revision_conflict")
    return dict(row)


async def set_group_models(conn: Any, *, tenant_id: str, group_id: str, revision: int, models: list[dict[str, Any]], default_model_id: str | None = None) -> dict[str, Any]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_GROUP_LOCK_KEY,))
    group_cursor = await conn.execute(
        "select id, type, revision, default_model_id from model_groups where tenant_id = %s and id = %s for update",
        (tenant_id, group_id),
    )
    group = await group_cursor.fetchone()
    if group is None or group["type"] not in {BUSINESS_GROUP_TYPE, DEFAULT_GROUP_TYPE}:
        raise ModelGroupPolicyError("group_not_available")
    if int(group["revision"]) != revision:
        raise ModelGroupPolicyError("model_policy_changed")
    model_ids = [str(item.get("model_id") or "") for item in models]
    if any(not model_id for model_id in model_ids) or len(model_ids) != len(set(model_ids)):
        raise ModelGroupPolicyError("model_group_model_duplicate")
    first_model = min(models, key=lambda item: (int(item.get("display_order", 0)), str(item.get("model_id") or "")), default=None)
    chosen_default = default_model_id or (str(group.get("default_model_id")) if group.get("default_model_id") in model_ids else None) or (str(first_model["model_id"]) if first_model else None)
    if chosen_default is not None and chosen_default not in model_ids:
        raise ModelGroupPolicyError("model_group_default_model_invalid")
    if chosen_default is not None and not any(str(item.get("model_id")) == chosen_default and bool(item.get("enabled", True)) for item in models):
        raise ModelGroupPolicyError("model_group_default_model_invalid")
    current = await (await conn.execute(
        "select model_id, enabled, display_order, priority, weight from model_group_models where group_id = %s",
        (group_id,),
    )).fetchall()
    desired = [{"model_id": m["model_id"], "enabled": m.get("enabled", True),
                "display_order": m.get("display_order", 0), "priority": m.get("priority", 0),
                "weight": m.get("weight", 100)} for m in models]
    if sorted((dict(m) for m in current), key=lambda m: m["model_id"]) == sorted(desired, key=lambda m: m["model_id"]):
        if str(group.get("default_model_id") or "") == str(chosen_default or ""):
            return {"group_id": group_id, "revision": revision, "default_model_id": chosen_default}
        cursor = await conn.execute(
            "update model_groups set default_model_id = %s, revision = revision + 1, updated_at = now() where id = %s and revision = %s returning revision, default_model_id",
            (chosen_default, group_id, revision),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise ModelGroupPolicyError("model_group_revision_conflict")
        return {"group_id": group_id, "revision": int(updated["revision"]), "default_model_id": updated["default_model_id"]}
    await conn.execute("delete from model_group_models where group_id = %s", (group_id,))
    for item in desired:
        model_id = str(item["model_id"])
        priority = int(item.get("priority", 0))
        weight = int(item.get("weight", 100))
        if not 0 <= priority <= 1000 or not 1 <= weight <= 1000:
            raise ModelGroupPolicyError("model_group_model_parameters_invalid")
        cursor = await conn.execute(
            """
            insert into model_group_models(group_id, model_id, enabled, display_order, priority, weight)
            select %s, c.model_id, %s, %s, %s, %s from model_catalog_entries c
            where c.model_id = %s
            on conflict (group_id, model_id) do update set enabled = excluded.enabled,
              display_order = excluded.display_order, priority = excluded.priority, weight = excluded.weight,
              revision = model_group_models.revision + 1
            returning model_id
            """,
            (group_id, bool(item.get("enabled", True)), int(item.get("display_order", 0)), priority, weight, model_id),
        )
        if await cursor.fetchone() is None:
            raise ModelGroupPolicyError("model_not_available")
    cursor = await conn.execute(
        "update model_groups set default_model_id = %s, revision = revision + 1, updated_at = now() where id = %s returning revision, default_model_id",
        (chosen_default, group_id),
    )
    current = await cursor.fetchone()
    return {"group_id": group_id, "revision": int(current["revision"]), "default_model_id": current["default_model_id"]}


async def archive_or_delete_group(
    conn: Any, *, tenant_id: str, group_id: str, revision: int,
    confirm_impact: bool = False, delete: bool = False,
) -> dict[str, Any]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_GROUP_LOCK_KEY,))
    cursor = await conn.execute(
        """
        select id, type, revision, archived_at,
          (select count(*) from model_group_members m where m.group_id = g.id) as member_count,
          (select count(*) from model_group_models gm where gm.group_id = g.id) as model_count,
          exists(select 1 from runs r where r.model_group_id = g.id) as has_run_references,
          exists(select 1 from runs r where r.model_group_id = g.id and r.status not in ('succeeded','failed','cancelled','canceled')) as has_active_runs
        from model_groups g where tenant_id = %s and id = %s for update
        """,
        (tenant_id, group_id),
    )
    group = await cursor.fetchone()
    if group is None:
        raise ModelGroupPolicyError("group_not_available")
    if group["type"] == DEFAULT_GROUP_TYPE:
        raise ModelGroupPolicyError("model_default_group_immutable")
    if int(group["member_count"]) or int(group["model_count"]):
        raise ModelGroupPolicyError("model_group_references_exist")
    if group["has_active_runs"]:
        raise ModelGroupPolicyError("model_group_active_runs")
    if delete:
        if group["has_run_references"]:
            raise ModelGroupPolicyError("model_group_references_exist")
        deleted = await conn.execute(
            "delete from model_groups where tenant_id = %s and id = %s and revision = %s returning id",
            (tenant_id, group_id, revision),
        )
        if await deleted.fetchone() is None:
            raise ModelGroupPolicyError("model_group_revision_conflict")
        return {"group_id": group_id, "deleted": True, "archived": False}
    updated = await conn.execute(
        """
        update model_groups set enabled = false, archived_at = coalesce(archived_at, now()),
          revision = revision + 1, updated_at = now()
        where tenant_id = %s and id = %s and revision = %s
        returning revision, archived_at
        """,
        (tenant_id, group_id, revision),
    )
    row = await updated.fetchone()
    if row is None:
        raise ModelGroupPolicyError("model_group_revision_conflict")
    return {"group_id": group_id, "deleted": False, "archived": True, "revision": int(row["revision"])}


async def resolve_group_model(conn: Any, *, tenant_id: str, user_id: str, group_id: str | None, model_id: str | None, model_value: str | None, selection_mode: str, usage_scene: str) -> ModelGroupSelection:
    if selection_mode not in {SELECTION_AUTO, SELECTION_EXPLICIT}:
        raise ModelGroupPolicyError("model_selection_conflict")
    explicit = model_id is not None or model_value is not None
    if (selection_mode == SELECTION_AUTO and explicit) or (selection_mode == SELECTION_EXPLICIT and not explicit):
        raise ModelGroupPolicyError("model_selection_conflict")
    group = await _effective_group(conn, tenant_id=tenant_id, user_id=user_id, group_id=group_id, usage_scene=usage_scene)
    if model_id is not None and model_value is not None:
        identity = await (await conn.execute(
            "select model_id from model_catalog_entries where model_id = %s and upstream_model_id = %s",
            (model_id, model_value),
        )).fetchone()
        if identity is None:
            raise ModelGroupPolicyError("model_selection_conflict")
    rows = await _available_group_models(conn, str(group["id"]))
    if explicit:
        rows = [r for r in rows if (model_id is None or r["model_id"] == model_id)
                and (model_value is None or r["upstream_model_id"] == model_value)]
        if not rows:
            raise ModelGroupPolicyError("model_not_available")
    if not rows:
        configured = await (await conn.execute(
            "select 1 from model_group_models where group_id = %s and enabled limit 1", (group["id"],),
        )).fetchone()
        raise ModelGroupPolicyError("group_models_unavailable" if configured else "group_has_no_models")
    if not explicit:
        default_id = str(group.get("default_model_id") or "")
        row = next((candidate for candidate in rows if str(candidate["model_id"]) == default_id), None)
        if row is None:
            raise ModelGroupPolicyError("model_not_available")
    else:
        row = rows[0]
    return ModelGroupSelection(
        group_id=str(group["id"]), group_code=str(group["code"]), group_type=str(group["type"]),
        group_revision=int(group["revision"]), policy_version="model-groups.v1", selection_mode=selection_mode,
        model_id=str(row["model_id"]), model_value=str(row["upstream_model_id"]), gateway_revision=int(row["gateway_revision"]),
        auto_compact_window=int(row["auto_compact_window"]), max_output_tokens=int(row["max_output_tokens"]),
        priority=int(row["priority"]), weight=int(row["weight"]),
    )

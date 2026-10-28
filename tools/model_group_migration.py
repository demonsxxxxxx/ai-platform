"""Explicit, tenant-scoped migration of model groups and complete user grants.

Preview is read-only. Apply requires the preview's revisions, drains active Runs,
and replaces only the named groups' models and the named users' memberships.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import transaction  # noqa: E402
from app.execution.domain.model_groups import normalize_group_code, normalize_group_name  # noqa: E402
from app.execution.infrastructure.model_group_memberships import GROUP_LOCK_KEY  # noqa: E402
from app.execution.infrastructure.model_management import _CONNECTION_LOCK_KEY  # noqa: E402

VERSION = "model-groups.v3"


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != VERSION or set(value) - {"version", "groups", "users", "expected_state"}:
        raise ValueError("model_group_migration_mapping_invalid")
    if not isinstance(value.get("groups"), list) or not isinstance(value.get("users"), list):
        raise ValueError("model_group_migration_mapping_invalid")
    groups = []
    seen = set()
    for raw in value["groups"]:
        if not isinstance(raw, dict) or set(raw) - {"tenant_id", "code", "name", "remark", "sort_order", "models"}:
            raise ValueError("model_group_migration_mapping_invalid")
        tenant, code = raw.get("tenant_id"), normalize_group_code(raw.get("code"))
        if not isinstance(tenant, str) or not tenant or (tenant, code) in seen:
            raise ValueError("model_group_migration_mapping_duplicate")
        seen.add((tenant, code))
        name = normalize_group_name(raw.get("name"))
        if code == "default" and name != "普通默认组":
            raise ValueError("model_default_group_immutable")
        models = raw.get("models")
        if not isinstance(models, list):
            raise ValueError("model_group_migration_mapping_invalid")
        checked = {}
        for model in models:
            if not isinstance(model, dict) or set(model) - {"model_id", "enabled", "display_order", "priority", "weight"}:
                raise ValueError("model_group_migration_mapping_invalid")
            mid = model.get("model_id")
            if not isinstance(mid, str) or not mid or mid in checked:
                raise ValueError("model_group_migration_mapping_duplicate")
            enabled = model.get("enabled", True)
            order, priority, weight = model.get("display_order", 0), model.get("priority", 0), model.get("weight", 100)
            if type(enabled) is not bool or any(type(v) is not int for v in (order, priority, weight)) or order < 0 or not 0 <= priority <= 1000 or not 1 <= weight <= 1000:
                raise ValueError("model_group_migration_mapping_invalid")
            checked[mid] = {"model_id": mid, "enabled": enabled, "display_order": order, "priority": priority, "weight": weight}
        order = raw.get("sort_order", 0)
        if type(order) is not int or order < 0 or (code == "default" and order != 0):
            raise ValueError("model_group_migration_mapping_invalid")
        groups.append({"tenant_id": tenant, "code": code, "name": name, "remark": str(raw.get("remark") or ""), "sort_order": order, "models": checked})
    users = []
    for raw in value["users"]:
        if not isinstance(raw, dict) or set(raw) - {"tenant_id", "user_id", "work_id", "group_codes", "primary_group_code"}:
            raise ValueError("model_group_migration_mapping_invalid")
        if (raw.get("user_id") is None) == (raw.get("work_id") is None):
            raise ValueError("model_group_migration_mapping_identity_invalid")
        codes = raw.get("group_codes")
        if not isinstance(raw.get("tenant_id"), str) or not isinstance(codes, list) or any(not isinstance(c, str) or c == "default" for c in codes) or len(codes) != len(set(codes)):
            raise ValueError("model_group_migration_mapping_invalid")
        if len(codes) > 1:
            raise ValueError("model_group_single_membership_required")
        primary = raw.get("primary_group_code")
        if (codes and primary not in codes) or (not codes and primary is not None):
            raise ValueError("model_group_migration_primary_invalid")
        users.append(dict(raw))
    if not groups and not users:
        raise ValueError("model_group_migration_mapping_invalid")
    return {"groups": groups, "users": users, "expected_state": value.get("expected_state")}


async def inventory() -> dict[str, Any]:
    async with transaction() as conn:
        tenants = await (await conn.execute("select id from tenants order by id")).fetchall()
        catalog = await (await conn.execute("select model_id, upstream_model_id, enabled, access_mode, allowed_user_ids, is_default from model_catalog_entries order by model_id")).fetchall()
        users = await (await conn.execute("select tenant_id, id as user_id, auth_source, external_id from users where status = 'active' order by tenant_id, id")).fetchall()
        groups = await (await conn.execute("select id, tenant_id, code, name, enabled, revision from model_groups order by tenant_id, code")).fetchall()
        members = await (await conn.execute("select tenant_id, group_id, user_id, is_primary from model_group_members order by tenant_id, group_id, user_id")).fetchall()
        models = await (await conn.execute("select g.tenant_id, m.group_id, m.model_id, m.enabled, m.display_order, m.priority, m.weight from model_group_models m join model_groups g on g.id=m.group_id order by g.tenant_id, m.group_id, m.model_id")).fetchall()
    return {"version": VERSION, "tenants": [t["id"] for t in tenants], "catalog": catalog, "users": users, "groups": groups,
            "memberships": members, "group_models": models, "legacy_all_is_not_approval": True}


async def _prepare(conn: Any, mapping: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # The order matches Run admission: catalog, then group policy.
    await conn.execute("select pg_advisory_xact_lock(%s)", (_CONNECTION_LOCK_KEY,))
    await conn.execute("select pg_advisory_xact_lock(%s)", (GROUP_LOCK_KEY,))
    tenants = sorted({g["tenant_id"] for g in mapping["groups"]} | {u["tenant_id"] for u in mapping["users"]})
    known = await (await conn.execute("select id from tenants order by id")).fetchall()
    if not set(tenants).issubset({t["id"] for t in known}):
        raise ValueError("model_group_migration_reference_unknown")
    active = await (await conn.execute("select 1 from runs where tenant_id = any(%s) and status not in ('succeeded','failed','cancelled','canceled','dead-letter') limit 1", (tenants,))).fetchone()
    if active:
        raise ValueError("model_group_migration_active_runs")
    catalog = await (await conn.execute("select model_id, enabled, access_mode, allowed_user_ids, is_default, last_seen_revision from model_catalog_entries order by model_id")).fetchall()
    catalog_ids = {r["model_id"] for r in catalog}
    current_groups = await (await conn.execute("select * from model_groups where tenant_id = any(%s) order by tenant_id, code", (tenants,))).fetchall()
    group_by_key = {(g["tenant_id"], g["code"]): dict(g) for g in current_groups}
    current_models = await (await conn.execute("select gm.* from model_group_models gm join model_groups g on g.id=gm.group_id where g.tenant_id=any(%s) order by gm.group_id, gm.model_id", (tenants,))).fetchall()
    models_by_key = {(g["tenant_id"], g["code"]): {r["model_id"]: {k: r[k] for k in ("model_id", "enabled", "display_order", "priority", "weight")} for r in current_models if r["group_id"] == g["id"]} for g in current_groups}
    desired_models = dict(models_by_key)
    for group in mapping["groups"]:
        key = (group["tenant_id"], group["code"])
        old = group_by_key.get(key)
        if old and (old["archived_at"] is not None or not old["enabled"]):
            raise ValueError("group_not_available")
        if set(group["models"]) - catalog_ids:
            raise ValueError("model_group_migration_reference_unknown")
        if old and old.get("default_model_id"):
            old_default = old["default_model_id"]
            if old_default not in group["models"] and group["models"]:
                raise ValueError("model_group_default_change_required")
            if old_default in group["models"] and not bool(group["models"][old_default].get("enabled", True)):
                raise ValueError("model_group_default_model_invalid")
        elif group["models"]:
            first_model = min(group["models"].values(), key=lambda model: (model["display_order"], model["model_id"]))
            if not bool(first_model.get("enabled", True)):
                raise ValueError("model_group_default_model_invalid")
        desired_models[key] = group["models"]
    user_rows = await (await conn.execute("select id, tenant_id, auth_source, external_id, status, model_group_membership_revision from users where tenant_id=any(%s) order by tenant_id,id", (tenants,))).fetchall()
    members = await (await conn.execute("select m.*, g.code from model_group_members m join model_groups g on g.id=m.group_id where m.tenant_id=any(%s) order by m.tenant_id,m.user_id,g.code", (tenants,))).fetchall()
    user_grants = {(u["tenant_id"], u["id"]): {m["code"]: bool(m["is_primary"]) for m in members if m["tenant_id"] == u["tenant_id"] and m["user_id"] == u["id"]} for u in user_rows}
    desired_grants = dict(user_grants)
    covered_users = set()
    for user in mapping["users"]:
        matches = [u for u in user_rows if u["tenant_id"] == user["tenant_id"] and u["status"] == "active" and ((user.get("user_id") is not None and u["id"] == user["user_id"]) or (user.get("work_id") is not None and u["auth_source"] == "company-login" and u["external_id"] == user["work_id"]))]
        if len(matches) != 1:
            raise ValueError("model_group_migration_identity_unresolved")
        key = (user["tenant_id"], matches[0]["id"])
        if key in covered_users:
            raise ValueError("model_group_migration_mapping_duplicate")
        covered_users.add(key)
        if any((user["tenant_id"], code) not in desired_models for code in user["group_codes"]):
            raise ValueError("model_group_migration_group_reference_invalid")
        if any(not group_by_key[(user["tenant_id"], code)]["enabled"]
               or group_by_key[(user["tenant_id"], code)]["archived_at"] is not None
               for code in user["group_codes"] if (user["tenant_id"], code) in group_by_key):
            raise ValueError("group_not_available")
        desired_grants[key] = {code: code == user["primary_group_code"] for code in user["group_codes"]}
    def models_for(tenant, grants, model_map):
        codes = grants.keys() if grants else ["default"]
        return sorted({mid for code in codes for mid, row in model_map.get((tenant, code), {}).items() if row["enabled"]})
    differences = []
    for key, before in user_grants.items():
        after = desired_grants[key]
        before_models, after_models = models_for(key[0], before, models_by_key), models_for(key[0], after, desired_models)
        if key in covered_users or before_models != after_models:
            legacy = sorted(r["model_id"] for r in catalog if r["enabled"] and (r["access_mode"] == "all" or key[1] in (r["allowed_user_ids"] or [])))
            differences.append({"tenant_id": key[0], "user_id": key[1], "before_groups": before, "after_groups": after, "before_models": before_models, "after_models": after_models, "legacy_models": legacy})
    expected = {
        "groups": [{"tenant_id": g["tenant_id"], "code": g["code"], "revision": g["revision"]} for g in current_groups],
        "users": [{"tenant_id": u["tenant_id"], "user_id": u["id"], "revision": u["model_group_membership_revision"],
                   "status": u["status"], "auth_source": u["auth_source"], "external_id": u["external_id"]} for u in user_rows],
        "catalog": catalog,
        "confirmed_mapping": {"groups": mapping["groups"], "users": mapping["users"]},
    }
    mapped_groups = {(g["tenant_id"], g["code"]) for g in mapping["groups"]}
    retained = {
        "users": [{"tenant_id": key[0], "user_id": key[1], "groups": grants,
                   "models": models_for(key[0], grants, desired_models)}
                  for key, grants in user_grants.items() if key not in covered_users],
        "groups": [{"tenant_id": key[0], "code": key[1], "models": list(models.values())}
                   for key, models in models_by_key.items() if key not in mapped_groups],
    }
    preview = {"version": VERSION, "status": "preview", "expected_state": expected,
               "covered_tenants": tenants, "outside_tenants": [t["id"] for t in known if t["id"] not in tenants],
               "retained": retained, "user_changes": differences, "requires_confirmation": True}
    state = {"groups": group_by_key, "models": models_by_key, "desired_models": desired_models, "grants": user_grants, "desired_grants": desired_grants, "covered_users": covered_users}
    return preview, state


async def preview_mapping(value: dict[str, Any]) -> dict[str, Any]:
    async with transaction() as conn:
        preview, _ = await _prepare(conn, _mapping(value))
        return preview


async def apply_mapping(mapping_path: Path) -> dict[str, Any]:
    mapping = _mapping(json.loads(mapping_path.read_text(encoding="utf-8")))
    async with transaction() as conn:
        preview, state = await _prepare(conn, mapping)
        groups, current_models = state["groups"], state["models"]
        unchanged = all(
            (item["tenant_id"], item["code"]) in groups
            and all(groups[(item["tenant_id"], item["code"])][k] == item[k] for k in ("name", "remark", "sort_order"))
            and current_models.get((item["tenant_id"], item["code"]), {}) == item["models"]
            for item in mapping["groups"]
        ) and all(state["grants"][key] == state["desired_grants"][key] for key in state["covered_users"])
        expected = mapping["expected_state"]
        if not isinstance(expected, dict) or set(expected) != set(preview["expected_state"]) or expected.get("confirmed_mapping") != preview["expected_state"]["confirmed_mapping"]:
            raise ValueError("model_group_migration_preview_changed")
        if unchanged:
            return {"version": VERSION, "status": "applied", "changed_groups": 0, "changed_users": 0}
        if mapping["expected_state"] != preview["expected_state"]:
            raise ValueError("model_group_migration_preview_changed")
        changed_groups = set()
        for item in mapping["groups"]:
            key = (item["tenant_id"], item["code"])
            old = groups.get(key)
            if old is None:
                row = await (await conn.execute("""insert into model_groups(tenant_id,code,name,type,remark,sort_order) values (%s,%s,%s,%s,%s,%s) returning *""", (key[0], key[1], item["name"], "ordinary_default" if key[1] == "default" else "business", item["remark"], item["sort_order"]))).fetchone()
                groups[key] = dict(row)
            elif any(old[k] != item[k] for k in ("name", "remark", "sort_order")):
                await conn.execute("update model_groups set name=%s,remark=%s,sort_order=%s where id=%s", (item["name"], item["remark"], item["sort_order"], old["id"]))
                changed_groups.add(key)
            if current_models.get(key, {}) != item["models"]:
                changed_groups.add(key)
                await conn.execute("delete from model_group_models where group_id=%s", (groups[key]["id"],))
        for item in mapping["groups"]:
            key = (item["tenant_id"], item["code"])
            if current_models.get(key, {}) != item["models"]:
                for m in item["models"].values():
                    await conn.execute("insert into model_group_models(group_id,model_id,enabled,display_order,priority,weight) values (%s,%s,%s,%s,%s,%s)", (groups[key]["id"], m["model_id"], m["enabled"], m["display_order"], m["priority"], m["weight"]))
                old_default = groups[key].get("default_model_id")
                new_default = old_default if old_default in item["models"] else None
                if not current_models.get(key) and item["models"]:
                    new_default = min(item["models"].values(), key=lambda m: (m["display_order"], m["model_id"]))["model_id"]
                await conn.execute("update model_groups set default_model_id=%s where id=%s", (new_default, groups[key]["id"]))
        changed_users = 0
        for key in state["covered_users"]:
            before, after = state["grants"][key], state["desired_grants"][key]
            if before == after:
                continue
            await conn.execute("delete from model_group_members where tenant_id=%s and user_id=%s", key)
            for code, primary in after.items():
                await conn.execute("insert into model_group_members(tenant_id,group_id,user_id,is_primary) values (%s,%s,%s,%s)", (key[0], groups[(key[0], code)]["id"], key[1], primary))
            await conn.execute("update users set model_group_membership_revision=model_group_membership_revision+1 where tenant_id=%s and id=%s", key)
            changed_users += 1
            changed_groups.update((key[0], code) for code in before.keys() | after.keys() if before.get(code) != after.get(code))
        for key in changed_groups:
            await conn.execute("update model_groups set revision=revision+1,updated_at=now() where id=%s", (groups[key]["id"],))
    return {"version": VERSION, "status": "applied", "changed_groups": len(changed_groups), "changed_users": changed_users}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--mapping", type=Path)
    args = parser.parse_args()
    if args.confirm:
        if args.mapping is None:
            parser.error("--confirm requires --mapping")
        result = asyncio.run(apply_mapping(args.mapping))
    elif args.preview:
        result = asyncio.run(preview_mapping(json.loads(args.preview.read_text(encoding="utf-8"))))
    else:
        result = asyncio.run(inventory())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

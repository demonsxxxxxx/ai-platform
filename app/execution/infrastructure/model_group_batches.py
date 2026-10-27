"""Atomic model-to-group batch operations."""

from __future__ import annotations

from typing import Any

from app.execution.domain.model_groups import ModelGroupPolicyError

from app.execution.infrastructure.model_group_memberships import GROUP_LOCK_KEY as _GROUP_LOCK_KEY


async def batch_group_models(
    conn: Any, *, tenant_id: str, group_ids: list[str], model_ids: list[str],
    operation: str, expected_group_revisions: dict[str, int], preview: bool = False,
) -> dict[str, Any]:
    """Atomically add, replace, or remove selected models across groups."""
    await conn.execute("select pg_advisory_xact_lock(%s)", (_GROUP_LOCK_KEY,))
    group_ids = [str(value).strip() for value in group_ids]
    model_ids = [str(value).strip() for value in model_ids]
    if not group_ids or len(group_ids) != len(set(group_ids)):
        raise ModelGroupPolicyError("model_group_batch_invalid")
    if len(model_ids) != len(set(model_ids)) or any(not value for value in model_ids):
        raise ModelGroupPolicyError("model_group_batch_invalid")
    if operation not in {"add", "replace", "remove"}:
        raise ModelGroupPolicyError("model_group_batch_invalid")
    requested_group_ids = list(group_ids)
    if operation != "replace" and set(expected_group_revisions) != set(requested_group_ids):
        raise ModelGroupPolicyError("model_group_batch_revision_required")
    existing_group_ids: list[str] = []
    if operation == "replace":
        existing_cursor = await conn.execute(
            """
            select distinct gm.group_id
            from model_group_models gm join model_groups g on g.id = gm.group_id
            where g.tenant_id = %s and g.archived_at is null and gm.model_id = any(%s)
            """,
            (tenant_id, model_ids),
        )
        existing_group_ids = [str(row["group_id"]) for row in await existing_cursor.fetchall()]
    group_ids = list(dict.fromkeys(requested_group_ids + existing_group_ids))
    if set(expected_group_revisions) != set(group_ids):
        raise ModelGroupPolicyError("model_group_batch_revision_required")
    if operation == "replace" and existing_group_ids:
        active_cursor = await conn.execute(
            """
            select 1 from runs r
            where r.tenant_id = %s and r.model_group_id = any(%s)
              and r.model_id = any(%s)
              and r.status not in ('succeeded', 'failed', 'cancelled', 'canceled')
            limit 1
            """,
            (tenant_id, existing_group_ids, model_ids),
        )
        if await active_cursor.fetchone() is not None:
            raise ModelGroupPolicyError("model_group_active_runs")
    groups_cursor = await conn.execute(
        """
        select id, type, enabled, archived_at, revision,
          (select count(*) from model_group_members m where m.group_id = model_groups.id) as member_count
        from model_groups where tenant_id = %s and id = any(%s) for update
        """,
        (tenant_id, group_ids),
    )
    groups = {str(row["id"]): dict(row) for row in await groups_cursor.fetchall()}
    if set(groups) != set(group_ids):
        raise ModelGroupPolicyError("group_not_available")
    if any(row["archived_at"] is not None or not bool(row["enabled"]) for row in groups.values()):
        raise ModelGroupPolicyError("group_not_available")
    if operation != "replace" and {str(row["type"]) for row in groups.values()} == {"ordinary_default", "business"}:
        raise ModelGroupPolicyError("model_group_default_business_mutually_exclusive")
    if any(int(groups[group_id]["revision"]) != int(expected_group_revisions[group_id]) for group_id in group_ids):
        raise ModelGroupPolicyError("model_policy_changed")
    if operation in {"add", "replace"}:
        for target_type in {groups[gid]["type"] for gid in requested_group_ids}:
            active = await (await conn.execute(
                """select 1 from runs where tenant_id = %s and model_id = any(%s)
                   and model_group_type is distinct from %s and status not in ('succeeded','failed','cancelled','canceled','dead-letter') limit 1""",
                (tenant_id, model_ids, target_type),
            )).fetchone()
            if active is not None:
                raise ModelGroupPolicyError("model_group_active_runs")
    models_cursor = await conn.execute(
        "select model_id from model_catalog_entries where model_id = any(%s) for update",
        (model_ids,),
    )
    known_models = {str(row["model_id"]) for row in await models_cursor.fetchall()}
    if known_models != set(model_ids):
        raise ModelGroupPolicyError("model_not_available")
    current_cursor = await conn.execute(
        """
        select group_id, model_id, enabled, display_order, priority, weight, revision
        from model_group_models where group_id = any(%s)
        """,
        (group_ids,),
    )
    current_rows = [dict(row) for row in await current_cursor.fetchall()]
    current_by_group: dict[str, dict[str, dict[str, Any]]] = {group_id: {} for group_id in group_ids}
    for row in current_rows:
        current_by_group[str(row["group_id"])][str(row["model_id"])] = row
    if operation in {"add", "replace"}:
        opposite_cursor = await conn.execute(
            """
            select gm.model_id, g.type, g.id
            from model_group_models gm join model_groups g on g.id = gm.group_id
            where g.tenant_id = %s and gm.model_id = any(%s)
            """,
            (tenant_id, model_ids),
        )
        opposite_rows = [dict(row) for row in await opposite_cursor.fetchall()]
        existing_locations: dict[str, list[tuple[str, str]]] = {}
        for row in opposite_rows:
            existing_locations.setdefault(str(row["model_id"]), []).append(
                (str(row["type"]), str(row["id"]))
            )
        for group_id in group_ids:
            target_type = str(groups[group_id]["type"])
            for model_id in model_ids:
                has_opposite = any(
                    existing_group_id != group_id
                    and not (operation == "replace" and existing_group_id in existing_group_ids)
                    and {
                        existing_type,
                        target_type,
                    } == {"ordinary_default", "business"}
                    for existing_type, existing_group_id in existing_locations.get(model_id, [])
                )
                if has_opposite:
                    raise ModelGroupPolicyError("model_group_default_business_mutually_exclusive")
    desired_by_group: dict[str, set[str]] = {}
    for group_id in group_ids:
        current = current_by_group[group_id]
        current_ids = set(current)
        if operation == "add":
            desired_by_group[group_id] = current_ids | (set(model_ids) if group_id in requested_group_ids else set())
        elif operation == "remove":
            desired_by_group[group_id] = current_ids - (set(model_ids) if group_id in requested_group_ids else set())
        elif group_id in requested_group_ids:
            desired_by_group[group_id] = current_ids | set(model_ids)
        else:
            desired_by_group[group_id] = current_ids - set(model_ids)
    changes: list[dict[str, Any]] = []
    for group_id in group_ids:
        current = current_by_group[group_id]
        desired = desired_by_group[group_id]
        projected: dict[str, dict[str, Any]] = {}
        for model_id in sorted(desired):
            old = current.get(model_id)
            projected[model_id] = {
                "model_id": model_id,
                "enabled": bool(old["enabled"]) if old else True,
                "display_order": int(old["display_order"]) if old else 0,
                "priority": int(old["priority"]) if old else 0,
                "weight": int(old["weight"]) if old else 100,
                "revision": int(old["revision"]) if old else 1,
            }
        changed = set(current) != desired
        before = {model_id: dict(row) for model_id, row in current.items()}
        changes.append({
            "group_id": group_id,
            "group_type": str(groups[group_id]["type"]),
            "before": {
                "revision": int(groups[group_id]["revision"]),
                "member_count": int(groups[group_id]["member_count"]),
                "models": list(before.values()),
            },
            "after": {
                "revision": int(groups[group_id]["revision"]) + int(changed),
                "member_count": int(groups[group_id]["member_count"]),
                "models": list(projected.values()),
                "affected_model_ids": sorted(set(before) ^ set(projected)),
            },
            "changed": changed,
        })
    if not preview:
        # Remove before adding so a default/business conversion cannot trip the
        # mutual-exclusion trigger while its old association is still present.
        for group_id, desired in desired_by_group.items():
            current = current_by_group[group_id]
            for model_id in set(current) - desired:
                await conn.execute(
                    "delete from model_group_models where group_id = %s and model_id = %s",
                    (group_id, model_id),
                )
        for group_id, desired in desired_by_group.items():
            current = current_by_group[group_id]
            for model_id in desired:
                old = current.get(model_id)
                await conn.execute(
                    """
                    insert into model_group_models(group_id, model_id, enabled, display_order, priority, weight)
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (group_id, model_id) do nothing
                    """,
                    (group_id, model_id, bool(old["enabled"]) if old else True,
                     int(old["display_order"]) if old else 0, int(old["priority"]) if old else 0,
                     int(old["weight"]) if old else 100),
                )
            if set(current) != desired:
                await conn.execute(
                    "update model_groups set revision = revision + 1, updated_at = now() where tenant_id = %s and id = %s and revision = %s",
                    (tenant_id, group_id, int(groups[group_id]["revision"])),
                )
    return {
        "tenant_id": tenant_id,
        "operation": operation,
        "preview": bool(preview),
        "changed": any(item["changed"] for item in changes),
        "groups": changes,
    }

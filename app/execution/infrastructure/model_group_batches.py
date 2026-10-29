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
        select id, type, enabled, archived_at, revision, default_model_id,
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
    if any(int(groups[group_id]["revision"]) != int(expected_group_revisions[group_id]) for group_id in group_ids):
        raise ModelGroupPolicyError("model_policy_changed")
    models_cursor = await conn.execute(
        "select model_id from model_catalog_entries where model_id = any(%s) and archived_at is null for update",
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
        default_before = groups[group_id].get("default_model_id")
        if default_before and default_before not in desired and desired:
            raise ModelGroupPolicyError("model_group_default_change_required")
        default_after = default_before if default_before in desired else None
        if default_before is None and not any(item["enabled"] for item in current.values()) and desired:
            first_enabled = min((item for item in projected.values() if item["enabled"]), key=lambda item: (item["display_order"], item["model_id"]), default=None)
            default_after = first_enabled["model_id"] if first_enabled else None
        changes.append({
            "group_id": group_id,
            "group_type": str(groups[group_id]["type"]),
            "before": {
                "revision": int(groups[group_id]["revision"]),
                "member_count": int(groups[group_id]["member_count"]),
                "default_model_id": default_before,
                "models": list(before.values()),
            },
            "after": {
                "revision": int(groups[group_id]["revision"]) + int(changed),
                "member_count": int(groups[group_id]["member_count"]),
                "default_model_id": default_after,
                "models": list(projected.values()),
                "affected_model_ids": sorted(set(before) ^ set(projected)),
            },
            "changed": changed,
        })
    if not preview:
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
                    "update model_groups set default_model_id = %s, revision = revision + 1, updated_at = now() where tenant_id = %s and id = %s and revision = %s",
                    (next(item["after"]["default_model_id"] for item in changes if item["group_id"] == group_id), tenant_id, group_id, int(groups[group_id]["revision"])),
                )
    return {
        "tenant_id": tenant_id,
        "operation": operation,
        "preview": bool(preview),
        "changed": any(item["changed"] for item in changes),
        "groups": changes,
    }

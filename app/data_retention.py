"""Bounded, fail-safe PostgreSQL and object-store retention maintenance."""

from __future__ import annotations

import asyncio
import time

from app import repositories
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.settings import get_settings
from app.storage import ObjectStorage


_next_cleanup_at = 0.0


def retention_policy_projection(settings: object) -> dict[str, object]:
    configurable = {
        "run_events": int(getattr(settings, "run_event_retention_days", 0)),
        "context_snapshots": int(getattr(settings, "context_snapshot_retention_days", 0)),
        "audit": int(getattr(settings, "audit_retention_days", 0)),
        "messages": int(getattr(settings, "message_retention_days", 0)),
        "files": int(getattr(settings, "file_retention_days", 0)),
    }
    unsupported = sorted(name for name, days in configurable.items() if days > 0)
    return {
        "artifacts": "expires_at_with_reference_safe_object_outbox",
        "memory": "soft_delete_then_reference_safe_physical_purge",
        "configurable_retention_days": configurable,
        "disabled_fail_safe": sorted(name for name, days in configurable.items() if days <= 0),
        "unsupported_not_implemented": unsupported,
        "runtime_status": {
            name: "unsupported_not_implemented" if days > 0 else "disabled_fail_safe"
            for name, days in configurable.items()
        },
    }


async def run_data_retention_maintenance(
    settings: object | None = None,
    *,
    now: float | None = None,
    storage: ObjectStorage | None = None,
) -> dict[str, object]:
    """Queue, claim, execute, and receipt one bounded cleanup batch."""

    global _next_cleanup_at
    settings = settings or get_settings()
    policy = retention_policy_projection(settings)
    unsupported = list(policy["unsupported_not_implemented"])
    if unsupported:
        return {
            "status": "unsupported_retention_configuration",
            "unsupported_retention_classes": unsupported,
            "deleted_objects": 0,
        }
    enabled = bool(getattr(settings, "data_retention_worker_cleanup_enabled", True))
    interval = float(getattr(settings, "data_retention_worker_cleanup_interval_seconds", 300.0))
    current_time = time.monotonic() if now is None else float(now)
    if not enabled or current_time < _next_cleanup_at:
        return {"status": "disabled" if not enabled else "not_due", "deleted_objects": 0}

    artifact_limit = int(getattr(settings, "artifact_retention_cleanup_limit", 50))
    object_delete_max_attempts = int(
        getattr(settings, "artifact_object_delete_max_attempts", 5)
    )
    object_delete_retry_base_seconds = int(
        getattr(settings, "artifact_object_delete_retry_base_seconds", 60)
    )
    object_delete_retry_cap_seconds = int(
        getattr(settings, "artifact_object_delete_retry_cap_seconds", 3600)
    )
    memory_limit = int(getattr(settings, "memory_physical_purge_limit", 50))
    grace_days = int(getattr(settings, "memory_physical_purge_grace_days", 7))
    async with transaction() as conn:
        queued = await repositories.queue_expired_artifacts_for_deletion(conn, limit=artifact_limit)
        purged_memory = await repositories.purge_deleted_memory_records(
            conn,
            grace_days=grace_days,
            limit=memory_limit,
        )
        tenant_counts: dict[str, dict[str, int]] = {}
        for item in queued:
            tenant_counts.setdefault(str(item["tenant_id"]), {"queued": 0, "purged": 0})["queued"] += 1
        for item in purged_memory:
            tenant_counts.setdefault(str(item["tenant_id"]), {"queued": 0, "purged": 0})["purged"] += 1
        for tenant_id, counts in tenant_counts.items():
            await repositories.append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=None,
                action="worker.data_retention.cleanup",
                target_type="data_retention",
                target_id="default",
                trace_id=standard_trace_id(f"data_retention_{tenant_id}"),
                payload_json={
                    "queued_artifact_count": counts["queued"],
                    "purged_memory_count": counts["purged"],
                    "source": "worker",
                },
            )

    async with transaction() as conn:
        claimed = await repositories.claim_object_deletions(
            conn,
            limit=artifact_limit,
            max_attempts=object_delete_max_attempts,
        )

    object_storage = storage or (ObjectStorage() if claimed else None)
    deleted_objects = 0
    failed_objects = 0
    for item in claimed:
        try:
            assert object_storage is not None
            await asyncio.to_thread(object_storage.delete_object, storage_key=str(item["storage_key"]))
        except Exception as exc:
            async with transaction() as conn:
                await repositories.fail_object_deletion(
                    conn,
                    outbox_id=str(item["id"]),
                    error_code=f"object_delete_{type(exc).__name__}".lower(),
                    max_attempts=object_delete_max_attempts,
                    retry_base_seconds=object_delete_retry_base_seconds,
                    retry_cap_seconds=object_delete_retry_cap_seconds,
                )
            failed_objects += 1
            continue
        async with transaction() as conn:
            completed = await repositories.complete_object_deletion(
                conn,
                outbox_id=str(item["id"]),
                tenant_id=str(item["tenant_id"]),
                artifact_id=str(item["artifact_id"]),
            )
        deleted_objects += int(completed)

    _next_cleanup_at = current_time + interval
    return {
        "status": "completed",
        "queued_artifacts": len(queued),
        "purged_memory": len(purged_memory),
        "claimed_objects": len(claimed),
        "deleted_objects": deleted_objects,
        "failed_objects": failed_objects,
    }

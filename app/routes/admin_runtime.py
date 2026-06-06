from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app import repositories
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text
from app.db import get_pool_status, transaction
from app.queue import get_queue_insight, get_queue_status
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_leases import lease_response
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings

router = APIRouter()

_SUCCESSFUL_PROVIDER_CLEANUP_STATUSES = {"stopped", "not_found"}
_OVERVIEW_FORBIDDEN_KEYS = {"skillid"}
_DATABASE_POOL_CONFIG_KEYS = {"min_size", "max_size", "timeout_seconds", "max_waiting"}
_QUEUE_REASON_VALUES = {
    "queued_behind_existing_work",
    "tenant_quota_full",
    "user_quota_full",
    "worker_available",
    "worker_capacity_full",
    "workers_busy",
}
_QUEUE_BACKPRESSURE_REASON_VALUES = _QUEUE_REASON_VALUES - {"worker_available"}


def _count_by_status(items: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = getattr(item, "status", None) if not isinstance(item, dict) else item.get("status")
        if status is None:
            continue
        status_key = str(status)
        counts[status_key] = counts.get(status_key, 0) + 1
    return counts


def _sanitize_dict(value: object) -> dict[str, object]:
    sanitized = sanitize_public_payload(value if isinstance(value, dict) else {})
    if not isinstance(sanitized, dict):
        return {}
    cleaned = _drop_overview_forbidden_keys(sanitized)
    return cleaned if isinstance(cleaned, dict) else {}


def _drop_overview_forbidden_keys(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
            if normalized_key in _OVERVIEW_FORBIDDEN_KEYS:
                continue
            cleaned[key] = _drop_overview_forbidden_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_overview_forbidden_keys(item) for item in value]
    return value


def _coerce_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sanitize_observability_summary(value: object) -> dict[str, object]:
    summary = _sanitize_dict(value)
    error_types = summary.get("error_types") if isinstance(summary.get("error_types"), dict) else {}
    summary["error_types"] = {
        sanitized_key: _coerce_int(count)
        for key, count in error_types.items()
        if (sanitized_key := sanitize_public_text(key))
    }
    latency = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), dict) else {}
    summary["latency_ms"] = {
        "avg": _coerce_int(latency["avg"]) if latency.get("avg") is not None else None,
        "max": _coerce_int(latency["max"]) if latency.get("max") is not None else None,
    }
    return summary


def _sanitize_database_pool_status(value: object) -> dict[str, object]:
    summary = _sanitize_dict(value)
    configured = summary.get("configured") if isinstance(summary.get("configured"), dict) else {}
    stats = summary.get("stats") if isinstance(summary.get("stats"), dict) else {}
    return {
        "configured": {
            key: configured[key]
            for key in _DATABASE_POOL_CONFIG_KEYS
            if isinstance(configured.get(key), int | float)
        },
        "open": bool(summary.get("open")),
        "stats": {
            str(key): item
            for key, item in stats.items()
            if isinstance(item, int | float) and not isinstance(item, bool)
        },
    }


def _sanitize_admission_summary(value: object) -> dict[str, object]:
    summary = value if isinstance(value, dict) else {}
    users = summary.get("top_users") if isinstance(summary.get("top_users"), list) else []
    top_users: list[dict[str, object]] = []
    for user in users:
        if not isinstance(user, dict) or not user.get("user_id"):
            continue
        user_id = sanitize_public_text(user.get("user_id"))
        if not user_id:
            continue
        top_users.append(
            {
                "user_id": user_id,
                "active": _coerce_int(user.get("active")),
                "saturated": bool(user.get("saturated")),
            }
        )
    return {
        "policy_active": bool(summary.get("policy_active")),
        "max_active_runs_per_user": _coerce_int(summary.get("max_active_runs_per_user")),
        "active_runs": _coerce_int(summary.get("active_runs")),
        "active_users": _coerce_int(summary.get("active_users")),
        "saturated_users": _coerce_int(summary.get("saturated_users")),
        "top_users": top_users,
    }


def _safe_queue_reason(value: object) -> str | None:
    text = sanitize_public_text(value)
    return text if text in _QUEUE_REASON_VALUES else None


def _queue_backpressure_snapshot(queue_insight: object) -> dict[str, object]:
    insight = queue_insight if isinstance(queue_insight, dict) else {}
    capacity = insight.get("capacity") if isinstance(insight.get("capacity"), dict) else {}
    throttling = insight.get("throttling") if isinstance(insight.get("throttling"), dict) else {}
    users = throttling.get("users") if isinstance(throttling.get("users"), dict) else {}
    queue_sample = insight.get("queue_sample") if isinstance(insight.get("queue_sample"), dict) else {}
    saturated_users = sum(
        1
        for user_state in users.values()
        if isinstance(user_state, dict) and bool(user_state.get("processing_saturated"))
    )
    return {
        "reason": _safe_queue_reason(insight.get("reason")),
        "worker_capacity": {
            "max_active_worker_runs": _coerce_int(capacity.get("max_active_worker_runs")),
            "processing_saturated": bool(capacity.get("processing_saturated")),
            "available_worker_slots": (
                _coerce_int(capacity.get("available_worker_slots"))
                if capacity.get("available_worker_slots") is not None
                else None
            ),
        },
        "quota": {
            "tenant_processing_limit": _coerce_int(
                throttling.get("tenant_processing_limit", capacity.get("queue_tenant_processing_limit"))
            ),
            "tenant_processing_saturated": bool(throttling.get("tenant_processing_saturated")),
            "user_processing_limit": _coerce_int(
                throttling.get("user_processing_limit", capacity.get("queue_user_processing_limit"))
            ),
            "saturated_users": saturated_users,
        },
        "sample": {
            "queued_scan_limit": _coerce_int(queue_sample.get("queued_scan_limit")),
            "queued_sampled": _coerce_int(queue_sample.get("queued_sampled")),
            "queued_sample_complete": bool(queue_sample.get("queued_sample_complete")),
        },
    }


def _database_pool_backpressure_snapshot(database_pool: object) -> dict[str, object]:
    pool = database_pool if isinstance(database_pool, dict) else {}
    configured = pool.get("configured") if isinstance(pool.get("configured"), dict) else {}
    stats = pool.get("stats") if isinstance(pool.get("stats"), dict) else {}
    requests_waiting = _coerce_int(stats.get("requests_waiting"))
    max_waiting = _coerce_int(configured.get("max_waiting"))
    return {
        "open": bool(pool.get("open")),
        "requests_waiting": requests_waiting,
        "max_waiting": max_waiting,
        "waiting_saturated": max_waiting > 0 and requests_waiting >= max_waiting,
    }


def _backpressure_snapshot(
    *,
    admission: dict[str, object],
    queue_insight: object,
    database_pool: dict[str, object],
) -> dict[str, object]:
    queue = _queue_backpressure_snapshot(queue_insight)
    pool = _database_pool_backpressure_snapshot(database_pool)
    reasons: list[str] = []
    if _coerce_int(admission.get("saturated_users")) > 0:
        reasons.append("active_run_limit_saturated")
    queue_reason = queue.get("reason")
    if isinstance(queue_reason, str) and queue_reason in _QUEUE_BACKPRESSURE_REASON_VALUES:
        reasons.append(queue_reason)
    worker_capacity = queue["worker_capacity"] if isinstance(queue.get("worker_capacity"), dict) else {}
    if worker_capacity.get("processing_saturated"):
        reasons.append("worker_capacity_saturated")
    quota = queue["quota"] if isinstance(queue.get("quota"), dict) else {}
    if quota.get("tenant_processing_saturated"):
        reasons.append("queue_tenant_quota_saturated")
    if _coerce_int(quota.get("saturated_users")) > 0:
        reasons.append("queue_user_quota_saturated")
    if _coerce_int(pool.get("requests_waiting")) > 0:
        reasons.append("database_pool_waiting")
    if pool.get("waiting_saturated"):
        reasons.append("database_pool_waiting_saturated")
    deduped_reasons = list(dict.fromkeys(reasons))
    return {
        "reasons": deduped_reasons,
        "queue": queue,
        "database_pool": pool,
    }


def _provider_cleanup_failed(cleanup_results: object) -> bool:
    if not isinstance(cleanup_results, list):
        return True
    for result in cleanup_results:
        if isinstance(result, dict):
            return True
        status = getattr(result, "status", None)
        if status not in _SUCCESSFUL_PROVIDER_CLEANUP_STATUSES:
            return True
    return False


def _sandbox_overview(containers: list[object], leases: list[dict[str, object]], lease_history: list[dict[str, object]]) -> dict[str, object]:
    tenant_leases = [lease for lease in leases if lease.get("tenant_id")]
    tenant_lease_history = [lease for lease in lease_history if lease.get("tenant_id")]
    active_containers = [container for container in containers if getattr(container, "status", None) == "running"]
    return {
        "containers": {
            "total": len(containers),
            "running": len(active_containers),
            "by_status": _count_by_status(containers),
            "ephemeral_running": sum(1 for container in active_containers if getattr(container, "sandbox_mode", None) == "ephemeral"),
            "persistent_running": sum(1 for container in active_containers if getattr(container, "sandbox_mode", None) == "persistent"),
        },
        "leases": {
            "active": sum(1 for lease in tenant_leases if lease.get("status") == "active"),
            "released": sum(1 for lease in tenant_lease_history if lease.get("status") == "released"),
            "expired": sum(1 for lease in tenant_lease_history if lease.get("status") == "expired"),
            "history_included": bool(lease_history),
        },
    }


@router.get("/admin/runtime/queue")
async def admin_runtime_queue(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    return {
        "tenant_id": principal.tenant_id,
        "queue": await get_queue_status(),
        "tenant_insight": await get_queue_insight(principal.tenant_id, include_user_breakdown=True),
    }


@router.post("/admin/runtime/multi-agent/dispatch/cleanup")
async def admin_runtime_multi_agent_dispatch_cleanup(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    async with transaction() as conn:
        expired_claims = await repositories.cleanup_expired_multi_agent_dispatch_claims(
            conn,
            tenant_id=principal.tenant_id,
            cleaned_by=principal.user_id,
            limit=100,
        )
    return {
        "tenant_id": principal.tenant_id,
        "expired_count": len(expired_claims),
        "expired_claims": expired_claims,
    }


@router.get("/admin/runtime/containers")
async def admin_runtime_containers(
    include_lease_history: bool = False,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    provider = create_container_provider()
    cleanup_orphan_containers = getattr(provider, "cleanup_orphan_containers", None)
    if cleanup_orphan_containers is not None:
        try:
            cleanup_results = await cleanup_orphan_containers({"tenant_id": principal.tenant_id}, reason="admin_runtime")
        except Exception as exc:
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed") from exc
        if _provider_cleanup_failed(cleanup_results):
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed")
    async with transaction() as conn:
        try:
            await cleanup_expired_sandbox_runtime_leases(
                conn,
                tenant_id=principal.tenant_id,
                provider_factory=create_container_provider,
            )
            await repositories.cleanup_expired_sandbox_leases(conn, tenant_id=principal.tenant_id)
        except SandboxRuntimeCleanupError as exc:
            raise HTTPException(status_code=500, detail="sandbox_runtime_cleanup_failed") from exc
        leases = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status="active")
        lease_history = (
            await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status=None)
            if include_lease_history
            else []
        )
    visible_leases = [lease for lease in leases if lease.get("tenant_id") == principal.tenant_id]
    visible_lease_history = [lease for lease in lease_history if lease.get("tenant_id") == principal.tenant_id]
    containers = await provider.list_runtime_containers({"tenant_id": principal.tenant_id})
    active_containers = [container for container in containers if container.status == "running"]

    payload = {
        "total_active": len(active_containers),
        "ephemeral_containers": sum(1 for container in active_containers if container.sandbox_mode == "ephemeral"),
        "persistent_containers": sum(1 for container in active_containers if container.sandbox_mode == "persistent"),
        "containers": [container.model_dump() for container in containers],
        "sandbox_leases": [lease_response(lease) for lease in visible_leases],
    }
    if include_lease_history:
        payload["sandbox_lease_history"] = [lease_response(lease) for lease in visible_lease_history]
    return payload


@router.get("/admin/runtime/overview")
async def admin_runtime_overview(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return an admin-only same-tenant runtime overview snapshot."""
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    provider = create_container_provider()
    cleanup_orphan_containers = getattr(provider, "cleanup_orphan_containers", None)
    if cleanup_orphan_containers is not None:
        try:
            cleanup_results = await cleanup_orphan_containers({"tenant_id": principal.tenant_id}, reason="admin_runtime")
        except Exception as exc:
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed") from exc
        if _provider_cleanup_failed(cleanup_results):
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed")

    async with transaction() as conn:
        try:
            await cleanup_expired_sandbox_runtime_leases(
                conn,
                tenant_id=principal.tenant_id,
                provider_factory=create_container_provider,
            )
            await repositories.cleanup_expired_sandbox_leases(conn, tenant_id=principal.tenant_id)
        except SandboxRuntimeCleanupError as exc:
            raise HTTPException(status_code=500, detail="sandbox_runtime_cleanup_failed") from exc
        leases = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status="active")
        lease_history = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status=None)

    visible_leases = [lease for lease in leases if lease.get("tenant_id") == principal.tenant_id]
    visible_lease_history = [lease for lease in lease_history if lease.get("tenant_id") == principal.tenant_id]
    containers = await provider.list_runtime_containers({"tenant_id": principal.tenant_id})

    async with transaction() as conn:
        run_summary = await repositories.get_admin_runtime_run_summary(conn, tenant_id=principal.tenant_id, limit=10)
        observability_summary = await repositories.get_admin_runtime_observability_summary(conn, tenant_id=principal.tenant_id)
        admission_summary = await repositories.get_admin_runtime_admission_summary(
            conn,
            tenant_id=principal.tenant_id,
            limit=int(get_settings().max_active_runs_per_user),
        )

    queue_status = await get_queue_status()
    tenant_queue_insight = await get_queue_insight(principal.tenant_id, include_user_breakdown=True)
    database_pool = _sanitize_database_pool_status(get_pool_status())
    admission = _sanitize_admission_summary(admission_summary)

    return {
        "tenant_id": principal.tenant_id,
        "queue": {
            "status": queue_status,
            "tenant_insight": tenant_queue_insight,
        },
        "runs": _sanitize_dict(run_summary),
        "sandbox": _sandbox_overview(containers, visible_leases, visible_lease_history),
        "observability": _sanitize_observability_summary(observability_summary),
        "database_pool": database_pool,
        "admission": admission,
        "backpressure": _backpressure_snapshot(
            admission=admission,
            queue_insight=tenant_queue_insight,
            database_pool=database_pool,
        ),
    }

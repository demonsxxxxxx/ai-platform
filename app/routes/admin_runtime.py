from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app import repositories
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text
from app.db import get_pool_status, transaction
from app.queue import get_queue_insight, get_queue_status
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_leases import lease_response
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases

router = APIRouter()

_SUCCESSFUL_PROVIDER_CLEANUP_STATUSES = {"stopped", "not_found"}
_OVERVIEW_FORBIDDEN_KEYS = {"skillid"}
_DATABASE_POOL_CONFIG_KEYS = {"min_size", "max_size", "timeout_seconds", "max_waiting"}


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
        "tenant_insight": await get_queue_insight(principal.tenant_id),
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

    return {
        "tenant_id": principal.tenant_id,
        "queue": {
            "status": await get_queue_status(),
            "tenant_insight": await get_queue_insight(principal.tenant_id),
        },
        "runs": _sanitize_dict(run_summary),
        "sandbox": _sandbox_overview(containers, visible_leases, visible_lease_history),
        "observability": _sanitize_observability_summary(observability_summary),
        "database_pool": _sanitize_database_pool_status(get_pool_status()),
    }

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app import repositories
from app.db import transaction
from app.queue import get_queue_insight, get_queue_status
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_leases import lease_response
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases

router = APIRouter()


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
        if any(result.status == "failed" for result in cleanup_results):
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

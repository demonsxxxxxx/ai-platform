from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import AdminRunDetailResponse, AdminRunListResponse, RunControlResponse
from app.queue import get_queue_insight, get_run_queue_position, remove_queued_run
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, stop_sandbox_leases
from app.runtime.sandbox.container_provider import create_container_provider
from app.control_plane_contracts import sanitize_public_text
from app.validation import assert_safe_id

router = APIRouter()

QUEUE_VISIBLE_STATUSES = {"queued", "running"}


async def attach_live_queue_context(run: dict, *, tenant_id: str, queue_insight: dict | None = None) -> dict:
    enriched = dict(run)
    enriched["error_code"] = sanitize_public_text(enriched.get("error_code")) or None
    enriched["error_message"] = sanitize_public_text(enriched.get("error_message"))
    status = enriched.get("status")
    if status not in QUEUE_VISIBLE_STATUSES:
        return enriched
    if queue_insight is None:
        queue_insight = await get_queue_insight(tenant_id)
    enriched["queue_insight"] = queue_insight
    if status == "queued":
        enriched["queue_position"] = await get_run_queue_position(
            tenant_id=tenant_id,
            run_id=enriched["run_id"],
        )
    return enriched


@router.get("/admin/runs", response_model=AdminRunListResponse)
async def admin_run_list(
    user_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminRunListResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        user_id = assert_safe_id(user_id, "user_id") if user_id else None
        status = assert_safe_id(status, "status") if status else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        rows = await repositories.list_admin_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            status=status,
            limit=limit,
        )
    queue_insight = await get_queue_insight(principal.tenant_id) if any(
        row.get("status") in QUEUE_VISIBLE_STATUSES for row in rows
    ) else None
    rows = [
        await attach_live_queue_context(row, tenant_id=principal.tenant_id, queue_insight=queue_insight)
        for row in rows
    ]
    return AdminRunListResponse(runs=rows, limit=limit)


@router.post("/admin/runs/{run_id}/cancel", response_model=RunControlResponse, response_model_exclude={"queue_position", "queue_insight"})
async def admin_run_cancel(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        result = await repositories.request_admin_run_cancel(
            conn,
            tenant_id=principal.tenant_id,
            admin_user_id=principal.user_id,
            run_id=run_id,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    try:
        stopped_sandbox_leases = await stop_sandbox_leases(
            result.get("active_sandbox_leases"),
            reason="admin_cancel_requested",
            provider_factory=create_container_provider,
        )
    except SandboxRuntimeCleanupError as exc:
        if exc.stopped_leases:
            async with transaction() as conn:
                await repositories.release_stopped_sandbox_leases_for_cancel(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=str(result["run_id"]),
                    reason="admin_cancel_requested",
                    lease_ids=[str(lease["id"]) for lease in exc.stopped_leases],
                    trace_id=result.get("trace_id"),
                    requested_by_role="admin",
                )
        raise HTTPException(status_code=502, detail="sandbox_runtime_cleanup_failed") from exc
    if stopped_sandbox_leases:
        async with transaction() as conn:
            await repositories.release_stopped_sandbox_leases_for_cancel(
                conn,
                tenant_id=principal.tenant_id,
                run_id=str(result["run_id"]),
                reason="admin_cancel_requested",
                lease_ids=[str(lease["id"]) for lease in stopped_sandbox_leases],
                trace_id=result.get("trace_id"),
                requested_by_role="admin",
            )
    if result["status"] == "cancelled":
        await remove_queued_run(tenant_id=principal.tenant_id, run_id=run_id)
    return RunControlResponse(run_id=result["run_id"], status=result["status"])


@router.get("/admin/runs/{run_id}", response_model=AdminRunDetailResponse)
async def admin_run_detail(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminRunDetailResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        async with transaction() as conn:
            detail = await repositories.get_admin_run_detail(conn, tenant_id=principal.tenant_id, run_id=run_id)
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    detail = dict(detail)
    detail["run"] = await attach_live_queue_context(detail["run"], tenant_id=principal.tenant_id)
    return AdminRunDetailResponse.model_validate(detail)

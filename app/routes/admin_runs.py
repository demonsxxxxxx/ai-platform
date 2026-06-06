from typing import Any

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


def _lease_ids_by_run_id(leases: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for lease in leases:
        run_id = str(lease.get("run_id") or "").strip()
        lease_id = str(lease.get("id") or "").strip()
        if not run_id or not lease_id:
            continue
        grouped.setdefault(run_id, []).append(lease_id)
    return grouped


async def _release_stopped_admin_cancel_leases(
    conn,
    *,
    tenant_id: str,
    leases: list[dict[str, Any]],
    reason: str,
    trace_id: str | None,
) -> None:
    for lease_run_id, lease_ids in _lease_ids_by_run_id(leases).items():
        await repositories.release_stopped_sandbox_leases_for_cancel(
            conn,
            tenant_id=tenant_id,
            run_id=lease_run_id,
            reason=reason,
            lease_ids=lease_ids,
            trace_id=trace_id,
            requested_by_role="admin",
        )


async def _remove_cancelled_queue_payloads(
    *,
    tenant_id: str,
    parent_run_id: str,
    result: dict[str, Any],
) -> list[Exception]:
    failures: list[Exception] = []
    run_ids: list[str] = []
    if result["status"] == "cancelled":
        run_ids.append(parent_run_id)
    run_ids.extend(str(child_run_id) for child_run_id in result.get("queued_child_run_ids") or [])
    for queued_run_id in run_ids:
        try:
            await remove_queued_run(tenant_id=tenant_id, run_id=queued_run_id)
        except Exception as exc:
            failures.append(exc)
    return failures


async def attach_live_queue_context(run: dict, *, tenant_id: str, queue_insight: dict | None = None) -> dict:
    enriched = dict(run)
    enriched["error_code"] = sanitize_public_text(enriched.get("error_code")) or None
    enriched["error_message"] = sanitize_public_text(enriched.get("error_message"))
    status = enriched.get("status")
    if status not in QUEUE_VISIBLE_STATUSES:
        return enriched
    if queue_insight is None:
        queue_insight = await get_queue_insight(tenant_id, include_user_breakdown=True)
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
    queue_insight = await get_queue_insight(principal.tenant_id, include_user_breakdown=True) if any(
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
        if result is not None:
            propagation = await repositories.propagate_multi_agent_parent_cancel(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
                requested_by=principal.user_id,
                requested_by_role="admin",
            )
            if propagation.get("queued_child_run_ids"):
                result["queued_child_run_ids"] = list(propagation["queued_child_run_ids"])
            if propagation.get("active_sandbox_leases"):
                result["active_sandbox_leases"] = [
                    *list(result.get("active_sandbox_leases") or []),
                    *list(propagation["active_sandbox_leases"]),
                ]
            finalized_parent = await repositories.finalize_multi_agent_parent_run_if_ready(
                conn,
                tenant_id=principal.tenant_id,
                parent_run_id=run_id,
            )
            if finalized_parent and finalized_parent.get("status"):
                result["status"] = str(finalized_parent["status"])
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    queue_cleanup_failures = await _remove_cancelled_queue_payloads(
        tenant_id=principal.tenant_id,
        parent_run_id=run_id,
        result=result,
    )
    try:
        stopped_sandbox_leases = await stop_sandbox_leases(
            result.get("active_sandbox_leases"),
            reason="admin_cancel_requested",
            provider_factory=create_container_provider,
        )
    except SandboxRuntimeCleanupError as exc:
        if exc.stopped_leases:
            async with transaction() as conn:
                await _release_stopped_admin_cancel_leases(
                    conn,
                    tenant_id=principal.tenant_id,
                    reason="admin_cancel_requested",
                    leases=exc.stopped_leases,
                    trace_id=result.get("trace_id"),
                )
        raise HTTPException(status_code=502, detail="sandbox_runtime_cleanup_failed") from exc
    if stopped_sandbox_leases:
        async with transaction() as conn:
            await _release_stopped_admin_cancel_leases(
                conn,
                tenant_id=principal.tenant_id,
                reason="admin_cancel_requested",
                leases=stopped_sandbox_leases,
                trace_id=result.get("trace_id"),
            )
    if queue_cleanup_failures:
        raise HTTPException(status_code=502, detail="queue_cleanup_failed") from queue_cleanup_failures[0]
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

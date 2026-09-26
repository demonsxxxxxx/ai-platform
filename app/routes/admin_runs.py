import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import sanitize_public_text
from app.db import transaction
from app.identity.infrastructure import audit_postgres as identity_audit
from app.models import RunControlResponse
from app.platform.postgres import errors as platform_errors
from app.platform.postgres import values as platform_values
from app.queue import get_queue_insight, get_run_queue_position, remove_queued_run
from app.routes.sandbox_runtime_cleanup import (
    SandboxRuntimeCleanupError,
    release_stopped_sandbox_leases_for_cancel,
    stop_sandbox_leases,
)
from app.runs.api import (
    ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
    ADMIN_TRAJECTORY_CONTRACT_VERSION,
    AdminDiagnosticExportTooLarge,
    AdminRunDetailResponse,
    AdminRunDiagnosticsResponse,
    AdminRunListResponse,
    RunCancellationUseCase,
    RunDiagnosticsService,
    build_admin_diagnostic_export,
    build_admin_worker_execution,
    project_admin_trajectory_page,
)
from app.runs.infrastructure import admin_queries_postgres as runs_admin_queries
from app.runs.infrastructure import postgres as runs_postgres
from app.runtime.sandbox.container_provider import create_container_provider
from app.sandbox.infrastructure import leases_postgres as sandbox_leases
from app.streaming.api import (
    V4_METADATA_KEY,
    V4PublicationTransportUnavailable,
    admit_v4_stream,
    publish_run_event,
)
from app.streaming.infrastructure import run_events_postgres as streaming_run_events
from app.validation import assert_safe_id

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_run_cancellation_use_case(request: Request) -> RunCancellationUseCase:
    use_case = getattr(request.app.state, "run_cancellation_use_case", None)
    if type(use_case) is not RunCancellationUseCase:
        raise RuntimeError("run_cancellation_use_case_unavailable")
    return use_case


def _require_run_diagnostics_service(request: Request) -> RunDiagnosticsService:
    service = getattr(request.app.state, "run_diagnostics_service", None)
    if not isinstance(service, RunDiagnosticsService):
        raise RuntimeError("run_diagnostics_service_unavailable")
    return service


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
        await release_stopped_sandbox_leases_for_cancel(
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
    run_id: str,
    result: dict[str, Any],
) -> list[Exception]:
    failures: list[Exception] = []
    if result["status"] == "cancelled":
        try:
            await remove_queued_run(tenant_id=tenant_id, run_id=run_id)
        except Exception as exc:
            failures.append(exc)
    return failures


async def attach_live_queue_context(run: dict, *, tenant_id: str, queue_insight: dict | None = None) -> dict:
    enriched = dict(run)
    enriched["execution_kind"] = enriched.get("execution_kind") or "skill"
    enriched.setdefault("queue_position", None)
    enriched.setdefault("queue_insight", None)
    for field, limit in (
        ("session_title", 240),
        ("task_summary", 240),
        ("user_display_name", 160),
        ("workspace_name", 160),
        ("agent_name", 160),
        ("skill_name", 160),
        ("model_value", 160),
    ):
        value = sanitize_public_text(enriched.get(field)).strip()
        enriched[field] = value[:limit] or None
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
    request: Request,
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
        rows = await runs_admin_queries.list_admin_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            status=status,
            limit=limit,
        )
        metadata = await _require_run_diagnostics_service(request).read_admin_monitor_metadata(
            conn,
            tenant_id=principal.tenant_id,
            run_ids=[str(row.get("run_id") or "") for row in rows],
        )
    rows = [{**row, **metadata.get(str(row.get("run_id") or ""), {})} for row in rows]
    queue_insight = await get_queue_insight(principal.tenant_id, include_user_breakdown=True) if any(
        row.get("status") in QUEUE_VISIBLE_STATUSES for row in rows
    ) else None
    rows = [
        await attach_live_queue_context(row, tenant_id=principal.tenant_id, queue_insight=queue_insight)
        for row in rows
    ]
    return {"runs": rows, "limit": limit}


@router.post("/admin/runs/{run_id}/cancel", response_model=RunControlResponse, response_model_exclude={"queue_position", "queue_insight"})
async def admin_run_cancel(
    run_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(require_principal),
) -> RunControlResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime = request.app.state.run_stream_runtime
    cancellation = await _require_run_cancellation_use_case(request).request_admin_cancel(
        tenant_id=principal.tenant_id,
        admin_user_id=principal.user_id,
        run_id=run_id,
    )
    if cancellation is not None and cancellation.attempt_id:
        try:
            await admit_v4_stream(
                runtime.worker_capabilities,
                tenant_id=principal.tenant_id,
                run_id=cancellation.run_id,
                attempt_id=cancellation.attempt_id,
            )
        except V4PublicationTransportUnavailable as exc:
            logger.warning("Cancellation v4 admission deferred", extra={"run_id": cancellation.run_id, "error": exc.error_code})
        except Exception as exc:  # noqa: BLE001 - cancellation cleanup must outlive publication faults
            logger.warning(
                "Cancellation v4 admission deferred",
                extra={"run_id": cancellation.run_id, "error": type(exc).__name__},
                exc_info=True,
            )
    result = cancellation.as_route_result() if cancellation is not None else None
    if result is not None:
        result.pop("_terminalization_progress", None)
    if cancellation is not None and cancellation.attempt_id:
        try:
            await publish_run_event(
                runtime.worker_capabilities,
                tenant_id=principal.tenant_id,
                run_id=cancellation.run_id,
            )
        except Exception as exc:  # noqa: BLE001 - durable publication retries after cleanup
            logger.warning(
                "Cancellation v4 terminal publication deferred",
                extra={"run_id": cancellation.run_id, "error": type(exc).__name__},
                exc_info=True,
            )
    if result is None:
        raise HTTPException(status_code=404, detail="active_run_not_found")
    queue_cleanup_failures = await _remove_cancelled_queue_payloads(
        tenant_id=principal.tenant_id,
        run_id=run_id,
        result=result,
    )
    try:
        stopped_sandbox_leases = await stop_sandbox_leases(
            result.get("active_sandbox_leases"),
            reason="admin_cancel_requested",
            provider_factory=create_container_provider,
        )
    except SandboxRuntimeCleanupError as exc:
        failed_lease_ids = [str(lease["id"]) for lease in exc.failed_leases if lease.get("id")]
        try:
            async with transaction() as conn:
                if exc.stopped_leases:
                    await _release_stopped_admin_cancel_leases(
                        conn,
                        tenant_id=principal.tenant_id,
                        reason="admin_cancel_requested",
                        leases=exc.stopped_leases,
                        trace_id=result.get("trace_id"),
                    )
                await sandbox_leases.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=str(result["run_id"]),
                    trace_id=result.get("trace_id"),
                    user_id=principal.user_id,
                    requested_by_role="admin",
                    reason="admin_cancel_requested",
                    status="failed",
                    lease_ids=failed_lease_ids,
                    failures=exc.failures,
                )
        except Exception as persistence_exc:
            raise HTTPException(status_code=503, detail="sandbox_cleanup_persistence_unavailable") from persistence_exc
        raise HTTPException(status_code=502, detail="sandbox_runtime_cleanup_failed") from exc
    if stopped_sandbox_leases:
        stopped_lease_ids = [str(lease["id"]) for lease in stopped_sandbox_leases if lease.get("id")]
        try:
            async with transaction() as conn:
                await _release_stopped_admin_cancel_leases(
                    conn,
                    tenant_id=principal.tenant_id,
                    reason="admin_cancel_requested",
                    leases=stopped_sandbox_leases,
                    trace_id=result.get("trace_id"),
                )
                await sandbox_leases.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=str(result["run_id"]),
                    trace_id=result.get("trace_id"),
                    user_id=principal.user_id,
                    requested_by_role="admin",
                    reason="admin_cancel_requested",
                    status="succeeded",
                    lease_ids=stopped_lease_ids,
                    failures=[],
                )
        except Exception as persistence_exc:
            raise HTTPException(status_code=503, detail="sandbox_cleanup_persistence_unavailable") from persistence_exc
    if queue_cleanup_failures:
        raise HTTPException(status_code=502, detail="queue_cleanup_failed") from queue_cleanup_failures[0]
    return RunControlResponse(run_id=result["run_id"], status=result["status"])


@router.get("/admin/runs/{run_id}", response_model=AdminRunDetailResponse)
async def admin_run_detail(
    run_id: str,
    request: Request,
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
            detail = await runs_admin_queries.get_admin_run_detail(conn, tenant_id=principal.tenant_id, run_id=run_id)
            metadata = (
                await _require_run_diagnostics_service(request).read_admin_monitor_metadata(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_ids=[run_id],
                )
                if detail is not None
                else {}
            )
    except platform_errors.RepositoryConflictError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    detail = dict(detail)
    detail["run"] = dict(detail["run"])
    detail["run"].update(metadata.get(run_id, {}))
    detail["worker_execution"] = build_admin_worker_execution(
        detail.get("events", []),
        sanitize_text=sanitize_public_text,
    )
    # The Worker projection needs persisted identities to join message chunks.
    # Keep private events and internal stream metadata out of the browser detail.
    detail["events"] = [
        {
            **event,
            "payload": {
                key: value
                for key, value in event["payload"].items()
                if key != V4_METADATA_KEY
            },
        }
        if isinstance(event.get("payload"), dict)
        else event
        for event in detail.get("events", [])
        if event.get("visible_to_user") is not False
    ]
    detail["run"]["model_output"] = detail["worker_execution"]["response"]
    detail["run"] = await attach_live_queue_context(detail["run"], tenant_id=principal.tenant_id)
    for collection in (
        "events",
        "steps",
        "artifacts",
        "sandbox_leases",
        "skill_snapshots",
        "audit",
    ):
        detail.setdefault(collection, [])
    return detail


@router.get("/admin/runs/{run_id}/trajectory")
async def admin_run_trajectory(
    run_id: str,
    response: Response,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Replay a page of committed, disclosure-safe event facts without execution."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        run = await runs_postgres.get_run(conn, tenant_id=principal.tenant_id, run_id=run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        source_rows = await streaming_run_events.list_run_events(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=limit + 1,
        )
    page_rows = source_rows[:limit]
    projection = project_admin_trajectory_page(page_rows, sanitize_text=sanitize_public_text)
    response.headers["Cache-Control"] = "no-store"
    return {
        "contract_version": ADMIN_TRAJECTORY_CONTRACT_VERSION,
        "run_id": run_id,
        "after_sequence": after_sequence,
        "next_after_sequence": int(page_rows[-1]["sequence"]) if page_rows else after_sequence,
        "has_more": len(source_rows) > limit,
        "source_count": len(page_rows),
        **projection,
    }


@router.get(
    "/admin/runs/{run_id}/diagnostics",
    response_model=AdminRunDiagnosticsResponse,
)
async def admin_run_diagnostics(
    run_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        diagnostics = await _require_run_diagnostics_service(request).read_admin(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
        )
    if diagnostics is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return diagnostics


async def _record_diagnostic_export_audit(
    *,
    principal: AuthPrincipal,
    run_id: str,
    export_id: str,
    diagnostics: dict[str, Any],
    result: str,
    size_bytes: int | None,
) -> None:
    async with transaction() as conn:
        await identity_audit.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="run.diagnostics.export",
            target_type="run",
            target_id=run_id,
            trace_id=diagnostics.get("run", {}).get("trace_id"),
            payload_json={
                "export_id": export_id,
                "diagnostic_id": diagnostics.get("diagnostic_id"),
                "diagnostic_revision": int(diagnostics.get("revision") or 0),
                "coverage": diagnostics.get("coverage"),
                "export_schema_version": ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
                "result": result,
                "size_bytes": size_bytes,
            },
        )


@router.post("/admin/runs/{run_id}/diagnostic-exports")
async def admin_run_diagnostic_export(
    run_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        run_id = assert_safe_id(run_id, "run_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with transaction() as conn:
        await conn.execute("set transaction isolation level repeatable read")
        diagnostics = await _require_run_diagnostics_service(request).read_admin(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
        )
    if diagnostics is None:
        raise HTTPException(status_code=404, detail="run_not_found")

    export_id = platform_values.new_id("rdiagexp")
    generated_at = datetime.now(timezone.utc)
    try:
        package = build_admin_diagnostic_export(
            diagnostics=diagnostics,
            export_id=export_id,
            generated_at=generated_at,
        )
    except AdminDiagnosticExportTooLarge as exc:
        await _record_diagnostic_export_audit(
            principal=principal,
            run_id=run_id,
            export_id=export_id,
            diagnostics=diagnostics,
            result="generation_failed",
            size_bytes=None,
        )
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    await _record_diagnostic_export_audit(
        principal=principal,
        run_id=run_id,
        export_id=export_id,
        diagnostics=diagnostics,
        result="response_started",
        size_bytes=len(package),
    )
    filename = f"run-diagnostics-{run_id}.zip"
    return Response(
        content=package,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Diagnostic-Export-Id": export_id,
        },
    )

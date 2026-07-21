import argparse
import asyncio
import contextlib
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import time
import uuid

from app import queue
from app import repositories
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import close_pool, transaction
from app.executors.registry import AdapterRegistry
from app.multi_agent_dispatcher import dispatch_multi_agent_ready_steps_for_worker
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings
from app.tool_permission_lifecycle import drain_run_tool_permission_terminalization, reconcile_terminalized_permission_run
from app.worker import WorkerOutcome, process_run_payload


_next_memory_cleanup_at = 0.0
logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"


def worker_runtime_heartbeat_path() -> Path:
    """Return the heartbeat path under the runtime-owned temporary directory."""

    return Path(os.environ.get("TMPDIR") or "/tmp") / "ai-platform-worker-runtime-heartbeat.json"


def write_worker_runtime_heartbeat(worker_id: str) -> None:
    payload = {
        "schema_version": "ai-platform.worker-runtime-heartbeat.v1",
        "worker_id": worker_id,
        "runtime_commit": os.environ.get("AI_PLATFORM_RUNTIME_COMMIT", "unknown"),
        "pid": os.getpid(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    heartbeat_path = worker_runtime_heartbeat_path()
    temporary = heartbeat_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(heartbeat_path)


async def _worker_runtime_heartbeat_until_done(worker_id: str, interval_seconds: float = 5.0) -> None:
    while True:
        write_worker_runtime_heartbeat(worker_id)
        await asyncio.sleep(interval_seconds)


async def _heartbeat_until_done(message_id: str, worker_id: str, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await queue.heartbeat_run(message_id, worker_id=worker_id)


async def cleanup_expired_sandbox_leases() -> None:
    async with transaction() as conn:
        await cleanup_expired_sandbox_runtime_leases(conn, provider_factory=create_container_provider)
        await repositories.cleanup_expired_sandbox_leases(conn)


async def cleanup_expired_memory_records_for_worker(settings: object | None = None, *, now: float | None = None) -> list[dict]:
    """Run bounded rotating-scope expired-memory cleanup for worker maintenance when due."""
    global _next_memory_cleanup_at

    settings = settings or get_settings()
    enabled = bool(getattr(settings, "memory_retention_worker_cleanup_enabled", True))
    interval_seconds = float(getattr(settings, "memory_retention_worker_cleanup_interval_seconds", 300.0))
    limit = int(getattr(settings, "memory_retention_worker_cleanup_limit", 200))
    if not enabled or interval_seconds <= 0 or limit <= 0:
        return []

    current_time = time.monotonic() if now is None else float(now)
    if current_time < _next_memory_cleanup_at:
        return []

    async with transaction() as conn:
        rows = await repositories.cleanup_expired_memory_records_across_scopes(
            conn,
            limit=limit,
        )
        rows_by_scope: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            scope = (str(row["tenant_id"]), str(row["workspace_id"]))
            rows_by_scope.setdefault(scope, []).append(row)
        for (tenant_id, workspace_id), scope_rows in rows_by_scope.items():
            await repositories.append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=None,
                action="worker.memory.retention.cleanup",
                target_type="memory_retention",
                target_id=workspace_id,
                trace_id=standard_trace_id(f"{tenant_id}_{workspace_id}"),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "deleted_count": len(scope_rows),
                        "memory_record_ids": [str(row.get("id")) for row in scope_rows],
                        "target_user_ids": sorted({str(row.get("user_id")) for row in scope_rows if row.get("user_id")}),
                        "reason": "retention_expired",
                        "source": "worker",
                    }
                ),
            )
    _next_memory_cleanup_at = current_time + interval_seconds
    return rows


async def progress_pending_tool_permission_terminalizations_for_worker(
    settings: object | None = None,
) -> list[dict[str, object]]:
    """Use worker maintenance as the durable, bounded owner of staged permission drains."""

    settings = settings or get_settings()
    limit = max(1, min(int(getattr(settings, "tool_permission_terminalization_maintenance_limit", 50)), 50))
    async with transaction() as conn:
        candidates = await repositories.list_runs_requiring_tool_permission_terminalization(conn, limit=limit)

    progress: list[dict[str, object]] = []
    for candidate in candidates:
        tenant_id = str(candidate.get("tenant_id") or "")
        run_id = str(candidate.get("run_id") or "")
        if not tenant_id or not run_id:
            continue
        outcome = await drain_run_tool_permission_terminalization(
            tenant_id=tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
            max_batches=4,
        )
        if outcome is not None and outcome.did_transition and outcome.needs_reconcile:
            await reconcile_terminalized_permission_run(
                tenant_id=tenant_id, run_id=run_id, progress=outcome, transaction_factory=transaction
            )
        progress.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "completed": outcome.completed if outcome is not None else False,
                "status": outcome.status if outcome is not None else None,
                "did_transition": outcome.did_transition if outcome is not None else False,
                "needs_reconcile": outcome.needs_reconcile if outcome is not None else False,
            }
        )
    async with transaction() as conn:
        recovery_candidates = await repositories.list_multi_agent_terminal_children_requiring_reconciliation(
            conn,
            limit=limit,
        )
    for candidate in recovery_candidates:
        tenant_id = str(candidate.get("tenant_id") or "")
        run_id = str(candidate.get("run_id") or "")
        if not tenant_id or not run_id:
            continue
        await reconcile_terminalized_permission_run(
            tenant_id=tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
        )
    async with transaction() as conn:
        parent_recovery_candidates = await repositories.list_multi_agent_parent_runs_requiring_finalization(
            conn,
            limit=limit,
        )
    for candidate in parent_recovery_candidates:
        tenant_id = str(candidate.get("tenant_id") or "")
        parent_run_id = str(candidate.get("run_id") or "")
        if not tenant_id or not parent_run_id:
            continue
        async with transaction() as conn:
            await repositories.finalize_multi_agent_parent_run_if_ready(
                conn,
                tenant_id=tenant_id,
                parent_run_id=parent_run_id,
            )
    return progress


async def reconcile_stale_runs_for_worker(
    settings: object | None = None,
) -> list[dict[str, object]]:
    """Recover a bounded batch only after authoritative owner absence is proven twice."""

    settings = settings or get_settings()
    limit = max(1, min(int(getattr(settings, "stale_run_reconciliation_limit", 20)), 50))
    stale_after_seconds = max(
        int(
            getattr(
                settings,
                "stale_run_reconciliation_seconds",
                getattr(settings, "queue_lease_visibility_timeout_seconds", 900),
            )
        ),
        1,
    )
    scan_limit = max(int(getattr(settings, "queue_metadata_fallback_scan_limit", 500)), 1)
    async with transaction() as conn:
        candidates = await repositories.list_stale_run_reconciliation_candidates(
            conn,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )

    results: list[dict[str, object]] = []
    for candidate in candidates:
        tenant_id = str(candidate.get("tenant_id") or "")
        workspace_id = str(candidate.get("workspace_id") or "")
        user_id = candidate.get("user_id")
        run_id = str(candidate.get("run_id") or "")
        expected_status = str(candidate.get("status") or "")
        if not tenant_id or not workspace_id or not run_id or expected_status not in {"queued", "running"}:
            continue
        try:
            no_owner = await queue.run_has_no_queue_owner(
                tenant_id=tenant_id,
                run_id=run_id,
                scan_limit=scan_limit,
            )
        except Exception:
            logger.exception("Stale run queue ownership inspection failed", extra={"run_id": run_id})
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "owner_unknown", "did_transition": False})
            continue
        if not no_owner:
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "owned", "did_transition": False})
            continue

        terminal_status = "cancelled" if candidate.get("cancel_requested_at") else "failed"
        error_code = None if terminal_status == "cancelled" else "stale_run_interrupted"
        error_message = (
            None
            if terminal_status == "cancelled"
            else "Run interrupted because no live execution owner remains."
        )
        async with transaction() as conn:
            try:
                no_owner = await queue.run_has_no_queue_owner(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    scan_limit=scan_limit,
                )
            except Exception:
                logger.exception("Stale run queue ownership recheck failed", extra={"run_id": run_id})
                no_owner = False
            staged = (
                await repositories.stage_stale_run_reconciliation(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=str(user_id) if user_id is not None else None,
                    run_id=run_id,
                    expected_status=expected_status,
                    stale_before=candidate.get("stale_before"),
                    terminal_status=terminal_status,
                    error_code=error_code,
                    error_message=error_message,
                )
                if no_owner
                else None
            )
        if not no_owner:
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "owned", "did_transition": False})
            continue
        if staged is None:
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "cas_lost", "did_transition": False})
            continue

        outcome = await drain_run_tool_permission_terminalization(
            tenant_id=tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
            max_batches=4,
        )
        if outcome is not None and outcome.did_transition and outcome.needs_reconcile:
            await reconcile_terminalized_permission_run(
                tenant_id=tenant_id,
                run_id=run_id,
                progress=outcome,
                transaction_factory=transaction,
            )
        results.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "status": outcome.status if outcome is not None else terminal_status,
                "did_transition": outcome.did_transition if outcome is not None else False,
            }
        )
    return results


async def run_worker_maintenance(settings: object | None = None) -> None:
    settings = settings or get_settings()
    await cleanup_expired_sandbox_leases()
    await cleanup_expired_memory_records_for_worker(settings)
    await progress_pending_tool_permission_terminalizations_for_worker(settings)
    await dispatch_multi_agent_ready_steps_for_worker(settings)
    await queue.reclaim_expired_leases(
        visibility_timeout_seconds=int(getattr(settings, "queue_lease_visibility_timeout_seconds", 900))
    )
    await reconcile_stale_runs_for_worker(settings)


def _worker_maintenance_interval_seconds(settings: object) -> float:
    try:
        interval = float(getattr(settings, "worker_maintenance_interval_seconds", 30.0))
    except (TypeError, ValueError):
        return 30.0
    return max(interval, 0.0)


async def _maintenance_until_done(settings: object, interval_seconds: float) -> None:
    if interval_seconds <= 0:
        return
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_worker_maintenance(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker background maintenance failed")


async def run_once(
    registry: AdapterRegistry | None = None,
    timeout_seconds: int = 5,
    *,
    worker_id: str | None = None,
    heartbeat_interval_seconds: float = 10.0,
    run_initial_maintenance: bool = True,
    run_background_maintenance: bool = True,
) -> WorkerOutcome:
    resolved_worker_id = worker_id or default_worker_id()
    settings = get_settings()
    if run_initial_maintenance:
        await run_worker_maintenance(settings)
    message = await queue.lease_run(
        timeout_seconds=timeout_seconds,
        worker_id=resolved_worker_id,
        max_processing_runs=settings.max_active_worker_runs,
        tenant_processing_limit=getattr(settings, "queue_tenant_processing_limit", 0),
        user_processing_limit=getattr(settings, "queue_user_processing_limit", 0),
        lease_scan_limit=getattr(settings, "queue_lease_scan_limit", 50),
    )
    if message is None:
        return WorkerOutcome(status="idle", run_id=None)

    heartbeat_task = asyncio.create_task(
        _heartbeat_until_done(message.message_id, resolved_worker_id, heartbeat_interval_seconds)
    )
    maintenance_task = (
        asyncio.create_task(
            _maintenance_until_done(settings, _worker_maintenance_interval_seconds(settings))
        )
        if run_background_maintenance
        else None
    )
    try:
        try:
            outcome = await process_run_payload(message.payload, registry=registry, worker_id=resolved_worker_id)
        except Exception as exc:
            outcome = WorkerOutcome(
                status="dead_letter",
                run_id=message.payload.get("run_id"),
                error_code="worker_process_exception",
                error_message=str(exc),
            )
    finally:
        tasks = [heartbeat_task]
        if maintenance_task is not None:
            tasks.append(maintenance_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
    if outcome.status in {"succeeded", "failed", "skipped", "cancelled"}:
        await queue.ack_run(message.raw, message_id=message.message_id)
    else:
        await queue.fail_leased_run(
            message.raw,
            error_code=outcome.error_code or "worker_unhandled",
            error_message=outcome.error_message or "Worker could not process leased payload",
            message_id=message.message_id,
            worker_id=resolved_worker_id,
        )
    return outcome


async def run_forever(poll_timeout_seconds: int = 5, idle_sleep_seconds: float = 0.5) -> None:
    registry = AdapterRegistry()
    worker_id = default_worker_id()
    heartbeat_task = asyncio.create_task(
        _worker_runtime_heartbeat_until_done(f"{socket.gethostname()}:{os.getpid()}"),
        name="ai-platform-worker-runtime-heartbeat",
    )
    try:
        while True:
            try:
                outcome = await run_once(registry=registry, timeout_seconds=poll_timeout_seconds, worker_id=worker_id)
            except Exception:
                logger.exception("Worker iteration failed")
                await asyncio.sleep(idle_sleep_seconds)
                continue
            if outcome.status == "idle":
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        if not heartbeat_task.done():
            heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await close_pool()


async def _run_worker_slot(
    *,
    worker_id: str,
    poll_timeout_seconds: int,
    idle_sleep_seconds: float,
) -> None:
    registry = AdapterRegistry()
    while True:
        try:
            outcome = await run_once(
                registry=registry,
                timeout_seconds=poll_timeout_seconds,
                worker_id=worker_id,
                run_initial_maintenance=False,
                run_background_maintenance=False,
            )
        except Exception:
            logger.exception("Worker slot iteration failed")
            await asyncio.sleep(idle_sleep_seconds)
            continue
        if outcome.status == "idle":
            await asyncio.sleep(idle_sleep_seconds)


async def run_worker_pool(
    *,
    worker_count: int,
    poll_timeout_seconds: int = 5,
    idle_sleep_seconds: float = 0.5,
) -> None:
    resolved_worker_count = max(int(worker_count), 1)
    if resolved_worker_count == 1:
        await run_forever(poll_timeout_seconds=poll_timeout_seconds, idle_sleep_seconds=idle_sleep_seconds)
        return

    settings = get_settings()
    process_worker_id = f"{socket.gethostname()}:{os.getpid()}"
    await run_worker_maintenance(settings)
    registry = AdapterRegistry()
    maintenance_task = asyncio.create_task(
        _maintenance_until_done(settings, _worker_maintenance_interval_seconds(settings)),
        name="ai-platform-worker-maintenance",
    )
    heartbeat_task = asyncio.create_task(
        _worker_runtime_heartbeat_until_done(process_worker_id),
        name="ai-platform-worker-runtime-heartbeat",
    )
    tasks = [
        asyncio.create_task(
            _run_worker_slot(
                worker_id=default_worker_id(),
                poll_timeout_seconds=poll_timeout_seconds,
                idle_sleep_seconds=idle_sleep_seconds,
            ),
            name=f"ai-platform-worker-{index + 1}",
        )
        for index in range(resolved_worker_count)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in [*tasks, maintenance_task, heartbeat_task]:
            if not task.done():
                task.cancel()
        for task in [*tasks, maintenance_task, heartbeat_task]:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await close_pool()


async def run_once_and_close(timeout_seconds: int) -> WorkerOutcome:
    try:
        return await run_once(timeout_seconds=timeout_seconds)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Platform worker")
    parser.add_argument("--once", action="store_true", help="Process a single leased run then exit")
    parser.add_argument("--timeout", type=int, default=5, help="Queue lease timeout in seconds")
    args = parser.parse_args()

    if args.once:
        outcome = asyncio.run(run_once_and_close(timeout_seconds=args.timeout))
        print(outcome)
        return
    settings = get_settings()
    asyncio.run(run_worker_pool(worker_count=settings.worker_concurrency, poll_timeout_seconds=args.timeout))


if __name__ == "__main__":
    main()

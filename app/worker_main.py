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
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text, standard_trace_id
from app.db import close_pool, transaction
from app.executors.registry import AdapterRegistry
from app.multi_agent_dispatcher import dispatch_multi_agent_ready_steps_for_worker
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings
from app.tool_permission_lifecycle import drain_run_tool_permission_terminalization, reconcile_terminalized_permission_run
from app.worker import WorkerOutcome, parse_leased_queue_envelope, process_run_payload


_next_memory_cleanup_at = 0.0
logger = logging.getLogger(__name__)
_CANCEL_REQUESTED_ORPHAN_RECONCILIATION_SECONDS = 5


class ReconciliationFenceLost(RuntimeError):
    """The worker can no longer prove exclusive ownership of one stale run."""


class _ReconciliationFenceGuard:
    """Keep a reconciliation fence live for one bounded terminalization attempt."""

    def __init__(self, fence: queue.RunReconciliationFence, *, ttl_seconds: int) -> None:
        self._fence = fence
        self._ttl_seconds = max(int(ttl_seconds), 1)
        self._renew_interval_seconds = min(max(self._ttl_seconds / 3, 1.0), 10.0)
        self._stop = asyncio.Event()
        self._lost = False
        self._renewal_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "_ReconciliationFenceGuard":
        await self.ensure_live()
        self._renewal_task = asyncio.create_task(self._renew_until_done())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal_task
        return False

    async def _renew_once(self) -> bool:
        if self._lost:
            return False
        try:
            renewed = await queue.renew_run_reconciliation_fence(
                self._fence,
                ttl_seconds=self._ttl_seconds,
            )
        except Exception:
            logger.exception(
                "Stale run queue fence renewal failed",
                extra={"run_id": self._fence.run_id},
            )
            self._lost = True
            return False
        if not renewed:
            logger.warning(
                "Stale run queue fence owner token lost",
                extra={"run_id": self._fence.run_id},
            )
            self._lost = True
        return renewed

    async def _renew_until_done(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._renew_interval_seconds)
                return
            except TimeoutError:
                if not await self._renew_once():
                    return

    async def ensure_live(self) -> None:
        if not await self._renew_once():
            raise ReconciliationFenceLost(self._fence.run_id)

    async def release_if_live(self) -> bool:
        if self._lost:
            return False
        try:
            return await queue.release_run_reconciliation_fence(self._fence)
        except Exception:
            logger.exception(
                "Stale run queue fence release failed",
                extra={"run_id": self._fence.run_id},
            )
            return False


def _fenced_transaction_factory(fence_guard: _ReconciliationFenceGuard):
    """Require a current fence token before each durable transaction commits."""

    @contextlib.asynccontextmanager
    async def fenced_transaction():
        await fence_guard.ensure_live()
        async with transaction() as conn:
            yield conn
            # Raising here makes the surrounding transaction roll back rather
            # than commit an intent or terminal transition after token loss.
            await fence_guard.ensure_live()

    return fenced_transaction


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


async def _heartbeat_until_done(
    message_id: str,
    worker_id: str,
    interval_seconds: float,
    ownership_lost: asyncio.Event,
) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            if not await queue.heartbeat_run(message_id, worker_id=worker_id):
                ownership_lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        ownership_lost.set()


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
    """Recover a bounded batch while one atomic queue fence excludes new owners."""

    settings = settings or get_settings()
    limit = int(settings.stale_run_reconciliation_limit)
    stale_after_seconds = int(settings.stale_run_reconciliation_seconds)
    cancel_requested_after_seconds = _CANCEL_REQUESTED_ORPHAN_RECONCILIATION_SECONDS
    scan_limit = int(settings.queue_metadata_fallback_scan_limit)
    fence_ttl_seconds = int(settings.stale_run_reconciliation_fence_ttl_seconds)
    async with transaction() as conn:
        candidates = await repositories.list_stale_run_reconciliation_candidates(
            conn,
            stale_after_seconds=stale_after_seconds,
            cancel_requested_after_seconds=cancel_requested_after_seconds,
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
            fence = await queue.acquire_run_reconciliation_fence(
                tenant_id=tenant_id,
                run_id=run_id,
                scan_limit=scan_limit,
                ttl_seconds=fence_ttl_seconds,
            )
        except Exception:
            logger.exception("Stale run queue fence acquisition failed", extra={"run_id": run_id})
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "owner_unknown", "did_transition": False})
            continue
        if fence is None:
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "owned", "did_transition": False})
            continue

        terminal_status = "cancelled" if candidate.get("cancel_requested_at") else "failed"
        error_code = None if terminal_status == "cancelled" else "stale_run_interrupted"
        error_message = (
            None
            if terminal_status == "cancelled"
            else "Run interrupted because no live execution owner remains."
        )
        try:
            async with _ReconciliationFenceGuard(fence, ttl_seconds=fence_ttl_seconds) as fence_guard:
                fenced_transaction = _fenced_transaction_factory(fence_guard)
                try:
                    async with fenced_transaction() as conn:
                        staged = await repositories.stage_stale_run_reconciliation(
                            conn,
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            user_id=str(user_id) if user_id is not None else None,
                            run_id=run_id,
                            expected_status=expected_status,
                            stale_before=candidate.get("stale_before"),
                            cancel_requested_before=candidate.get("cancel_requested_before"),
                            terminal_status=terminal_status,
                            error_code=error_code,
                            error_message=error_message,
                        )
                except ReconciliationFenceLost:
                    results.append(
                        {"tenant_id": tenant_id, "run_id": run_id, "status": "fence_renewal_failed", "did_transition": False}
                    )
                    continue
                except Exception:
                    logger.exception("Stale run DB reconciliation failed with fence retained", extra={"run_id": run_id})
                    results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "db_unknown", "did_transition": False})
                    continue
                if staged is None:
                    await fence_guard.release_if_live()
                    results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "cas_lost", "did_transition": False})
                    continue

                try:
                    await fence_guard.ensure_live()
                    outcome = await drain_run_tool_permission_terminalization(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        transaction_factory=fenced_transaction,
                        max_batches=4,
                    )
                    await fence_guard.ensure_live()
                    if outcome is not None and outcome.did_transition and outcome.needs_reconcile:
                        await fence_guard.ensure_live()
                        await reconcile_terminalized_permission_run(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            progress=outcome,
                            transaction_factory=fenced_transaction,
                        )
                        await fence_guard.ensure_live()
                except ReconciliationFenceLost:
                    results.append(
                        {"tenant_id": tenant_id, "run_id": run_id, "status": "fence_renewal_failed", "did_transition": False}
                    )
                    continue
                except Exception:
                    logger.exception("Stale run permission drain failed with fence retained", extra={"run_id": run_id})
                    results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "drain_unknown", "did_transition": False})
                    continue
                if outcome is not None and outcome.completed and outcome.is_terminal():
                    try:
                        await fence_guard.ensure_live()
                    except ReconciliationFenceLost:
                        results.append(
                            {"tenant_id": tenant_id, "run_id": run_id, "status": "fence_renewal_failed", "did_transition": False}
                        )
                        continue
                    await fence_guard.release_if_live()
                results.append(
                    {
                        "tenant_id": tenant_id,
                        "run_id": run_id,
                        "status": outcome.status if outcome is not None else terminal_status,
                        "did_transition": outcome.did_transition if outcome is not None else False,
                    }
                )
        except ReconciliationFenceLost:
            results.append({"tenant_id": tenant_id, "run_id": run_id, "status": "fence_renewal_failed", "did_transition": False})
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


async def _terminalize_escaped_process_exception(
    message: queue.QueueMessage,
    worker_id: str,
    exc: Exception,
) -> WorkerOutcome:
    """Converge one valid claimed run after processing escapes its normal terminal path."""

    try:
        payload = parse_leased_queue_envelope(message.payload).payload
    except Exception:
        raw_run_id = message.payload.get("run_id")
        return WorkerOutcome(
            status="dead_letter",
            run_id=str(raw_run_id) if isinstance(raw_run_id, str) else None,
            error_code="worker_process_exception",
            error_message=sanitize_public_text(str(exc)) or "Worker processing failed unexpectedly.",
        )

    run_id = payload.run_id
    error_code = "worker_process_exception"
    error_message = sanitize_public_text(str(exc)) or "Worker processing failed unexpectedly."
    progress = None
    if not (await queue.verify_lease_ownership(message, worker_id=worker_id)).succeeded:
        return _queue_ownership_lost_outcome(run_id)
    async with transaction() as conn:
        if not (await queue.verify_lease_ownership(message, worker_id=worker_id)).succeeded:
            return _queue_ownership_lost_outcome(run_id)
        locked_run = await repositories.get_run(
            conn,
            tenant_id=payload.tenant_id,
            run_id=run_id,
            for_update=True,
        )
        if locked_run is None:
            return WorkerOutcome("dead_letter", run_id, error_code, error_message)
        locked_identity = {
            "tenant_id": str(locked_run.get("tenant_id") or ""),
            "workspace_id": str(locked_run.get("workspace_id") or ""),
            "user_id": str(locked_run.get("user_id") or ""),
            "session_id": str(locked_run.get("session_id") or ""),
            "run_id": str(locked_run.get("id") or ""),
            "agent_id": str(locked_run.get("agent_id") or ""),
            "skill_id": str(locked_run.get("skill_id") or ""),
        }
        payload_identity = {
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "agent_id": payload.agent_id,
            "skill_id": payload.skill_id,
        }
        if locked_identity != payload_identity:
            return WorkerOutcome("dead_letter", run_id, error_code, error_message)
        current_status = str(locked_run.get("status") or "")
        if current_status in {"succeeded", "failed", "cancelled"}:
            if not (await queue.verify_lease_ownership(message, worker_id=worker_id)).succeeded:
                return _queue_ownership_lost_outcome(run_id)
            return WorkerOutcome(
                current_status,
                run_id,
                str(locked_run.get("error_code") or "") or None,
                sanitize_public_text(locked_run.get("error_message")) or None,
            )
        cancel_requested = bool(locked_run.get("cancel_requested_at")) or str(
            locked_run.get("permission_terminalization_target") or ""
        ) in {"cancel_requested", "cancelled"}
        if cancel_requested:
            progress = await repositories.cancel_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                result_json={"message": "任务已取消"},
            )
        else:
            progress = await repositories.fail_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                error_code=error_code,
                error_message=error_message,
                result_json={"message": "Worker processing failed unexpectedly."},
            )
        if not (await queue.verify_lease_ownership(message, worker_id=worker_id)).succeeded:
            raise _EscapedTerminalizationOwnershipLost(run_id)

    if progress is None or not progress.is_terminal():
        progress = await drain_run_tool_permission_terminalization(
            tenant_id=payload.tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
            max_batches=4,
        )
    if progress is not None and progress.did_transition and progress.needs_reconcile:
        try:
            await reconcile_terminalized_permission_run(
                tenant_id=payload.tenant_id,
                run_id=run_id,
                progress=progress,
                transaction_factory=transaction,
            )
        except Exception:
            logger.exception(
                "Worker process exception terminalized before child reconciliation completed",
                extra={"run_id": run_id},
            )
    if progress is not None and progress.is_terminal():
        terminal_status = str(progress.status)
        return WorkerOutcome(
            terminal_status,
            run_id,
            error_code if terminal_status == "failed" else None,
            error_message if terminal_status == "failed" else None,
        )
    return WorkerOutcome("dead_letter", run_id, error_code, error_message)


class _EscapedTerminalizationOwnershipLost(RuntimeError):
    """Abort the SQL transaction when its queue lease proof becomes stale."""


def _queue_ownership_lost_outcome(run_id: str | None) -> WorkerOutcome:
    return WorkerOutcome(
        status="ownership_lost",
        run_id=run_id,
        error_code="queue_ownership_lost",
        error_message="Queue execution ownership was lost.",
    )


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

    ownership_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_until_done(
            message.message_id,
            resolved_worker_id,
            heartbeat_interval_seconds,
            ownership_lost,
        )
    )
    maintenance_task = (
        asyncio.create_task(
            _maintenance_until_done(settings, _worker_maintenance_interval_seconds(settings))
        )
        if run_background_maintenance
        else None
    )

    async def process_leased_message() -> WorkerOutcome:
        try:
            return await process_run_payload(message.payload, registry=registry, worker_id=resolved_worker_id)
        except Exception as exc:
            logger.exception(
                "Worker payload processing escaped its terminal path",
                extra={"run_id": message.payload.get("run_id")},
            )
            try:
                return await _terminalize_escaped_process_exception(message, resolved_worker_id, exc)
            except _EscapedTerminalizationOwnershipLost:
                return _queue_ownership_lost_outcome(message.payload.get("run_id"))
            except Exception:
                logger.exception(
                    "Worker process exception terminalization failed",
                    extra={"run_id": message.payload.get("run_id")},
                )
                return WorkerOutcome(
                    status="dead_letter",
                    run_id=message.payload.get("run_id"),
                    error_code="worker_process_exception",
                    error_message=sanitize_public_text(str(exc)) or "Worker processing failed unexpectedly.",
                )

    processing_task = asyncio.create_task(process_leased_message())
    ownership_task = asyncio.create_task(ownership_lost.wait())
    try:
        await asyncio.wait(
            {processing_task, ownership_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat_task.done() and not heartbeat_task.cancelled():
            # A heartbeat loop only terminates after ownership loss or a fail-closed IO error.
            heartbeat_task.exception()
            ownership_lost.set()
        if ownership_lost.is_set():
            processing_task.cancel()
            await asyncio.gather(processing_task, return_exceptions=True)
            return _queue_ownership_lost_outcome(message.payload.get("run_id"))
        outcome = processing_task.result()
    finally:
        tasks = [heartbeat_task, ownership_task, processing_task]
        if maintenance_task is not None:
            tasks.append(maintenance_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if ownership_lost.is_set():
        return _queue_ownership_lost_outcome(message.payload.get("run_id"))
    if outcome.status == "ownership_lost":
        return outcome
    if outcome.status in {"succeeded", "failed", "skipped", "cancelled"}:
        try:
            mutation = await queue.ack_run(message.raw, message_id=message.message_id)
        except Exception:
            return _queue_ownership_lost_outcome(message.payload.get("run_id"))
    else:
        try:
            mutation = await queue.fail_leased_run(
                message.raw,
                error_code=outcome.error_code or "worker_unhandled",
                error_message=outcome.error_message or "Worker could not process leased payload",
                message_id=message.message_id,
                worker_id=resolved_worker_id,
            )
        except Exception:
            return _queue_ownership_lost_outcome(message.payload.get("run_id"))
    if not isinstance(mutation, queue.LeaseMutationOutcome) or not mutation.succeeded:
        return _queue_ownership_lost_outcome(message.payload.get("run_id"))
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

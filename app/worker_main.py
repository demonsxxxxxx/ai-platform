import argparse
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import socket
import time
import uuid

from app import queue
from app import repositories
from app.bootstrap.model_services import configure_model_services
from app.bootstrap.streaming import build_worker_v4_runtime
from app.bootstrap.worker_maintenance import (
    close_runtime_clients as _close_runtime_clients,
    maintenance_until_done,
    run_maintenance_phases,
    worker_maintenance_interval_seconds as _worker_maintenance_interval_seconds,
)
from app.execution.api import WorkerQueueLease, stage_stale_run_reconciliation
from app.control_plane_contracts import (
    RUN_EXECUTION_KIND_SKILL,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.data_retention import run_data_retention_maintenance
from app.db import transaction
from app.executors.registry import AdapterRegistry
from app.executor_reconciler import run_executor_terminal_reconciler
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_runtime_cleanup import (
    SandboxRuntimeCleanupError,
    cleanup_expired_sandbox_leases as _cleanup_expired_sandbox_lease_records,
    cleanup_expired_sandbox_runtime_leases,
)
from app.runs.api import (
    assert_worker_run_attempt_current,
    get_latest_run_attempt,
    heartbeat_worker_run_attempt,
    prepare_stale_run_attempt_reconciliation,
    request_run_attempt_cancel,
    run_attempt_id_for_queue_attempt,
    terminalize_latest_run_attempt,
    terminalize_run_attempt,
)
from app.schema_migrations import require_schema_current
from app.settings import get_settings
from app.tool_permission_lifecycle import (
    cancel_run_with_v4,
    drain_run_tool_permission_terminalization,
    fail_run_with_v4,
    reconcile_terminalized_permission_run,
)
from app.worker import WorkerOutcome, parse_leased_queue_envelope, process_run_payload
from app.streaming.api import (
    WorkerV4Capabilities,
    publish_due_v4_events,
    publish_pending_admissions,
    publish_pending_run_terminal,
)


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
    message: queue.QueueMessage,
    worker_id: str,
    interval_seconds: float,
    visibility_timeout_seconds: int,
    ownership_lost: asyncio.Event,
) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            heartbeat = await queue.heartbeat_run(
                message.message_id,
                worker_id=worker_id,
            )
            if not heartbeat.succeeded or heartbeat.heartbeat_at is None:
                ownership_lost.set()
                return
            last_heartbeat_at = datetime.fromtimestamp(
                heartbeat.heartbeat_at,
                tz=timezone.utc,
            )
            async with transaction() as conn:
                await heartbeat_worker_run_attempt(
                    conn,
                    tenant_id=str(message.payload.get("tenant_id") or ""),
                    run_id=str(message.payload.get("run_id") or ""),
                    queue_attempt_id=message.attempt_id,
                    queue_message_id=message.queue_message_id,
                    worker_id=worker_id,
                    last_heartbeat_at=last_heartbeat_at,
                    lease_expires_at=last_heartbeat_at
                    + timedelta(seconds=visibility_timeout_seconds),
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        ownership_lost.set()


def _durable_queue_lease(
    message: queue.QueueMessage,
    *,
    visibility_timeout_seconds: int,
) -> WorkerQueueLease | None:
    if message.leased_at is None:
        return None
    if visibility_timeout_seconds <= 0:
        raise ValueError("queue_lease_visibility_timeout_invalid")
    last_heartbeat_at = datetime.fromtimestamp(message.leased_at, tz=timezone.utc)
    return WorkerQueueLease(
        queue_message_id=message.queue_message_id,
        last_heartbeat_at=last_heartbeat_at,
        lease_expires_at=last_heartbeat_at
        + timedelta(seconds=visibility_timeout_seconds),
    )


async def cleanup_expired_sandbox_leases() -> None:
    async with transaction() as conn:
        try:
            await cleanup_expired_sandbox_runtime_leases(
                conn,
                provider_factory=create_container_provider,
            )
        except SandboxRuntimeCleanupError:
            logger.exception("Sandbox runtime cleanup maintenance failed")
        await _cleanup_expired_sandbox_lease_records(conn)


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
    *,
    v4_capabilities: WorkerV4Capabilities,
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
        async with transaction() as conn:
            run = await repositories.get_run(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                for_update=True,
            )
            attempt = await get_latest_run_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                for_update=True,
            )
        outcome = await drain_run_tool_permission_terminalization(
            tenant_id=tenant_id,
            run_id=run_id,
            capabilities=v4_capabilities,
            transaction_factory=transaction,
            max_batches=4,
            attempt_id=str((attempt or {}).get("id") or "") or None,
            attempt_error_code=(
                str((run or {}).get("permission_terminalization_error_code") or "")
                or None
            ),
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
            finalized = await repositories.finalize_multi_agent_parent_run_if_ready(
                conn,
                tenant_id=tenant_id,
                parent_run_id=parent_run_id,
            )
            if finalized is not None:
                parent_run = await repositories.get_run(
                    conn,
                    tenant_id=tenant_id,
                    run_id=parent_run_id,
                    for_update=True,
                )
                parent_status = str(finalized.get("status") or "")
                await terminalize_latest_run_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=parent_run_id,
                    status=parent_status,
                    terminal_reason=f"multi_agent_parent_{parent_status}",
                    error_code=(
                        str((parent_run or {}).get("error_code") or "") or None
                        if parent_status == "failed"
                        else None
                    ),
                )
    return progress


async def reconcile_stale_runs_for_worker(
    settings: object | None = None,
    *,
    v4_capabilities: WorkerV4Capabilities,
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
        attempt_id: str | None = None
        try:
            async with _ReconciliationFenceGuard(fence, ttl_seconds=fence_ttl_seconds) as fence_guard:
                fenced_transaction = _fenced_transaction_factory(fence_guard)
                try:
                    async with fenced_transaction() as conn:
                        staged = await stage_stale_run_reconciliation(
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
                            append_event=repositories.append_event,
                            append_audit_log=repositories.append_audit_log,
                        )
                        if staged is not None:
                            attempt = await prepare_stale_run_attempt_reconciliation(
                                conn,
                                tenant_id=tenant_id,
                                run_id=run_id,
                                terminal_status=terminal_status,
                                reconciler_id="stale-run-maintenance",
                            )
                            attempt_id = (
                                str(attempt.get("id") or "")
                                if attempt is not None
                                else None
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
                        capabilities=v4_capabilities,
                        transaction_factory=fenced_transaction,
                        max_batches=4,
                        attempt_id=attempt_id,
                        attempt_error_code=error_code,
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


async def run_worker_maintenance(
    settings: object | None = None,
    *,
    v4_capabilities: WorkerV4Capabilities | None = None,
) -> None:
    settings = settings or get_settings()
    if v4_capabilities is None:
        raise RuntimeError("worker_v4_capabilities_unavailable")
    phases = {
        "sandbox_cleanup": cleanup_expired_sandbox_leases,
        "memory_cleanup": lambda: cleanup_expired_memory_records_for_worker(settings),
        "data_retention": lambda: run_data_retention_maintenance(settings),
        "tool_permission_terminalization": lambda: progress_pending_tool_permission_terminalizations_for_worker(
            settings,
            v4_capabilities=v4_capabilities,
        ),
        "queue_reclaim": lambda: queue.reclaim_expired_leases(
            visibility_timeout_seconds=int(getattr(settings, "queue_lease_visibility_timeout_seconds", 900))
        ),
        "stale_run_reconciliation": lambda: reconcile_stale_runs_for_worker(
            settings,
            v4_capabilities=v4_capabilities,
        ),
    }
    if v4_capabilities is not None:
        async def drain_due_v4_publication() -> int:
            scope_limit = max(1, min(int(getattr(settings, "v4_publication_scope_limit", 64)), 256))
            event_limit = max(1, min(int(getattr(settings, "v4_publication_event_limit", 64)), 256))
            return await publish_due_v4_events(
                v4_capabilities.publication_claims,
                v4_capabilities.publication_transport,
                scope_limit=scope_limit,
                event_limit=event_limit,
            )

        async def drain_pending_v4_admissions() -> int:
            limit = max(1, min(int(getattr(settings, "v4_pending_admission_limit", 64)), 256))
            return await publish_pending_admissions(
                v4_capabilities,
                limit=limit,
            )

        phases["v4_pending_admission"] = drain_pending_v4_admissions
        phases["v4_publication"] = drain_due_v4_publication
    await run_maintenance_phases(
        phases,
        logger=logger,
    )


async def _maintenance_until_done(
    settings: object,
    interval_seconds: float,
    v4_capabilities: WorkerV4Capabilities | None = None,
) -> None:
    await maintenance_until_done(
        settings,
        interval_seconds,
        lambda current_settings: run_worker_maintenance(
            current_settings,
            v4_capabilities=v4_capabilities,
        ),
        logger=logger,
    )


async def _terminalize_escaped_process_exception(
    message: queue.QueueMessage,
    worker_id: str,
    exc: Exception,
    *,
    v4_capabilities: WorkerV4Capabilities,
) -> WorkerOutcome:
    """Converge one valid claimed run after processing escapes its normal terminal path."""

    try:
        envelope = parse_leased_queue_envelope(message.payload)
        payload = envelope.payload
        queue_attempt_id = envelope.attempt_id
        attempt_id = run_attempt_id_for_queue_attempt(
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            queue_attempt_id=queue_attempt_id,
        )
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
            "execution_kind": str(
                locked_run.get("execution_kind") or RUN_EXECUTION_KIND_SKILL
            ),
            "skill_id": str(locked_run.get("skill_id") or ""),
        }
        payload_identity = {
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "agent_id": payload.agent_id,
            "execution_kind": payload.execution_kind,
            "skill_id": str(payload.skill_id or ""),
        }
        if locked_identity != payload_identity:
            return WorkerOutcome("dead_letter", run_id, error_code, error_message)
        current_status = str(locked_run.get("status") or "")
        if current_status in {"succeeded", "failed", "cancelled"}:
            return WorkerOutcome(
                current_status,
                run_id,
                str(locked_run.get("error_code") or "") or None,
                sanitize_public_text(locked_run.get("error_message")) or None,
            )
        await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
            conn,
            tenant_id=payload.tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        attempt_authority = await assert_worker_run_attempt_current(
            conn,
            tenant_id=payload.tenant_id,
            run_id=run_id,
            queue_attempt_id=queue_attempt_id,
            worker_id=worker_id,
        )
        if attempt_authority is None:
            return _queue_ownership_lost_outcome(run_id)
        validated_attempt_id = str(attempt_authority["id"])
        cancel_requested = bool(locked_run.get("cancel_requested_at")) or str(
            locked_run.get("permission_terminalization_target") or ""
        ) in {"cancel_requested", "cancelled"}
        if cancel_requested:
            await request_run_attempt_cancel(
                conn,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                attempt_id=validated_attempt_id,
            )
            progress = await cancel_run_with_v4(
                conn,
                capabilities=v4_capabilities,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                result_json={"message": "任务已取消"},
            )
        else:
            progress = await fail_run_with_v4(
                conn,
                capabilities=v4_capabilities,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                error_code=error_code,
                error_message=error_message,
                result_json={"message": "Worker processing failed unexpectedly."},
            )
        if progress is not None and progress.is_terminal():
            await terminalize_run_attempt(
                conn,
                tenant_id=payload.tenant_id,
                run_id=run_id,
                attempt_id=validated_attempt_id,
                status=str(progress.status),
                terminal_reason=f"run_{progress.status}",
                error_code=error_code if progress.status == "failed" else None,
            )

    if progress is None or not progress.is_terminal():
        progress = await drain_run_tool_permission_terminalization(
            tenant_id=payload.tenant_id,
            run_id=run_id,
            capabilities=v4_capabilities,
            transaction_factory=transaction,
            max_batches=4,
            attempt_id=validated_attempt_id,
            attempt_error_code=error_code,
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
        await publish_pending_run_terminal(
            v4_capabilities,
            tenant_id=payload.tenant_id,
            run_id=run_id,
        )
        terminal_status = str(progress.status)
        return WorkerOutcome(
            terminal_status,
            run_id,
            error_code if terminal_status == "failed" else None,
            error_message if terminal_status == "failed" else None,
        )
    return WorkerOutcome("dead_letter", run_id, error_code, error_message)


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
    v4_capabilities: WorkerV4Capabilities,
) -> WorkerOutcome:
    resolved_worker_id = worker_id or default_worker_id()
    settings = get_settings()
    if run_initial_maintenance:
        await run_worker_maintenance(settings, v4_capabilities=v4_capabilities)
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
    durable_queue_lease = _durable_queue_lease(
        message,
        visibility_timeout_seconds=int(
            getattr(settings, "queue_lease_visibility_timeout_seconds", 900)
        ),
    )

    ownership_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_until_done(
            message,
            resolved_worker_id,
            heartbeat_interval_seconds,
            int(getattr(settings, "queue_lease_visibility_timeout_seconds", 900)),
            ownership_lost,
        )
    )
    maintenance_task = (
        asyncio.create_task(
            _maintenance_until_done(
                settings,
                _worker_maintenance_interval_seconds(settings),
                v4_capabilities,
            )
        )
        if run_background_maintenance
        else None
    )

    async def process_leased_message() -> WorkerOutcome:
        try:
            process_kwargs = {
                "registry": registry,
                "worker_id": resolved_worker_id,
                "v4_capabilities": v4_capabilities,
            }
            if durable_queue_lease is not None:
                process_kwargs["queue_lease"] = durable_queue_lease
            return await process_run_payload(message.payload, **process_kwargs)
        except Exception as exc:
            logger.exception(
                "Worker payload processing escaped its terminal path",
                extra={"run_id": message.payload.get("run_id")},
            )
            try:
                return await _terminalize_escaped_process_exception(
                    message,
                    resolved_worker_id,
                    exc,
                    v4_capabilities=v4_capabilities,
                )
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
    if outcome.status in {"running", "succeeded", "failed", "skipped", "cancelled"}:
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


def _raise_if_background_task_stopped(task: asyncio.Task[None]) -> None:
    if not task.done():
        return
    if task.cancelled():
        raise RuntimeError(f"background task cancelled unexpectedly: {task.get_name()}")
    error = task.exception()
    if error is not None:
        raise RuntimeError(f"background task failed: {task.get_name()}") from error
    raise RuntimeError(f"background task exited unexpectedly: {task.get_name()}")


async def run_forever(poll_timeout_seconds: int = 5, idle_sleep_seconds: float = 0.5) -> None:
    await require_schema_current()
    worker_runtime = build_worker_v4_runtime(transaction)
    registry = AdapterRegistry()
    worker_id = default_worker_id()
    reconciler_stop = asyncio.Event()
    reconciler_task = asyncio.create_task(
        run_executor_terminal_reconciler(
            reconciler_stop,
            registry=registry,
            worker_id=worker_id,
            v4_capabilities=worker_runtime.capabilities,
        ),
        name="ai-platform-executor-terminal-reconciler",
    )
    heartbeat_task = asyncio.create_task(
        _worker_runtime_heartbeat_until_done(f"{socket.gethostname()}:{os.getpid()}"),
        name="ai-platform-worker-runtime-heartbeat",
    )
    try:
        while True:
            _raise_if_background_task_stopped(reconciler_task)
            _raise_if_background_task_stopped(heartbeat_task)
            try:
                outcome = await run_once(
                registry=registry,
                timeout_seconds=poll_timeout_seconds,
                worker_id=worker_id,
                v4_capabilities=worker_runtime.capabilities,
            )
            except Exception:
                logger.exception("Worker iteration failed")
                await asyncio.sleep(idle_sleep_seconds)
                continue
            if outcome.status == "idle":
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        reconciler_stop.set()
        for task in (reconciler_task, heartbeat_task):
            if not task.done():
                task.cancel()
        for task in (reconciler_task, heartbeat_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await worker_runtime.aclose()
        await _close_runtime_clients()


async def _run_worker_slot(
    *,
    worker_id: str,
    poll_timeout_seconds: int,
    idle_sleep_seconds: float,
    v4_capabilities: WorkerV4Capabilities,
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
                v4_capabilities=v4_capabilities,
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

    await require_schema_current()
    settings = get_settings()
    process_worker_id = f"{socket.gethostname()}:{os.getpid()}"
    worker_runtime = build_worker_v4_runtime(transaction)
    await run_worker_maintenance(settings, v4_capabilities=worker_runtime.capabilities)
    reconciler_stop = asyncio.Event()
    reconciler_task = asyncio.create_task(
        run_executor_terminal_reconciler(
            reconciler_stop,
            registry=AdapterRegistry(),
            worker_id=process_worker_id,
            v4_capabilities=worker_runtime.capabilities,
        ),
        name="ai-platform-executor-terminal-reconciler",
    )
    maintenance_task = asyncio.create_task(
        _maintenance_until_done(
            settings,
            _worker_maintenance_interval_seconds(settings),
            worker_runtime.capabilities,
        ),
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
                v4_capabilities=worker_runtime.capabilities,
            ),
            name=f"ai-platform-worker-{index + 1}",
        )
        for index in range(resolved_worker_count)
    ]
    try:
        await asyncio.gather(*tasks, reconciler_task, maintenance_task, heartbeat_task)
    finally:
        reconciler_stop.set()
        for task in [*tasks, reconciler_task, maintenance_task, heartbeat_task]:
            if not task.done():
                task.cancel()
        for task in [*tasks, reconciler_task, maintenance_task, heartbeat_task]:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await worker_runtime.aclose()
        await _close_runtime_clients()


async def run_once_and_close(timeout_seconds: int) -> WorkerOutcome:
    worker_runtime = build_worker_v4_runtime(transaction)
    try:
        return await run_once(
            timeout_seconds=timeout_seconds,
            v4_capabilities=worker_runtime.capabilities,
        )
    finally:
        await worker_runtime.aclose()
        await _close_runtime_clients()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Platform worker")
    parser.add_argument("--once", action="store_true", help="Process a single leased run then exit")
    parser.add_argument("--timeout", type=int, default=5, help="Queue lease timeout in seconds")
    args = parser.parse_args()

    configure_model_services()
    if args.once:
        outcome = asyncio.run(run_once_and_close(timeout_seconds=args.timeout))
        print(outcome)
        return
    settings = get_settings()
    asyncio.run(run_worker_pool(worker_count=settings.worker_concurrency, poll_timeout_seconds=args.timeout))


if __name__ == "__main__":
    main()

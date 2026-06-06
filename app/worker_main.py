import argparse
import asyncio
import socket
import time
import uuid

from app import queue
from app import repositories
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import transaction
from app.executors.registry import AdapterRegistry
from app.multi_agent_dispatcher import dispatch_multi_agent_ready_steps_for_worker
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings
from app.worker import WorkerOutcome, process_run_payload


_next_memory_cleanup_at = 0.0


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"


async def _heartbeat_until_done(message_id: str, worker_id: str, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await queue.heartbeat_run(message_id, worker_id=worker_id)


async def cleanup_expired_sandbox_leases() -> None:
    async with transaction() as conn:
        await cleanup_expired_sandbox_runtime_leases(conn, provider_factory=create_container_provider)
        await repositories.cleanup_expired_sandbox_leases(conn)


async def cleanup_expired_memory_records_for_worker(settings: object | None = None, *, now: float | None = None) -> list[dict]:
    """Run bounded expired-memory cleanup for worker maintenance when due."""
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

    tenant_id = str(getattr(settings, "default_tenant_id", "default"))
    workspace_id = str(getattr(settings, "default_workspace_id", "default"))
    async with transaction() as conn:
        rows = await repositories.cleanup_expired_memory_records(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=limit,
        )
        if rows:
            await repositories.append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=None,
                action="worker.memory.retention.cleanup",
                target_type="memory_retention",
                target_id=workspace_id,
                trace_id=standard_trace_id(workspace_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "deleted_count": len(rows),
                        "memory_record_ids": [str(row.get("id")) for row in rows],
                        "target_user_ids": sorted({str(row.get("user_id")) for row in rows if row.get("user_id")}),
                        "reason": "retention_expired",
                        "source": "worker",
                    }
                ),
            )
    _next_memory_cleanup_at = current_time + interval_seconds
    return rows


async def run_once(
    registry: AdapterRegistry | None = None,
    timeout_seconds: int = 5,
    *,
    worker_id: str | None = None,
    heartbeat_interval_seconds: float = 10.0,
) -> WorkerOutcome:
    resolved_worker_id = worker_id or default_worker_id()
    settings = get_settings()
    await cleanup_expired_sandbox_leases()
    await cleanup_expired_memory_records_for_worker(settings)
    await dispatch_multi_agent_ready_steps_for_worker(settings)
    await queue.reclaim_expired_leases()
    message = await queue.lease_run(
        timeout_seconds=timeout_seconds,
        worker_id=resolved_worker_id,
        max_processing_runs=settings.max_active_worker_runs,
    )
    if message is None:
        return WorkerOutcome(status="idle", run_id=None)

    heartbeat_task = asyncio.create_task(
        _heartbeat_until_done(message.message_id, resolved_worker_id, heartbeat_interval_seconds)
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
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
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
    while True:
        outcome = await run_once(registry=registry, timeout_seconds=poll_timeout_seconds, worker_id=worker_id)
        if outcome.status == "idle":
            await asyncio.sleep(idle_sleep_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Platform worker")
    parser.add_argument("--once", action="store_true", help="Process a single leased run then exit")
    parser.add_argument("--timeout", type=int, default=5, help="Queue lease timeout in seconds")
    args = parser.parse_args()

    if args.once:
        outcome = asyncio.run(run_once(timeout_seconds=args.timeout))
        print(outcome)
        return
    asyncio.run(run_forever(poll_timeout_seconds=args.timeout))


if __name__ == "__main__":
    main()

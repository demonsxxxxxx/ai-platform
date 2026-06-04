import argparse
import asyncio
import socket
import uuid

from app import queue
from app import repositories
from app.db import transaction
from app.executors.registry import AdapterRegistry
from app.runtime.sandbox.container_provider import create_container_provider
from app.routes.sandbox_runtime_cleanup import cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings
from app.worker import WorkerOutcome, process_run_payload


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


async def run_once(
    registry: AdapterRegistry | None = None,
    timeout_seconds: int = 5,
    *,
    worker_id: str | None = None,
    heartbeat_interval_seconds: float = 10.0,
) -> WorkerOutcome:
    resolved_worker_id = worker_id or default_worker_id()
    await cleanup_expired_sandbox_leases()
    await queue.reclaim_expired_leases()
    settings = get_settings()
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

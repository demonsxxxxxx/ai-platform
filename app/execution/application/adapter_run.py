import asyncio
import time as _time
from collections.abc import Callable
from typing import Any


class _WorkerClock:
    @staticmethod
    def monotonic() -> float:
        return _time.monotonic()


time = _WorkerClock()


class WorkerRunCancelled(asyncio.CancelledError):
    """Raised when a running adapter observes a platform cancel request."""


_RUN_CANCEL_POLL_INTERVAL_SECONDS = 1.0
_RUN_STOP_ATTEMPT_TIMEOUT_SECONDS = 5.0
_RUN_PROGRESS_INTERVAL_SECONDS = 15.0


async def submit_run_until_cancelled(
    adapter: Any,
    run_payload: Any,
    *,
    owner_factory: Callable[[str], Any],
    event_sink: Any,
    cancel_requested: Any,
    poll_interval_seconds: float = _RUN_CANCEL_POLL_INTERVAL_SECONDS,
    stop_timeout_seconds: float = _RUN_STOP_ATTEMPT_TIMEOUT_SECONDS,
    progress_interval_seconds: float = _RUN_PROGRESS_INTERVAL_SECONDS,
) -> Any:
    """Own one adapter until dispatch acceptance, result, or bounded cancellation."""

    owner = owner_factory(run_payload.run_id)
    submit_task = owner.start_adapter(adapter, run_payload, event_sink=event_sink)
    last_progress_at = time.monotonic()

    async def stop_until_quiescent(reason: str) -> None:
        attempt = 0
        while True:
            attempt += 1
            stop_result = await owner.stop(
                reason=reason,
                timeout_seconds=stop_timeout_seconds,
            )
            if stop_result.quiescent:
                return
            try:
                await event_sink(
                    event_type="cancel_requested",
                    stage="status",
                    message="任务取消仍在等待执行器安全停止",
                    payload={
                        "progress_kind": "waiting",
                        "wait_reason": "cancellation",
                        "heartbeat": True,
                        "stop_status": stop_result.status,
                        "stop_attempt": attempt,
                        "visible_to_user": True,
                        "severity": "warning",
                    },
                )
            except WorkerRunCancelled:
                pass
            await asyncio.sleep(max(float(poll_interval_seconds), 0.0))

    try:
        await asyncio.sleep(0)
        if submit_task.done():
            return submit_task.result()
        while True:
            if await cancel_requested():
                await stop_until_quiescent("cancel_requested")
                raise WorkerRunCancelled
            done, _ = await asyncio.wait(
                {submit_task},
                timeout=max(float(poll_interval_seconds), 0.0),
            )
            if submit_task in done:
                return submit_task.result()
            now = time.monotonic()
            if now - last_progress_at >= max(float(progress_interval_seconds), 0.0):
                await event_sink(
                    event_type="run_started",
                    stage="status",
                    message="任务仍在处理中",
                    payload={
                        "progress_kind": "active",
                        "heartbeat": True,
                        "visible_to_user": True,
                        "severity": "info",
                    },
                )
                last_progress_at = now
    except BaseException as exc:
        if isinstance(exc, WorkerRunCancelled) and owner.done:
            raise
        if not owner.done:
            reason = (
                "cancel_requested"
                if isinstance(exc, WorkerRunCancelled)
                else "worker_interrupted"
            )
            await stop_until_quiescent(reason)
        raise

"""Failure isolation for independent worker maintenance phases."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from app.db import close_pool
from app.redis_client import close_redis_client


async def close_runtime_clients() -> None:
    try:
        await close_redis_client()
    finally:
        await close_pool()


def worker_maintenance_interval_seconds(settings: object) -> float:
    try:
        interval = float(getattr(settings, "worker_maintenance_interval_seconds", 30.0))
    except (TypeError, ValueError):
        return 30.0
    return max(interval, 0.0)


async def maintenance_phase_until_done(
    name: str,
    operation: Callable[[], Awaitable[object]],
    interval_seconds: float,
    phase_budget_seconds: float,
    *,
    logger: logging.Logger,
    run_immediately: bool = True,
    repeat_on_progress: bool = False,
) -> None:
    """Supervise one attempt at a time; optionally drain truthy progress results."""

    if not run_immediately:
        if interval_seconds <= 0:
            await asyncio.Event().wait()
        await asyncio.sleep(interval_seconds)
    while True:
        phase_task = asyncio.create_task(operation(), name=f"worker-maintenance-phase-{name}")
        try:
            done, _pending = await asyncio.wait(
                {phase_task},
                timeout=max(float(phase_budget_seconds), 0.0),
            )
            if not done:
                logger.warning(
                    "Worker maintenance phase exceeded its time budget",
                    extra={"maintenance_phase": name, "budget_seconds": phase_budget_seconds},
                )
                phase_task.cancel()
            # A cancellation-resistant operation remains the sole attempt for
            # this phase until it really ends. Storage threads retain their slots.
            result = (await asyncio.gather(phase_task, return_exceptions=True))[0]
            if isinstance(result, asyncio.CancelledError):
                if done:
                    raise result
            elif isinstance(result, BaseException):
                raise result
            elif done and repeat_on_progress and result and interval_seconds > 0:
                # Only successful, in-budget progress may skip the idle delay.
                # Yield between batches so draining cannot starve other phases
                # or defer shutdown. A timed-out attempt still backs off even if
                # it suppresses cancellation and eventually reports progress.
                await asyncio.sleep(0)
                continue
        except asyncio.CancelledError:
            if not phase_task.done():
                phase_task.cancel()
            await asyncio.gather(phase_task, return_exceptions=True)
            raise
        except Exception:  # noqa: BLE001 - phases fail independently.
            logger.exception(
                "Worker background maintenance phase failed",
                extra={"maintenance_phase": name},
            )
        if interval_seconds <= 0:
            await asyncio.Event().wait()
        await asyncio.sleep(interval_seconds)


async def run_maintenance_phases(
    phases: Mapping[str, Callable[[], Awaitable[object]]],
    *,
    logger: logging.Logger,
    phase_budget_seconds: float,
) -> None:
    for name, operation in phases.items():
        try:
            async with asyncio.timeout(phase_budget_seconds):
                await operation()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - maintenance phases must be failure-isolated.
            logger.exception(
                "Worker maintenance phase failed",
                extra={"maintenance_phase": name},
            )

"""Failure isolation for independent worker maintenance phases."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.db import close_pool
from app.redis_client import close_redis_client
from app.streaming.infrastructure.publication_wakeup import run_publication_loop


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


async def maintenance_until_done(
    settings: object,
    interval_seconds: float,
    operation: Callable[[object], Awaitable[Any]],
    *,
    logger: logging.Logger,
) -> None:
    if interval_seconds <= 0:
        return
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await operation(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker background maintenance failed")


def worker_publication_interval_seconds(settings: object) -> float:
    return float(getattr(settings, "worker_publication_interval_seconds", 1.0))


async def publication_until_done(
    settings: object,
    interval_seconds: float,
    operation: Callable[[object], Awaitable[int]],
    *,
    logger: logging.Logger,
) -> None:
    await run_publication_loop(
        str(settings.database_url),
        lambda: operation(settings),
        fallback_seconds=interval_seconds,
        logger=logger,
    )


async def run_maintenance_phases(
    phases: Mapping[str, Callable[[], Awaitable[object]]],
    *,
    logger: logging.Logger,
) -> None:
    for name, operation in phases.items():
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - maintenance phases must be failure-isolated.
            logger.exception(
                "Worker maintenance phase failed",
                extra={"maintenance_phase": name},
            )

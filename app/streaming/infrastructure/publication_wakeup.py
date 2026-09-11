"""PostgreSQL commit notifications wake the existing durable publication scan."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from psycopg import AsyncConnection

PUBLICATION_CHANNEL = "ai_platform_stream_publication"


async def run_publication_loop(
    database_url: str,
    drain: Callable[[], Awaitable[int]],
    *,
    fallback_seconds: float,
    logger: logging.Logger,
) -> None:
    """Listen before scanning; notifications are hints, never event authority.

    PostgreSQL retains notifications received during a drain for this connection.
    An empty pass waits for a notification or the periodic fallback. Successful
    bounded passes continue without a maintenance sleep while work remains.
    Closing/cancelling this loop also closes its dedicated listener connection.
    """
    if fallback_seconds <= 0:
        raise ValueError("publication_fallback_interval_must_be_positive")
    while True:
        try:
            async with await AsyncConnection.connect(
                database_url, autocommit=True, connect_timeout=max(1, int(fallback_seconds))
            ) as conn:
                await conn.execute(f"LISTEN {PUBLICATION_CHANNEL}")
                while True:
                    if await drain():
                        await asyncio.sleep(0)
                        continue
                    async for _ in conn.notifies(timeout=fallback_seconds, stop_after=1):
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            # No exception text: connection failures can contain private DSNs.
            logger.warning("Stream publication listener unavailable; using periodic recovery")
            try:
                while await drain():
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Stream publication recovery pass failed")
            await asyncio.sleep(fallback_seconds)

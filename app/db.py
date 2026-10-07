from collections.abc import AsyncIterator
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.settings import get_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
logger = logging.getLogger(__name__)
_POOL_CLOSE_CONNECTION_GRACE_SECONDS = 1.0

_pool: AsyncConnectionPool | None = None
_pool_signature: tuple[str, int, int, float, int] | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None
_pool_lock: asyncio.Lock | None = None
_pool_lock_loop: asyncio.AbstractEventLoop | None = None


async def connect() -> AsyncConnection:
    settings = get_settings()
    return await AsyncConnection.connect(settings.database_url, row_factory=dict_row)


def _get_pool_lock() -> asyncio.Lock:
    global _pool_lock, _pool_lock_loop
    loop = asyncio.get_running_loop()
    if _pool_lock is None or _pool_lock_loop is not loop:
        _pool_lock = asyncio.Lock()
        _pool_lock_loop = loop
    return _pool_lock


def _pool_config(settings: Any) -> dict[str, int | float]:
    max_size = max(int(getattr(settings, "database_pool_max_size", 10)), 1)
    min_size = max(int(getattr(settings, "database_pool_min_size", 1)), 0)
    timeout_seconds = max(float(getattr(settings, "database_pool_timeout_seconds", 10.0)), 0.1)
    max_waiting = max(int(getattr(settings, "database_pool_max_waiting", 0)), 0)
    return {
        "min_size": min(min_size, max_size),
        "max_size": max_size,
        "timeout_seconds": timeout_seconds,
        "max_waiting": max_waiting,
    }


def _pool_signature_for(settings: Any, config: dict[str, int | float]) -> tuple[str, int, int, float, int]:
    return (
        str(settings.database_url),
        int(config["min_size"]),
        int(config["max_size"]),
        float(config["timeout_seconds"]),
        int(config["max_waiting"]),
    )


async def _close_pool_for_owner_loop(
    pool: AsyncConnectionPool,
    *,
    owner_loop: asyncio.AbstractEventLoop | None,
    timeout: float,
) -> None:
    if pool.closed:
        return
    current_loop = asyncio.get_running_loop()
    if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
        return

    async def close() -> None:
        # psycopg spends its timeout waiting for workers, then closes pooled
        # connections. Do not cancel exactly as that second phase starts.
        async with asyncio.timeout(max(timeout, 0.0) + _POOL_CLOSE_CONNECTION_GRACE_SECONDS):
            if owner_loop is current_loop:
                await pool.close(timeout=timeout)
            else:
                await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(pool.close(timeout=timeout), owner_loop)
                )

    close_task = asyncio.create_task(close(), name="ai-platform-database-pool-close")
    cancellation: asyncio.CancelledError | None = None
    while not close_task.done():
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError as exc:
            if not close_task.cancelled():
                cancellation = cancellation or exc
        except Exception:
            break
    if cancellation is not None:
        # Retrieve a secondary close error without replacing cancellation.
        if not close_task.cancelled():
            close_task.exception()
        raise cancellation
    try:
        close_task.result()
    except Exception:
        if owner_loop is current_loop:
            raise
        # Replacing another loop's pool remains best-effort, but caller
        # cancellation above is never treated as a successful close.
        logger.warning("Database pool owner-loop cleanup failed")


async def get_pool() -> AsyncConnectionPool:
    global _pool, _pool_loop, _pool_signature
    settings = get_settings()
    config = _pool_config(settings)
    signature = _pool_signature_for(settings, config)
    loop = asyncio.get_running_loop()
    async with _get_pool_lock():
        current_pool = _pool
        if current_pool is not None and _pool_signature == signature and _pool_loop is loop and not current_pool.closed:
            return current_pool
        if current_pool is not None and not current_pool.closed:
            current_pool_loop = _pool_loop
            _pool = None
            _pool_loop = None
            _pool_signature = None
            await _close_pool_for_owner_loop(
                current_pool,
                owner_loop=current_pool_loop,
                timeout=float(getattr(settings, "database_pool_close_timeout_seconds", 5.0)),
            )
        next_pool = AsyncConnectionPool(
            settings.database_url,
            kwargs={"row_factory": dict_row},
            min_size=int(config["min_size"]),
            max_size=int(config["max_size"]),
            timeout=float(config["timeout_seconds"]),
            max_waiting=int(config["max_waiting"]),
            open=False,
        )
        try:
            await next_pool.open(wait=True, timeout=float(config["timeout_seconds"]))
        except BaseException:
            _pool = None
            _pool_loop = None
            _pool_signature = None
            try:
                await _close_pool_for_owner_loop(
                    next_pool,
                    owner_loop=loop,
                    timeout=float(getattr(settings, "database_pool_close_timeout_seconds", 5.0)),
                )
            except BaseException:
                # Failed/cancelled initialization remains the primary error;
                # neither close diagnostics nor an unpublished pool escape.
                logger.warning("Database pool initialization cleanup failed")
            raise
        _pool = next_pool
        _pool_loop = loop
        _pool_signature = signature
        return next_pool


async def close_pool() -> None:
    global _pool, _pool_loop, _pool_signature
    async with _get_pool_lock():
        current_pool = _pool
        current_pool_loop = _pool_loop
        _pool = None
        _pool_loop = None
        _pool_signature = None
        if current_pool is not None and not current_pool.closed:
            await _close_pool_for_owner_loop(
                current_pool,
                owner_loop=current_pool_loop,
                timeout=float(getattr(get_settings(), "database_pool_close_timeout_seconds", 5.0)),
            )


def get_pool_status() -> dict[str, Any]:
    settings = get_settings()
    config = _pool_config(settings)
    current_pool = _pool
    stats: dict[str, Any] = {}
    is_open = bool(current_pool is not None and not current_pool.closed)
    if is_open and current_pool is not None:
        stats = dict(current_pool.get_stats())
    return {
        "configured": {
            "min_size": int(config["min_size"]),
            "max_size": int(config["max_size"]),
            "timeout_seconds": float(config["timeout_seconds"]),
            "max_waiting": int(config["max_waiting"]),
        },
        "open": is_open,
        "stats": stats,
    }


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    pool = await get_pool()
    timeout_seconds = float(_pool_config(get_settings())["timeout_seconds"])
    async with pool.connection(timeout=timeout_seconds) as conn:
        async with conn.transaction():
            yield conn


async def apply_schema() -> None:
    """Compatibility entrypoint routed through versioned migrations."""

    from app.schema_migrations import apply_migrations

    await apply_migrations()

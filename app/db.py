from collections.abc import AsyncIterator
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.settings import get_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

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
    if owner_loop is current_loop:
        await pool.close(timeout=timeout)
        return
    if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
        return
    try:
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(pool.close(timeout=timeout), owner_loop)
        )
    except BaseException:
        return


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
        except Exception:
            await next_pool.close(timeout=float(getattr(settings, "database_pool_close_timeout_seconds", 5.0)))
            _pool = None
            _pool_loop = None
            _pool_signature = None
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
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with transaction() as conn:
        await conn.execute(sql)

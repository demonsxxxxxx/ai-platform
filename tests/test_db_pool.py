import asyncio
import threading

import pytest

import app.db as db


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, name: str):
        self.name = name

    def transaction(self):
        return FakeTransaction()


class FakePoolConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncConnectionPool:
    instances = []

    def __init__(self, conninfo, *, kwargs, min_size, max_size, timeout, max_waiting, open):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.max_waiting = max_waiting
        self.open_arg = open
        self.open_calls = []
        self.connection_timeouts = []
        self.close_calls = []
        self.closed = False
        self.fake_connection = FakeConnection(f"conn-{len(self.instances) + 1}")
        self.stats = {"pool_available": 1, "requests_waiting": 0}
        self.instances.append(self)

    async def open(self, *, wait, timeout):
        self.open_calls.append((wait, timeout))

    def connection(self, *, timeout):
        self.connection_timeouts.append(timeout)
        return FakePoolConnectionContext(self.fake_connection)

    async def close(self, *, timeout=5.0):
        self.close_calls.append(timeout)
        self.closed = True

    def get_stats(self):
        return dict(self.stats)


class PoolSettings:
    database_url = "postgresql://user:secret-password@db.example/internal"
    database_pool_min_size = 2
    database_pool_max_size = 8
    database_pool_timeout_seconds = 3.5
    database_pool_max_waiting = 12
    database_pool_close_timeout_seconds = 1.25


async def fail_direct_connect():
    raise AssertionError("transaction must use the shared pool, not direct connect")


@pytest.fixture(autouse=True)
async def reset_pool():
    if hasattr(db, "close_pool"):
        await db.close_pool()
    FakeAsyncConnectionPool.instances.clear()
    yield
    if hasattr(db, "close_pool"):
        await db.close_pool()
    FakeAsyncConnectionPool.instances.clear()


@pytest.mark.asyncio
async def test_transaction_reuses_bounded_async_connection_pool(monkeypatch):
    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(db, "connect", fail_direct_connect)

    async with db.transaction() as first_conn:
        assert first_conn.name == "conn-1"
    async with db.transaction() as second_conn:
        assert second_conn.name == "conn-1"

    assert len(FakeAsyncConnectionPool.instances) == 1
    pool = FakeAsyncConnectionPool.instances[0]
    assert pool.conninfo == PoolSettings.database_url
    assert pool.kwargs["row_factory"] is db.dict_row
    assert pool.min_size == 2
    assert pool.max_size == 8
    assert pool.timeout == 3.5
    assert pool.max_waiting == 12
    assert pool.open_arg is False
    assert pool.open_calls == [(True, 3.5)]
    assert pool.connection_timeouts == [3.5, 3.5]


@pytest.mark.asyncio
async def test_close_pool_closes_current_pool_and_allows_new_pool(monkeypatch):
    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(db, "connect", fail_direct_connect)

    async with db.transaction() as first_conn:
        assert first_conn.name == "conn-1"

    await db.close_pool()

    assert FakeAsyncConnectionPool.instances[0].closed is True
    assert FakeAsyncConnectionPool.instances[0].close_calls == [1.25]

    async with db.transaction() as second_conn:
        assert second_conn.name == "conn-2"

    assert len(FakeAsyncConnectionPool.instances) == 2


@pytest.mark.asyncio
async def test_get_pool_recreates_pool_when_owner_loop_unavailable(monkeypatch):
    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(db, "connect", fail_direct_connect)

    async with db.transaction() as first_conn:
        assert first_conn.name == "conn-1"

    first_pool = FakeAsyncConnectionPool.instances[0]
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    monkeypatch.setattr(db, "_pool_loop", closed_loop, raising=False)

    async with db.transaction() as second_conn:
        assert second_conn.name == "conn-2"

    assert first_pool.closed is False
    assert len(FakeAsyncConnectionPool.instances) == 2


@pytest.mark.asyncio
async def test_concurrent_first_transactions_share_one_pool(monkeypatch):
    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(db, "connect", fail_direct_connect)

    async def use_transaction():
        async with db.transaction() as conn:
            return conn.name

    results = await asyncio.gather(use_transaction(), use_transaction(), use_transaction())

    assert results == ["conn-1", "conn-1", "conn-1"]
    assert len(FakeAsyncConnectionPool.instances) == 1
    assert FakeAsyncConnectionPool.instances[0].open_calls == [(True, 3.5)]


def test_get_pool_recreates_real_pool_after_previous_event_loop_closed(monkeypatch):
    class RealPoolSettings:
        database_url = "postgresql://invalid.invalid/nope"
        database_pool_min_size = 0
        database_pool_max_size = 1
        database_pool_timeout_seconds = 0.1
        database_pool_max_waiting = 1
        database_pool_close_timeout_seconds = 0.1

    pool_ids = []

    monkeypatch.setattr(db, "_pool", None, raising=False)
    monkeypatch.setattr(db, "_pool_loop", None, raising=False)
    monkeypatch.setattr(db, "_pool_signature", None, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: RealPoolSettings())

    async def use_pool():
        pool = await db.get_pool()
        pool_ids.append(id(pool))
        assert pool.closed is False

    try:
        asyncio.run(use_pool())
        asyncio.run(use_pool())
        assert len(pool_ids) == 2
        assert pool_ids[0] != pool_ids[1]
    finally:
        monkeypatch.setattr(db, "_pool", None, raising=False)
        monkeypatch.setattr(db, "_pool_loop", None, raising=False)
        monkeypatch.setattr(db, "_pool_signature", None, raising=False)


@pytest.mark.asyncio
async def test_pool_status_exposes_safe_config_and_stats_without_database_url(monkeypatch):
    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool, raising=False)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(db, "connect", fail_direct_connect)

    async with db.transaction():
        pass

    status = db.get_pool_status()

    assert status == {
        "configured": {
            "min_size": 2,
            "max_size": 8,
            "timeout_seconds": 3.5,
            "max_waiting": 12,
        },
        "open": True,
        "stats": {"pool_available": 1, "requests_waiting": 0},
    }
    assert "secret-password" not in str(status)
    assert "db.example" not in str(status)


@pytest.mark.asyncio
async def test_cancelled_pool_open_finishes_close_before_retry_even_if_cancelled_again(monkeypatch):
    opening = asyncio.Event()
    closing = asyncio.Event()
    release_close = asyncio.Event()
    original_open = FakeAsyncConnectionPool.open

    async def blocked_open(self, *, wait, timeout):
        opening.set()
        await asyncio.Event().wait()

    async def blocked_close(self, *, timeout):
        self.close_calls.append(timeout)
        closing.set()
        await release_close.wait()
        self.closed = True

    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(FakeAsyncConnectionPool, "open", blocked_open)
    monkeypatch.setattr(FakeAsyncConnectionPool, "close", blocked_close)
    opener = asyncio.create_task(db.get_pool())
    retry = None
    try:
        await asyncio.wait_for(opening.wait(), timeout=0.5)
        opener.cancel("opening cancelled")
        await asyncio.wait_for(closing.wait(), timeout=0.5)
        opener.cancel("cancelled again")
        monkeypatch.setattr(FakeAsyncConnectionPool, "open", original_open)
        retry = asyncio.create_task(db.get_pool())
        await asyncio.sleep(0)
        assert not opener.done()
        assert not retry.done()
        assert db._pool is None
        assert len(FakeAsyncConnectionPool.instances) == 1
        release_close.set()
        with pytest.raises(asyncio.CancelledError, match="opening cancelled"):
            await opener
        replacement = await asyncio.wait_for(retry, timeout=0.5)
        assert replacement is FakeAsyncConnectionPool.instances[1]
        assert FakeAsyncConnectionPool.instances[0].closed
        assert FakeAsyncConnectionPool.instances[0].close_calls == [1.25]
        assert db._pool is replacement
    finally:
        release_close.set()
        opener.cancel()
        if retry is not None:
            retry.cancel()
        await asyncio.gather(opener, *([retry] if retry is not None else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_open", [False, True])
async def test_pool_initialization_error_survives_close_failure(monkeypatch, caplog, cancel_open):
    primary = asyncio.CancelledError("open cancelled") if cancel_open else RuntimeError("open failed")

    async def failed_open(self, **_kwargs):
        raise primary

    async def failed_close(self, *, timeout):
        self.close_calls.append(timeout)
        raise RuntimeError("private close detail")

    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(FakeAsyncConnectionPool, "open", failed_open)
    monkeypatch.setattr(FakeAsyncConnectionPool, "close", failed_close)

    with pytest.raises(type(primary)) as caught:
        await db.get_pool()

    assert caught.value is primary
    assert db._pool is None
    assert db._pool_loop is None
    assert db._pool_signature is None
    assert FakeAsyncConnectionPool.instances[0].close_calls == [1.25]
    assert "private close detail" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_pool_open_bounds_unresponsive_close(monkeypatch):
    close_cancelled = asyncio.Event()
    primary = asyncio.CancelledError("open cancelled")

    class Settings(PoolSettings):
        database_pool_close_timeout_seconds = 0.01

    async def failed_open(self, **_kwargs):
        raise primary

    async def blocked_close(self, *, timeout):
        self.close_calls.append(timeout)
        try:
            await asyncio.Event().wait()
        finally:
            close_cancelled.set()

    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(db, "get_settings", lambda: Settings())
    monkeypatch.setattr(db, "_POOL_CLOSE_CONNECTION_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(FakeAsyncConnectionPool, "open", failed_open)
    monkeypatch.setattr(FakeAsyncConnectionPool, "close", blocked_close)

    with pytest.raises(asyncio.CancelledError, match="open cancelled"):
        await asyncio.wait_for(db.get_pool(), timeout=0.5)

    assert close_cancelled.is_set()
    assert db._pool is None
    assert FakeAsyncConnectionPool.instances[0].close_calls == [0.01]


@pytest.mark.asyncio
async def test_close_pool_preserves_cancellation_after_finishing_close(monkeypatch):
    closing = asyncio.Event()
    release_close = asyncio.Event()

    async def blocked_close(self, *, timeout):
        self.close_calls.append(timeout)
        closing.set()
        await release_close.wait()
        self.closed = True

    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(FakeAsyncConnectionPool, "close", blocked_close)
    pool = await db.get_pool()
    closer = asyncio.create_task(db.close_pool())
    try:
        await asyncio.wait_for(closing.wait(), timeout=0.5)
        closer.cancel("close cancelled")
        await asyncio.sleep(0)
        assert not closer.done()
        assert db._pool is None
        release_close.set()
        with pytest.raises(asyncio.CancelledError, match="close cancelled"):
            await closer
        assert pool.closed
    finally:
        release_close.set()
        closer.cancel()
        await asyncio.gather(closer, return_exceptions=True)


@pytest.mark.asyncio
async def test_pool_close_allows_connection_cleanup_after_worker_stop_budget(monkeypatch):
    async def close_after_worker_timeout(self, *, timeout):
        await asyncio.sleep(timeout)
        self.closed = True

    monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
    monkeypatch.setattr(FakeAsyncConnectionPool, "close", close_after_worker_timeout)
    pool = await db.get_pool()

    await db._close_pool_for_owner_loop(pool, owner_loop=asyncio.get_running_loop(), timeout=0.01)

    assert pool.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_cross_loop_pool_close_keeps_owner_and_propagates_cancellation(monkeypatch, cancel_close):
    owner_loop = asyncio.new_event_loop()
    owner_ready = threading.Event()
    owner_loop.call_soon(owner_ready.set)
    owner_thread = threading.Thread(target=owner_loop.run_forever)
    owner_thread.start()
    current_loop = asyncio.get_running_loop()
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closer = None

    async def remote_close(self, *, timeout):
        assert asyncio.get_running_loop() is owner_loop
        self.close_calls.append(timeout)
        current_loop.call_soon_threadsafe(closing.set)
        await release_close.wait()
        self.closed = True
        raise RuntimeError("private owner-loop close detail")

    try:
        assert owner_ready.wait(timeout=0.5)
        monkeypatch.setattr(db, "AsyncConnectionPool", FakeAsyncConnectionPool)
        monkeypatch.setattr(db, "get_settings", lambda: PoolSettings())
        monkeypatch.setattr(FakeAsyncConnectionPool, "close", remote_close)
        pool = await db.get_pool()
        monkeypatch.setattr(db, "_pool_loop", owner_loop)
        closer = asyncio.create_task(db.close_pool())
        await asyncio.wait_for(closing.wait(), timeout=0.5)
        if cancel_close:
            closer.cancel("owner close cancelled")
            await asyncio.sleep(0)
            assert not closer.done()
        owner_loop.call_soon_threadsafe(release_close.set)
        if cancel_close:
            with pytest.raises(asyncio.CancelledError, match="owner close cancelled"):
                await closer
        else:
            await closer
        assert pool.closed
        assert pool.close_calls == [1.25]
        assert db._pool is None
    finally:
        owner_loop.call_soon_threadsafe(release_close.set)
        if closer is not None:
            closer.cancel()
            await asyncio.gather(closer, return_exceptions=True)
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        owner_thread.join(timeout=1.0)
        assert not owner_thread.is_alive()
        owner_loop.close()

import asyncio

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

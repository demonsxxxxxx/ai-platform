import asyncio
import types

import pytest

from app import auth_sessions, queue, redis_client


class FakeRedis:
    def __init__(self, *, close_error: Exception | None = None):
        self.connection_pool = object()
        self.close_calls = 0
        self.close_error = close_error

    async def ping(self):
        return True

    async def aclose(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(redis_client, "_clients", {})


@pytest.mark.asyncio
async def test_same_loop_reuses_one_bounded_pool_and_operation_close_does_not_destroy_it(monkeypatch):
    created = []

    def from_url(url, **kwargs):
        created.append((url, kwargs, FakeRedis()))
        return created[-1][2]

    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: types.SimpleNamespace(redis_url="redis://user:secret@redis:6379/0", redis_max_connections=10),
    )

    first = redis_client.get_redis_client()
    second = redis_client.get_redis_client()

    assert first.connection_pool is second.connection_pool
    assert created[0][1]["max_connections"] == 10
    await first.aclose()
    assert await second.ping() is True
    assert created[0][2].close_calls == 0

    await redis_client.close_redis_client()
    assert created[0][2].close_calls == 1


@pytest.mark.asyncio
async def test_queue_and_auth_share_the_current_loop_pool(monkeypatch):
    created = []

    def from_url(_url, **_kwargs):
        created.append(FakeRedis())
        return created[-1]

    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: types.SimpleNamespace(redis_url="redis://redis:6379/0", redis_max_connections=10),
    )

    queue_handle = await queue.get_redis()
    auth_handle = auth_sessions.get_redis()

    assert queue_handle.connection_pool is auth_handle.connection_pool
    assert len(created) == 1
    await queue_handle.aclose()
    await auth_handle.aclose()
    await redis_client.close_redis_client()


def test_cross_loop_isolation_and_close_recreate(monkeypatch):
    created = []

    def from_url(_url, **_kwargs):
        client = FakeRedis()
        created.append(client)
        return client

    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: types.SimpleNamespace(redis_url="redis://redis:6379/0", redis_max_connections=10),
    )

    async def acquire_close_recreate():
        first = redis_client.get_redis_client()
        first_pool = first.connection_pool
        await redis_client.close_redis_client()
        second = redis_client.get_redis_client()
        second_pool = second.connection_pool
        assert first_pool is not second_pool
        await redis_client.close_redis_client()
        return first_pool, second_pool

    first_loop_pools = asyncio.run(acquire_close_recreate())
    second_loop_pools = asyncio.run(acquire_close_recreate())

    assert set(first_loop_pools).isdisjoint(second_loop_pools)
    assert len(created) == 4
    assert all(client.close_calls == 1 for client in created)


@pytest.mark.asyncio
async def test_close_failure_is_secret_safe_and_poisoned_client_fails_closed(monkeypatch):
    secret_url = "redis://user:super-secret@redis:6379/0"
    client = FakeRedis(close_error=RuntimeError(f"failed to close {secret_url}"))
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: types.SimpleNamespace(redis_url=secret_url, redis_max_connections=10),
    )
    redis_client.get_redis_client()

    with pytest.raises(redis_client.RedisClientCloseError) as exc_info:
        await redis_client.close_redis_client()

    assert str(exc_info.value) == "redis_client_close_failed"
    assert "super-secret" not in repr(exc_info.value)
    with pytest.raises(redis_client.RedisClientUnavailable, match="redis_client_unavailable"):
        redis_client.get_redis_client()


@pytest.mark.asyncio
async def test_api_lifespan_closes_redis_then_database_even_if_redis_close_fails(monkeypatch):
    from app import main

    calls = []

    async def close_redis():
        calls.append("redis")
        raise redis_client.RedisClientCloseError("redis_client_close_failed")

    async def close_database():
        calls.append("database")

    async def require_schema_current():
        calls.append("schema")
        return {}

    monkeypatch.setattr(main, "close_redis_client", close_redis)
    monkeypatch.setattr(main, "close_pool", close_database)
    monkeypatch.setattr(
        main,
        "require_schema_current",
        require_schema_current,
    )

    with pytest.raises(redis_client.RedisClientCloseError, match="redis_client_close_failed"):
        async with main.lifespan(types.SimpleNamespace(state=types.SimpleNamespace())):
            calls.append("app")

    assert calls == ["schema", "app", "redis", "database"]

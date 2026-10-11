import asyncio
import types

import pytest

from app import auth_sessions, queue, redis_client


class FakeRedis:
    def __init__(self, *, close_error: BaseException | None = None):
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

    async def close_stream_runtime():
        calls.append("stream_runtime")

    monkeypatch.setattr(
        main, "build_run_stream_runtime",
        lambda *_args: types.SimpleNamespace(aclose=close_stream_runtime),
    )
    monkeypatch.setattr(main, "build_run_cancellation_use_case", lambda **_kwargs: object())
    monkeypatch.setattr(main, "close_redis_client", close_redis)
    monkeypatch.setattr(main, "close_pool", close_database)

    with pytest.raises(redis_client.RedisClientCloseError, match="redis_client_close_failed"):
        async with main.lifespan(
            types.SimpleNamespace(state=types.SimpleNamespace(run_attempt_lifecycle=object()))
        ):
            calls.append("app")

    assert calls == ["app", "stream_runtime", "redis", "database"]


class BlockingCloseRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.close_started = asyncio.Event()
        self.finish_close = asyncio.Event()
        self.active_closes = 0
        self.max_active_closes = 0

    async def aclose(self):
        self.close_calls += 1
        self.active_closes += 1
        self.max_active_closes = max(self.max_active_closes, self.active_closes)
        self.close_started.set()
        try:
            await self.finish_close.wait()
        finally:
            self.active_closes -= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_close_keeps_same_client_for_exclusive_cleanup_retry(monkeypatch, cancellations):
    client = BlockingCloseRedis()
    created = []

    def from_url(*_args, **_kwargs):
        created.append(client if not created else FakeRedis())
        return created[-1]

    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)
    handle = redis_client.get_redis_client()
    old_ping = handle.ping
    entry = redis_client._clients[asyncio.get_running_loop()]

    for _ in range(cancellations):
        client.close_started.clear()
        closing = asyncio.create_task(redis_client.close_redis_client())
        try:
            await asyncio.wait_for(client.close_started.wait(), timeout=1)
            with pytest.raises(redis_client.RedisClientUnavailable):
                await redis_client.close_redis_client()
            with pytest.raises(redis_client.RedisClientUnavailable):
                redis_client.get_redis_client()
            closing.cancel("shutdown deadline")
            with pytest.raises(asyncio.CancelledError, match="shutdown deadline"):
                await closing
        finally:
            if not closing.done():
                closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)

        assert redis_client._clients[asyncio.get_running_loop()] is entry
        assert entry.state == "close_failed"
        assert client.active_closes == 0
        with pytest.raises(redis_client.RedisClientUnavailable):
            redis_client.get_redis_client()
        with pytest.raises(redis_client.RedisClientUnavailable):
            await old_ping()
        assert len(created) == 1

    client.finish_close.set()
    await redis_client.close_redis_client()
    assert client.close_calls == cancellations + 1
    assert client.max_active_closes == 1
    assert asyncio.get_running_loop() not in redis_client._clients
    with pytest.raises(redis_client.RedisClientUnavailable):
        await old_ping()
    replacement = redis_client.get_redis_client()
    assert replacement.connection_pool is not client.connection_pool
    assert len(created) == 2
    await redis_client.close_redis_client()
    await redis_client.close_redis_client()
    assert created[1].close_calls == 1


@pytest.mark.asyncio
async def test_failed_close_retries_same_client_before_rebuilding(monkeypatch):
    client = FakeRedis(close_error=RuntimeError("synthetic close failure"))
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    handle = redis_client.get_redis_client()
    with pytest.raises(redis_client.RedisClientCloseError):
        await redis_client.close_redis_client()
    with pytest.raises(redis_client.RedisClientUnavailable):
        await handle.ping()
    client.close_error = None
    await redis_client.close_redis_client()
    assert client.close_calls == 2
    with pytest.raises(redis_client.RedisClientUnavailable):
        await handle.ping()


def test_cancelled_close_does_not_poison_another_loop(monkeypatch):
    clients = [FakeRedis(close_error=asyncio.CancelledError()), FakeRedis()]
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: clients.pop(0))
    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()

    async def acquire():
        return redis_client.get_redis_client()

    async def ping(handle):
        return await handle.ping()

    try:
        first = first_loop.run_until_complete(acquire())
        with pytest.raises(asyncio.CancelledError):
            first_loop.run_until_complete(redis_client.close_redis_client())
        second = second_loop.run_until_complete(acquire())
        assert second_loop.run_until_complete(ping(second)) is True
        second_loop.run_until_complete(redis_client.close_redis_client())
        assert redis_client._clients[first_loop] is first._entry
        first._entry.client.close_error = None
        first_loop.run_until_complete(redis_client.close_redis_client())
        assert not redis_client._clients
    finally:
        first_loop.close()
        second_loop.close()


@pytest.mark.asyncio
async def test_repeated_cancellation_during_close_unwind_retains_cleanup_owner(monkeypatch):
    unwinding = asyncio.Event()

    class UnwindingRedis(FakeRedis):
        async def aclose(self):
            self.close_calls += 1
            if self.close_calls > 1:
                return
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                unwinding.set()
                await asyncio.Event().wait()
                raise

    client = UnwindingRedis()
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    redis_client.get_redis_client()
    closing = asyncio.create_task(redis_client.close_redis_client())
    try:
        # Let aclose enter its first suspension before cancelling its owner.
        await asyncio.sleep(0)
        closing.cancel("first cancel")
        await asyncio.wait_for(unwinding.wait(), timeout=1)
        with pytest.raises(redis_client.RedisClientUnavailable):
            await redis_client.close_redis_client()
        assert client.close_calls == 1
        closing.cancel("second cancel")
        with pytest.raises(asyncio.CancelledError, match="second cancel"):
            await closing
        await redis_client.close_redis_client()
        assert client.close_calls == 2
        assert not redis_client._clients
    finally:
        if not closing.done():
            closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)

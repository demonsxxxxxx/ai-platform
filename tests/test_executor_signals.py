from __future__ import annotations

import pytest

from app.runtime.sandbox import executor_signals


class FakeRedisHandle:
    def __init__(self, *, rows=None, fail: bool = False, close_fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.close_fail = close_fail
        self.xadd_calls = []
        self.xread_calls = []
        self.closed = False

    async def xadd(self, key, fields, *, maxlen, approximate):
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.xadd_calls.append((key, fields, maxlen, approximate))
        return "1-0"

    async def xread(self, streams, *, count, block):
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.xread_calls.append((streams, count, block))
        return self.rows

    async def aclose(self):
        self.closed = True
        if self.close_fail:
            raise RuntimeError("redis close failed")


@pytest.mark.asyncio
async def test_publish_executor_terminal_signal_contains_only_fixed_wake_marker(monkeypatch):
    client = FakeRedisHandle()
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    await executor_signals.publish_executor_terminal_signal()

    assert client.xadd_calls == [
        ("ai-platform:executor-terminal:v1:reconcile", {"wake": "1"}, 1024, True)
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_wait_for_executor_reconciliation_signal_is_global(monkeypatch):
    client = FakeRedisHandle(rows=[("stream", [("1-0", {"wake": "1"})])])
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    found = await executor_signals.wait_for_executor_reconciliation_signal(block_ms=5000)

    assert found is True
    assert client.xread_calls == [
        ({"ai-platform:executor-terminal:v1:reconcile": "$"}, 1, 5000)
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_executor_signal_failure_is_fail_open_for_postgres_recovery(monkeypatch):
    client = FakeRedisHandle(fail=True)
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_terminal_signal_unavailable",
    ):
        await executor_signals.publish_executor_terminal_signal()

    assert client.closed is True


@pytest.mark.asyncio
async def test_executor_signal_close_failure_is_visible(monkeypatch):
    client = FakeRedisHandle(close_fail=True)
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_signal_close_unavailable",
    ):
        await executor_signals.publish_executor_terminal_signal()


@pytest.mark.asyncio
async def test_executor_signal_operation_error_is_not_masked_by_close(monkeypatch):
    client = FakeRedisHandle(fail=True, close_fail=True)
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_terminal_signal_unavailable",
    ):
        await executor_signals.publish_executor_terminal_signal()

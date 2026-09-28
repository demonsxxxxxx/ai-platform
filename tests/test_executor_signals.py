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
        self.xrevrange_calls = []
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

    async def xrevrange(self, key, *, max, min, count):
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.xrevrange_calls.append((key, max, min, count))
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
async def test_publish_executor_terminal_signal_wraps_client_acquisition_failure(monkeypatch):
    def unavailable_client():
        raise RuntimeError("redis client unavailable")

    monkeypatch.setattr(executor_signals, "get_redis_client", unavailable_client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_terminal_signal_unavailable",
    ):
        await executor_signals.publish_executor_terminal_signal()


@pytest.mark.asyncio
async def test_initialize_executor_reconciliation_signal_cursor_uses_stream_tail(
    monkeypatch,
):
    client = FakeRedisHandle(rows=[("7-0", {"wake": "1"})])
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    cursor = await executor_signals.initialize_executor_reconciliation_signal_cursor()

    assert cursor.last_id == "7-0"
    assert client.xrevrange_calls == [
        ("ai-platform:executor-terminal:v1:reconcile", "+", "-", 1)
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_initialize_executor_reconciliation_signal_cursor_uses_zero_for_empty_stream(
    monkeypatch,
):
    client = FakeRedisHandle()
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    cursor = await executor_signals.initialize_executor_reconciliation_signal_cursor()

    assert cursor.last_id == "0-0"
    assert client.closed is True


@pytest.mark.asyncio
async def test_initialize_executor_reconciliation_signal_cursor_wraps_redis_failure(
    monkeypatch,
):
    client = FakeRedisHandle(fail=True)
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_reconciliation_signal_cursor_unavailable",
    ):
        await executor_signals.initialize_executor_reconciliation_signal_cursor()

    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_id", ["$", "+", "1", "-", "١-1", b"1-0"])
async def test_initialize_executor_reconciliation_signal_cursor_rejects_sentinels(
    monkeypatch,
    entry_id,
):
    client = FakeRedisHandle(rows=[(entry_id, {"wake": "1"})])
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_reconciliation_signal_cursor_unavailable",
    ):
        await executor_signals.initialize_executor_reconciliation_signal_cursor()

    assert client.closed is True


@pytest.mark.asyncio
async def test_wait_for_executor_reconciliation_signal_uses_and_advances_cursor(
    monkeypatch,
):
    client = FakeRedisHandle(rows=[("stream", [("8-0", {"wake": "1"})])])
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)
    cursor = executor_signals.ExecutorReconciliationSignalCursor("7-0")

    found = await executor_signals.wait_for_executor_reconciliation_signal(
        block_ms=5000,
        cursor=cursor,
    )

    assert found is True
    assert cursor.last_id == "8-0"
    assert client.xread_calls == [
        ({"ai-platform:executor-terminal:v1:reconcile": "7-0"}, 1, 5000)
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_wait_for_executor_reconciliation_signal_rejects_invalid_cursor(monkeypatch):
    client = FakeRedisHandle()
    monkeypatch.setattr(executor_signals, "get_redis_client", lambda: client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_reconciliation_signal_unavailable",
    ):
        await executor_signals.wait_for_executor_reconciliation_signal(
            block_ms=5000,
            cursor=executor_signals.ExecutorReconciliationSignalCursor("$"),
        )

    assert client.xread_calls == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_wait_for_executor_reconciliation_signal_wraps_client_acquisition_failure(monkeypatch):
    def unavailable_client():
        raise RuntimeError("redis client unavailable")

    monkeypatch.setattr(executor_signals, "get_redis_client", unavailable_client)

    with pytest.raises(
        executor_signals.ExecutorSignalUnavailable,
        match="executor_reconciliation_signal_unavailable",
    ):
        await executor_signals.wait_for_executor_reconciliation_signal(
            block_ms=5000,
            cursor=executor_signals.ExecutorReconciliationSignalCursor("0-0"),
        )


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

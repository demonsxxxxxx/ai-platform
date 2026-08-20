from types import SimpleNamespace

import pytest

from app.streaming import worker_projection


class _Transaction:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_publish_pending_run_terminal_marks_committed_intent(monkeypatch):
    calls = []
    intent = SimpleNamespace(state="pending")

    class Bridge:
        async def aclose(self):
            calls.append("close")

    async def authority(_conn, *, tenant_id, run_id):
        calls.append(("authority", tenant_id, run_id))
        return SimpleNamespace()

    async def get_intent(_conn, *, tenant_id, run_id):
        calls.append(("intent", tenant_id, run_id))
        return intent

    async def publish(bridge, *, authority, intent):
        calls.append(("publish", bridge, intent))

    async def mark(_conn, *, intent):
        calls.append(("mark", intent))
        return intent

    monkeypatch.setattr(worker_projection, "RedisStreamBridge", Bridge)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection, "get_terminal_intent", get_intent)
    monkeypatch.setattr(worker_projection, "publish_terminal_intent", publish)
    monkeypatch.setattr(worker_projection, "mark_terminal_intent_published", mark)

    assert await worker_projection.publish_pending_run_terminal(
        _Transaction,
        tenant_id="tenant-a",
        run_id="run-a",
    ) is True
    assert [call[0] if isinstance(call, tuple) else call for call in calls] == [
        "authority",
        "intent",
        "publish",
        "close",
        "mark",
    ]


@pytest.mark.asyncio
async def test_publish_pending_run_terminal_rejects_stale_attempt_fence(monkeypatch, caplog):
    intent = SimpleNamespace(state="pending", attempt_id="attempt-b", stream_incarnation=8)

    async def authority(_conn, *, tenant_id, run_id):
        return SimpleNamespace(attempt_id="attempt-b", stream_incarnation=8)

    async def get_intent(_conn, *, tenant_id, run_id, attempt_id):
        assert attempt_id == "attempt-a"
        return intent

    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection, "get_terminal_intent", get_intent)
    caplog.set_level("WARNING")

    assert await worker_projection.publish_pending_run_terminal(
        _Transaction,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=8,
    ) is False
    record = next(record for record in caplog.records if record.message == "terminal_intent_publish_failed")
    assert record.reason_code == "attempt_fence_rejected"


@pytest.mark.asyncio
async def test_publish_pending_run_terminal_logs_bounded_transport_failure(monkeypatch, caplog):
    intent = SimpleNamespace(state="pending")

    class Bridge:
        async def aclose(self):
            return None

    async def authority(_conn, *, tenant_id, run_id):
        return SimpleNamespace()

    async def get_intent(_conn, *, tenant_id, run_id):
        return intent

    async def publish(*_args, **_kwargs):
        raise worker_projection.StreamTransportUnavailable("redis unavailable")

    monkeypatch.setattr(worker_projection, "RedisStreamBridge", Bridge)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection, "get_terminal_intent", get_intent)
    monkeypatch.setattr(worker_projection, "publish_terminal_intent", publish)
    caplog.set_level("WARNING")

    assert await worker_projection.publish_pending_run_terminal(
        _Transaction,
        tenant_id="tenant-a",
        run_id="run-a",
    ) is False
    record = next(record for record in caplog.records if record.message == "terminal_intent_publish_failed")
    assert record.reason_code == "redis_publication_failed"
    assert record.tenant_id == "tenant-a"
    assert record.run_id == "run-a"


@pytest.mark.asyncio
async def test_publish_pending_run_terminal_logs_bridge_close_failure(monkeypatch, caplog):
    intent = SimpleNamespace(state="pending")

    class Bridge:
        async def aclose(self):
            raise RuntimeError("close failed")

    async def authority(_conn, *, tenant_id, run_id):
        return SimpleNamespace()

    async def get_intent(_conn, *, tenant_id, run_id):
        return intent

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker_projection, "RedisStreamBridge", Bridge)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection, "get_terminal_intent", get_intent)
    monkeypatch.setattr(worker_projection, "publish_terminal_intent", publish)
    caplog.set_level("WARNING")

    assert await worker_projection.publish_pending_run_terminal(
        _Transaction,
        tenant_id="tenant-a",
        run_id="run-a",
    ) is False
    record = next(record for record in caplog.records if record.message == "terminal_intent_publish_failed")
    assert record.reason_code == "redis_close_failed"


@pytest.mark.asyncio
async def test_publish_pending_run_terminal_logs_ack_failure(monkeypatch, caplog):
    intent = SimpleNamespace(state="pending")

    class Bridge:
        async def aclose(self):
            return None

    async def authority(_conn, *, tenant_id, run_id):
        return SimpleNamespace()

    async def get_intent(_conn, *, tenant_id, run_id):
        return intent

    async def publish(*_args, **_kwargs):
        return None

    async def mark(*_args, **_kwargs):
        raise RuntimeError("ack failed")

    monkeypatch.setattr(worker_projection, "RedisStreamBridge", Bridge)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection, "get_terminal_intent", get_intent)
    monkeypatch.setattr(worker_projection, "publish_terminal_intent", publish)
    monkeypatch.setattr(worker_projection, "mark_terminal_intent_published", mark)
    caplog.set_level("WARNING")

    assert await worker_projection.publish_pending_run_terminal(
        _Transaction,
        tenant_id="tenant-a",
        run_id="run-a",
    ) is False
    record = next(record for record in caplog.records if record.message == "terminal_intent_publish_failed")
    assert record.reason_code == "intent_ack_failed"

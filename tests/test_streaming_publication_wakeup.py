import asyncio
import logging

import pytest

from app.streaming.infrastructure import publication_wakeup


@pytest.mark.parametrize("notify_during_drain", [True, False], ids=["retained-notification", "lost-notification-fallback"])
async def test_publication_listens_before_startup_drain_and_never_sleeps_with_backlog(monkeypatch, notify_during_drain):
    actions = []
    notification = asyncio.Event()

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            actions.append("closed")

        async def execute(self, sql):
            assert sql == "LISTEN ai_platform_stream_publication"
            actions.append("listen")

        async def notifies(self, *, timeout, stop_after):
            assert stop_after == 1
            actions.append("wait")
            try:
                await asyncio.wait_for(notification.wait(), timeout=timeout)
            except TimeoutError:
                return
            notification.clear()
            yield object()

    async def connect(*args, **kwargs):
        assert kwargs["autocommit"] is True
        return Connection()

    passes = 0

    async def drain():
        nonlocal passes
        passes += 1
        actions.append("drain")
        if passes == 1 and notify_during_drain:
            notification.set()
        if passes < 3:
            return 64
        if passes == 4:
            raise asyncio.CancelledError
        return 0

    monkeypatch.setattr(publication_wakeup.AsyncConnection, "connect", connect)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            publication_wakeup.run_publication_loop(
                "synthetic", drain, fallback_seconds=0.01, logger=logging.getLogger(__name__)
            ),
            timeout=1,
        )
    assert actions == ["listen", "drain", "drain", "drain", "wait", "drain", "closed"]


async def test_listener_failure_keeps_recovery_scanning_and_reconnect_scans_immediately(monkeypatch):
    actions = []
    attempts = 0

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            actions.append("closed")

        async def execute(self, sql):
            actions.append("listen")

    async def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic connection failure")
        return Connection()

    async def drain():
        actions.append("drain")
        if attempts == 2:
            raise asyncio.CancelledError
        return 0

    monkeypatch.setattr(publication_wakeup.AsyncConnection, "connect", connect)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            publication_wakeup.run_publication_loop(
                "synthetic", drain, fallback_seconds=0.01, logger=logging.getLogger(__name__)
            ),
            timeout=1,
        )
    assert actions == ["drain", "listen", "drain", "closed"]

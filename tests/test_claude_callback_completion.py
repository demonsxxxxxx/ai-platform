"""Platform callback finalizers settle before completion using the pinned Query."""

import asyncio

import pytest
from claude_agent_sdk._internal.query import Query

from app.execution.infrastructure.harness.claude_client_lifecycle import ClaudeInjectedCallbackTracker
from app.executors.claude_agent_sdk_runner import _SessionStoreAppendTracker


async def test_public_eof_seals_platform_callbacks_and_joins_their_finalizers():
    tracker = ClaudeInjectedCallbackTracker()
    started, release, finalized = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def callback(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()
            finalized.set()

    class Transport:
        async def read_messages(self):
            yield {"type": "control_request", "request_id": "request", "request": {
                "subtype": "hook_callback", "callback_id": "hook", "input": {},
            }}
            await started.wait()

        async def write(self, _value):
            pass

        async def close(self):
            pass

    query = Query(Transport(), is_streaming_mode=True)
    query.hook_callbacks["hook"] = tracker.wrap(callback)
    await query.start()
    seal = None
    try:
        assert [item async for item in query.receive_messages()] == []
        assert tracker.active_calls == 1
        seal = asyncio.create_task(tracker.seal_and_wait())
        await asyncio.sleep(0)
        assert not seal.done() and not finalized.is_set()
        # A callback queued by the SDK but not entered before EOF cannot write
        # new platform facts after a terminal result is published.
        with pytest.raises(RuntimeError, match="callback_after_stream_closed"):
            await tracker.wrap(callback)()
        release.set()
        await asyncio.wait_for(seal, 1)
        assert finalized.is_set() and tracker.active_calls == 0
    finally:
        release.set()
        if seal is not None:
            await asyncio.gather(seal, return_exceptions=True)
        await query.close()
        query.close_receive_stream()


async def test_repeated_seal_does_not_cancel_an_active_finalizer_twice():
    tracker = ClaudeInjectedCallbackTracker()
    started, finalizing, release, finalized = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    )

    async def callback():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await release.wait()
            finalized.set()

    callback_task = asyncio.create_task(tracker.wrap(callback)())
    first = second = None
    try:
        await started.wait()
        first = asyncio.create_task(tracker.seal_and_wait())
        await finalizing.wait()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(tracker.seal_and_wait())
        await asyncio.sleep(0)
        assert not second.done() and tracker.active_calls == 1
        release.set()
        await asyncio.wait_for(second, 1)
        assert finalized.is_set() and tracker.active_calls == 0
    finally:
        release.set()
        await asyncio.gather(
            callback_task, *(task for task in (first, second) if task is not None),
            return_exceptions=True,
        )


async def test_confirmed_retry_resolves_only_its_failed_session_store_batch():
    class Store:
        def __init__(self):
            self.unconfirmed = {"retry", "other"}

        async def append(self, _key, entries):
            if entries[0]["uuid"] in self.unconfirmed:
                raise RuntimeError("unconfirmed append")

    store = Store()
    tracked = _SessionStoreAppendTracker(store)
    for identifier in ("retry", "other"):
        with pytest.raises(RuntimeError):
            await tracked.append("session", [{"uuid": identifier}])
    await tracked.append("session", [{"uuid": "different"}])
    assert tracked.failed
    store.unconfirmed.remove("retry")
    await tracked.append("session", [{"uuid": "retry"}])
    assert tracked.failed
    store.unconfirmed.remove("other")
    await tracked.append("session", [{"uuid": "other"}])
    assert not tracked.failed

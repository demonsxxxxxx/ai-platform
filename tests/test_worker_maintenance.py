import asyncio
import logging
from contextlib import suppress

import pytest

from app.bootstrap.worker_maintenance import maintenance_phase_until_done


@pytest.mark.asyncio
async def test_timed_out_phase_remains_owned_until_cancelled_operation_finishes(caplog):
    cancellation_seen = asyncio.Event()
    release = asyncio.Event()
    other_progress = asyncio.Event()
    retried = asyncio.Event()
    calls = 0
    other_calls = 0

    async def slow_operation():
        nonlocal calls
        calls += 1
        if calls > 1:
            retried.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release.wait()

    async def other_operation():
        nonlocal other_calls
        other_calls += 1
        if other_calls >= 2:
            other_progress.set()

    logger = logging.getLogger("worker-maintenance-test")
    slow = asyncio.create_task(maintenance_phase_until_done(
        "slow", slow_operation, 0.001, 0.001, logger=logger,
    ))
    other = asyncio.create_task(maintenance_phase_until_done(
        "recovery", other_operation, 0.001, 1, logger=logger,
    ))
    try:
        await asyncio.wait_for(asyncio.gather(cancellation_seen.wait(), other_progress.wait()), 1)
        assert calls == 1
        assert not slow.done()
        assert "exceeded its time budget" in caplog.text
        release.set()
        await asyncio.wait_for(retried.wait(), 1)
    finally:
        release.set()
        slow.cancel()
        other.cancel()
        await asyncio.gather(slow, other, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_scheduler_closes_the_active_phase():
    started = asyncio.Event()
    closed = asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    task = asyncio.create_task(maintenance_phase_until_done(
        "cleanup", operation, 30, 30, logger=logging.getLogger(__name__),
    ))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_zero_repeat_interval_runs_initial_pass_and_stays_supervised():
    ran = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        ran.set()

    task = asyncio.create_task(maintenance_phase_until_done(
        "cleanup", operation, 0, 1, logger=logging.getLogger(__name__),
    ))
    try:
        await asyncio.wait_for(ran.wait(), 1)
        await asyncio.sleep(0)
        assert calls == 1
        assert not task.done()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

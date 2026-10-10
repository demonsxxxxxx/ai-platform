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
@pytest.mark.parametrize("repeat_on_progress", [False, True])
async def test_cancelling_scheduler_closes_the_active_phase(repeat_on_progress):
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
        repeat_on_progress=repeat_on_progress,
    ))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_on_progress", [False, True])
async def test_zero_repeat_interval_runs_initial_pass_and_stays_supervised(repeat_on_progress):
    ran = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        ran.set()
        return 1

    task = asyncio.create_task(maintenance_phase_until_done(
        "cleanup", operation, 0, 1, logger=logging.getLogger(__name__),
        repeat_on_progress=repeat_on_progress,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repeat_on_progress", "results", "expected_delays"),
    [
        (True, [1, 1, 0], [0, 0, 30]),
        (True, [1, RuntimeError("probe unavailable")], [0, 30]),
        (False, [1], [30]),
    ],
)
async def test_phase_drains_only_opted_in_progress_and_backs_off(
    monkeypatch, repeat_on_progress, results, expected_delays,
):
    idle = asyncio.Event()
    delays = []
    calls = []
    yielded = []
    original_sleep = asyncio.sleep

    async def observe_sleep(delay):
        delays.append(delay)
        if delay == 0:
            await original_sleep(0)
        else:
            idle.set()
            await asyncio.Event().wait()

    async def operation():
        # A ready callback must run between batches, even when the operation
        # completes without performing any I/O.
        assert yielded == calls
        calls.append(len(calls) + 1)
        asyncio.get_running_loop().call_soon(yielded.append, calls[-1])
        result = results[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.bootstrap.worker_maintenance.asyncio.sleep", observe_sleep)
    task = asyncio.create_task(maintenance_phase_until_done(
        "probe", operation, 30, 1, logger=logging.getLogger(__name__),
        repeat_on_progress=repeat_on_progress,
    ))
    try:
        await asyncio.wait_for(idle.wait(), 1)
        assert len(calls) == len(results)
        assert delays == expected_delays
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_timed_out_progress_backs_off_after_sole_attempt_finishes(monkeypatch):
    cancelled = asyncio.Event()
    release = asyncio.Event()
    idle = asyncio.Event()
    calls = 0
    delays = []

    async def operation():
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return 1

    async def observe_sleep(delay):
        delays.append(delay)
        idle.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.bootstrap.worker_maintenance.asyncio.sleep", observe_sleep)
    task = asyncio.create_task(maintenance_phase_until_done(
        "probe", operation, 30, 0.001, logger=logging.getLogger(__name__),
        repeat_on_progress=True,
    ))
    try:
        await asyncio.wait_for(cancelled.wait(), 1)
        assert calls == 1
        assert not idle.is_set()
        assert not task.done()
        release.set()
        await asyncio.wait_for(idle.wait(), 1)
        assert calls == 1
        assert delays == [30]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

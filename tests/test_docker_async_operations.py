"""Real-thread regression tests for the Docker provider's async boundary."""
import asyncio
import threading

import pytest

from app.platform.sandbox.docker_operations import (
    DockerOperationLane,
    DockerOperationUnavailable,
    docker_operation_checkpoint,
)


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_cancelled_sync_io_holds_capacity_until_actual_completion():
    lane = DockerOperationLane(capacity=1, name="test-docker-io")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        started.set()
        release.wait(3)
        finished.set()

    task = asyncio.create_task(lane.run(blocked, timeout=2))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        with pytest.raises(DockerOperationUnavailable, match="capacity"):
            await lane.run(lambda: None, timeout=1)
        release.set()
        await wait_until(finished.is_set)
        await wait_until(lambda: not lane._executor._threads or lane._slots._value == 1)
        assert await lane.run(lambda: "available", timeout=1) == "available"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lane.close()


@pytest.mark.asyncio
async def test_sync_deadline_does_not_release_capacity_or_block_loop():
    lane = DockerOperationLane(capacity=1, name="test-docker-deadline")
    release = threading.Event()
    task = asyncio.create_task(lane.run(lambda: release.wait(3), timeout=0.03))
    try:
        await asyncio.sleep(0)
        with pytest.raises(DockerOperationUnavailable, match="deadline"):
            await task
        with pytest.raises(DockerOperationUnavailable, match="capacity"):
            await lane.run(lambda: None, timeout=1)
    finally:
        release.set()
        lane.close()


@pytest.mark.asyncio
async def test_saturated_lifecycle_keeps_cleanup_lane_available_and_attempt_fenced():
    lifecycle = DockerOperationLane(capacity=1, name="test-docker-lifecycle")
    cleanup = DockerOperationLane(capacity=1, name="test-docker-cleanup", claims=lifecycle)
    started, release = threading.Event(), threading.Event()

    async def blocked():
        started.set()
        release.wait(3)

    task = asyncio.create_task(lifecycle.run_lifecycle(
        blocked, timeout=2, cleanup_timeout=0.02, key="attempt-a",
    ))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await cleanup.run(lambda: "cleaned", timeout=1, key="attempt-b") == "cleaned"
        with pytest.raises(DockerOperationUnavailable, match="already in progress"):
            await cleanup.run(lambda: None, timeout=1, key="attempt-a")
        with pytest.raises(DockerOperationUnavailable, match="capacity"):
            await lifecycle.run(lambda: None, timeout=1)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lifecycle.close()
        cleanup.close()


@pytest.mark.asyncio
async def test_cancelled_create_settles_then_compensates_before_key_is_reused():
    lane = DockerOperationLane(capacity=1, name="test-docker-create")
    started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    effects = []

    async def create():
        started.set()
        release.wait(3)
        effects.append("created")
        docker_operation_checkpoint()
        effects.append("started")
        return "lease"

    def compensate():
        effects.append("removed")
        cleaned.set()

    task = asyncio.create_task(lane.run_lifecycle(
        create, timeout=2, cleanup_timeout=0.02, key="attempt-a", on_cancel=compensate,
    ))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(DockerOperationUnavailable, match="already in progress"):
            await lane.run_lifecycle(create, timeout=1, cleanup_timeout=1, key="attempt-a")
        release.set()
        await wait_until(cleaned.is_set)
        assert effects == ["created", "removed"]
        await wait_until(lambda: lane._slots._value == 1)
        assert lane._keys == {}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lane.close()


@pytest.mark.asyncio
async def test_cancelled_synchronous_tail_cannot_publish_unclaimed_lease():
    lane = DockerOperationLane(capacity=1, name="test-docker-tail")
    started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    tracked = []

    async def create():
        started.set()
        release.wait(3)
        tracked.append("lease")
        return "lease"

    def compensate():
        tracked.clear()
        cleaned.set()

    task = asyncio.create_task(lane.run_lifecycle(
        create, timeout=2, cleanup_timeout=0.02, key="attempt-a", on_cancel=compensate,
    ))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await wait_until(cleaned.is_set)
        assert tracked == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lane.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_failure", [False, True])
async def test_caller_cancellation_only_yields_to_explicit_cleanup_failure(typed_failure):
    lane = DockerOperationLane(capacity=1, name="test-docker-cleanup-failure")
    started = threading.Event()

    class CleanupPending(Exception):
        pass

    async def operation():
        started.set()
        await asyncio.sleep(3)

    def compensate():
        raise CleanupPending() if typed_failure else RuntimeError("secondary worker failure")

    task = asyncio.create_task(lane.run_lifecycle(
        operation, timeout=2, cleanup_timeout=1, on_cancel=compensate,
        cleanup_errors=(CleanupPending,),
    ))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(CleanupPending if typed_failure else asyncio.CancelledError):
            await task
    finally:
        await asyncio.gather(task, return_exceptions=True)
        lane.close()


@pytest.mark.asyncio
async def test_cancellation_between_result_delivery_and_acceptance_retains_cleanup_owner(monkeypatch):
    import app.platform.sandbox.docker_operations as operations

    lane = DockerOperationLane(capacity=1, name="test-docker-result-race")
    loop = asyncio.get_running_loop()
    cleanup_started, cleanup_release, cleanup_finished = threading.Event(), threading.Event(), threading.Event()
    tracked = []

    class CancelOnDelivery(operations.Future):
        def set_result(self, value):
            super().set_result(value)
            # The asyncio result propagation callback is queued, but the
            # waiting caller cannot accept it before this queued cancellation.
            loop.call_soon_threadsafe(task.cancel)

    monkeypatch.setattr(operations, "Future", CancelOnDelivery)

    async def create():
        tracked.append("lease")
        return "lease"

    def compensate():
        cleanup_started.set()
        cleanup_release.wait(3)
        tracked.clear()
        cleanup_finished.set()

    task = asyncio.create_task(lane.run_lifecycle(
        create, timeout=2, cleanup_timeout=0.02, key="attempt-a", on_cancel=compensate,
    ))
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup_started.is_set()
        assert tracked == ["lease"]
        with pytest.raises(DockerOperationUnavailable, match="already in progress"):
            await lane.run(lambda: None, timeout=1, key="attempt-a")
        cleanup_release.set()
        await wait_until(cleanup_finished.is_set)
        await wait_until(lambda: lane._slots._value == 1)
        assert tracked == []
        assert lane._keys == {}
    finally:
        cleanup_release.set()
        await asyncio.gather(task, return_exceptions=True)
        lane.close()


@pytest.mark.asyncio
async def test_successful_lifecycle_handoff_does_not_spuriously_exhaust_capacity():
    lane = DockerOperationLane(capacity=1, name="test-docker-handoff")

    async def complete():
        return "lease"

    try:
        for _ in range(20):
            assert await lane.run_lifecycle(
                complete, timeout=1, cleanup_timeout=1, key="attempt-a",
            ) == "lease"
    finally:
        lane.close()


@pytest.mark.asyncio
async def test_cancelled_blocking_error_still_compensates_committed_side_effect():
    lane = DockerOperationLane(capacity=1, name="test-docker-cancel-error")
    started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    resources = []

    async def create():
        started.set()
        release.wait(3)
        resources.append("committed-with-lost-response")
        raise RuntimeError("SDK response lost")

    def compensate():
        resources.clear()
        cleaned.set()

    task = asyncio.create_task(lane.run_lifecycle(
        create, timeout=2, cleanup_timeout=0.02, key="attempt-a", on_cancel=compensate,
    ))
    try:
        await wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await wait_until(cleaned.is_set)
        assert resources == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lane.close()

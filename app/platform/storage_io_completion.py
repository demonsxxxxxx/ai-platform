"""One-call physical completion capability for bounded storage operations."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import Future
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_Result = TypeVar("_Result")


@dataclass(frozen=True)
class _PhysicalOutcome(Generic[_Result]):
    value: _Result | None = None
    error: BaseException | None = None

    def result(self) -> _Result:
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


class StorageIOCompletion:
    """Private per-call physical outcome; callers cannot cancel its worker.

    Used only by owners that must join physical work before releasing their
    claim. A cancelled asyncio waiter is not proof that a thread has stopped.
    """

    def __init__(self) -> None:
        self._claimed = False
        self._physical: asyncio.Future[_PhysicalOutcome[Any]] | None = None
        self._executor: asyncio.Future[None] | None = None
        self._outcome: Future[_PhysicalOutcome[Any]] | None = None

    @property
    def submitted(self) -> bool:
        return self._physical is not None

    @property
    def finished(self) -> bool:
        return self._outcome is not None and self._outcome.done()

    def claim(self) -> None:
        if self._claimed:
            raise ValueError("storage_io_completion_already_used")
        self._claimed = True

    def submit(self, operation: Callable[[], _Result]) -> asyncio.Task[_Result]:
        if not self._claimed or self.submitted:
            raise ValueError("storage_io_completion_not_available")
        loop = asyncio.get_running_loop()
        physical: asyncio.Future[_PhysicalOutcome[_Result]] = loop.create_future()
        context = contextvars.copy_context()
        outcome_future: Future[_PhysicalOutcome[_Result]] = Future()

        def execute() -> None:
            try:
                outcome = _PhysicalOutcome(value=operation())
            except BaseException as error:
                outcome = _PhysicalOutcome(error=error)
            outcome_future.set_result(outcome)
            try:
                loop.call_soon_threadsafe(physical.set_result, outcome)
            except RuntimeError:
                # A closed loop cannot erase the actual physical outcome.
                # Normal worker shutdown must settle the owner before closing.
                pass

        # Unlike an asyncio.to_thread Task, this private executor Future is not
        # cancelled by asyncio task shutdown. No caller receives its handle.
        self._executor = loop.run_in_executor(None, context.run, execute)
        self._physical = physical
        self._outcome = outcome_future

        async def wait() -> _Result:
            return (await asyncio.shield(physical)).result()

        waiter = wait()
        try:
            task = asyncio.create_task(waiter)
        except BaseException:
            waiter.close()
            raise
        task.add_done_callback(_retrieve_waiter_outcome)
        return task

    def add_done_callback(self, callback: Callable[[object], None]) -> None:
        if self._physical is None:
            raise RuntimeError("storage_io_completion_not_submitted")
        self._physical.add_done_callback(callback)

    def result(self) -> Any:
        if self._outcome is None:
            raise RuntimeError("storage_io_completion_not_submitted")
        return self._outcome.result().result()

    async def settle(self) -> None:
        """Join an already-submitted operation while preserving caller failure.

        Intended for exception unwinding. Repeated owner cancellation cannot
        release physical resources early; the original exception stays primary.
        """
        physical = self._physical
        if physical is None:
            return
        while not physical.done():
            try:
                await asyncio.shield(physical)
            except asyncio.CancelledError:
                continue
        # Retrieve both outcomes, without replacing the exception being unwound.
        physical.result()
        if self._executor is not None:
            while not self._executor.done():
                try:
                    await asyncio.shield(self._executor)
                except asyncio.CancelledError:
                    continue
            self._executor.result()


def _retrieve_waiter_outcome(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass

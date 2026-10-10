"""Public client shutdown and injected-callback lifecycle support."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar


Callback = TypeVar("Callback", bound=Callable[..., Awaitable[Any]])


class ClaudeInjectedCallbackTracker:
    """Track only callbacks the platform injects into the SDK."""

    def __init__(self) -> None:
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._sealed = False
        self._tasks: dict[asyncio.Task[Any], int] = {}

    @property
    def active_calls(self) -> int:
        return self._active

    def wrap(self, callback: Callback) -> Callback:
        @wraps(callback)
        async def tracked(*args: Any, **kwargs: Any) -> Any:
            if self._sealed:
                raise RuntimeError("claude_callback_after_stream_closed")
            task = asyncio.current_task()
            self._active += 1
            self._idle.clear()
            if task is not None:
                self._tasks[task] = self._tasks.get(task, 0) + 1
            try:
                return await callback(*args, **kwargs)
            finally:
                self._active -= 1
                if task is not None:
                    depth = self._tasks[task] - 1
                    if depth:
                        self._tasks[task] = depth
                    else:
                        del self._tasks[task]
                if self._active == 0:
                    self._idle.set()

        return tracked  # type: ignore[return-value]

    async def wait_idle(self) -> None:
        await self._idle.wait()

    async def seal_and_wait(self) -> None:
        # EOF prevents further protocol work. Reject callbacks that the SDK
        # scheduled but has not entered, and join our active cancellation
        # finalizers before publishing a stable Run result.
        if not self._sealed:
            self._sealed = True
            current = asyncio.current_task()
            for task in tuple(self._tasks):
                if task is not current:
                    task.cancel()
        # An interrupted waiter may enter this barrier again. Only the first
        # seal sends cancellation; finalizers retain their single cancellation.
        await self.wait_idle()


class ClaudeClientCloseBoundary:
    """Observe SessionStore mirror errors and delegate physical close publicly.

    The pinned SDK has no public mirror-error callback. Keep this single
    version-sensitive observer isolated here; the public receive iterator EOF
    and the platform callback trackers define protocol completion.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self.mirror_failed = False
        self._bound = False

    def bind(self) -> None:
        if self._bound:
            return
        self._bound = True
        query = getattr(self.client, "_query", None)
        batcher = getattr(query, "_transcript_mirror_batcher", None)
        if batcher is None:
            return
        report_error = batcher.on_error

        async def on_mirror_error(key: Any, error: str) -> None:
            self.mirror_failed = True
            await report_error(key, error)

        batcher.on_error = on_mirror_error

    async def interrupt(self) -> None:
        await self.client.interrupt()

    async def disconnect(self) -> None:
        await self.client.disconnect()

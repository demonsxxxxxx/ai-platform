"""Track Claude SessionStore receipts and bounded append observations."""

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from app.platform.tracing import log_session_store_append_summary

_MAX_OBSERVATION = 1_000_000_000


class SessionStoreAppendTracker:
    def __init__(self, store: Any) -> None:
        self._append_calls = self._append_failures = 0
        self._append_total_seconds = self._append_max_seconds = 0.0
        self._summary_logged = False
        self._store = store
        self.main_append_acknowledged = False
        self.final_sequence: int | None = None
        self._failed_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.active_calls = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def _call(self, operation: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        self.active_calls += 1
        self._idle.clear()
        try:
            result = await operation(*args)
        except BaseException:
            failure = (operation.__name__, args)
            if failure not in self._failed_calls:
                self._failed_calls.append(failure)
            raise
        else:
            # The SDK retries an identical mirror batch. A confirmed receipt
            # resolves that failure; unrelated successful writes do not.
            self._failed_calls = [item for item in self._failed_calls if item != (operation.__name__, args)]
            return result
        finally:
            self.active_calls -= 1
            if self.active_calls == 0:
                self._idle.set()

    @property
    def failed(self) -> bool:
        return bool(self._failed_calls)

    async def wait_idle(self) -> None:
        await self._idle.wait()

    async def load(self, key: Any) -> Any:
        return await self._call(self._store.load, key)

    async def append(self, key: Any, entries: Any) -> None:
        started = monotonic()
        self._append_calls = min(self._append_calls + 1, _MAX_OBSERVATION)
        try:
            await self._call(self._store.append, key, entries)
        except BaseException:
            self._append_failures = min(self._append_failures + 1, _MAX_OBSERVATION)
            raise
        finally:
            elapsed = max(monotonic() - started, 0.0)
            self._append_total_seconds = min(self._append_total_seconds + elapsed, _MAX_OBSERVATION)
            self._append_max_seconds = min(max(self._append_max_seconds, elapsed), _MAX_OBSERVATION)
        sequence = getattr(self._store, "accepted_final_sequence", None)
        if type(sequence) is int and sequence >= 1:
            self.final_sequence = sequence
        subpath = key.get("subpath") if isinstance(key, dict) else None
        if not subpath:
            self.main_append_acknowledged = True

    async def list_subkeys(self, key: Any = None) -> Any:
        return await self._call(self._store.list_subkeys, key)


    def log_append_summary(self, *, run_id: str, attempt_id: str) -> None:
        """Report full-run append work, not a terminal-tail duration."""
        if self._summary_logged:
            return
        self._summary_logged = True
        log_session_store_append_summary(
            run_id=run_id, attempt_id=attempt_id,
            calls=self._append_calls, failed_calls=self._append_failures,
            total_ms=min(int(self._append_total_seconds * 1000), _MAX_OBSERVATION),
            max_ms=min(int(self._append_max_seconds * 1000), _MAX_OBSERVATION),
            active_calls=min(self.active_calls, _MAX_OBSERVATION),
        )

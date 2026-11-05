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
        self._public_message_at: float | None = None
        self._gap_append_seconds = 0.0
        self._active_append_starts: dict[object, float] = {}
        self._latest_result_gap_ms: int | None = None
        self._append_in_latest_result_gap_ms: int | None = None
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

    @property
    def ack_unknown(self) -> bool:
        receipt = getattr(self._store, "continuation_receipt", None)
        if isinstance(receipt, dict):
            return receipt.get("ack_unknown") is not False
        return self.failed or bool(self._active_append_starts)

    async def wait_idle(self) -> None:
        await self._idle.wait()

    async def load(self, key: Any) -> Any:
        return await self._call(self._store.load, key)

    async def append(self, key: Any, entries: Any) -> None:
        started = monotonic()
        observation = object()
        self._active_append_starts[observation] = started
        self._append_calls = min(self._append_calls + 1, _MAX_OBSERVATION)
        try:
            await self._call(self._store.append, key, entries)
        except BaseException:
            self._append_failures = min(self._append_failures + 1, _MAX_OBSERVATION)
            raise
        finally:
            finished = monotonic()
            self._active_append_starts.pop(observation, None)
            elapsed = max(finished - started, 0.0)
            if self._public_message_at is not None:
                self._gap_append_seconds = min(
                    self._gap_append_seconds + max(finished - max(started, self._public_message_at), 0.0),
                    _MAX_OBSERVATION,
                )
            self._append_total_seconds = min(self._append_total_seconds + elapsed, _MAX_OBSERVATION)
            self._append_max_seconds = min(max(self._append_max_seconds, elapsed), _MAX_OBSERVATION)
        sequence = getattr(self._store, "accepted_final_sequence", None)
        if type(sequence) is int and sequence >= 1:
            self.final_sequence = sequence
        subpath = key.get("subpath") if isinstance(key, dict) else None
        if not subpath:
            self.main_append_acknowledged = True

    def observe_public_message(self, *, is_result: bool) -> None:
        """Observe SDK-public message gaps, never raw Result or native fsync.

        Snapshot the latest Result window before resetting for the next message.
        Append call intervals are clipped to the window, including in-flight
        calls. Concurrent intervals are cumulative, not a union or causal claim.
        """
        now = monotonic()
        if is_result:
            previous = self._public_message_at
            self._latest_result_gap_ms = (
                min(int(max(now - previous, 0.0) * 1000), _MAX_OBSERVATION)
                if previous is not None else None
            )
            self._append_in_latest_result_gap_ms = (
                min(int((self._gap_append_seconds + sum(
                    max(now - max(started, previous), 0.0)
                    for started in self._active_append_starts.values()
                )) * 1000), _MAX_OBSERVATION)
                if previous is not None else None
            )
        self._public_message_at = now
        self._gap_append_seconds = 0.0

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
            latest_public_result_gap_ms=self._latest_result_gap_ms,
            append_await_in_latest_result_gap_ms=self._append_in_latest_result_gap_ms,
        )

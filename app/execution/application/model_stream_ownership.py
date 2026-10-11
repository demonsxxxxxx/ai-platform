"""Own synchronous model streams across cancellation and ASGI handoff."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Protocol, TypeVar


_logger = logging.getLogger(__name__)


class ModelResponseStream(Protocol):
    status: int
    content_type: str

    def body(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


_Stream = TypeVar("_Stream", bound=ModelResponseStream)


def close_model_stream(close: Callable[[], None]) -> None:
    """Attempt owned cleanup without replacing a primary cancellation/error."""
    try:
        close()
    except Exception:
        # Provider exception messages can contain endpoint or credential data.
        try:
            _logger.warning("model_proxy_stream_cleanup_failed")
        except Exception:
            pass


async def open_owned_model_stream(operation: Callable[[], _Stream]) -> _Stream:
    """Give either the caller or the existing worker a late result's close duty.

    pending -> delivered -> accepted; cancellation makes pending/delivered
    abandoned. The lock protects only handoff, never opening or closing I/O.
    """
    lock = threading.Lock()
    abandoned = False
    delivered: _Stream | None = None

    def open_and_deliver() -> None:
        nonlocal delivered
        with lock:
            if abandoned:
                return
        stream = operation()
        with lock:
            rejected = abandoned
            if not rejected:
                delivered = stream
        if rejected:
            close_model_stream(stream.close)

    try:
        await asyncio.to_thread(open_and_deliver)
        with lock:
            stream = delivered
            delivered = None
        if stream is None:
            raise RuntimeError("model_proxy_stream_handoff_missing")
        return stream
    except BaseException:
        with lock:
            abandoned = True
            stream = delivered
            delivered = None
        if stream is not None:
            close_model_stream(stream.close)
        raise


class OwnedModelResponseBody(Iterator[bytes]):
    """Close resources now, but only close an iterator outside its active next.

    A synchronous socket close need not wake a blocking read. Its worker keeps
    the iterator until next returns, then performs deferred iterator cleanup.
    """

    def __init__(self, body: Iterable[bytes], *, close: Callable[[], None]) -> None:
        self._iterator = iter(body)
        self._resource_close = close
        self._lock = threading.Lock()
        self._reading = False
        self._closed = False
        self._iterator_closed = False
        self._resource_closed = False

    def __iter__(self) -> OwnedModelResponseBody:
        return self

    def __next__(self) -> bytes:
        with self._lock:
            if self._closed:
                raise StopIteration
            if self._reading:
                raise RuntimeError("model_proxy_body_already_reading")
            self._reading = True
        try:
            chunk = next(self._iterator)
        except BaseException:
            with self._lock:
                self._closed = True
            raise
        finally:
            with self._lock:
                self._reading = False
                closing = self._closed
            if closing:
                close_model_stream(self.close)
        with self._lock:
            if self._closed:
                raise StopIteration
        return chunk

    def close(self) -> None:
        with self._lock:
            self._closed = True
            close_iterator = not self._reading and not self._iterator_closed
            close_resource = not self._resource_closed
            if close_iterator:
                self._iterator_closed = True
            if close_resource:
                self._resource_closed = True
        # Never call generator.close() while another thread is inside next().
        # Its reader will revisit close after leaving the iterator.
        try:
            if close_iterator:
                close = getattr(self._iterator, "close", None)
                if close is not None:
                    close()
        finally:
            if close_resource:
                self._resource_close()

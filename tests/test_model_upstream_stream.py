from __future__ import annotations

import http.client
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.execution.infrastructure.model_upstream import (
    ModelUpstreamError,
    UpstreamStream,
)


@pytest.mark.parametrize("chunked", [True, False])
def test_small_event_is_forwarded_before_upstream_response_completes(
    chunked: bool,
) -> None:
    first = b"data: first\n\n"
    last = b"data: last\n\n"
    if chunked:
        framing = b"Transfer-Encoding: chunked\r\n"
        initial = f"{len(first):x}\r\n".encode() + first + b"\r\n"
        tail = f"{len(last):x}\r\n".encode() + last + b"\r\n0\r\n\r\n"
    else:
        framing = f"Content-Length: {len(first) + len(last)}\r\n".encode()
        initial, tail = first, last
    closed = []
    with _socket_pair() as (upstream, downstream):
        # Use HTTPResponse's actual parser; a fake read returning individual
        # chunks cannot expose read(size)'s buffering across wire chunks.
        downstream.settimeout(3)
        upstream.sendall(
            b"HTTP/1.1 200 OK\r\n"
            + framing
            + b"Content-Type: text/event-stream\r\n\r\n"
            + initial
        )
        response = http.client.HTTPResponse(downstream)
        response.begin()
        stream = UpstreamStream(
            200,
            "text/event-stream",
            response,
            SimpleNamespace(close=lambda: closed.append(True)),
            1024,
        )
        body = stream.body()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(next, body)
                try:
                    assert pending.result(timeout=1) == first
                finally:
                    # Unblock the regression's old buffering behavior even if
                    # the assertion times out, then join the owned reader.
                    upstream.sendall(tail)
            assert b"".join(body) == last
        finally:
            body.close()
            response.close()
        assert response.isclosed()
        assert closed == [True]


@contextmanager
def _socket_pair():
    upstream, downstream = socket.socketpair()
    try:
        yield upstream, downstream
    finally:
        upstream.close()
        downstream.close()


def test_stream_limit_is_cumulative_and_does_not_yield_oversized_chunk() -> None:
    chunks = iter((b"123", b"456", b""))
    closed = []
    response = SimpleNamespace(
        read1=lambda _size: next(chunks), close=lambda: closed.append("response")
    )
    stream = UpstreamStream(
        200,
        "text/event-stream",
        response,
        SimpleNamespace(close=lambda: closed.append("connection")),
        5,
    )
    body = stream.body()
    assert next(body) == b"123"
    with pytest.raises(ModelUpstreamError, match="model_upstream_response_too_large"):
        next(body)
    assert closed == ["response", "connection"]


def test_closing_started_stream_closes_response_and_connection() -> None:
    closed = []
    response = SimpleNamespace(
        read1=lambda _size: b"event", close=lambda: closed.append("response")
    )
    stream = UpstreamStream(
        200,
        "text/event-stream",
        response,
        SimpleNamespace(close=lambda: closed.append("connection")),
        1024,
    )
    body = stream.body()
    assert next(body) == b"event"
    body.close()
    assert closed == ["response", "connection"]


def test_read_failure_closes_response_and_connection() -> None:
    closed = []

    def fail(_size):
        raise TimeoutError("synthetic idle timeout")

    response = SimpleNamespace(read1=fail, close=lambda: closed.append("response"))
    stream = UpstreamStream(
        200,
        "text/event-stream",
        response,
        SimpleNamespace(close=lambda: closed.append("connection")),
        1024,
    )
    with pytest.raises(TimeoutError, match="synthetic idle timeout"):
        next(stream.body())
    assert closed == ["response", "connection"]


def test_connection_is_closed_even_if_response_close_fails() -> None:
    closed = []

    def fail_close():
        raise OSError("synthetic close failure")

    response = SimpleNamespace(read1=lambda _size: b"", close=fail_close)
    stream = UpstreamStream(
        200,
        "text/event-stream",
        response,
        SimpleNamespace(close=lambda: closed.append(True)),
        1024,
    )
    with pytest.raises(OSError, match="synthetic close failure"):
        next(stream.body())
    assert closed == [True]

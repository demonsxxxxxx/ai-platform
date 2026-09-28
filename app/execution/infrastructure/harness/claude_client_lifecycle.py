"""The protocol/transport shutdown seam of the pinned Claude SDK 0.2.130.

Keep the SDK-created client and transport: supplying a custom transport at
construction would bypass native SessionStore resume materialization.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class ClaudeClientCloseBoundary:
    def __init__(self, client: Any, on_protocol_closed: Callable[[bool], Awaitable[None]]):
        self.client = client
        self.on_protocol_closed = on_protocol_closed
        self.mirror_failed = False
        self._query = None
        self._transport = None
        self._children = ()
        self._transport_close_started = False

    def bind(self) -> None:
        # A failed connection (and injected test clients) may have no Query.
        self._query = getattr(self.client, "_query", None)
        if self._query is None:
            return
        self._transport = self._query.transport
        batcher = self._query._transcript_mirror_batcher
        if batcher is not None:
            report_error = batcher.on_error

            async def on_mirror_error(key, error):
                self.mirror_failed = True
                await report_error(key, error)

            batcher.on_error = on_mirror_error
        self._query.transport = _ClosingTransport(self)

    async def disconnect(self) -> None:
        if self._query is not None:
            # Query.close cancels these handles but does not join them. Keep
            # the handles even when their done callbacks remove them from Query.
            self._children = tuple(self._query._child_tasks)
        try:
            await self.client.disconnect()
            if self._query is None:
                await self.on_protocol_closed(self.mirror_failed)
        finally:
            if self._transport is not None and not self._transport_close_started:
                # Failure in protocol shutdown still owns physical cleanup;
                # it must never announce a successful protocol barrier.
                await self._transport.close()

    async def close_transport(self) -> None:
        self._transport_close_started = True
        try:
            # Query has now flushed the mirror, stopped/joined its reader, and
            # closed its message producer. Join cancelled control callbacks too.
            outcomes = await asyncio.gather(
                *(child.wait() for child in self._children), return_exceptions=True
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            await self.on_protocol_closed(self.mirror_failed)
        finally:
            await self._transport.close()


class _ClosingTransport:
    def __init__(self, boundary: ClaudeClientCloseBoundary):
        self._boundary = boundary

    def __getattr__(self, name: str) -> Any:
        return getattr(self._boundary._transport, name)

    async def close(self) -> None:
        await self._boundary.close_transport()

from __future__ import annotations
from builtins import anext


def native_client_factory(message_source):
    """Adapt a test message source to the ClaudeSDKClient lifecycle."""

    class NativeClient:
        def __init__(self, options):
            self._options = options
            self._messages = None
            self._input_stream = None

        async def connect(self, prompt):
            self._input_stream = prompt
            first = await anext(prompt)

            async def initial_message():
                yield first

            self._messages = message_source(prompt=initial_message(), options=self._options)

        async def query(self, prompt, session_id="default"):
            assert session_id
            self._messages = message_source(prompt=prompt, options=self._options)

        async def receive_messages(self):
            async for message in self._messages:
                yield message
            # Match the public SDK EOF contract: an interactive input producer
            # must finish before the native receive stream can close.
            async for _message in self._input_stream:
                raise AssertionError("unexpected additional initial-stream message")

        async def disconnect(self):
            if self._messages is not None:
                await self._messages.aclose()
            if self._input_stream is not None:
                await self._input_stream.aclose()

    return NativeClient

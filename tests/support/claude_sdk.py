from __future__ import annotations


def native_client_factory(message_source):
    """Adapt a test message source to the ClaudeSDKClient lifecycle."""

    class NativeClient:
        def __init__(self, options):
            self._options = options
            self._messages = None

        async def connect(self):
            return None

        async def get_context_usage(self):
            return {"totalTokens": 0}

        async def set_permission_mode(self, _mode):
            return None

        async def query(self, prompt, session_id="default"):
            assert session_id
            self._messages = message_source(prompt=prompt, options=self._options)

        async def receive_response(self):
            async for message in self._messages:
                yield message

        async def disconnect(self):
            if self._messages is not None:
                await self._messages.aclose()

    return NativeClient

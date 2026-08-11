from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def opaque_transaction() -> AsyncIterator[object]:
    """Yield a fresh opaque connection when every database operation is mocked."""
    yield object()

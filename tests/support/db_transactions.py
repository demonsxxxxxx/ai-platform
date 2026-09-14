import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Opt PostgreSQL integration modules into a psycopg-compatible loop."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


class OpaqueConnection:
    """Nominal connection marker for tests that mock every database operation."""


@asynccontextmanager
async def opaque_transaction() -> AsyncIterator[object]:
    """Yield a fresh opaque connection when every database operation is mocked."""
    yield object()


@asynccontextmanager
async def opaque_connection_transaction() -> AsyncIterator[OpaqueConnection]:
    """Yield a fresh typed marker when a test asserts the connection type."""
    yield OpaqueConnection()

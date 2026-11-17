"""Keep a work group's lifetime attached to all of its asynchronous operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_completed(*operations: Awaitable[T]) -> list[T]:
    """Wait for every child on failure or caller cancellation before returning."""
    group = asyncio.gather(*operations, return_exceptions=True)
    try:
        outcomes = await asyncio.shield(group)
    except asyncio.CancelledError:
        while not group.done():
            try:
                await asyncio.shield(group)
            except asyncio.CancelledError:
                continue
        # Retrieve all outcomes even when the caller no longer needs them.
        group.result()
        raise
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return outcomes  # type: ignore[return-value]

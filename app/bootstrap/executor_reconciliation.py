"""Compose executor reconciliation with Sandbox-owned persistence capabilities."""

import asyncio
from typing import Any

from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
from app.executor_reconciler import run_executor_terminal_reconciler as _run_executor_terminal_reconciler


async def run_executor_terminal_reconciler(
    stop_event: asyncio.Event,
    **kwargs: Any,
) -> None:
    await _run_executor_terminal_reconciler(
        stop_event,
        lease_port=build_sandbox_executor_reconciliation_port(),
        **kwargs,
    )

"""Durably deliver bounded executor cancellation requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

_MAX_DELIVERY_LIMIT = 20
_SCAN_BUDGET = 100
# Endpoint recovery performs fresh provider identity/network inspections before
# the five-second HTTP request. Keep room for that work within one batch budget.
_SEND_DEADLINE_SECONDS = 25.0
_CLAIM_STALE_AFTER_SECONDS = 60


class SandboxCancelDeliveryRepository(Protocol):
    async def stage_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str
    ) -> int: ...

    async def claim_due(
        self,
        conn: Any,
        *,
        tenant_id: str | None,
        run_id: str | None,
        limit: int,
        stale_after_seconds: int,
        after_lease_key: tuple[str, str, str] | None,
        scan_budget: int,
    ) -> tuple[list[dict[str, Any]], tuple[str, str, str] | None]: ...

    async def record_delivery_result(
        self,
        conn: Any,
        *,
        claimed_row: Mapping[str, Any],
        accepted: bool,
        error_code: str,
    ) -> str | None: ...


SendCancel = Callable[[dict[str, Any]], Awaitable[object]]
TransactionFactory = Callable[[], Any]


class SandboxCancelDeliveryService:
    """Persist cancellation intent separately from provider HTTP and Run state."""

    def __init__(
        self,
        repository: SandboxCancelDeliveryRepository,
        transaction_factory: TransactionFactory,
        send_cancel: SendCancel,
    ) -> None:
        self._repository = repository
        self._transaction_factory = transaction_factory
        self._send_cancel = send_cancel
        self._global_scan_cursor: tuple[str, str, str] | None = None
        self._global_scan_lock = asyncio.Lock()

    async def stage_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> int:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (tenant_id, run_id, attempt_id)
        ):
            raise ValueError("sandbox_cancel_delivery_scope_invalid")
        return await self._repository.stage_in_transaction(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )

    async def deliver_once(
        self,
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> int:
        if (tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id.strip())):
            raise ValueError("sandbox_cancel_delivery_scope_invalid")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError("sandbox_cancel_delivery_scope_invalid")
        if type(limit) is not int or not 1 <= limit <= _MAX_DELIVERY_LIMIT:
            raise ValueError("sandbox_cancel_delivery_limit_invalid")

        if tenant_id is None and run_id is None:
            # The in-memory cursor only spreads global scans across bounded
            # pages. It is not claim authority; a restart simply scans again.
            async with self._global_scan_lock:
                async with self._transaction_factory() as conn:
                    claimed_rows, next_cursor = await self._repository.claim_due(
                        conn,
                        tenant_id=None,
                        run_id=None,
                        limit=limit,
                        stale_after_seconds=_CLAIM_STALE_AFTER_SECONDS,
                        after_lease_key=self._global_scan_cursor,
                        scan_budget=_SCAN_BUDGET,
                    )
                self._global_scan_cursor = next_cursor
        else:
            # Explicit tenant/run delivery starts in its own scope and never
            # inherits the global scheduler's position.
            async with self._transaction_factory() as conn:
                claimed_rows, _ = await self._repository.claim_due(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    limit=limit,
                    stale_after_seconds=_CLAIM_STALE_AFTER_SECONDS,
                    after_lease_key=None,
                    scan_budget=_SCAN_BUDGET,
                )
        if not claimed_rows:
            return 0

        # Calls share a single deadline, so a full batch cannot hold an HTTP
        # worker for the entire budget per lease. Claims survive process death and
        # are recovered by the repository's stale-claim fence.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SEND_DEADLINE_SECONDS

        async def send_one(row: dict[str, Any]) -> object:
            async with asyncio.timeout_at(deadline):
                public_claim = {
                    key: value
                    for key, value in row.items()
                    if key != "cancel_delivery_claim_token"
                }
                return await self._send_cancel(public_claim)

        try:
            responses = await asyncio.gather(
                *(send_one(row) for row in claimed_rows),
                return_exceptions=True,
            )
        except TimeoutError:
            responses = [TimeoutError() for _ in claimed_rows]

        completed = 0
        for row, response in zip(claimed_rows, responses, strict=True):
            accepted = _valid_cancel_response(row, response)
            if isinstance(response, TimeoutError):
                error_code = "timeout"
            elif isinstance(response, BaseException):
                error_code = "provider_error"
            elif accepted:
                error_code = ""
            else:
                error_code = "invalid_response"

            async with self._transaction_factory() as conn:
                outcome = await self._repository.record_delivery_result(
                    conn,
                    claimed_row=row,
                    accepted=accepted,
                    error_code=error_code,
                )
            if outcome is not None:
                completed += 1
        return completed


def _valid_cancel_response(row: Mapping[str, Any], response: object) -> bool:
    if not isinstance(response, Mapping) or set(response) != {
        "status",
        "run_id",
        "attempt_id",
    }:
        return False
    return (
        all(
            type(response.get(key)) is str
            for key in ("status", "run_id", "attempt_id")
        )
        and response.get("status") == "cancel_requested"
        and response.get("run_id") == row.get("run_id")
        and response.get("attempt_id") == row.get("attempt_id")
    )

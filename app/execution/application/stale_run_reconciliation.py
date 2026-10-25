"""Execution-owned PostgreSQL stale Run claim workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
from typing import Any

from app.execution.application.stale_terminalization import stage_stale_run_reconciliation
from app.runs.api import (
    RunAttemptLifecycleService,
    RunLifecycleService,
    finish_run_terminalization,
)
from app.streaming.api import WorkerV4Capabilities


logger = logging.getLogger(__name__)


class StaleRunClaimLost(RuntimeError):
    """The worker cannot prove its PostgreSQL stale-Run claim is current."""


class _PostgresClaimGuard:
    """Renew one claim while its bounded database work is in flight."""

    def __init__(
        self,
        lifecycle: RunLifecycleService,
        claim: dict[str, Any],
        *,
        transaction_factory: Callable[[], Any],
        ttl_seconds: int,
    ) -> None:
        self._lifecycle = lifecycle
        self._claim = claim
        self._transaction_factory = transaction_factory
        self._ttl_seconds = max(int(ttl_seconds), 1)
        self._renew_interval_seconds = min(max(self._ttl_seconds / 3, 1.0), 10.0)
        self._stop = asyncio.Event()
        self._lost = False
        self._renewal_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _PostgresClaimGuard:
        await self.ensure_live()
        self._renewal_task = asyncio.create_task(self._renew_until_done())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal_task
        return False

    async def _renew_once(self) -> bool:
        if self._lost:
            return False
        try:
            async with self._transaction_factory() as conn:
                renewed = await self._lifecycle.renew_stale_run_reconciliation_claim(
                    conn,
                    claim=self._claim,
                    claim_ttl_seconds=self._ttl_seconds,
                )
        except Exception:
            logger.exception(
                "stale_run_postgres_claim_renewal_failed",
                extra={"run_id": self._claim["run_id"]},
            )
            self._lost = True
            return False
        if not renewed:
            self._lost = True
        return renewed

    async def _renew_until_done(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._renew_interval_seconds)
                return
            except TimeoutError:
                if not await self._renew_once():
                    return

    async def ensure_live(self, conn: Any | None = None, *, for_update: bool = False) -> None:
        if self._lost:
            raise StaleRunClaimLost(str(self._claim["run_id"]))
        if conn is None:
            async with self._transaction_factory() as claim_conn:
                current = await self._lifecycle.stale_run_reconciliation_claim_is_current(
                    claim_conn,
                    claim=self._claim,
                    for_update=for_update,
                )
        else:
            current = await self._lifecycle.stale_run_reconciliation_claim_is_current(
                conn,
                claim=self._claim,
                for_update=for_update,
            )
        if not current:
            self._lost = True
            raise StaleRunClaimLost(str(self._claim["run_id"]))

    async def release_owned(self) -> bool:
        """Delete only this exact token, including after its Run was terminalized."""
        try:
            async with self._transaction_factory() as conn:
                return await self._lifecycle.release_stale_run_reconciliation_claim(
                    conn, claim=self._claim
                )
        except Exception:
            logger.exception(
                "stale_run_postgres_claim_release_failed",
                extra={"run_id": self._claim["run_id"]},
            )
            return False


def _claimed_transaction_factory(
    claim_guard: _PostgresClaimGuard,
    *,
    transaction_factory: Callable[[], Any],
):
    """Take Run, Attempt, and claim locks in that order for final writeback."""

    @contextlib.asynccontextmanager
    async def claimed_transaction():
        await claim_guard.ensure_live()
        async with transaction_factory() as conn:
            # The persistence check locks Run, Sandbox receipts, Attempt, then
            # the claim row. Those locks stay held through the terminal CAS.
            await claim_guard.ensure_live(conn, for_update=True)
            yield conn
            # The binding is consumed by terminalization, so the held claim-row
            # lock is the commit fence; rechecking Run/Attempt here would reject
            # the successful terminal transition and roll it back.

    return claimed_transaction


class StaleRunReconciliationService:
    """Reconcile stale Runs through durable per-Run PostgreSQL claims."""

    def __init__(
        self,
        *,
        lifecycle: RunLifecycleService,
        attempt_lifecycle: RunAttemptLifecycleService,
        v4_capabilities: WorkerV4Capabilities,
        transaction_factory: Callable[[], Any],
        append_event: Callable[..., Any],
        append_audit_log: Callable[..., Any],
    ) -> None:
        self._lifecycle = lifecycle
        self._attempt_lifecycle = attempt_lifecycle
        self._v4_capabilities = v4_capabilities
        self._transaction_factory = transaction_factory
        self._append_event = append_event
        self._append_audit_log = append_audit_log

    async def reconcile_postgres_claims(
        self,
        *,
        worker_id: str,
        limit: int,
        stale_after_seconds: int,
        cancel_requested_after_seconds: int,
        claim_ttl_seconds: int,
    ) -> list[dict[str, object]]:
        async with self._transaction_factory() as conn:
            candidates = await self._lifecycle.list_stale_run_reconciliation_candidates(
                conn,
                stale_after_seconds=stale_after_seconds,
                cancel_requested_after_seconds=cancel_requested_after_seconds,
                limit=limit,
                postgres_claim_mode=True,
            )

        results: list[dict[str, object]] = []
        for candidate in candidates:
            tenant_id = str(candidate.get("tenant_id") or "")
            workspace_id = str(candidate.get("workspace_id") or "")
            user_id = candidate.get("user_id")
            run_id = str(candidate.get("run_id") or "")
            expected_status = str(candidate.get("status") or "")
            if not tenant_id or not workspace_id or not run_id or expected_status != "running":
                continue

            claim: dict[str, Any] | None = None
            staged: dict[str, Any] | None = None
            attempt_id: str | None = None
            terminal_status = "failed"
            error_code = "stale_run_interrupted"
            error_message = "Run interrupted because no live execution owner remains."
            try:
                async with self._transaction_factory() as conn:
                    claim = await self._lifecycle.claim_stale_run_reconciliation(
                        conn,
                        candidate=candidate,
                        worker_id=worker_id,
                        stale_after_seconds=stale_after_seconds,
                        cancel_requested_after_seconds=cancel_requested_after_seconds,
                        claim_ttl_seconds=claim_ttl_seconds,
                    )
                    if claim is not None:
                        terminal_status = (
                            "cancelled" if claim.get("cancel_requested_at") else "failed"
                        )
                        error_code = None if terminal_status == "cancelled" else "stale_run_interrupted"
                        error_message = (
                            None
                            if terminal_status == "cancelled"
                            else "Run interrupted because no live execution owner remains."
                        )
                        staged = await stage_stale_run_reconciliation(
                            conn,
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            user_id=str(user_id) if user_id is not None else None,
                            run_id=run_id,
                            expected_status=expected_status,
                            stale_before=claim.get("stale_before"),
                            cancel_requested_before=claim.get("cancel_requested_at"),
                            terminal_status=terminal_status,
                            error_code=error_code,
                            error_message=error_message,
                            claim_token=str(claim["claim_token"]),
                            expected_attempt_id=claim.get("expected_attempt_id"),
                            expected_owner_generation=int(claim["expected_owner_generation"]),
                            append_event=self._append_event,
                            append_audit_log=self._append_audit_log,
                        )
                        if staged is None:
                            await self._lifecycle.release_stale_run_reconciliation_claim(
                                conn, claim=claim
                            )
                        else:
                            attempt = await self._attempt_lifecycle.prepare_stale_reconciliation(
                                conn,
                                tenant_id=tenant_id,
                                run_id=run_id,
                                terminal_status=terminal_status,
                                reconciler_id="stale-run-maintenance",
                            )
                            attempt_id = str(attempt.get("id") or "") if attempt else None
                            prepared_generation = (
                                int(attempt["owner_generation"]) if attempt is not None else 0
                            )
                            expected_generation = int(claim["expected_owner_generation"])
                            if (
                                attempt_id != claim.get("expected_attempt_id")
                                or prepared_generation not in {
                                    expected_generation,
                                    expected_generation + 1,
                                }
                            ):
                                raise RuntimeError(
                                    "stale_run_reconciliation_attempt_binding_changed"
                                )
            except Exception:
                logger.exception(
                    "stale_run_postgres_claim_transaction_failed",
                    extra={"run_id": run_id},
                )
                results.append(
                    {"tenant_id": tenant_id, "run_id": run_id, "status": "db_unknown", "did_transition": False}
                )
                continue

            if claim is None:
                results.append(
                    {"tenant_id": tenant_id, "run_id": run_id, "status": "owned", "did_transition": False}
                )
                continue
            if staged is None:
                results.append(
                    {"tenant_id": tenant_id, "run_id": run_id, "status": "cas_lost", "did_transition": False}
                )
                continue

            claim_guard = _PostgresClaimGuard(
                self._lifecycle,
                claim,
                transaction_factory=self._transaction_factory,
                ttl_seconds=claim_ttl_seconds,
            )
            outcome = None
            try:
                async with claim_guard:
                    outcome = await finish_run_terminalization(
                        lifecycle=self._lifecycle,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        transaction_factory=_claimed_transaction_factory(
                            claim_guard,
                            transaction_factory=self._transaction_factory,
                        ),
                        capabilities=self._v4_capabilities,
                        attempt_lifecycle=self._attempt_lifecycle,
                        attempt_id=attempt_id,
                        attempt_error_code=error_code,
                    )
                    if outcome is None or not outcome.is_terminal():
                        await claim_guard.ensure_live()
            except StaleRunClaimLost:
                results.append(
                    {"tenant_id": tenant_id, "run_id": run_id, "status": "claim_lost", "did_transition": False}
                )
                continue
            except Exception:
                logger.exception(
                    "stale_run_postgres_terminalization_failed",
                    extra={"run_id": run_id},
                )
                results.append(
                    {"tenant_id": tenant_id, "run_id": run_id, "status": "terminalization_unknown", "did_transition": False}
                )
                continue

            if outcome is not None and outcome.completed and outcome.is_terminal():
                await claim_guard.release_owned()
            results.append(
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "status": outcome.status if outcome is not None else terminal_status,
                    "did_transition": outcome.did_transition if outcome is not None else False,
                }
            )
        return results

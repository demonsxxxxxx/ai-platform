"""Own Sandbox stop/release settlement without HTTP or concrete adapters."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager, Literal, Protocol

from app.sandbox.application.runtime_handle import PersistedRuntimeHandleRecovery


class CleanupProvider(Protocol):
    async def stop(self, lease: Any, *, reason: str) -> Any: ...
    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[Any]: ...


ProviderFactory = Callable[[str | None], CleanupProvider]
TransactionFactory = Callable[[], AsyncContextManager[Any]]


class CleanupLeaseRepository(Protocol):
    async def release_stopped_sandbox_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def cleanup_expired_sandbox_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def list_expired_active_sandbox_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def claim_failed_sandbox_executor_reconciliation_cleanups(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def has_failed_sandbox_executor_reconciliation_cleanup_claim(self, conn: Any, **kwargs: Any) -> bool: ...
    async def quarantine_failed_sandbox_executor_reconciliation_cleanup(self, conn: Any, **kwargs: Any) -> Any: ...
    async def release_failed_sandbox_executor_reconciliation_cleanup_claim(self, conn: Any, **kwargs: Any) -> Any: ...
    async def finalize_failed_sandbox_executor_reconciliation_cleanup(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...


class CleanupOutcomeWriter(Protocol):
    async def record_sandbox_runtime_cleanup_outcome(self, conn: Any, **kwargs: Any) -> None: ...


class SandboxCleanupOutcomes:
    """Record Sandbox cleanup facts using the caller-owned transaction."""

    def __init__(
        self,
        *,
        append_event: Callable[..., Awaitable[Any]],
        append_audit_log: Callable[..., Awaitable[Any]],
    ) -> None:
        self._append_event = append_event
        self._append_audit_log = append_audit_log

    async def record_sandbox_runtime_cleanup_outcome(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        trace_id: str | None = None,
        user_id: str | None = None,
        requested_by_role: str | None = None,
        reason: str,
        status: str,
        lease_ids: list[str] | None = None,
        failures: list[dict[str, str]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "visible_to_user": False,
            "reason": reason,
            "status": status,
            "lease_ids": lease_ids or [],
            "failure_count": len(failures or []),
        }
        if requested_by_role:
            payload["requested_by_role"] = requested_by_role
        if failures:
            payload["failures"] = failures
        event_type = "sandbox_runtime_cleanup_failed" if status == "failed" else "sandbox_runtime_cleanup_succeeded"
        message = "Sandbox runtime cleanup failed" if status == "failed" else "Sandbox runtime cleanup succeeded"
        await self._append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type=event_type,
            stage="sandbox",
            message=message,
            payload=payload,
        )
        await self._append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action=f"sandbox.runtime.cleanup.{status}",
            target_type="run",
            target_id=run_id,
            trace_id=trace_id,
            payload_json={
                "run_id": run_id,
                "reason": reason,
                "status": status,
                "lease_ids": lease_ids or [],
                "failures": failures or [],
                "requested_by_role": requested_by_role,
            },
        )


class SandboxRuntimeCleanupError(RuntimeError):
    """Raised when one or more active sandbox leases cannot be stopped."""

    def __init__(
        self,
        failures: list[dict[str, str]],
        *,
        stopped_leases: list[dict[str, Any]] | None = None,
        failed_leases: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__("sandbox_runtime_cleanup_failed")
        self.failures = failures
        self.stopped_leases = stopped_leases or []
        self.failed_leases = failed_leases or []


class SandboxCleanupPersistenceError(RuntimeError):
    """Cancellation cleanup receipts could not commit durably."""


def _sandbox_lease_release_message(reason: str) -> str:
    if reason == "expired":
        return "已释放过期 Sandbox 租约"
    if reason in {"cancel_requested", "admin_cancel_requested"}:
        return "已因取消释放 Sandbox 租约"
    return "已释放 Sandbox 租约"


def _provider_cleanup_failures(results: object) -> list[dict[str, str]]:
    if not isinstance(results, list):
        return [{"container_id": "unknown", "message": "Malformed provider cleanup result"}]
    failures: list[dict[str, str]] = []
    for result in results:
        if isinstance(result, dict):
            failures.append({"container_id": "unknown", "message": "Malformed provider cleanup result"})
        elif getattr(result, "status", None) not in {"stopped", "not_found"}:
            failures.append({
                "container_id": str(getattr(result, "container_id", None) or "unknown"),
                "message": "Provider orphan cleanup failed",
            })
    return failures


class SandboxRuntimeCleanup:
    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory,
        lease_repository: CleanupLeaseRepository,
        outcomes: CleanupOutcomeWriter,
        append_event: Callable[..., Awaitable[Any]],
        append_audit: Callable[..., Awaitable[Any]],
        list_tenant_ids: Callable[[Any], Awaitable[list[str]]],
        handle_recovery: PersistedRuntimeHandleRecovery,
        settings: Callable[[], Any],
    ) -> None:
        self._transaction_factory = transaction_factory
        self._lease_repository = lease_repository
        self._outcomes = outcomes
        self._append_event = append_event
        self._append_audit = append_audit
        self._list_tenant_ids = list_tenant_ids
        self._handle_recovery = handle_recovery
        self._settings = settings

    async def cleanup_provider_orphans(self, *, provider_factory: ProviderFactory) -> None:
        """Run tenant-scoped orphan maintenance and durably report safe failures."""
        async with self._transaction_factory() as conn:
            tenant_ids = await self._list_tenant_ids(conn)
        provider = provider_factory(None)
        all_failures: list[dict[str, str]] = []
        for tenant_id in tenant_ids:
            try:
                results = await provider.cleanup_orphan_containers(
                    {"tenant_id": tenant_id}, reason="worker_maintenance",
                )
            except Exception:
                failures = [{"container_id": "unknown", "message": "Provider orphan cleanup raised an exception"}]
            else:
                failures = _provider_cleanup_failures(results)
            if not failures:
                continue
            try:
                async with self._transaction_factory() as conn:
                    await self._append_audit(
                        conn,
                        tenant_id=tenant_id,
                        user_id=None,
                        action="sandbox.runtime.orphan_cleanup.failed",
                        target_type="sandbox_runtime",
                        target_id=tenant_id,
                        payload_json={"source": "worker", "failure_count": len(failures), "failures": failures},
                    )
            except Exception as exc:
                raise SandboxCleanupPersistenceError("sandbox_cleanup_audit_unavailable") from exc
            all_failures.extend(failures)
        if all_failures:
            raise SandboxRuntimeCleanupError(all_failures)

    def container_lease_from_persisted_row(
        self, row: dict[str, Any], *, allow_historical_internal_test_cleanup: bool = False,
    ) -> Any | None:
        return self._handle_recovery.from_persisted_row(
            row, allow_historical_internal_test_cleanup=allow_historical_internal_test_cleanup,
        )

    def _sandbox_cleanup_timeout_seconds(self) -> float:
        return max(
            float(getattr(self._settings(), "sandbox_cleanup_timeout_seconds", 30) or 30),
            0.001,
        )

    async def _stop_failed_reconciliation_lease(
        self,
        provider: CleanupProvider,
        lease: Any,
    ) -> Any:
        stop_task = asyncio.create_task(
            provider.stop(lease, reason="executor_reconciliation_cleanup")
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(stop_task),
                timeout=self._sandbox_cleanup_timeout_seconds(),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return None
        except Exception:  # noqa: BLE001 - the claim is released for bounded cleanup retry.
            return None
        finally:
            if not stop_task.done():
                stop_task.cancel()
            # Keep the sole cleanup slot until the external operation really ends.
            joined = asyncio.gather(stop_task, return_exceptions=True)
            cancelled = False
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError

    async def stop_sandbox_leases(
        self,
        sandbox_leases: list[dict[str, Any]] | None,
        *,
        reason: str,
        provider_factory: ProviderFactory,
    ) -> list[dict[str, Any]]:
        """Stop runtime containers for active sandbox lease rows."""
        failures: list[dict[str, str]] = []
        stopped_leases: list[dict[str, Any]] = []
        failed_leases: list[dict[str, Any]] = []
        for row in sandbox_leases or []:
            if (
                row.get("executor_terminal_json") is not None
                and row.get("executor_reconciliation_status") != "finalized"
            ):
                continue
            lease = self.container_lease_from_persisted_row(
                row,
                allow_historical_internal_test_cleanup=True,
            )
            if lease is None:
                failures.append(
                    {
                        "container_id": str(row.get("id") or row.get("run_id") or "unknown"),
                        "message": f"Unsupported sandbox provider: {row.get('provider')}",
                    }
                )
                failed_leases.append(row)
                continue
            try:
                provider = provider_factory(lease.provider)
                result = await provider.stop(lease, reason=reason)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures.append(
                    {
                        "container_id": lease.container_id,
                        "message": "Sandbox provider stop raised an exception",
                    }
                )
                failed_leases.append(row)
                continue
            if result.status == "failed":
                failures.append({"container_id": result.container_id, "message": "Sandbox provider stop failed"})
                failed_leases.append(row)
                continue
            stopped_leases.append(row)
        if failures:
            raise SandboxRuntimeCleanupError(
                failures,
                stopped_leases=stopped_leases,
                failed_leases=failed_leases,
            )
        return stopped_leases

    async def release_stopped_sandbox_leases_for_cancel(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        reason: str,
        lease_ids: list[str],
        trace_id: str | None = None,
        requested_by_role: str | None = None,
    ) -> list[dict[str, Any]]:
        """Release leases after their runtime containers have been stopped."""
        if not lease_ids:
            return []
        released_leases = await self._lease_repository.release_stopped_sandbox_leases(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            reason=reason,
            lease_ids=lease_ids,
        )
        for lease in released_leases:
            payload: dict[str, Any] = {
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            }
            if requested_by_role:
                payload["requested_by_role"] = requested_by_role
            await self._append_event(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                trace_id=lease.get("trace_id") or trace_id,
                event_type="sandbox_lease_released",
                stage="sandbox",
                message="已因取消释放 Sandbox 租约",
                payload=payload,
            )
        return released_leases

    async def release_stopped_sandbox_leases(
        self,
        conn: Any,
        *,
        tenant_id: str,
        reason: str,
        lease_ids: list[str],
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Release DB leases only after their runtime stop operation has succeeded."""
        if not lease_ids:
            return []
        released_leases = await self._lease_repository.release_stopped_sandbox_leases(
            conn,
            tenant_id=tenant_id,
            reason=reason,
            lease_ids=lease_ids,
        )
        for lease in released_leases:
            await self._append_event(
                conn,
                tenant_id=tenant_id,
                run_id=str(lease["run_id"]),
                trace_id=lease.get("trace_id") or trace_id,
                event_type="sandbox_lease_released",
                stage="sandbox",
                message=_sandbox_lease_release_message(reason),
                payload={
                    "visible_to_user": True,
                    "lease_id": lease.get("id"),
                    "reason": reason,
                },
            )
        return released_leases

    async def cleanup_expired_sandbox_leases(
        self,
        conn: Any,
        *,
        tenant_id: str | None = None,
        reason: str = "expired",
    ) -> list[dict[str, Any]]:
        """Release expired DB-only leases; runtime providers must be stopped first."""
        rows = await self._lease_repository.cleanup_expired_sandbox_leases(
            conn,
            tenant_id=tenant_id,
            reason=reason,
        )
        for lease in rows:
            await self._append_event(
                conn,
                tenant_id=str(lease["tenant_id"]),
                run_id=str(lease["run_id"]),
                trace_id=lease.get("trace_id"),
                event_type="sandbox_lease_released",
                stage="sandbox",
                message="已释放过期 Sandbox 租约",
                payload={
                    "visible_to_user": True,
                    "lease_id": lease.get("id"),
                    "reason": reason,
                },
            )
        return rows

    async def cleanup_failed_sandbox_executor_reconciliation_leases(
        self,
        *,
        tenant_id: str | None = None,
        provider_factory: ProviderFactory,
        stale_after_seconds: int = 300,
        transaction_factory: TransactionFactory | None = None,
    ) -> list[dict[str, Any]]:
        """Retry one verified runtime cleanup without holding DB locks across provider I/O."""

        transaction_factory = transaction_factory or self._transaction_factory
        claim_token = str(uuid.uuid4())
        async with transaction_factory() as conn:
            claimed = await self._lease_repository.claim_failed_sandbox_executor_reconciliation_cleanups(
                conn,
                claim_token=claim_token,
                tenant_id=tenant_id,
                limit=1,
                stale_after_seconds=stale_after_seconds,
            )
        released: list[dict[str, Any]] = []
        for row in claimed:
            async with transaction_factory() as conn:
                owns_claim = await self._lease_repository.has_failed_sandbox_executor_reconciliation_cleanup_claim(
                    conn,
                    lease_id=str(row["id"]),
                    claim_token=claim_token,
                )
                if not owns_claim:
                    continue
                lease = self.container_lease_from_persisted_row(
                    row,
                    allow_historical_internal_test_cleanup=True,
                )
                if lease is None:
                    await self._lease_repository.quarantine_failed_sandbox_executor_reconciliation_cleanup(
                        conn,
                        lease_id=str(row["id"]),
                        claim_token=claim_token,
                        error="executor_reconciliation_runtime_handle_invalid",
                    )
                    continue
            stop_result = await self._stop_failed_reconciliation_lease(
                provider_factory(lease.provider),
                lease,
            )
            if getattr(stop_result, "status", "failed") not in {"stopped", "not_found"}:
                async with transaction_factory() as conn:
                    await self._lease_repository.release_failed_sandbox_executor_reconciliation_cleanup_claim(
                        conn,
                        lease_id=str(row["id"]),
                        claim_token=claim_token,
                        error="executor_reconciliation_sandbox_stop_failed",
                    )
                continue
            async with transaction_factory() as conn:
                finalized = await self._lease_repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
                    conn,
                    tenant_id=str(row["tenant_id"]),
                    user_id=str(row["user_id"]),
                    run_id=str(row["run_id"]),
                    lease_id=str(row["id"]),
                    claim_token=claim_token,
                    reason="executor_reconciliation_cleanup",
                )
                if finalized is None:
                    continue
                await self._append_event(
                    conn,
                    tenant_id=str(finalized["tenant_id"]),
                    run_id=str(finalized["run_id"]),
                    trace_id=finalized.get("trace_id"),
                    event_type="sandbox_lease_released",
                    stage="sandbox",
                    message="已释放 Sandbox 租约",
                    payload={
                        "visible_to_user": True,
                        "lease_id": finalized.get("id"),
                        "reason": "executor_reconciliation_cleanup",
                    },
                )
                released.append(finalized)
        return released

    async def cleanup_expired_sandbox_runtime_leases(
        self,
        *,
        tenant_id: str | None = None,
        reason: str = "expired",
        provider_factory: ProviderFactory,
        limit: int = 100,
        transaction_factory: TransactionFactory | None = None,
    ) -> list[dict[str, Any]]:
        """Own the locked cleanup transaction and commit outcomes before reporting failure."""
        transaction_factory = transaction_factory or self._transaction_factory

        async def release_stopped_with_conn(release_conn: Any, stopped_leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
            released: list[dict[str, Any]] = []
            grouped: dict[str, list[dict[str, Any]]] = {}
            for lease in stopped_leases:
                grouped.setdefault(str(lease["tenant_id"]), []).append(lease)
            for release_tenant_id, tenant_leases in grouped.items():
                released.extend(
                    await self.release_stopped_sandbox_leases(
                        release_conn,
                        tenant_id=release_tenant_id,
                        reason=reason,
                        lease_ids=[str(lease["id"]) for lease in tenant_leases],
                    )
                )
            return released

        async def record_partial_failure(conn: Any, exc: SandboxRuntimeCleanupError) -> None:
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for lease, failure in zip(exc.failed_leases, exc.failures, strict=True):
                key = (str(lease["tenant_id"]), str(lease["run_id"]))
                subject = grouped.setdefault(
                    key,
                    {"lease_ids": [], "failures": [], "trace_id": lease.get("trace_id")},
                )
                subject["lease_ids"].append(str(lease["id"]))
                subject["failures"].append(failure)
            if exc.stopped_leases:
                await release_stopped_with_conn(conn, exc.stopped_leases)
            for (failure_tenant_id, failure_run_id), subject in grouped.items():
                await self._outcomes.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=failure_tenant_id,
                    run_id=failure_run_id,
                    trace_id=subject["trace_id"],
                    requested_by_role="maintenance",
                    reason=reason,
                    status="failed",
                    lease_ids=subject["lease_ids"],
                    failures=subject["failures"],
                )

        failure: SandboxRuntimeCleanupError | None = None
        async with transaction_factory() as conn:
            expired_leases = await self._lease_repository.list_expired_active_sandbox_leases(
                conn, tenant_id=tenant_id, limit=limit,
            )
            if not expired_leases:
                return []
            try:
                stopped_leases = await self.stop_sandbox_leases(
                    expired_leases, reason=reason, provider_factory=provider_factory,
                )
            except SandboxRuntimeCleanupError as exc:
                await record_partial_failure(conn, exc)
                failure = exc
            else:
                released = await release_stopped_with_conn(conn, stopped_leases)
        if failure is not None:
            raise failure
        return released

    async def cleanup_cancelled_run_leases(
        self,
        sandbox_leases: list[dict[str, Any]] | None,
        *,
        tenant_id: str,
        run_id: str,
        user_id: str,
        requested_by_role: Literal["owner", "admin"],
        reason: str,
        trace_id: str | None,
        provider_factory: ProviderFactory,
        transaction_factory: TransactionFactory | None = None,
    ) -> None:
        """Settle stopped leases and the cancel cleanup outcome in one transaction.

        The Runs cancellation fact has already committed. Provider stop remains
        outside this settlement transaction, unlike locked single-lease release
        and expiry cleanup. A partial stop failure is reported only after its
        successful releases and failure audit commit together.
        """
        failure: SandboxRuntimeCleanupError | None = None
        try:
            stopped_leases = await self.stop_sandbox_leases(
                sandbox_leases, reason=reason, provider_factory=provider_factory,
            )
        except SandboxRuntimeCleanupError as exc:
            failure = exc
            stopped_leases = exc.stopped_leases
        if failure is None and not stopped_leases:
            return

        transaction_factory = transaction_factory or self._transaction_factory
        try:
            async with transaction_factory() as conn:
                grouped: dict[str, list[str]] = {}
                for lease in stopped_leases:
                    lease_run_id = str(lease.get("run_id") or "").strip()
                    lease_id = str(lease.get("id") or "").strip()
                    if lease_run_id and lease_id:
                        grouped.setdefault(lease_run_id, []).append(lease_id)
                for lease_run_id, lease_ids in grouped.items():
                    await self.release_stopped_sandbox_leases_for_cancel(
                        conn,
                        tenant_id=tenant_id,
                        run_id=lease_run_id,
                        reason=reason,
                        lease_ids=lease_ids,
                        trace_id=trace_id,
                        **({"requested_by_role": "admin"} if requested_by_role == "admin" else {}),
                    )
                outcome_leases = failure.failed_leases if failure is not None else stopped_leases
                await self._outcomes.record_sandbox_runtime_cleanup_outcome(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    requested_by_role=requested_by_role,
                    reason=reason,
                    status="failed" if failure is not None else "succeeded",
                    lease_ids=[str(lease["id"]) for lease in outcome_leases if lease.get("id")],
                    failures=failure.failures if failure is not None else [],
                )
        except Exception as exc:
            raise SandboxCleanupPersistenceError("sandbox_cleanup_persistence_unavailable") from exc
        if failure is not None:
            raise failure

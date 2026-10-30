"""Application-owned Run cancellation orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Literal, Protocol

from app.runs.domain.terminalization import RunTerminalizationProgress

CancelRequesterRole = Literal["owner", "admin"]
CancelEventSource = Literal["user", "system"]


class RunCancellationQueueCleanupError(RuntimeError):
    """Queued cancellation payload cleanup failed after Run cancellation committed."""

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        super().__init__("queue_cleanup_failed")


class RunCancellationDeliveryDeferred(RuntimeError):
    """Immediate executor cancellation failed; durable delivery remains pending."""

    error_code = "sandbox_cancel_delivery_failed"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class RunCancellationSandboxCleanup(Protocol):
    async def cleanup_cancelled_run_leases(
        self,
        sandbox_leases: list[dict[str, Any]] | None,
        *,
        tenant_id: str,
        run_id: str,
        user_id: str,
        requested_by_role: CancelRequesterRole,
        reason: str,
        trace_id: str | None,
        provider_factory: Callable[..., Any],
        transaction_factory: Callable[[], AsyncContextManager[object]],
    ) -> list[dict[str, Any]]: ...


RunCancellationQueueCleanup = Callable[..., Awaitable[Any]]
RunCancellationStreamOperation = Callable[..., Awaitable[Any]]
RunCancellationDelivery = Callable[..., Awaitable[int]]
RunCancellationWarning = Callable[[str, str, Exception], None]


class RunCancellationPostProcessor:
    """Perform cancellation side effects after the Run transaction commits."""

    def __init__(
        self,
        *,
        queue_cleanup: RunCancellationQueueCleanup,
        sandbox_cleanup: RunCancellationSandboxCleanup,
        provider_factory: Callable[..., Any],
        transaction_factory: Callable[[], AsyncContextManager[object]],
        admit_stream: RunCancellationStreamOperation,
        publish_event: RunCancellationStreamOperation,
        warn: RunCancellationWarning,
        handoff_cancelled_attempt: Callable[..., Awaitable[bool]] | None = None,
        deliver_cancel: RunCancellationDelivery | None = None,
    ) -> None:
        self._queue_cleanup = queue_cleanup
        self._sandbox_cleanup = sandbox_cleanup
        self._provider_factory = provider_factory
        self._transaction_factory = transaction_factory
        self.admit_v4_stream = admit_stream
        self.publish_run_event = publish_event
        self._warn = warn
        self._handoff_cancelled_attempt = handoff_cancelled_attempt
        self._deliver_cancel = deliver_cancel

    async def post_cancel(
        self,
        cancellation: "CancelRequestResult | None",
        *,
        tenant_id: str,
        user_id: str,
        requested_by_role: CancelRequesterRole,
        reason: str,
        worker_capabilities: Any,
    ) -> dict[str, Any] | None:
        if cancellation is None:
            return None
        result = cancellation.as_route_result()
        result.pop("_terminalization_progress", None)
        if (
            result["status"] == "cancel_requested"
            and cancellation.attempt_id
            and self._deliver_cancel is not None
        ):
            delivery_failed = False
            try:
                await self._deliver_cancel(
                    tenant_id=tenant_id,
                    run_id=cancellation.run_id,
                )
            except Exception:  # noqa: BLE001 - durable delivery retries after this immediate attempt.
                delivery_failed = True
            if delivery_failed:
                self._warn(
                    "Sandbox cancellation delivery deferred",
                    cancellation.run_id,
                    RunCancellationDeliveryDeferred(),
                )
        if cancellation.attempt_id:
            try:
                await self.admit_v4_stream(
                    worker_capabilities,
                    tenant_id=tenant_id,
                    run_id=cancellation.run_id,
                    attempt_id=cancellation.attempt_id,
                )
            except Exception as exc:  # noqa: BLE001 - publication never blocks cleanup
                self._warn(
                    "Cancellation v4 admission deferred", cancellation.run_id, exc,
                )
        if cancellation.attempt_id:
            try:
                await self.publish_run_event(
                    worker_capabilities,
                    tenant_id=tenant_id,
                    run_id=cancellation.run_id,
                )
            except Exception as exc:  # noqa: BLE001 - publication retries after cleanup
                self._warn(
                    "Cancellation v4 terminal publication deferred", cancellation.run_id, exc,
                )
        queue_cleanup_failures: list[Exception] = []
        if result["status"] == "cancelled":
            try:
                await self._queue_cleanup(tenant_id=tenant_id, run_id=cancellation.run_id)
            except Exception as exc:  # noqa: BLE001 - report after sandbox cleanup
                queue_cleanup_failures.append(exc)
        if result["status"] == "cancelled":
            released_leases = await self._sandbox_cleanup.cleanup_cancelled_run_leases(
                result.get("active_sandbox_leases"),
                tenant_id=tenant_id,
                run_id=cancellation.run_id,
                user_id=user_id,
                requested_by_role=requested_by_role,
                reason=reason,
                trace_id=result.get("trace_id"),
                provider_factory=self._provider_factory,
                transaction_factory=self._transaction_factory,
            )
            if (
                self._handoff_cancelled_attempt is not None
                and cancellation.attempt_id
                and cancellation.attempt_owner_generation is not None
            ):
                released_ids = tuple(
                    str(lease["id"])
                    for lease in released_leases
                    if isinstance(lease, dict) and lease.get("id")
                )
                await self._handoff_cancelled_attempt(
                    tenant_id=tenant_id,
                    run_id=cancellation.run_id,
                    attempt_id=cancellation.attempt_id,
                    expected_owner_generation=cancellation.attempt_owner_generation,
                    resource_lease_ids=released_ids,
                )
        if queue_cleanup_failures:
            raise RunCancellationQueueCleanupError(queue_cleanup_failures)
        return result


@dataclass(frozen=True, slots=True)
class CancelRequestAuthority:
    run_id: str
    attempt_id: str | None
    prior_status: str
    trace_ref: str | None
    target_user_id: str
    actor_user_id: str
    requested_by_role: CancelRequesterRole
    source: CancelEventSource
    newly_requested: bool
    attempt_owner_generation: int | None = None


@dataclass(frozen=True, slots=True)
class CancelRequestResult:
    run_id: str
    status: str
    trace_ref: str | None
    active_sandbox_leases: tuple[dict[str, Any], ...]
    initial_terminalization_progress: RunTerminalizationProgress | None
    attempt_id: str | None = None
    attempt_owner_generation: int | None = None

    def as_route_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"run_id": self.run_id, "status": self.status}
        progress = self.initial_terminalization_progress
        if progress is not None and progress.did_transition:
            result["_terminalization_progress"] = progress
        if self.active_sandbox_leases:
            result["trace_id"] = self.trace_ref
            result["active_sandbox_leases"] = list(self.active_sandbox_leases)
        return result


class RunCancellationPersistence(Protocol):
    async def begin_owner_request(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        owner_user_id: str,
    ) -> CancelRequestAuthority | None: ...

    async def begin_admin_request(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        admin_user_id: str,
    ) -> CancelRequestAuthority | None: ...

    async def finish_request(
        self,
        conn: object,
        *,
        tenant_id: str,
        authority: CancelRequestAuthority,
        progress: RunTerminalizationProgress | None,
    ) -> CancelRequestResult: ...

    async def handoff_cancelled_attempt(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        expected_owner_generation: int,
        resource_lease_ids: tuple[str, ...],
    ) -> bool: ...


class RunCancellationEventWriter(Protocol):
    async def prepare_pending_authority(
        self,
        conn: object,
        *,
        tenant_id: str,
        authority: CancelRequestAuthority,
    ) -> None: ...

    async def append_terminal(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
    ) -> None: ...

    async def append_cancel_requested(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        source: CancelEventSource,
        trace_ref: str | None,
    ) -> None: ...


class RunTerminalizationProgressor(Protocol):
    async def __call__(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
    ) -> RunTerminalizationProgress | None: ...


class RunCancellationUseCase:
    def __init__(
        self,
        *,
        transaction_factory: Callable[[], AsyncContextManager[object]],
        persistence: RunCancellationPersistence,
        event_writer: RunCancellationEventWriter,
        progress_terminalization: RunTerminalizationProgressor,
        post_processor: RunCancellationPostProcessor | None = None,
        stage_cancel_delivery: Callable[..., Awaitable[int]] | None = None,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._persistence = persistence
        self._event_writer = event_writer
        self._progress_terminalization = progress_terminalization
        self._post_processor = post_processor
        self._stage_cancel_delivery = stage_cancel_delivery

    async def post_cancel(
        self,
        cancellation: CancelRequestResult | None,
        *,
        tenant_id: str,
        user_id: str,
        requested_by_role: CancelRequesterRole,
        reason: str,
        worker_capabilities: Any,
    ) -> dict[str, Any] | None:
        if self._post_processor is None:
            raise RuntimeError("run_cancellation_post_processor_unavailable")
        return await self._post_processor.post_cancel(
            cancellation,
            tenant_id=tenant_id,
            user_id=user_id,
            requested_by_role=requested_by_role,
            reason=reason,
            worker_capabilities=worker_capabilities,
        )

    async def request_owner_cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        owner_user_id: str,
    ) -> CancelRequestResult | None:
        async with self._transaction_factory() as conn:
            authority = await self._persistence.begin_owner_request(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                owner_user_id=owner_user_id,
            )
            return await self._finish_authorized_request(
                conn,
                tenant_id=tenant_id,
                authority=authority,
            )

    async def request_admin_cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        admin_user_id: str,
    ) -> CancelRequestResult | None:
        async with self._transaction_factory() as conn:
            authority = await self._persistence.begin_admin_request(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                admin_user_id=admin_user_id,
            )
            return await self._finish_authorized_request(
                conn,
                tenant_id=tenant_id,
                authority=authority,
            )

    async def _finish_authorized_request(
        self,
        conn: object,
        *,
        tenant_id: str,
        authority: CancelRequestAuthority | None,
    ) -> CancelRequestResult | None:
        if authority is None:
            return None
        if authority.attempt_id is not None:
            await self._event_writer.prepare_pending_authority(
                conn,
                tenant_id=tenant_id,
                authority=authority,
            )
        if authority.newly_requested:
            await self._event_writer.append_cancel_requested(
                conn,
                tenant_id=tenant_id,
                run_id=authority.run_id,
                source=authority.source,
                trace_ref=authority.trace_ref,
            )
        progress = None
        # An admitted Attempt owns SDK drain and settlement; intent is not EOF.
        if authority.prior_status == "queued" and authority.attempt_id is None:
            progress = await self._progress_terminalization(
                conn,
                tenant_id=tenant_id,
                run_id=authority.run_id,
            )
        if progress is not None and progress.did_transition:
            await self._event_writer.append_terminal(
                conn,
                tenant_id=tenant_id,
                run_id=authority.run_id,
            )
        cancellation = await self._persistence.finish_request(
            conn,
            tenant_id=tenant_id,
            authority=authority,
            progress=progress,
        )
        if (
            self._stage_cancel_delivery is not None
            and cancellation.status == "cancel_requested"
            and cancellation.attempt_id
        ):
            await self._stage_cancel_delivery(
                conn,
                tenant_id=tenant_id,
                run_id=cancellation.run_id,
                attempt_id=cancellation.attempt_id,
            )
        return cancellation

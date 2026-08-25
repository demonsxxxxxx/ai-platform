"""Application-owned Run cancellation orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Literal, Protocol

from app.runs.domain.terminalization import RunTerminalizationProgress

CancelRequesterRole = Literal["owner", "admin"]
CancelEventSource = Literal["user", "system"]


@dataclass(frozen=True, slots=True)
class CancelRequestAuthority:
    run_id: str
    attempt_id: str
    prior_status: str
    trace_ref: str | None
    target_user_id: str
    actor_user_id: str
    requested_by_role: CancelRequesterRole
    source: CancelEventSource
    newly_requested: bool


@dataclass(frozen=True, slots=True)
class CancelRequestResult:
    run_id: str
    status: str
    trace_ref: str | None
    active_sandbox_leases: tuple[dict[str, Any], ...]
    initial_terminalization_progress: RunTerminalizationProgress | None
    attempt_id: str | None = None

    def as_route_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"run_id": self.run_id, "status": self.status}
        progress = self.initial_terminalization_progress
        if progress is not None and progress.did_transition and progress.needs_reconcile:
            result["_permission_terminalization_progress"] = progress
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
    ) -> None:
        self._transaction_factory = transaction_factory
        self._persistence = persistence
        self._event_writer = event_writer
        self._progress_terminalization = progress_terminalization

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
        return await self._persistence.finish_request(
            conn,
            tenant_id=tenant_id,
            authority=authority,
            progress=progress,
        )

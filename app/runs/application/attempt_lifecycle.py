"""Application-owned Run-attempt persistence boundary and lifecycle use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.runs.domain.execution_spec import ExecutionSpec


class RunAttemptPersistence(Protocol):
    """Persistence operations required by Run-attempt application workflows."""

    async def get_run_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None: ...

    async def get_run_attempt_for_queue_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        queue_attempt_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None: ...

    async def get_latest_run_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None: ...

    async def lock_queued_run_for_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None: ...

    async def start_worker_run_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        queue_attempt_id: str,
        worker_id: str,
        execution_spec: ExecutionSpec,
    ) -> dict[str, Any]: ...

    async def assert_worker_run_attempt_current(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        queue_attempt_id: str,
        worker_id: str,
    ) -> dict[str, Any] | None: ...

    async def request_run_attempt_cancel(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        next_owner_kind: str | None = None,
        next_owner_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    async def terminalize_run_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        status: str,
        terminal_reason: str,
        error_code: str | None = None,
    ) -> dict[str, Any] | None: ...

    async def prepare_stale_run_attempt_reconciliation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        terminal_status: str,
        reconciler_id: str,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class RunAttemptLifecycleService:
    """Own attempt orchestration while a configured adapter owns SQL details."""

    persistence: RunAttemptPersistence

    async def get(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        return await self.persistence.get_run_attempt(conn, **kwargs)

    async def get_for_queue_attempt(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.get_run_attempt_for_queue_attempt(conn, **kwargs)

    async def get_latest(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.get_latest_run_attempt(conn, **kwargs)

    async def lock_queued_run(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.lock_queued_run_for_attempt(conn, **kwargs)

    async def start_worker(self, conn: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.persistence.start_worker_run_attempt(conn, **kwargs)

    async def assert_worker_current(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.assert_worker_run_attempt_current(conn, **kwargs)

    async def request_cancel(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.request_run_attempt_cancel(conn, **kwargs)

    async def terminalize(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.terminalize_run_attempt(conn, **kwargs)

    async def prepare_stale_reconciliation(
        self,
        conn: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.persistence.prepare_stale_run_attempt_reconciliation(
            conn,
            **kwargs,
        )

    async def terminalize_latest(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        status: str,
        terminal_reason: str,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        """Mirror one terminal Run onto its latest durable attempt atomically."""

        attempt = await self.persistence.get_latest_run_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            for_update=True,
        )
        if attempt is None:
            return None
        if status == "cancelled":
            attempt = await self.persistence.request_run_attempt_cancel(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=str(attempt["id"]),
            )
            if attempt is None:
                return None
        return await self.persistence.terminalize_run_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=str(attempt["id"]),
            status=status,
            terminal_reason=terminal_reason,
            error_code=error_code,
        )


_service: RunAttemptLifecycleService | None = None


def configure_run_attempt_lifecycle(service: RunAttemptLifecycleService) -> None:
    global _service
    _service = service


def _configured_service() -> RunAttemptLifecycleService:
    if _service is None:
        raise RuntimeError("run_attempt_lifecycle_service_not_configured")
    return _service


async def get_run_attempt(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await _configured_service().get(conn, **kwargs)


async def get_run_attempt_for_queue_attempt(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().get_for_queue_attempt(conn, **kwargs)


async def get_latest_run_attempt(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await _configured_service().get_latest(conn, **kwargs)


async def lock_queued_run_for_attempt(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().lock_queued_run(conn, **kwargs)


async def start_worker_run_attempt(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _configured_service().start_worker(conn, **kwargs)


async def assert_worker_run_attempt_current(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().assert_worker_current(conn, **kwargs)


async def request_run_attempt_cancel(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().request_cancel(conn, **kwargs)


async def terminalize_run_attempt(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().terminalize(conn, **kwargs)


async def prepare_stale_run_attempt_reconciliation(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().prepare_stale_reconciliation(conn, **kwargs)


async def terminalize_latest_run_attempt(
    conn: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return await _configured_service().terminalize_latest(conn, **kwargs)

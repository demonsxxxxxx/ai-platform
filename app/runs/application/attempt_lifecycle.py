"""Application-owned Run-attempt persistence boundary and lifecycle use cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.runs.domain.execution_spec import ExecutionSpec, model_auto_compact_window


class ReconciliationAuthorityError(ValueError):
    """Untrusted receipt authority: quarantine without changing a Run."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RestoredReconciliationAttempt:
    attempt: dict[str, Any]
    execution_spec: ExecutionSpec


class RunAttemptPersistence(Protocol):
    """Persistence operations required by Run-attempt application workflows."""

    async def get_scoped_reconciliation_run(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

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
        queue_message_id: str | None = None,
        lease_expires_at: datetime | None = None,
        last_heartbeat_at: datetime | None = None,
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

    async def heartbeat_worker_run_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        queue_attempt_id: str,
        queue_message_id: str,
        worker_id: str,
        expected_owner_generation: int,
        lease_expires_at: datetime,
        last_heartbeat_at: datetime,
    ) -> dict[str, Any]: ...

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

    async def restore_reconciliation(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, claim_token: str, expected_owner_generation: int,
        scope: dict[str, str],
        receipt_authority: Callable[..., Awaitable[bool]],
    ) -> RestoredReconciliationAttempt:
        """Read one original spec under exact scope, receipt and owner fences."""
        if (type(expected_owner_generation) is not int or expected_owner_generation < 1
            or set(scope) != {"workspace_id", "user_id", "session_id", "agent_id"}
            or any(not isinstance(value, str) or not value for value in scope.values())):
            raise ReconciliationAuthorityError("run_attempt_reconciliation_scope_invalid")
        run = await self.persistence.get_scoped_reconciliation_run(
            conn, tenant_id=tenant_id, run_id=run_id, scope=scope,
        )
        if run is None:
            raise ReconciliationAuthorityError("run_attempt_reconciliation_scope_invalid")
        if not await receipt_authority(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, claim_token=claim_token,
            expected_owner_generation=expected_owner_generation, scope=scope,
        ):
            raise ReconciliationAuthorityError("run_attempt_reconciliation_claim_stale")
        row = await self.persistence.get_run_attempt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id, for_update=True,
        )
        if row is None:
            raise ReconciliationAuthorityError("run_attempt_reconciliation_attempt_missing")
        generation = row.get("owner_generation")
        cancellation = (row.get("status") == "cancel_requested"
                        and run.get("cancel_requested_at") is not None
                        and generation == expected_owner_generation + 1)
        if (row.get("tenant_id") != tenant_id or row.get("run_id") != run_id
            or row.get("id") != attempt_id or row.get("owner_kind") != "queue_worker"
            or not row.get("owner_id") or not row.get("queue_attempt_id")
            or type(generation) is not int
            or not ((row.get("status") == "running" and generation == expected_owner_generation)
                    or cancellation)):
            raise ReconciliationAuthorityError("run_attempt_reconciliation_owner_stale")
        canonical, digest = row.get("execution_spec_canonical_json"), row.get("execution_spec_sha256")
        try:
            if not isinstance(canonical, str) or not isinstance(digest, str):
                raise ValueError("missing spec")
            spec = ExecutionSpec.from_canonical_json(canonical.encode("utf-8"), expected_sha256=digest)
            frozen = spec.to_mapping()
            if (frozen != row.get("execution_spec_json")
                or frozen["schema_version"] != row.get("execution_spec_schema_version")
                or any(frozen.get(key) != value for key, value in
                       {"tenant_id": tenant_id, "run_id": run_id, **scope}.items())
                or any(str(frozen.get(key) or "") != str(run.get(key) or "")
                       for key in ("execution_kind", "skill_id", "executor_type", "context_snapshot_id", "model_id", "model_value"))
                or frozen.get("model_gateway_revision") != run.get("model_gateway_revision")
                # Historical v2 attempts froze an input capacity that the runner
                # clamped into the CLI range to derive the same window.
                or model_auto_compact_window(frozen) != run.get("auto_compact_window")
                or frozen.get("model_max_output_tokens") != run.get("max_output_tokens")):
                raise ValueError("spec binding mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise ReconciliationAuthorityError("run_attempt_reconciliation_spec_invalid") from exc
        return RestoredReconciliationAttempt(row, spec)

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

    async def heartbeat_worker(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        queue_attempt_id: str,
        queue_message_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        last_heartbeat_at: datetime,
    ) -> dict[str, Any] | None:
        """Persist one Redis-proven heartbeat through the current attempt fence."""

        attempt = await self.persistence.assert_worker_run_attempt_current(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            queue_attempt_id=queue_attempt_id,
            worker_id=worker_id,
        )
        if attempt is None:
            return None
        return await self.persistence.heartbeat_worker_run_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=str(attempt["id"]),
            queue_attempt_id=queue_attempt_id,
            queue_message_id=queue_message_id,
            worker_id=worker_id,
            expected_owner_generation=int(attempt["owner_generation"]),
            lease_expires_at=lease_expires_at,
            last_heartbeat_at=last_heartbeat_at,
        )

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

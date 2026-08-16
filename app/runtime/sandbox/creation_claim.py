"""Cross-process real-sandbox creation claims backed by PostgreSQL sessions."""

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.db import connect


class SandboxCreationClaimError(RuntimeError):
    """Base class for safe failures acquiring a real-sandbox creation claim."""


class SandboxCreationClaimTimeoutError(SandboxCreationClaimError):
    """Raised when a same-attempt owner does not release its claim in time."""


@dataclass(frozen=True)
class SandboxCreationScope:
    """Canonical ownership scope for one real sandbox creation attempt."""

    provider: str
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    attempt_id: str

    def canonical_key(self) -> str:
        """Return the run-granular advisory-lock input.

        Docker primary container names are run-scoped. Keep the attempt in the
        inventory query, but serialize provider creation at the coarser naming
        boundary so two attempts cannot race for the same runtime identity.
        """

        return f"{self.provider}\x1f{self.run_id}"


@dataclass(frozen=True)
class SandboxCreationClaim:
    """Result of ownership acquisition before a provider may create a sandbox."""

    active_lease_exists: bool


ConnectionFactory = Callable[[], Awaitable[Any]]


async def _try_acquire_session_lock(connection: Any, lock_key: str) -> bool:
    cursor = await connection.execute(
        "select pg_try_advisory_lock(hashtextextended(%s::text, 0::bigint)) as acquired",
        (lock_key,),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("acquired") is True)


async def _release_session_lock(connection: Any, lock_key: str) -> None:
    await connection.execute(
        "select pg_advisory_unlock(hashtextextended(%s::text, 0::bigint))",
        (lock_key,),
    )


async def _release_claim_session(
    connection: Any,
    lock_key: str,
    *,
    acquired: bool,
) -> None:
    """Release the advisory lock and close its session without skipping close."""

    cleanup_error: BaseException | None = None
    if acquired:
        try:
            await _release_session_lock(connection, lock_key)
        except BaseException as exc:
            cleanup_error = exc
    try:
        await connection.close()
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if cleanup_error is not None:
        raise cleanup_error


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


def _force_finish_connection(connection: Any) -> None:
    """Synchronously sever the dedicated PostgreSQL session after cleanup timeout."""

    pgconn = getattr(connection, "pgconn", None)
    finish = getattr(pgconn, "finish", None)
    if not callable(finish):
        raise RuntimeError("sandbox creation claim connection cannot be force-closed")
    finish()


async def _await_cleanup_task(
    task: asyncio.Task[None],
    *,
    timeout_seconds: float,
    force_finish: Callable[[], None],
) -> None:
    """Finish session cleanup by a deadline even when the owner is cancelled."""

    cancellation: asyncio.CancelledError | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout_seconds), 0.001)
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            task.cancel()
            task.add_done_callback(_consume_background_task)
            try:
                force_finish()
            finally:
                raise TimeoutError("sandbox creation claim cleanup timed out")
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
        except TimeoutError:
            task.cancel()
            task.add_done_callback(_consume_background_task)
            try:
                force_finish()
            finally:
                raise TimeoutError("sandbox creation claim cleanup timed out") from None
    try:
        task.result()
    except BaseException:
        raise
    if cancellation is not None:
        raise cancellation


async def _has_active_exact_attempt_lease(connection: Any, scope: SandboxCreationScope) -> bool:
    cursor = await connection.execute(
        """
        select exists(
          select 1
          from sandbox_leases
          where provider = %s
            and tenant_id = %s
            and workspace_id = %s
            and user_id = %s
            and session_id = %s
            and run_id = %s
            and coalesce(attempt_id, lease_payload_json ->> 'attempt_id') = %s
            and status = 'active'
            and expires_at is not null
            and expires_at > clock_timestamp()
        ) as active
        """,
        (
            scope.provider,
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.run_id,
            scope.attempt_id,
        ),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("active") is True)


@asynccontextmanager
async def acquire_sandbox_creation_claim(
    scope: SandboxCreationScope,
    *,
    timeout_seconds: float,
    connection_factory: ConnectionFactory = connect,
) -> AsyncIterator[SandboxCreationClaim]:
    """Hold one dedicated session lock through create and lease persistence.

    The connection itself is the crash and cancellation fence: PostgreSQL releases
    its session advisory lock when this context closes the dedicated connection.
    """

    timeout = max(float(timeout_seconds), 0.001)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    connection: Any | None = None
    acquired = False
    lock_key = scope.canonical_key()
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
        try:
            connection = await asyncio.wait_for(connection_factory(), timeout=remaining)
        except TimeoutError as exc:
            raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable") from exc
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
            try:
                acquired = await asyncio.wait_for(
                    _try_acquire_session_lock(connection, lock_key),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                raise SandboxCreationClaimTimeoutError(
                    "sandbox creation claim unavailable"
                ) from exc
            if acquired:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
            await asyncio.sleep(min(0.05, remaining))
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
        try:
            active_lease_exists = await asyncio.wait_for(
                _has_active_exact_attempt_lease(connection, scope),
                timeout=remaining,
            )
        except TimeoutError as exc:
            raise SandboxCreationClaimTimeoutError(
                "sandbox creation claim unavailable"
            ) from exc
        yield SandboxCreationClaim(active_lease_exists=active_lease_exists)
    finally:
        if connection is not None:
            body_raised = sys.exc_info()[0] is not None
            body_error = sys.exc_info()[1]
            try:
                cleanup_task = asyncio.create_task(
                    _release_claim_session(
                        connection,
                        lock_key,
                        acquired=acquired,
                    )
                )
                await _await_cleanup_task(
                    cleanup_task,
                    timeout_seconds=timeout,
                    force_finish=lambda: _force_finish_connection(connection),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if (
                    not body_raised
                    or isinstance(body_error, asyncio.CancelledError)
                    or isinstance(exc, TimeoutError)
                ):
                    raise SandboxCreationClaimError(
                        "sandbox creation claim release failed"
                    ) from exc

"""Cross-process OpenSandbox creation claims backed by PostgreSQL sessions."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any

from app.db import connect


class SandboxCreationClaimError(RuntimeError):
    """Base class for safe failures acquiring an OpenSandbox creation claim."""


class SandboxCreationClaimTimeoutError(SandboxCreationClaimError):
    """Raised when a same-attempt owner does not release its claim in time."""


@dataclass(frozen=True)
class SandboxCreationScope:
    """Canonical ownership scope for one remote sandbox creation attempt."""

    provider: str
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    attempt_id: str

    def canonical_key(self) -> str:
        """Return the stable advisory-lock input without exposing it to callers."""

        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


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
            and lease_payload_json ->> 'attempt_id' = %s
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
            acquired = await _try_acquire_session_lock(connection, lock_key)
            if acquired:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
            await asyncio.sleep(min(0.05, remaining))
        active_lease_exists = await _has_active_exact_attempt_lease(connection, scope)
        yield SandboxCreationClaim(active_lease_exists=active_lease_exists)
    finally:
        if connection is not None:
            try:
                if acquired:
                    await _release_session_lock(connection, lock_key)
            finally:
                await connection.close()

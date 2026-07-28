"""Cross-process OpenSandbox creation claims backed by PostgreSQL sessions."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any

from app import repositories
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
            acquired = await repositories.try_acquire_sandbox_creation_claim(connection, lock_key=lock_key)
            if acquired:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SandboxCreationClaimTimeoutError("sandbox creation claim unavailable")
            await asyncio.sleep(min(0.05, remaining))
        active_lease_exists = await repositories.has_active_sandbox_creation_lease(
            connection,
            provider=scope.provider,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            session_id=scope.session_id,
            run_id=scope.run_id,
            attempt_id=scope.attempt_id,
        )
        yield SandboxCreationClaim(active_lease_exists=active_lease_exists)
    finally:
        if connection is not None:
            try:
                if acquired:
                    await repositories.release_sandbox_creation_claim(connection, lock_key=lock_key)
            finally:
                await connection.close()

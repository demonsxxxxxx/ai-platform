"""Sandbox-owned durable authority shared by authenticated callback entrances."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class SandboxCallbackAuthorityError(RuntimeError):
    """A stable rejection reason; transport status mapping belongs to the caller."""


@dataclass(frozen=True, slots=True)
class SandboxCallbackAuthority:
    is_terminal_run_status: Callable[[object], bool]
    get_run_identity: Callable[..., Awaitable[dict[str, Any] | None]]
    list_current_leases: Callable[..., Awaitable[list[dict[str, Any]]]]
    list_provider_tail_leases: Callable[..., Awaitable[list[dict[str, Any]]]]

    async def recheck_attempt(
        self, conn,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        callback_token_id: str,
        allow_provider_tail: bool = False,
    ) -> dict[str, Any]:
        """Recheck the exact Attempt within the caller-owned transaction."""
        leases = await self.list_current_leases(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if not leases and allow_provider_tail:
            leases = await self.list_provider_tail_leases(
                conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            )
        if len(leases) != 1:
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_inactive")
        lease = leases[0]
        payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
        persisted_attempt_id = str(lease.get("attempt_id") or "") if isinstance(lease, dict) else ""
        payload_attempt_id = str(payload.get("attempt_id") or "") if isinstance(payload, dict) else ""
        if (persisted_attempt_id or payload_attempt_id) != attempt_id or (
            persisted_attempt_id and payload_attempt_id and persisted_attempt_id != payload_attempt_id
        ):
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_mismatch")
        persisted_token_id = str(payload.get("callback_token_id") or "") if isinstance(payload, dict) else ""
        if persisted_token_id and persisted_token_id != callback_token_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_owner_generation_stale")
        return lease

    async def lock_run_then_attempt(
        self, conn,
        *,
        run_id: str,
        attempt_id: str,
        callback_token_id: str,
        session_id: str | None = None,
        allow_provider_tail: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Lock Run before its exact Attempt; derive tenant from persisted Run identity."""
        run_hint = await self.get_run_identity(conn, run_id=run_id, for_update=False)
        if run_hint is None:
            raise SandboxCallbackAuthorityError("run_not_found")
        if session_id is not None and str(run_hint.get("session_id") or "") != session_id:
            raise SandboxCallbackAuthorityError("callback_session_mismatch")
        if self.is_terminal_run_status(run_hint.get("status")):
            raise SandboxCallbackAuthorityError("run_already_terminal")
        tenant_id = str(run_hint.get("tenant_id") or "")
        locked_run = await self.get_run_identity(conn, run_id=run_id, for_update=True)
        if locked_run is None or str(locked_run.get("tenant_id") or "") != tenant_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_inactive")
        if session_id is not None and str(locked_run.get("session_id") or "") != session_id:
            raise SandboxCallbackAuthorityError("callback_session_mismatch")
        if self.is_terminal_run_status(locked_run.get("status")):
            raise SandboxCallbackAuthorityError("run_already_terminal")
        lease = await self.recheck_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            callback_token_id=callback_token_id,
            allow_provider_tail=allow_provider_tail,
        )
        return locked_run, lease

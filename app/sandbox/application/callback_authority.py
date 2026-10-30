"""Sandbox-owned durable authority shared by authenticated callback entrances."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class SandboxCallbackAuthorityError(RuntimeError):
    """A stable rejection reason; transport status mapping belongs to the caller."""


@dataclass(frozen=True, slots=True)
class SandboxCallbackIdentity:
    """Typed durable identity shared by authenticated Sandbox callback entrances."""

    run_id: str
    attempt_id: str
    callback_token_id: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSessionAppendIdentity(SandboxCallbackIdentity):
    """Identity for the one SessionStore append allowed during provider tail drain."""


@dataclass(frozen=True, slots=True)
class ExecutorTerminalReceiptIdentity(SandboxCallbackIdentity):
    """Identity for one terminal receipt, including its narrow cancellation tail."""


@dataclass(frozen=True, slots=True)
class SandboxCallbackAuthority:
    is_terminal_run_status: Callable[[object], bool]
    get_run_identity: Callable[..., Awaitable[dict[str, Any] | None]]
    list_current_leases: Callable[..., Awaitable[list[dict[str, Any]]]]
    list_provider_tail_leases: Callable[..., Awaitable[list[dict[str, Any]]]]
    list_terminal_tail_leases: Callable[..., Awaitable[list[dict[str, Any]]]]

    async def recheck_attempt(
        self, conn,
        *,
        tenant_id: str,
        identity: SandboxCallbackIdentity,
    ) -> dict[str, Any]:
        """Recheck the exact Attempt within the caller-owned transaction."""
        return await self._recheck_attempt(conn, tenant_id=tenant_id, identity=identity)

    async def recheck_provider_session_append(
        self, conn,
        *,
        tenant_id: str,
        identity: ProviderSessionAppendIdentity,
    ) -> dict[str, Any]:
        """Recheck the narrowly typed SessionStore append tail writer."""
        if not isinstance(identity, ProviderSessionAppendIdentity):
            raise SandboxCallbackAuthorityError("provider_session_append_identity_required")
        return await self._recheck_attempt(
            conn, tenant_id=tenant_id, identity=identity, allow_provider_tail=True,
        )

    async def recheck_executor_terminal_receipt(
        self, conn,
        *,
        tenant_id: str,
        identity: ExecutorTerminalReceiptIdentity,
    ) -> dict[str, Any]:
        """Recheck a terminal receipt under current or cancellation-tail authority."""
        if not isinstance(identity, ExecutorTerminalReceiptIdentity):
            raise SandboxCallbackAuthorityError("executor_terminal_receipt_identity_required")
        lease, _cancellation_tail = await self._recheck_executor_terminal_receipt(
            conn, tenant_id=tenant_id, identity=identity,
        )
        return lease

    async def _recheck_attempt(
        self, conn,
        *,
        tenant_id: str,
        identity: SandboxCallbackIdentity,
        allow_provider_tail: bool = False,
    ) -> dict[str, Any]:
        leases = await self.list_current_leases(
            conn,
            tenant_id=tenant_id,
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
        )
        if not leases and allow_provider_tail:
            leases = await self.list_provider_tail_leases(
                conn, tenant_id=tenant_id, run_id=identity.run_id, attempt_id=identity.attempt_id,
            )
        if len(leases) != 1:
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_inactive")
        lease = leases[0]
        payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
        persisted_attempt_id = str(lease.get("attempt_id") or "") if isinstance(lease, dict) else ""
        payload_attempt_id = str(payload.get("attempt_id") or "") if isinstance(payload, dict) else ""
        if (persisted_attempt_id or payload_attempt_id) != identity.attempt_id or (
            persisted_attempt_id and payload_attempt_id and persisted_attempt_id != payload_attempt_id
        ):
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_mismatch")
        persisted_token_id = str(payload.get("callback_token_id") or "") if isinstance(payload, dict) else ""
        if persisted_token_id and persisted_token_id != identity.callback_token_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_owner_generation_stale")
        return lease

    async def _recheck_executor_terminal_receipt(
        self,
        conn,
        *,
        tenant_id: str,
        identity: ExecutorTerminalReceiptIdentity,
    ) -> tuple[dict[str, Any], bool]:
        leases = await self.list_current_leases(
            conn,
            tenant_id=tenant_id,
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
        )
        cancellation_tail = False
        if not leases:
            leases = await self.list_terminal_tail_leases(
                conn,
                tenant_id=tenant_id,
                run_id=identity.run_id,
                attempt_id=identity.attempt_id,
            )
            cancellation_tail = bool(leases)
        if len(leases) != 1:
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_inactive")
        lease = leases[0]
        payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
        persisted_attempt_id = str(lease.get("attempt_id") or "") if isinstance(lease, dict) else ""
        payload_attempt_id = str(payload.get("attempt_id") or "") if isinstance(payload, dict) else ""
        if (persisted_attempt_id or payload_attempt_id) != identity.attempt_id or (
            persisted_attempt_id and payload_attempt_id and persisted_attempt_id != payload_attempt_id
        ):
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_mismatch")
        persisted_token_id = str(payload.get("callback_token_id") or "") if isinstance(payload, dict) else ""
        if cancellation_tail and not persisted_token_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_owner_generation_stale")
        if persisted_token_id and persisted_token_id != identity.callback_token_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_owner_generation_stale")
        return lease, cancellation_tail

    async def lock_run_then_attempt(
        self, conn,
        identity: SandboxCallbackIdentity,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Lock Run before its exact Attempt; derive tenant from persisted Run identity."""
        return await self._lock_run_then_attempt(conn, identity=identity)

    async def lock_provider_session_append(
        self, conn,
        identity: ProviderSessionAppendIdentity,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Lock a SessionStore append identity with its explicit tail-drain scope."""
        if not isinstance(identity, ProviderSessionAppendIdentity):
            raise SandboxCallbackAuthorityError("provider_session_append_identity_required")
        return await self._lock_run_then_attempt(
            conn, identity=identity, allow_provider_tail=True,
        )

    async def lock_executor_terminal_receipt(
        self,
        conn,
        identity: ExecutorTerminalReceiptIdentity,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Lock one current terminal writer or its narrowly scoped cancel tail."""
        if not isinstance(identity, ExecutorTerminalReceiptIdentity):
            raise SandboxCallbackAuthorityError("executor_terminal_receipt_identity_required")
        return await self._lock_run_then_attempt(
            conn, identity=identity, allow_executor_terminal_tail=True,
        )

    async def _lock_run_then_attempt(
        self, conn,
        *,
        identity: SandboxCallbackIdentity,
        allow_provider_tail: bool = False,
        allow_executor_terminal_tail: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]] | tuple[dict[str, Any], dict[str, Any], bool]:
        run_hint = await self.get_run_identity(conn, run_id=identity.run_id, for_update=False)
        if run_hint is None:
            raise SandboxCallbackAuthorityError("run_not_found")
        if identity.session_id is not None and str(run_hint.get("session_id") or "") != identity.session_id:
            raise SandboxCallbackAuthorityError("callback_session_mismatch")
        if self.is_terminal_run_status(run_hint.get("status")):
            raise SandboxCallbackAuthorityError("run_already_terminal")
        tenant_id = str(run_hint.get("tenant_id") or "")
        locked_run = await self.get_run_identity(conn, run_id=identity.run_id, for_update=True)
        if locked_run is None or str(locked_run.get("tenant_id") or "") != tenant_id:
            raise SandboxCallbackAuthorityError("sandbox_runtime_attempt_inactive")
        if identity.session_id is not None and str(locked_run.get("session_id") or "") != identity.session_id:
            raise SandboxCallbackAuthorityError("callback_session_mismatch")
        if self.is_terminal_run_status(locked_run.get("status")):
            raise SandboxCallbackAuthorityError("run_already_terminal")
        if allow_executor_terminal_tail:
            if not isinstance(identity, ExecutorTerminalReceiptIdentity):
                raise SandboxCallbackAuthorityError("executor_terminal_receipt_identity_required")
            lease, cancellation_tail = await self._recheck_executor_terminal_receipt(
                conn, tenant_id=tenant_id, identity=identity,
            )
            return locked_run, lease, cancellation_tail
        lease = await self._recheck_attempt(
            conn,
            tenant_id=tenant_id,
            identity=identity,
            allow_provider_tail=allow_provider_tail,
        )
        # The PostgreSQL lease selector locks the lease set first and then the
        # exact Attempt, preserving Run -> lease -> Attempt ordering.
        return locked_run, lease

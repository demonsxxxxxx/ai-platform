"""Execution-owned stop/save barrier before atomic Context and Run settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContinuationSettlementPorts:
    save_workspace_version: Callable[..., Awaitable[Any]]
    settle_provider_continuation: Callable[..., Awaitable[dict[str, Any]]]
    release_runtime_lease: Callable[..., Awaitable[Any]] | None = None

    async def prepare(
        self, *, request: Any, workspace_lease: Any, stop_result: Any,
        terminal_result: dict[str, Any],
    ) -> dict[str, Any]:
        if stop_result.status not in {"stopped", "not_found"}:
            raise RuntimeError("continuation_writer_unverified")
        stop_evidence = (
            "provider_stopped" if stop_result.status == "stopped" else "provider_not_found"
        )
        try:
            # The Sandbox port reads the trusted host bind after stop, never the live executor.
            receipt = await self.save_workspace_version(
                request=request, workspace_lease=workspace_lease, stop_evidence=stop_evidence,
            )
            if not isinstance(receipt, Mapping) or receipt.get("state") != "saved":
                raise ValueError("workspace_version_receipt_invalid")
            receipt = dict(receipt)
        except Exception:
            receipt = {"state": "unsaved", "reason_code": "workspace_unsaved"}
        sdk = terminal_result.get("sdk_settlement")
        sdk = dict(sdk) if isinstance(sdk, dict) else {}
        sdk["status"] = terminal_result.get("status", "failed")
        sdk.setdefault("terminal_reason", terminal_result.get("sdk_terminal_reason"))
        sdk.setdefault("mirror_complete", False)
        sdk.setdefault("received_result", False)
        return {
            "tenant_id": request.tenant_id, "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "final_sequence": terminal_result.get("provider_session_final_sequence"),
            "workspace_receipt": receipt, "stop_evidence": stop_evidence,
            "sdk_settlement": sdk,
            "external_effect_unknown": sdk.get("external_effect_unknown") is True,
        }

    async def commit(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        settlement: dict[str, Any],
    ) -> dict[str, Any]:
        if any(settlement.get(key) != value for key, value in {
            "tenant_id": tenant_id, "run_id": run_id, "attempt_id": attempt_id,
        }.items()):
            raise ValueError("continuation_settlement_scope_invalid")
        if settlement.get("stop_evidence") not in {"provider_stopped", "provider_not_found"}:
            raise ValueError("continuation_writer_unverified")
        context_settlement = dict(settlement)
        runtime_lease = context_settlement.pop("runtime_lease", None)
        if runtime_lease is not None:
            self._validate_runtime_lease(settlement)
            if self.release_runtime_lease is None:
                raise RuntimeError("continuation_lease_release_unconfigured")
        return await self.settle_provider_continuation(conn, **context_settlement)

    @staticmethod
    def _validate_runtime_lease(settlement: dict[str, Any]) -> dict[str, Any]:
        lease = settlement.get("runtime_lease")
        if not isinstance(lease, dict) or not lease.get("lease_id") or not lease.get("user_id") or any(
            lease.get(key) != settlement.get(key) for key in ("tenant_id", "run_id", "attempt_id")
        ):
            raise ValueError("continuation_runtime_lease_scope_invalid")
        return lease

    async def release(self, conn: Any, *, settlement: dict[str, Any], reason: str) -> None:
        if "runtime_lease" not in settlement:
            return
        lease = self._validate_runtime_lease(settlement)
        if self.release_runtime_lease is None:
            raise RuntimeError("continuation_lease_release_unconfigured")
        await self.release_runtime_lease(
            conn, tenant_id=lease["tenant_id"], user_id=lease["user_id"], run_id=lease["run_id"],
            lease_id=lease["lease_id"], reason=reason,
        )

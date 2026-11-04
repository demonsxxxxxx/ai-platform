"""Sandbox-owned exact terminal receipt claim authorization."""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SandboxReconciliationReceiptAuthority:
    lock_receipt: Callable[..., Awaitable[bool]]

    async def is_current(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, claim_token: str, expected_owner_generation: int,
        scope: dict[str, str],
    ) -> bool:
        """Caller holds its Run lock; retain the receipt lock until commit."""
        return await self.lock_receipt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, claim_token=claim_token,
            expected_owner_generation=expected_owner_generation,
            workspace_id=scope["workspace_id"], user_id=scope["user_id"], session_id=scope["session_id"],
        )


async def finalize_stopped_executor_checkpoint(
    conn: Any, *, lease_row: dict[str, Any], claim_token: str,
    finalize: Callable[..., Awaitable[bool]],
) -> None:
    """Consume the exact durable stop/save checkpoint inside the Run terminal transaction."""
    scope = {key: lease_row.get(key) for key in (
        "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
    )}
    payload = lease_row.get("lease_payload_json") or {}
    stage = payload.get("terminal_reconciliation")
    continuation = stage.get("continuation") if isinstance(stage, dict) else None
    if (any(not isinstance(value, str) or not value for value in scope.values())
        or not isinstance(stage, dict)
        or stage.get("schema_version") != "ai-platform.terminal-reconciliation.v1"
        or stage.get("scope") != scope
        or type(stage.get("owner_generation")) is not int or stage["owner_generation"] < 1
        or stage["owner_generation"] != payload.get("owner_generation")
        or not stage.get("container_id") or stage["container_id"] != lease_row.get("runtime_container_id")
        or not isinstance(continuation, dict)
        or any(continuation.get(key) != scope[key] for key in ("tenant_id", "run_id", "attempt_id"))
        or continuation.get("stop_evidence") not in {"provider_stopped", "provider_not_found"}):
        raise RuntimeError("executor_reconciliation_stage_scope_invalid")
    if not await finalize(conn, scope=scope, lease_id=str(lease_row["id"]), claim_token=claim_token,
                          expected_stage=stage):
        raise RuntimeError("executor_reconciliation_claim_lost")

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

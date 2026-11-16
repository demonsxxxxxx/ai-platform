"""Narrow original-writer receipt authority after cancellation revokes execution."""

from datetime import datetime
from typing import Any


def cancel_tail_receipt_allowed(
    *, run: dict[str, Any], attempt: dict[str, Any], lease: dict[str, Any],
    tenant_id: str, run_id: str, attempt_id: str, owner_generation: int,
    operation: str, now: datetime, operation_started: bool = False,
) -> bool:
    if operation not in {"provider_append", "executor_terminal", "tool_completion"}:
        return False
    if operation == "tool_completion" and operation_started is not True:
        return False
    if type(owner_generation) is not int or owner_generation < 1:
        return False
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    expires_at = lease.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        return False
    payload = lease.get("lease_payload_json")
    if not isinstance(payload, dict) or type(payload.get("owner_generation")) is not int:
        return False
    if type(attempt.get("owner_generation")) is not int:
        return False
    return (
        run.get("tenant_id") == attempt.get("tenant_id") == lease.get("tenant_id") == tenant_id
        and run.get("id") == attempt.get("run_id") == lease.get("run_id") == run_id
        and attempt.get("id") == lease.get("attempt_id") == attempt_id
        and all(run.get(key) == lease.get(key) and bool(run.get(key))
                for key in ("workspace_id", "user_id", "session_id"))
        and run.get("status") == "running" and run.get("cancel_requested_at") is not None
        and attempt.get("status") == "cancel_requested"
        and attempt.get("owner_kind") == "queue_worker" and bool(attempt.get("owner_id"))
        and attempt["owner_generation"] == owner_generation + 1
        and payload["owner_generation"] == owner_generation
        and lease.get("status") == "active" and lease.get("released_at") is None
        and expires_at > now
    )

"""Bind a worker-owned runtime lease to its durable attempt generation."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerRuntimeSandboxLease:
    lease_id: str
    tenant_id: str
    user_id: str
    run_id: str


async def create_worker_runtime_sandbox_lease(
    conn,
    *,
    executor_type: str,
    run_identity: dict[str, str],
    trace_id: str,
    attempt_id: str,
    owner_generation: int,
    worker_id: str | None,
    ttl_seconds: int,
    create_lease: Callable[..., Awaitable[dict[str, Any]]],
) -> WorkerRuntimeSandboxLease:
    lease_payload = {
        "source": "sdk_only_lifecycle_placeholder",
        "evidence_class": "sdk_only_lifecycle_placeholder",
        "executor_type": executor_type,
        "attempt_id": attempt_id,
        "owner_generation": owner_generation,
    }
    if worker_id:
        lease_payload["worker_id"] = worker_id
    row = await create_lease(
        conn,
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        attempt_id=attempt_id,
        trace_id=trace_id,
        sandbox_mode="ephemeral",
        provider="fake",
        browser_enabled=False,
        ttl_seconds=ttl_seconds,
        resource_limits_json={},
        user_visible_payload_json={"workspace": "/workspace", "inputs": "/workspace/inputs"},
        lease_payload_json=lease_payload,
    )
    return WorkerRuntimeSandboxLease(
        lease_id=str(row["id"]),
        tenant_id=run_identity["tenant_id"],
        user_id=run_identity["user_id"],
        run_id=run_identity["run_id"],
    )


async def release_worker_runtime_sandbox_lease(
    conn,
    lease: WorkerRuntimeSandboxLease | None,
    *,
    reason: str,
    release_lease: Callable[..., Awaitable[Any]],
) -> None:
    if lease is None:
        return
    await release_lease(
        conn,
        tenant_id=lease.tenant_id,
        user_id=lease.user_id,
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        reason=reason,
    )

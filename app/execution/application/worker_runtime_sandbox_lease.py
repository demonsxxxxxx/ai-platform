from __future__ import annotations

from dataclasses import dataclass

from app.models import QueueRunPayload
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.settings import get_settings


@dataclass(frozen=True)
class WorkerRuntimeSandboxLease:
    lease_id: str
    tenant_id: str
    user_id: str
    run_id: str


async def create_worker_runtime_sandbox_lease(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    trace_id: str,
    attempt_id: str,
    worker_id: str | None,
) -> WorkerRuntimeSandboxLease:
    lease_payload = {
        "source": "sdk_only_lifecycle_placeholder",
        "evidence_class": "sdk_only_lifecycle_placeholder",
        "executor_type": payload.executor_type,
        "attempt_id": attempt_id,
    }
    if worker_id:
        lease_payload["worker_id"] = worker_id
    row = await sandbox_lease_repository.create_sandbox_lease(
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
        ttl_seconds=get_settings().sandbox_lease_ttl_seconds,
        resource_limits_json={},
        user_visible_payload_json={
            "workspace": "/workspace",
            "inputs": "/workspace/inputs",
        },
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
) -> None:
    if lease is None:
        return
    await sandbox_lease_repository.release_sandbox_lease(
        conn,
        tenant_id=lease.tenant_id,
        user_id=lease.user_id,
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        reason=reason,
    )

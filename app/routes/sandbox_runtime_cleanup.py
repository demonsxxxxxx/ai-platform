from collections.abc import Callable
from typing import Any

from app.runtime.sandbox.container_provider import ContainerProvider
from app.runtime.sandbox.contracts import ContainerLease
from app import repositories
from app.db import transaction


ProviderFactory = Callable[[str | None], ContainerProvider]


class SandboxRuntimeCleanupError(RuntimeError):
    """Raised when one or more active sandbox leases cannot be stopped."""

    def __init__(self, failures: list[dict[str, str]], *, stopped_leases: list[dict[str, Any]] | None = None) -> None:
        super().__init__("sandbox_runtime_cleanup_failed")
        self.failures = failures
        self.stopped_leases = stopped_leases or []


def _container_lease_from_row(row: dict[str, Any]) -> ContainerLease | None:
    provider = str(row.get("provider") or "fake")
    if provider not in {"fake", "docker", "opensandbox"}:
        return None
    run_id = str(row["run_id"])
    raw_payload = row.get("lease_payload_json")
    lease_payload = raw_payload if isinstance(raw_payload, dict) else {}
    container_id = str(lease_payload.get("container_id") or f"exec-{run_id}")
    if provider == "opensandbox" and not lease_payload.get("container_id"):
        return None
    container_name = str(lease_payload.get("container_name") or f"executor-{container_id}")
    executor_url = str(lease_payload.get("executor_url") or "http://sandbox-runtime.invalid")
    workspace_container_path = str(lease_payload.get("workspace_container_path") or "/workspace")
    labels_payload = lease_payload.get("labels")
    labels = {str(key): str(value) for key, value in labels_payload.items()} if isinstance(labels_payload, dict) else {}
    return ContainerLease(
        container_id=container_id,
        container_name=container_name,
        provider=provider,
        executor_url=executor_url,
        tenant_id=str(row["tenant_id"]),
        workspace_id=str(row["workspace_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        run_id=run_id,
        sandbox_mode=str(row["sandbox_mode"]),
        browser_enabled=bool(row.get("browser_enabled")),
        workspace_host_path="",
        workspace_container_path=workspace_container_path,
        labels=labels,
    )


async def stop_sandbox_leases(
    sandbox_leases: list[dict[str, Any]] | None,
    *,
    reason: str,
    provider_factory: ProviderFactory,
) -> list[dict[str, Any]]:
    """Stop runtime containers for active sandbox lease rows."""
    failures: list[dict[str, str]] = []
    stopped_leases: list[dict[str, Any]] = []
    for row in sandbox_leases or []:
        lease = _container_lease_from_row(row)
        if lease is None:
            failures.append(
                {
                    "container_id": str(row.get("id") or row.get("run_id") or "unknown"),
                    "message": f"Unsupported sandbox provider: {row.get('provider')}",
                }
            )
            continue
        provider = provider_factory(lease.provider)
        result = await provider.stop(lease, reason=reason)
        if result.status == "failed":
            failures.append({"container_id": result.container_id, "message": result.message})
            continue
        stopped_leases.append(row)
    if failures:
        raise SandboxRuntimeCleanupError(failures, stopped_leases=stopped_leases)
    return stopped_leases


async def cleanup_expired_sandbox_runtime_leases(
    conn: Any,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
    provider_factory: ProviderFactory,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Stop expired runtime containers before releasing their DB lease rows."""
    expired_leases = await repositories.list_expired_active_sandbox_leases(conn, tenant_id=tenant_id, limit=limit)
    if not expired_leases:
        return []

    async def release_stopped_with_conn(release_conn: Any, stopped_leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        released: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for lease in stopped_leases:
            grouped.setdefault(str(lease["tenant_id"]), []).append(lease)
        for release_tenant_id, tenant_leases in grouped.items():
            released.extend(
                await repositories.release_stopped_sandbox_leases(
                    release_conn,
                    tenant_id=release_tenant_id,
                    reason=reason,
                    lease_ids=[str(lease["id"]) for lease in tenant_leases],
                )
            )
        return released

    async def release_stopped_committed(stopped_leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with transaction() as release_conn:
            return await release_stopped_with_conn(release_conn, stopped_leases)

    try:
        stopped_leases = await stop_sandbox_leases(
            expired_leases,
            reason=reason,
            provider_factory=provider_factory,
        )
    except SandboxRuntimeCleanupError as exc:
        if exc.stopped_leases:
            await release_stopped_committed(exc.stopped_leases)
        raise
    if not stopped_leases:
        return []
    return await release_stopped_with_conn(conn, stopped_leases)

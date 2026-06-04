from collections.abc import Callable
from typing import Any

from app.runtime.sandbox.container_provider import ContainerProvider
from app.runtime.sandbox.contracts import ContainerLease


ProviderFactory = Callable[[str | None], ContainerProvider]


class SandboxRuntimeCleanupError(RuntimeError):
    """Raised when one or more active sandbox leases cannot be stopped."""

    def __init__(self, failures: list[dict[str, str]], *, stopped_leases: list[dict[str, Any]] | None = None) -> None:
        super().__init__("sandbox_runtime_cleanup_failed")
        self.failures = failures
        self.stopped_leases = stopped_leases or []


def _container_lease_from_row(row: dict[str, Any]) -> ContainerLease | None:
    provider = str(row.get("provider") or "fake")
    if provider not in {"fake", "docker"}:
        return None
    run_id = str(row["run_id"])
    container_id = f"exec-{run_id}"
    return ContainerLease(
        container_id=container_id,
        container_name=f"executor-{container_id}",
        provider=provider,
        executor_url="http://sandbox-runtime.invalid",
        tenant_id=str(row["tenant_id"]),
        workspace_id=str(row["workspace_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        run_id=run_id,
        sandbox_mode=str(row["sandbox_mode"]),
        browser_enabled=bool(row.get("browser_enabled")),
        workspace_host_path="",
        workspace_container_path="/workspace",
        labels={},
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

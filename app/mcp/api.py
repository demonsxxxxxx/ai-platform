"""Public in-process MCP contracts used by legacy delivery and execution code."""

from __future__ import annotations

from typing import Any

from app.mcp.application.runtime_registry import (
    LiveMcpCatalogProxy,
    RelayAuthFailureLimiterProxy,
    RunCapabilityManagerProxy,
    configure_mcp_runtime_services,
    mcp_runtime_services,
)
from app.mcp.application.live_catalog import (
    GatewayRevisions,
    MCP_CACHE_INVALIDATION_TOKEN_HEADER,
    read_cached_live_mcp_tool,
    service_token_matches,
)
from app.mcp.domain.errors import (
    McpRelayError,
    McpRuntimeContextError,
    McpToolSelectionRequired,
)
from app.mcp.domain.headers import (
    MCP_JWT_AUTHORIZATION_HEADER,
    normalize_static_mcp_headers,
)
from app.mcp.domain.identifiers import is_safe_mcp_id
from app.mcp.domain.tool_references import (
    assert_mcp_tool_reference,
    build_mcp_tool_reference,
    is_valid_mcp_public_tool_name,
    parse_mcp_tool_reference,
)
from app.mcp.domain.targets import (
    mcp_targets_from_policy_subjects,
    mcp_targets_from_reconciliation_snapshot,
    normalize_mcp_targets,
)
_CAPABILITY_MANAGER_PROXY = RunCapabilityManagerProxy()
_FAILURE_LIMITER_PROXY = RelayAuthFailureLimiterProxy()
_LIVE_CATALOG_PROXY = LiveMcpCatalogProxy()
_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}


def get_mcp_run_capability_manager() -> RunCapabilityManagerProxy:
    return _CAPABILITY_MANAGER_PROXY


def get_mcp_relay_auth_failure_limiter() -> RelayAuthFailureLimiterProxy:
    return _FAILURE_LIMITER_PROXY


def get_live_mcp_catalog() -> LiveMcpCatalogProxy:
    return _LIVE_CATALOG_PROXY


async def store_mcp_principal_jwt(principal: Any, jwt: str) -> None:
    """Replace the encrypted company JWT bound to one Principal."""

    await mcp_runtime_services().principal_jwt_store.put(principal, jwt)


async def read_mcp_principal_jwt(principal: Any) -> str:
    """Read the current unexpired company JWT bound to one Principal."""

    return await mcp_runtime_services().principal_jwt_store.get(principal)


def create_host_mcp_relay(*, capability_manager: Any | None = None) -> Any:
    return mcp_runtime_services().create_host_relay(
        capability_manager=capability_manager or _CAPABILITY_MANAGER_PROXY,
    )


def seal_mcp_server_credentials(**kwargs: Any) -> str:
    return mcp_runtime_services().seal_server_credentials(**kwargs)


def open_mcp_server_credentials(**kwargs: Any) -> tuple[str | None, dict[str, str]]:
    return mcp_runtime_services().open_server_credentials(**kwargs)


async def record_mcp_server_credential(conn: Any, **kwargs: Any) -> Any:
    return await mcp_runtime_services().record_server_credential(conn, **kwargs)


async def list_mcp_server_registry(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return await mcp_runtime_services().list_server_registry(conn, **kwargs)


async def upsert_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await mcp_runtime_services().upsert_server_registry(conn, **kwargs)


async def toggle_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await mcp_runtime_services().toggle_server_registry(conn, **kwargs)


async def delete_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await mcp_runtime_services().delete_server_registry(conn, **kwargs)


async def upsert_mcp_distribution(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await mcp_runtime_services().upsert_distribution(conn, **kwargs)


async def toggle_mcp_distribution(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await mcp_runtime_services().toggle_distribution(conn, **kwargs)


async def release_mcp_run_grant(
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> None:
    """Best-effort cleanup for one Run's JWT-free capability grant."""

    try:
        await mcp_runtime_services().capability_manager.release_run_grant(
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001 - grant expiry remains the final cleanup fence.
        pass


async def release_terminal_mcp_run_grant(
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    status: object,
) -> None:
    """Release a Run grant only after its terminal status is committed."""

    if str(status or "") not in _TERMINAL_RUN_STATUSES:
        return
    await release_mcp_run_grant(
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )


async def release_committed_terminal_mcp_run_grant(
    *,
    tenant_id: str,
    run_id: str,
    status: object,
    transaction_factory: Any,
) -> None:
    """Best-effort grant cleanup after a terminal Run transaction has committed."""

    if str(status or "") not in _TERMINAL_RUN_STATUSES:
        return
    try:
        async with transaction_factory() as conn:
            identity = await mcp_runtime_services().get_run_identity(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
            )
    except Exception:  # noqa: BLE001 - expiry remains the final cleanup fence.
        return
    if identity is None:
        return
    await release_mcp_run_grant(
        tenant_id=identity["tenant_id"],
        user_id=identity["user_id"],
        run_id=identity["run_id"],
    )


__all__ = [
    "MCP_JWT_AUTHORIZATION_HEADER",
    "MCP_CACHE_INVALIDATION_TOKEN_HEADER",
    "GatewayRevisions",
    "McpRelayError",
    "McpRuntimeContextError",
    "McpToolSelectionRequired",
    "assert_mcp_tool_reference",
    "build_mcp_tool_reference",
    "configure_mcp_runtime_services",
    "create_host_mcp_relay",
    "delete_mcp_server_registry",
    "get_mcp_relay_auth_failure_limiter",
    "get_mcp_run_capability_manager",
    "get_live_mcp_catalog",
    "read_mcp_principal_jwt",
    "release_committed_terminal_mcp_run_grant",
    "release_mcp_run_grant",
    "release_terminal_mcp_run_grant",
    "is_safe_mcp_id",
    "is_valid_mcp_public_tool_name",
    "list_mcp_server_registry",
    "mcp_targets_from_policy_subjects",
    "mcp_targets_from_reconciliation_snapshot",
    "normalize_mcp_targets",
    "normalize_static_mcp_headers",
    "open_mcp_server_credentials",
    "parse_mcp_tool_reference",
    "read_cached_live_mcp_tool",
    "record_mcp_server_credential",
    "seal_mcp_server_credentials",
    "service_token_matches",
    "store_mcp_principal_jwt",
    "toggle_mcp_distribution",
    "toggle_mcp_server_registry",
    "upsert_mcp_distribution",
    "upsert_mcp_server_registry",
]

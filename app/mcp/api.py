"""Public MCP contracts for legacy delivery and execution callers."""

from __future__ import annotations

from typing import Any

from app.mcp.application.live_catalog import (
    LiveMcpServerResult,
    LiveMcpTool,
)
from app.mcp.application.runtime_registry import (
    LiveMcpCatalogProxy,
    configure_mcp_runtime_services,
    mcp_runtime_services,
)
from app.mcp.domain.errors import McpRuntimeContextError
from app.mcp.domain.headers import (
    MCP_JWT_AUTHORIZATION_HEADER,
    normalize_static_mcp_headers,
)
from app.mcp.domain.identifiers import is_safe_mcp_id
from app.mcp.domain.tool_references import (
    assert_mcp_tool_reference,
    build_mcp_tool_reference,
    is_valid_mcp_public_tool_name,
    mcp_runtime_metadata_usable,
    parse_mcp_tool_reference,
)


_LIVE_CATALOG_PROXY = LiveMcpCatalogProxy()


def get_live_mcp_catalog() -> LiveMcpCatalogProxy:
    return _LIVE_CATALOG_PROXY


def get_mcp_principal_jwt_store() -> Any:
    return mcp_runtime_services().principal_jwt_store


async def store_mcp_principal_jwt(principal: Any, jwt: str) -> None:
    await mcp_runtime_services().principal_jwt_store.put(principal, jwt)


async def read_mcp_principal_jwt(principal: Any) -> str:
    return await mcp_runtime_services().principal_jwt_store.get(principal)


def seal_mcp_server_credentials(**kwargs: Any) -> str:
    return mcp_runtime_services().seal_server_credentials(**kwargs)


def open_mcp_server_credentials(**kwargs: Any) -> tuple[str | None, dict[str, str]]:
    return mcp_runtime_services().open_server_credentials(**kwargs)


async def attach_mcp_server_configs(
    conn: Any,
    *,
    principal: Any,
    run_payload: Any,
) -> Any:
    return await mcp_runtime_services().attach_server_configs(
        conn,
        principal=principal,
        run_payload=run_payload,
    )


async def _repository_call(operation: str, conn: Any, **kwargs: Any) -> Any:
    return await mcp_runtime_services().repository_call(operation, conn, **kwargs)


async def authorize_selected_chat_mcp_tools(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return await _repository_call("authorize_selected_chat_mcp_tools", conn, **kwargs)


async def get_mcp_server_registry_entry(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await _repository_call("get_mcp_server_registry_entry", conn, **kwargs)


async def get_mcp_server_runtime_target(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await _repository_call("get_mcp_server_runtime_target", conn, **kwargs)


async def get_mcp_tool_registry_entry(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await _repository_call("get_mcp_tool_registry_entry", conn, **kwargs)


async def list_mcp_server_registry(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return await _repository_call("list_mcp_server_registry", conn, **kwargs)


async def upsert_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _repository_call("upsert_mcp_server_registry", conn, **kwargs)


async def toggle_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _repository_call("toggle_mcp_server_registry", conn, **kwargs)


async def delete_mcp_server_registry(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _repository_call("delete_mcp_server_registry", conn, **kwargs)


async def record_mcp_server_credential(conn: Any, **kwargs: Any) -> None:
    await _repository_call("record_mcp_server_credential", conn, **kwargs)


__all__ = [
    "LiveMcpServerResult",
    "LiveMcpTool",
    "MCP_JWT_AUTHORIZATION_HEADER",
    "McpRuntimeContextError",
    "assert_mcp_tool_reference",
    "attach_mcp_server_configs",
    "authorize_selected_chat_mcp_tools",
    "build_mcp_tool_reference",
    "configure_mcp_runtime_services",
    "delete_mcp_server_registry",
    "get_live_mcp_catalog",
    "get_mcp_principal_jwt_store",
    "get_mcp_server_registry_entry",
    "get_mcp_server_runtime_target",
    "get_mcp_tool_registry_entry",
    "is_safe_mcp_id",
    "is_valid_mcp_public_tool_name",
    "list_mcp_server_registry",
    "mcp_runtime_metadata_usable",
    "normalize_static_mcp_headers",
    "open_mcp_server_credentials",
    "parse_mcp_tool_reference",
    "read_mcp_principal_jwt",
    "record_mcp_server_credential",
    "seal_mcp_server_credentials",
    "store_mcp_principal_jwt",
    "toggle_mcp_server_registry",
    "upsert_mcp_server_registry",
]

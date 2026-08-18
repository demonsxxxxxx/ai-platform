"""Public in-process MCP contracts used by legacy delivery and execution code."""

from __future__ import annotations

from typing import Any

from app.mcp.application.runtime_registry import (
    RelayAuthFailureLimiterProxy,
    RuntimeContextManagerProxy,
    configure_mcp_runtime_services,
    mcp_runtime_services,
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
from app.mcp.domain.identifiers import assert_safe_mcp_id
from app.mcp.domain.targets import (
    mcp_targets_from_policy_subjects,
    normalize_mcp_targets,
)


_CONTEXT_MANAGER_PROXY = RuntimeContextManagerProxy()
_FAILURE_LIMITER_PROXY = RelayAuthFailureLimiterProxy()


def get_mcp_runtime_context_manager() -> RuntimeContextManagerProxy:
    return _CONTEXT_MANAGER_PROXY


def get_mcp_relay_auth_failure_limiter() -> RelayAuthFailureLimiterProxy:
    return _FAILURE_LIMITER_PROXY


def queue_input_with_mcp_context(
    input_payload: dict[str, Any],
    context_id: str | None,
) -> dict[str, Any]:
    """Bind only the trusted runtime-context ID into a queue-visible input."""

    result = dict(input_payload)
    result.pop("mcp_context_id", None)
    if context_id:
        result["mcp_context_id"] = assert_safe_mcp_id(context_id, "mcp_context_id")
    return result


def persisted_mcp_context_id(run: object) -> str | None:
    if not isinstance(run, dict):
        return None
    input_json = run.get("input_json")
    persisted = input_json.get("mcp_context_id") if isinstance(input_json, dict) else None
    context_id = run.get("mcp_context_id") or persisted
    return str(context_id) if isinstance(context_id, str) and context_id else None


def create_host_mcp_relay(*, context_manager: Any | None = None) -> Any:
    return mcp_runtime_services().create_host_relay(
        context_manager=context_manager or _CONTEXT_MANAGER_PROXY,
    )


def seal_mcp_server_credentials(**kwargs: Any) -> str:
    return mcp_runtime_services().seal_server_credentials(**kwargs)


def open_mcp_server_credentials(**kwargs: Any) -> tuple[str | None, dict[str, str]]:
    return mcp_runtime_services().open_server_credentials(**kwargs)


async def record_mcp_server_credential(conn: Any, **kwargs: Any) -> Any:
    return await mcp_runtime_services().record_server_credential(conn, **kwargs)


async def bind_run_mcp_context(conn: Any, **kwargs: Any) -> None:
    await mcp_runtime_services().bind_run_context(conn, **kwargs)


async def invalidate_mcp_runtime_context(context_id: str | None) -> None:
    """Best-effort cleanup for a context whose owning Run will not execute."""

    if not context_id:
        return
    try:
        await mcp_runtime_services().context_manager.invalidate_context(context_id)
    except Exception:  # noqa: BLE001 - expiry remains the final cleanup fence.
        pass


async def discard_unbound_mcp_runtime_context(
    context_id: str | None,
    principal: Any,
) -> None:
    """Best-effort cleanup for an unused context supplied to an idempotent replay."""

    if not context_id:
        return
    try:
        await mcp_runtime_services().context_manager.discard_unbound_context(
            context_id,
            principal,
        )
    except Exception:  # noqa: BLE001 - expiry remains the final cleanup fence.
        pass


async def migrate_legacy_mcp_credentials() -> dict[str, int]:
    """Idempotently move legacy plaintext MCP targets into encrypted envelopes."""

    return await mcp_runtime_services().migrate_legacy_credentials()


async def preflight_mcp_admission(
    *,
    context_id: str | None,
    principal: Any,
    run_id: str,
    selected_tool_names: list[str] | tuple[str, ...] | None,
    mcp_required: bool,
    context_manager: Any | None = None,
) -> Any | None:
    if not mcp_required:
        return None
    if not context_id:
        raise McpRuntimeContextError("mcp_context_required", status_code=409)
    relay = create_host_mcp_relay(context_manager=context_manager)
    return await relay.preflight(
        context_id=context_id,
        principal=principal,
        run_id=run_id,
        selected_tool_names=selected_tool_names,
    )


__all__ = [
    "MCP_JWT_AUTHORIZATION_HEADER",
    "McpRelayError",
    "McpRuntimeContextError",
    "McpToolSelectionRequired",
    "bind_run_mcp_context",
    "configure_mcp_runtime_services",
    "create_host_mcp_relay",
    "discard_unbound_mcp_runtime_context",
    "get_mcp_relay_auth_failure_limiter",
    "get_mcp_runtime_context_manager",
    "invalidate_mcp_runtime_context",
    "migrate_legacy_mcp_credentials",
    "mcp_targets_from_policy_subjects",
    "normalize_mcp_targets",
    "normalize_static_mcp_headers",
    "open_mcp_server_credentials",
    "persisted_mcp_context_id",
    "preflight_mcp_admission",
    "queue_input_with_mcp_context",
    "record_mcp_server_credential",
    "seal_mcp_server_credentials",
]

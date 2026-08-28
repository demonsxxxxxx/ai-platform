"""Compose MCP application ports with concrete platform adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import repositories
from app.capability_distribution import (
    CapabilityDistributionSubject,
    resolve_capability_access,
)
from app.db import transaction
from app.mcp.api import configure_mcp_runtime_services
from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.infrastructure import catalog as mcp_catalog
from app.mcp.infrastructure import postgres as mcp_postgres
from app.mcp.infrastructure import runtime as mcp_runtime
from app.redis_client import get_redis_client
from app.settings import get_settings
from app.tool_policy import evaluate_tool_policy


@dataclass(frozen=True)
class _LiveMcpTarget:
    endpoint: str
    static_headers: dict[str, str]


class _McpRuntimeServices:
    def __init__(self) -> None:
        mcp_runtime.configure_runtime_dependencies(
            settings_provider=get_settings,
            redis_provider=get_redis_client,
        )
        self.principal_jwt_store = mcp_runtime.get_mcp_principal_jwt_store()
        self.live_catalog = LiveMcpCatalogService(
            target_resolver=self._resolve_live_target,
            discovery=mcp_catalog.StreamableHttpMcpToolDiscoveryAdapter(),
        )

    @staticmethod
    async def _resolve_live_target(tenant_id: str, server_id: str) -> _LiveMcpTarget:
        async with transaction() as conn:
            row = await mcp_postgres.get_mcp_server_runtime_target(
                conn,
                tenant_id=tenant_id,
                server_name=server_id,
            )
        if row is None:
            raise mcp_runtime.McpRuntimeContextError(
                "mcp_server_not_available",
                status_code=503,
            )
        endpoint, static_headers = mcp_runtime.open_mcp_server_credentials(
            tenant_id=tenant_id,
            server_id=server_id,
            envelope=str(row.get("credential_envelope") or ""),
        )
        if not endpoint:
            raise mcp_runtime.McpRuntimeContextError(
                "mcp_server_not_available",
                status_code=503,
            )
        return _LiveMcpTarget(endpoint=endpoint, static_headers=static_headers)

    @staticmethod
    def seal_server_credentials(**kwargs: Any) -> str:
        return mcp_runtime.seal_mcp_server_credentials(**kwargs)

    @staticmethod
    def open_server_credentials(**kwargs: Any) -> tuple[str | None, dict[str, str]]:
        return mcp_runtime.open_mcp_server_credentials(**kwargs)

    async def _get_tool(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        if kwargs.get("tool_id") == mcp_postgres.TRUSTED_BUILTIN_MCP_TOOL_ID:
            return await repositories.get_mcp_tool_registry_entry(conn, **kwargs)
        return await mcp_postgres.get_mcp_tool_registry_entry(conn, **kwargs)

    async def _authorize_tools(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        tenant_id = kwargs["tenant_id"]
        tool_ids = kwargs["tool_ids"]
        context = repositories._chat_mcp_access_context(
            tenant_id=tenant_id,
            principal_department_id=kwargs["principal_department_id"],
            principal_roles=kwargs["principal_roles"],
            is_admin=kwargs["is_admin"],
            permissions=kwargs["permissions"],
        )
        if len(tool_ids) != len(set(tool_ids)):
            raise repositories._capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id="mcp_tool",
            )
        authorized: list[dict[str, Any]] = []
        for tool_id in tool_ids:
            tool = await self._get_tool(conn, tenant_id=tenant_id, tool_id=tool_id)
            if tool is None or not mcp_postgres.mcp_runtime_metadata_usable(tool):
                raise repositories._capability_not_authorized(
                    context=context,
                    capability_kind="mcp_tool",
                    capability_id=tool_id,
                )
            server_id = str(tool.get("server_id") or "")
            distribution = await repositories.get_capability_distribution_row(
                conn,
                tenant_id=tenant_id,
                capability_kind="mcp_server",
                capability_id=server_id,
            )
            lifecycle_status = (
                "active"
                if str(tool.get("effective_status") or "disabled") == "active"
                and str(tool.get("server_status") or "disabled") == "active"
                and bool(tool.get("visible_to_user"))
                else "disabled"
            )
            decision = resolve_capability_access(
                context,
                CapabilityDistributionSubject(
                    capability_kind="mcp_tool",
                    capability_id=tool_id,
                    lifecycle_status=lifecycle_status,
                    distribution=distribution,
                    inherited_distribution_source=f"mcp_server:{server_id}",
                ),
                intent="use",
            )
            policy = evaluate_tool_policy(
                tool={
                    "mcp_server": server_id,
                    "mcp_tool": str((tool.get("allowed_tools") or [""])[0]),
                    "registered": True,
                    "declared": True,
                    "active": lifecycle_status == "active",
                    "distributed": decision.usable,
                    "identity_authorized": True,
                    "object_authorized": True,
                    "parameters_authorized": True,
                    "risk_level": str(tool.get("risk_level") or "low"),
                    "write_capable": bool(tool.get("write_capable")),
                }
            )
            if not decision.usable or not policy.allowed:
                raise repositories._capability_not_authorized(
                    context=context,
                    capability_kind="mcp_tool",
                    capability_id=tool_id,
                    decision=decision,
                )
            authorized.append(tool)
        return authorized

    async def repository_call(self, operation: str, conn: Any, **kwargs: Any) -> Any:
        if operation == "authorize_selected_chat_mcp_tools":
            return await self._authorize_tools(conn, **kwargs)
        if operation == "get_mcp_tool_registry_entry":
            return await self._get_tool(conn, **kwargs)
        operations = {
            "delete_mcp_server_registry": mcp_postgres.delete_mcp_server_registry,
            "get_mcp_server_registry_entry": mcp_postgres.get_mcp_server_registry_entry,
            "get_mcp_server_runtime_target": mcp_postgres.get_mcp_server_runtime_target,
            "list_mcp_server_registry": mcp_postgres.list_mcp_server_registry,
            "record_mcp_server_credential": mcp_postgres.record_mcp_server_credential,
            "toggle_mcp_server_registry": mcp_postgres.toggle_mcp_server_registry,
            "upsert_mcp_server_registry": mcp_postgres.upsert_mcp_server_registry,
        }
        try:
            handler = operations[operation]
        except KeyError as exc:
            raise mcp_runtime.McpRuntimeContextError(
                "mcp_repository_operation_invalid",
                status_code=503,
            ) from exc
        return await handler(conn, **kwargs)

    @staticmethod
    async def attach_server_configs(
        conn: Any,
        *,
        principal: Any,
        run_payload: Any,
    ) -> Any:
        return await mcp_runtime.attach_mcp_server_configs(
            conn,
            principal=principal,
            run_payload=run_payload,
        )


def configure_mcp_runtime() -> None:
    configure_mcp_runtime_services(_McpRuntimeServices())

"""Resolve current-user Gateway contributions before freezing a new Run."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.mcp.domain.errors import McpRuntimeContextError
from app.mcp.domain.tool_references import parse_mcp_tool_reference


@dataclass(frozen=True)
class TaskGatewaySelection:
    server_ids: frozenset[str]
    tool_ids: tuple[str, ...]

    def without_gateway(self, tool_ids: list[str] | tuple[str, ...]) -> list[str]:
        return [tool_id for tool_id in tool_ids if tool_id.partition("::")[0] not in self.server_ids]


async def select_task_gateway_tools(
    conn: Any, *, principal: Any, list_servers, lock_servers, read_opt_outs, list_distributions,
    may_use, read_target, read_jwt, discover_tools,
    selected_tool_ids: list[str] | tuple[str, ...] = (),
) -> TaskGatewaySelection:
    tenant_id = principal.tenant_id
    servers = await list_servers(conn, tenant_id=tenant_id)
    gateway_names = {row["name"] for row in servers}
    async def read_targets(names: list[str]) -> dict[str, Any]:
        if not names:
            return {}
        disabled = await read_opt_outs(conn, principal)
        rows = await list_distributions(
            conn, tenant_id=tenant_id, capability_kind="mcp_server", include_disabled=False, capability_ids=names,
        )
        eligible = {}
        for row in rows:
            name = row["capability_id"]
            if name in disabled or not may_use(principal, row):
                continue
            try:
                target = await read_target(conn, tenant_id, name)
            except McpRuntimeContextError:
                continue
            if target.integration_kind == "enterprise_gateway":
                eligible[name] = target
        return eligible

    targets = await read_targets([
        row["name"] for row in servers if row["status"] == "active" and "::" not in row["name"]
        and row["name"] not in {"ai-platform-context", "ai-platform-response"}
    ])
    discovered: dict[str, tuple[str, ...]] = {}
    if targets:
        semaphore = asyncio.Semaphore(8)
        try:
            async with asyncio.timeout(10):
                jwt = await read_jwt(principal)

                async def discover(name: str, target: Any) -> None:
                    async with semaphore:
                        result = await discover_tools(
                            target, tenant_id=tenant_id, user_id=principal.user_id, server_id=name, jwt=jwt,
                        )
                        discovered[name] = tuple(tool.tool_id for tool in result.tools)

                async with asyncio.TaskGroup() as tasks:
                    for name, target in targets.items():
                        tasks.create_task(discover(name, target))
        except (TimeoutError, McpRuntimeContextError):
            # An unavailable optional Gateway does not block a valid task.
            pass

    # Order the locks and acquire them only after the network wait. A concurrent
    # mode change of a submitted ordinary Server must be classified at this fence.
    names = set(discovered)
    for tool_id in selected_tool_ids:
        try:
            server_id, _ = parse_mcp_tool_reference(tool_id)
        except ValueError:
            continue  # Ordinary selection validation still owns invalid references.
        names.add(server_id)
    if names:
        locked = await lock_servers(conn, tenant_id=tenant_id, names=sorted(names))
        gateway_names.update(row["name"] for row in locked if row["integration_kind"] == "enterprise_gateway")
    current = await read_targets([name for name, tools in discovered.items() if tools])
    return TaskGatewaySelection(frozenset(gateway_names), tuple(
        tool_id for name, target in targets.items() if current.get(name) == target
        for tool_id in discovered.get(name, ())
    ))

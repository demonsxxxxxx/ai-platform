"""Expose a run's selected MCP tools through Claude's in-process adapter."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent


class ClaudeMcpRegistration:
    def __init__(self, subjects, configs, *, session_factory, list_tools):
        self.configs = configs
        self._session_factory = session_factory
        self._list_tools = list_tools
        self.aliases: dict[str, str] = {}
        self.sdk_names: dict[str, str] = {}
        self.server_aliases: dict[str, str] = {}
        self.selected: dict[str, set[str]] = {}
        server_aliases = {"ai-platform-context": "ai-platform-context"}
        for identity, subject in subjects.items():
            server = subject.get("mcp_server")
            if not identity.startswith("mcp__") or server == "ai-platform-context":
                continue
            if server not in configs:
                raise ValueError("mcp_server_configuration_missing")
            server_alias = re.sub(r"[^a-zA-Z0-9_-]", "_", server)
            if server_aliases.setdefault(server_alias, server) != server:
                raise ValueError("mcp_sdk_name_collision")
            self.server_aliases[server] = server_alias
            tool = subject["mcp_tool"]
            alias = f"mcp__{server_alias}__{re.sub(r'[^a-zA-Z0-9_-]', '_', tool)}"
            if self.aliases.setdefault(alias, identity) != identity:
                raise ValueError("mcp_sdk_name_collision")
            self.sdk_names[identity] = alias
            self.selected.setdefault(server, set()).add(tool)

    def canonical_identity(self, name: object) -> str:
        value = str(name or "")
        return self.aliases.get(value, value)

    @staticmethod
    def _server(server_name, tools, session):
        selected = {tool.name: tool for tool in tools}
        server = Server(server_name)

        @server.list_tools()
        async def list_selected_tools():
            return list(selected.values())

        @server.call_tool(validate_input=False)
        async def call_selected_tool(name: str, arguments: dict[str, Any]):
            if name not in selected:
                return CallToolResult(
                    content=[TextContent(type="text", text="Tool is not authorized")],
                    isError=True,
                )
            # Preserve the original name, structuredContent, content and isError.
            result = await session.call_tool(name, arguments)
            # The installed Claude SDK bridge forwards MCP content and isError
            # but drops structuredContent. Keep that data available to the model
            # while retaining the original typed result for direct MCP callers.
            structured = getattr(result, "structuredContent", None)
            if isinstance(structured, dict):
                result = result.model_copy(update={
                    "content": [
                        *result.content,
                        TextContent(
                            type="text",
                            text="[structuredContent]\n" + json.dumps(
                                structured, ensure_ascii=True, separators=(",", ":")
                            ),
                        ),
                    ],
                })
            return result

        return {"type": "sdk", "name": server_name, "instance": server}

    @asynccontextmanager
    async def activate(self, options):
        async with AsyncExitStack() as stack:
            servers = self.configs
            for server_name, names in self.selected.items():
                async with asyncio.timeout(10):
                    session = await stack.enter_async_context(
                        self._session_factory(self.configs[server_name])
                    )
                    tools = await self._list_tools(session)
                selected = [tool for tool in tools if tool.name in names]
                if {tool.name for tool in selected} != names:
                    raise ValueError("mcp_selected_tool_unavailable")
                server_alias = self.server_aliases[server_name]
                servers.pop(server_name, None)
                servers[server_alias] = self._server(server_alias, selected, session)
            options.mcp_servers = servers
            yield

    def check_message(self, message):
        if getattr(message, "subtype", None) != "init":
            return
        data = getattr(message, "data", {})
        statuses = data.get("mcp_servers", []) if isinstance(data, dict) else []
        if any(
            status.get("name") in self.configs
            and status.get("status") in {"failed", "needs-auth", "disabled"}
            for status in statuses if isinstance(status, dict)
        ):
            raise ValueError("mcp_server_connection_failed")

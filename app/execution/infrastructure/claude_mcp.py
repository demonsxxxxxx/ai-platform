"""Expose a run's selected MCP tools through Claude's in-process adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent


_BUILTIN_SERVERS = frozenset({"ai-platform-context", "ai-platform-response"})
_MAX_SDK_TOOL_NAME_LENGTH = 64
_MAX_SDK_SERVER_NAME_LENGTH = 24
_MCP_DISCOVERY_TIMEOUT_SECONDS = 10


def _safe_component(value: str) -> str:
    component = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    component = re.sub(r"_+", "_", component)
    # Keep the namespace separator unique in the composed SDK tool name.
    if component.startswith("_"):
        component = "x" + component
    return component


def _stable_aliases(
    values: list[str],
    *,
    make_base,
    max_length: int,
    reserved: set[str] | None = None,
) -> dict[str, str]:
    """Keep readable aliases where possible and disambiguate collisions by identity."""

    reserved_names = {name.casefold() for name in reserved or set()}
    bases = {value: make_base(value) for value in values}
    shortened = {value: base[:max_length] for value, base in bases.items()}
    counts: dict[str, int] = {}
    for candidate in shortened.values():
        folded = candidate.casefold()
        counts[folded] = counts.get(folded, 0) + 1

    collisions = {
        value
        for value, base in bases.items()
        if len(base) > max_length
        or counts[shortened[value].casefold()] > 1
        or shortened[value].casefold() in reserved_names
    }
    aliases: dict[str, str] = {}
    occupied = set(reserved_names)
    for value in values:
        if value not in collisions:
            candidate = shortened[value]
            if candidate.casefold() not in occupied:
                aliases[value] = candidate
                occupied.add(candidate.casefold())
                continue
            collisions.add(value)

    for value in values:
        if value not in collisions:
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        for digest_length in (8, 12, 16, 24, 32, 40, 48):
            suffix = "_" + digest[:digest_length]
            if len(suffix) >= max_length:
                continue
            candidate = bases[value][: max_length - len(suffix)].rstrip("_") + suffix
            if candidate.casefold() not in occupied:
                aliases[value] = candidate
                occupied.add(candidate.casefold())
                break
        else:  # pragma: no cover - requires a SHA-256 collision.
            raise ValueError("mcp_sdk_alias_unavailable")
    return aliases


class ClaudeMcpRegistration:
    def __init__(self, subjects, configs, *, session_factory, list_tools):
        self.configs = configs
        self._session_factory = session_factory
        self._list_tools = list_tools
        self.aliases: dict[str, str] = {}
        self.sdk_names: dict[str, str] = {}
        self.server_aliases: dict[str, str] = {
            name: name for name in _BUILTIN_SERVERS
        }
        self.selected: dict[str, set[str]] = {}
        self.unavailable: dict[str, str] = {}
        self._identity_components: dict[str, tuple[str, str]] = {}
        self._tool_aliases: dict[tuple[str, str], str] = {}

        selected_identities: list[tuple[str, str, str]] = []
        for identity, subject in sorted(subjects.items()):
            server = str(subject.get("mcp_server") or "")
            if not identity.startswith("mcp__") or server in _BUILTIN_SERVERS:
                continue
            tool = str(subject.get("mcp_tool") or "")
            if not server or not tool:
                continue
            selected_identities.append((identity, server, tool))
            self._identity_components[identity] = (server, tool)
            self.selected.setdefault(server, set()).add(tool)

        servers = sorted(self.selected)
        self.server_aliases.update(
            _stable_aliases(
                servers,
                make_base=_safe_component,
                max_length=_MAX_SDK_SERVER_NAME_LENGTH,
                reserved=set(_BUILTIN_SERVERS),
            )
        )

        tool_aliases: dict[tuple[str, str], str] = {}
        for server in servers:
            server_alias = self.server_aliases[server]
            max_tool_length = _MAX_SDK_TOOL_NAME_LENGTH - len("mcp__") - 2 - len(server_alias)
            remote_names = sorted(self.selected[server])
            components = _stable_aliases(
                remote_names,
                make_base=_safe_component,
                max_length=max_tool_length,
            )
            tool_aliases.update({(server, remote): alias for remote, alias in components.items()})
        self._tool_aliases = tool_aliases

        sdk_alias_by_identity = {
            identity: f"mcp__{self.server_aliases[server]}__{tool_aliases[(server, tool)]}"
            for identity, server, tool in selected_identities
        }
        if len({alias.casefold() for alias in sdk_alias_by_identity.values()}) != len(
            sdk_alias_by_identity
        ):
            raise ValueError("mcp_sdk_name_collision")
        for identity, server, tool in selected_identities:
            alias = sdk_alias_by_identity[identity]
            # These are the exact names produced by the SDK from server + tool
            # aliases. Do not independently rename the flattened name.
            assert alias == (
                f"mcp__{self.server_aliases[server]}__"
                f"{self._tool_aliases[(server, tool)]}"
            )
            assert len(alias) <= _MAX_SDK_TOOL_NAME_LENGTH
            self.sdk_names[identity] = alias
            self.aliases[alias] = identity

    def canonical_identity(self, name: object) -> str:
        value = str(name or "")
        return self.aliases.get(value, value)

    def _server(self, server_name, tools, session):
        selected = {alias: (tool, remote_name) for alias, tool, remote_name in tools}
        server = Server(server_name)

        @server.list_tools()
        async def list_selected_tools():
            return [tool for tool, _remote_name in selected.values()]

        @server.call_tool(validate_input=False)
        async def call_selected_tool(name: str, arguments: dict[str, Any]):
            if name not in selected:
                return CallToolResult(
                    content=[TextContent(type="text", text="Tool is not authorized")],
                    isError=True,
                )
            _tool, remote_name = selected[name]
            # Preserve the original remote name, structuredContent, content and isError.
            result = await session.call_tool(remote_name, arguments)
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

    async def _serve_server(self, server_name, names, release_event, ready):
        config = self.configs.get(server_name)
        if not isinstance(config, dict):
            self.unavailable[server_name] = "server_configuration_missing"
            ready.set_result(None)
            return

        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(_MCP_DISCOVERY_TIMEOUT_SECONDS):
                    session = await stack.enter_async_context(self._session_factory(config))
                    tools = await self._list_tools(session)
                by_name = {tool.name: tool for tool in tools}
                available_names = names.intersection(by_name)
                if available_names != names:
                    self.unavailable[server_name] = "selected_tool_unavailable"
                if not available_names:
                    if not ready.done():
                        ready.set_result(None)
                    return

                alias_by_remote = {
                    remote_name: alias
                    for (server, remote_name), alias in self._tool_aliases.items()
                    if server == server_name
                }
                selected = []
                for remote_name in sorted(available_names):
                    alias = alias_by_remote[remote_name]
                    tool = by_name[remote_name]
                    if tool.name != alias:
                        tool = tool.model_copy(update={"name": alias})
                    selected.append((alias, tool, remote_name))
                if not ready.done():
                    ready.set_result((selected, session))
                # This task owns the session context until activation exits.
                # The SDK may call the session from another task, but cleanup
                # always runs in the task that entered the context manager.
                await release_event.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - an external discovery failure is local to this Server.
            if isinstance(exc, TimeoutError):
                reason = "connection_timeout"
            else:
                reason = getattr(exc, "reason", None)
                if not isinstance(reason, str) or not reason:
                    reason = "connection_failed"
            self.unavailable[server_name] = reason
            if not ready.done():
                ready.set_result(None)

    @asynccontextmanager
    async def activate(self, options):
        self.unavailable = {}
        release_event = asyncio.Event()
        ready_by_server = {
            server: asyncio.get_running_loop().create_future()
            for server in sorted(self.selected)
        }
        tasks = [
            asyncio.create_task(
                self._serve_server(server, names, release_event, ready_by_server[server])
            )
            for server, names in sorted(self.selected.items())
        ]
        try:
            outcomes = await asyncio.gather(*ready_by_server.values())
            servers = dict(self.configs)
            for server_name in self.selected:
                servers.pop(server_name, None)
                servers.pop(self.server_aliases[server_name], None)
            for server_name, outcome in zip(ready_by_server, outcomes):
                if outcome is None:
                    continue
                selected, session = outcome
                server_alias = self.server_aliases[server_name]
                servers[server_alias] = self._server(server_alias, selected, session)
            options.mcp_servers = servers
            yield
        finally:
            release_event.set()
            # Discovery has no consumer after activation exits. Cancel only
            # owners that have not become ready; healthy sessions close via
            # their release event in the task that opened them.
            for ready, task in zip(ready_by_server.values(), tasks):
                if not ready.done() or ready.cancelled():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def check_message(self, message):
        if getattr(message, "subtype", None) != "init":
            return
        data = getattr(message, "data", {})
        statuses = data.get("mcp_servers", []) if isinstance(data, dict) else []
        reverse_aliases = {alias: server for server, alias in self.server_aliases.items()}
        for status in statuses:
            if not isinstance(status, dict) or status.get("status") not in {
                "failed", "needs-auth", "disabled"
            }:
                continue
            server = reverse_aliases.get(status.get("name"), status.get("name"))
            if server in self.selected:
                self.unavailable.setdefault(server, "connection_failed")

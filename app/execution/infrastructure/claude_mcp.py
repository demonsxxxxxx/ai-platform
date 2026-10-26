"""Expose a run's selected MCP tools through Claude's in-process adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import Any, Awaitable, Callable

from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError, UrlElicitationRequiredError
from app.execution.infrastructure.original_file_delivery import OriginalFileDeliveryError
from app.mcp.api import (
    MAX_ORIGINAL_FILE_RESPONSE_BYTES,
    OriginalFileResultError,
    extract_original_file_result,
    original_file_result_metadata,
)
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    ErrorData,
    InputRequiredResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)


_BUILTIN_SERVERS = frozenset({"ai-platform-context", "ai-platform-response"})
_MAX_SDK_TOOL_NAME_LENGTH = 64
_MAX_SDK_SERVER_NAME_LENGTH = 24
_MCP_DISCOVERY_TIMEOUT_SECONDS = 10
_MCP_DISCOVERY_RESPONSE_BYTES = 1024 * 1024


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


def _safe_workspace_reference(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("inputs/"):
        return None
    if "\\" in value or "\x00" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return value


def _tool_arguments_digest(arguments: object) -> str | None:
    try:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ClaudeMcpRegistration:
    def __init__(self, subjects, configs, *, session_factory, list_tools, callback_wrapper=None, failure_observer=None, credential_resolver=None, original_file_delivery: Callable[[dict[str, Any], bytes], Awaitable[dict[str, Any]]] | None = None):
        self._credential_resolver = credential_resolver
        self._original_file_delivery = original_file_delivery
        self._failure_observer = failure_observer
        self.configs = configs
        self._session_factory = session_factory
        self._list_tools = list_tools
        self._track_callback = callback_wrapper or (lambda callback: callback)
        self.aliases: dict[str, str] = {}
        self.sdk_names: dict[str, str] = {}
        self.server_aliases: dict[str, str] = {
            name: name for name in _BUILTIN_SERVERS
        }
        self.selected: dict[str, set[str]] = {}
        self.unavailable: dict[str, str] = {}
        self._identity_components: dict[str, tuple[str, str]] = {}
        self._tool_aliases: dict[tuple[str, str], str] = {}
        self._tool_calls: dict[tuple[str, str], str] = {}
        self._tool_error_results: set[tuple[str, str]] = set()

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

    def track_tool_call(self, identity: str, call_id: str, arguments: object) -> None:
        if identity in self._identity_components:
            digest = _tool_arguments_digest(arguments)
            if digest is not None:
                self._tool_calls.setdefault((identity, call_id), digest)

    def consume_tool_error_result(self, identity: str, call_id: str) -> bool:
        key = (identity, call_id)
        self._tool_calls.pop(key, None)
        received = key in self._tool_error_results
        self._tool_error_results.discard(key)
        return received

    def _server(self, server_name, tools, session, original_file_session_factory=None):
        selected = {alias: (tool, remote_name) for alias, tool, remote_name in tools}

        async def list_selected_tools(
            _ctx: ServerRequestContext,
            _params: PaginatedRequestParams | None,
        ) -> ListToolsResult:
            return ListToolsResult(
                tools=[tool for tool, _remote_name in selected.values()]
            )

        async def call_selected_tool(
            _ctx: ServerRequestContext,
            params: CallToolRequestParams,
        ) -> CallToolResult | InputRequiredResult:
            name = params.name
            arguments: dict[str, Any] = params.arguments or {}
            if name not in selected:
                return CallToolResult(
                    content=[TextContent(type="text", text="Tool is not authorized")],
                    is_error=True,
                )
            tool, remote_name = selected[name]
            # Preserve the original remote tool name and typed result fields.
            try:
                if original_file_result_metadata(tool.input_schema):
                    if original_file_session_factory is None:
                        raise MCPError(-32000, "original_file_session_unavailable")
                    async with original_file_session_factory() as original_session:
                        seed_tool_definition = getattr(original_session, "seed_tool_definition", None)
                        if callable(seed_tool_definition):
                            seed_tool_definition(tool.model_copy(update={"name": remote_name}))
                        result = await original_session.call_tool(remote_name, arguments)
                else:
                    result = await session.call_tool(remote_name, arguments)
            except UrlElicitationRequiredError:
                # Keep the MCP server's special elicitation handling unchanged.
                raise
            except MCPError as exc:
                # Observe only typed client evidence, never text/data or HTTP status.
                # No native tool_use_id crosses this boundary, so no call-level join.
                if self._failure_observer is not None:
                    try:
                        observation = {
                            "source": "mcp_session_call",
                            "scope": "run_attempt",
                            "exception_type": "MCPError",
                        }
                        code = exc.error.code if isinstance(exc.error, ErrorData) else None
                        # Preserve exact numbers through JSON and JavaScript consumers.
                        if type(code) is int and -(2**53 - 1) <= code <= 2**53 - 1:
                            observation["code"] = code
                        self._failure_observer(observation)
                    except Exception:
                        # Diagnostics must never replace the model-facing exception.
                        pass
                raise
            if (
                isinstance(result, CallToolResult)
                and result.is_error is False
                and original_file_result_metadata(tool.input_schema)
            ):
                if self._original_file_delivery is None:
                    raise MCPError(-32000, "original_file_delivery_unavailable")
                try:
                    requested_max_bytes = arguments.get("maxBytes")
                    if requested_max_bytes is None:
                        requested_max_bytes = arguments.get("max_bytes")
                    metadata, raw_bytes = extract_original_file_result(
                        result, max_bytes=requested_max_bytes
                    )
                    delivery = await self._original_file_delivery(metadata, raw_bytes)
                    workspace_path = _safe_workspace_reference(
                        delivery.get("workspace_path") if isinstance(delivery, dict) else None
                    )
                    if workspace_path is None:
                        raise OriginalFileResultError("original_file_delivery_invalid")
                    model_metadata = {
                        key: metadata[key]
                        for key in ("format", "source", "name", "size_bytes", "content_type")
                    }
                    model_metadata["workspace_path"] = workspace_path
                    result = result.model_copy(update={
                        "content": [TextContent(
                            type="text",
                            text=json.dumps(model_metadata, ensure_ascii=True, separators=(",", ":")),
                        )],
                        "structured_content": None,
                    })
                except OriginalFileResultError as exc:
                    raise MCPError(-32000, str(exc)) from None
                except OriginalFileDeliveryError:
                    # Local materialization failure is deliberately distinct from
                    # the remote CallToolResult and contains no host/path detail.
                    raise MCPError(-32001, "original_file_delivery_failed") from None
            if isinstance(result, CallToolResult) and result.is_error is True:
                identity = self.canonical_identity(f"mcp__{server_name}__{name}")
                digest = _tool_arguments_digest(arguments)
                matching = [key for key, value in self._tool_calls.items()
                            if key[0] == identity and value == digest]
                # MCP carries no native call ID. Identical concurrent inputs
                # cannot prove which admitted call received this error result.
                if len(matching) == 1:
                    self._tool_error_results.add(matching[0])
            # MCP model attributes are snake_case; Server.run() emits their
            # camelCase wire aliases. Keep structured output visible in content
            # while retaining the original typed field for MCP callers.
            structured = result.structured_content if isinstance(result, CallToolResult) else None
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

        server = Server(
            server_name,
            on_list_tools=list_selected_tools,
            on_call_tool=self._track_callback(call_selected_tool),
        )
        return {"type": "sdk", "name": server_name, "instance": server}

    async def _serve_server(self, server_name, names, release_event, ready):
        config = self.configs.get(server_name)
        if not isinstance(config, dict):
            self.unavailable[server_name] = "server_configuration_missing"
            ready.set_result(None)
            return

        try:
            async with AsyncExitStack() as stack:
                credential_options = ({"credential_resolver": partial(self._credential_resolver, server_name)}
                                      if self._credential_resolver is not None else {})
                discovery_config = {
                    **config,
                    "max_response_bytes": _MCP_DISCOVERY_RESPONSE_BYTES,
                    "max_response_total_bytes": _MCP_DISCOVERY_RESPONSE_BYTES,
                }
                discovery_context = self._session_factory(discovery_config, **credential_options)
                discovery_session = await discovery_context.__aenter__()
                try:
                    async with asyncio.timeout(_MCP_DISCOVERY_TIMEOUT_SECONDS):
                        tools = await self._list_tools(discovery_session)
                except BaseException as exc:
                    await discovery_context.__aexit__(type(exc), exc, exc.__traceback__)
                    raise
                by_name = {tool.name: tool for tool in tools}
                available_names = names.intersection(by_name)
                if available_names != names:
                    self.unavailable[server_name] = "selected_tool_unavailable"
                if not available_names:
                    await discovery_context.__aexit__(None, None, None)
                    if not ready.done():
                        ready.set_result(None)
                    return

                stack.push_async_exit(discovery_context)
                session = discovery_session
                original_file_session_factory = None
                if any(
                    original_file_result_metadata(by_name[name].input_schema)
                    for name in available_names
                ):
                    expanded_config = {
                        **config,
                        "max_response_bytes": MAX_ORIGINAL_FILE_RESPONSE_BYTES,
                        "max_response_total_bytes": MAX_ORIGINAL_FILE_RESPONSE_BYTES,
                    }

                    def original_file_session_factory(
                        *, _config=expanded_config, _credential_options=credential_options,
                    ):
                        return self._session_factory(_config, **_credential_options)
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
                    ready.set_result((selected, session, original_file_session_factory))
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

    def _configure_tool_search(self, options):
        """Allow SDK catalog discovery without granting a business capability."""
        hooks = (getattr(options, "hooks", None) or {}).get("PreToolUse", [])
        generic_hooks = [matcher for matcher in hooks if matcher.matcher is None]
        if not generic_hooks or not isinstance(getattr(options, "tools", None), list):
            return

        def wrap(callback):
            async def admit_search(hook_input, tool_use_id=None, context=None):
                if not isinstance(hook_input, dict) or hook_input.get("tool_name") != "ToolSearch":
                    return await callback(hook_input, tool_use_id, context)
                # Search only sees the run's registered SDK catalog. Execution of
                # a discovered tool still passes the ordinary per-call policy.
                value = hook_input.get("tool_input")
                valid = (
                    isinstance(value, dict) and not set(value) - {"query", "max_results"}
                    and isinstance(value.get("query"), str) and 0 < len(value["query"]) <= 4096
                    and type(value.get("max_results", 5)) is int
                    and 1 <= value.get("max_results", 5) <= 20
                )
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow" if valid else "deny",
                    "permissionDecisionReason": "sdk_tool_discovery" if valid else "sdk_tool_search_input_invalid",
                }}
            return self._track_callback(admit_search)

        for matcher in generic_hooks:
            matcher.hooks = [wrap(callback) for callback in matcher.hooks]
        if "ToolSearch" not in options.tools:
            options.tools.append("ToolSearch")
        if "ToolSearch" not in options.allowed_tools:
            options.allowed_tools.append("ToolSearch")

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
                selected, session, original_file_session_factory = outcome
                server_alias = self.server_aliases[server_name]
                servers[server_alias] = self._server(
                    server_alias, selected, session, original_file_session_factory
                )
            options.mcp_servers = servers
            if any(outcome is not None for outcome in outcomes):
                self._configure_tool_search(options)
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

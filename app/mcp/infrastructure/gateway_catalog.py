"""Read the enterprise Gateway's anonymous catalog without runtime credentials."""

from __future__ import annotations

import asyncio
import json

import httpx

from app.mcp.domain.errors import McpToolDiscoveryError
from app.mcp.infrastructure.catalog import _canonical_live_definition, _validated_discovery_target


_GATEWAY_UPSTREAM_LABEL_MAX_LENGTH = 128


def _upstream_server_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip()
    if not label or len(label.encode("utf-8")) > _GATEWAY_UPSTREAM_LABEL_MAX_LENGTH or any(
        ord(character) < 32 or ord(character) == 127 for character in label
    ):
        return None
    return label


class GatewayCatalogAdapter:
    def __init__(self, *, timeout_seconds: float = 10.0, max_response_bytes: int = 1024 * 1024):
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def discover_definitions(
        self, endpoint: str, *, allow_ambiguous_names: bool = False,
    ) -> tuple[dict, ...]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                target = await _validated_discovery_target(endpoint)
                url = httpx.URL(target.connect_url).copy_with(path="/api/mcp/catalog")
                # A fresh credential-free client never inherits user or Server headers/cookies.
                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=self._timeout_seconds,
                ) as client:
                    async with client.stream(
                        "GET", url,
                        headers={"Host": target.host_header, "Accept": "application/json", "Accept-Encoding": "identity"},
                        extensions={"sni_hostname": target.sni_hostname},
                    ) as response:
                        if response.is_redirect:
                            raise McpToolDiscoveryError("transport_failure")
                        response.raise_for_status()
                        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                            raise McpToolDiscoveryError("unsupported_content_encoding")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > self._max_response_bytes:
                                raise McpToolDiscoveryError("response_too_large")
                            body.extend(chunk)
                return _catalog_definitions(
                    json.loads(body), allow_ambiguous_names=allow_ambiguous_names,
                )
        except McpToolDiscoveryError:
            raise
        except (TimeoutError, httpx.HTTPError):
            raise McpToolDiscoveryError("transport_failure") from None
        except (ValueError, TypeError):
            raise McpToolDiscoveryError("protocol_error") from None


def _catalog_definitions(
    payload: object, *, allow_ambiguous_names: bool = False,
) -> tuple[dict, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise McpToolDiscoveryError("protocol_error")
    definitions = []
    names: set[str] = set()
    ambiguous_names: set[str] = set()
    for server in payload["servers"]:
        if (not isinstance(server, dict) or not isinstance(server.get("tools"), list)
                or type(server.get("catalogGeneration")) is not int):
            raise McpToolDiscoveryError("protocol_error")
        upstream_server = _upstream_server_label(server.get("projectName"))
        for tool in server["tools"]:
            if (not isinstance(tool, dict) or type(tool.get("available")) is not bool
                    or type(tool.get("definitionRevision")) is not int
                    or type(tool.get("catalogGeneration")) is not int
                    or not isinstance(tool.get("inputSchema"), dict)):
                raise McpToolDiscoveryError("protocol_error")
            definition = _canonical_live_definition(tool)
            definition["upstream_server"] = upstream_server
            if definition["name"] in names:
                if not allow_ambiguous_names:
                    raise McpToolDiscoveryError("protocol_error")
                ambiguous_names.add(definition["name"])
            names.add(definition["name"])
            definition["available"] = (
                tool["available"] and tool["catalogGeneration"] > 0
                and tool["catalogGeneration"] == server["catalogGeneration"]
            )
            definitions.append(definition)
    if ambiguous_names:
        for definition in definitions:
            if definition["name"] in ambiguous_names:
                definition["upstream_server"] = None
    return tuple(definitions)

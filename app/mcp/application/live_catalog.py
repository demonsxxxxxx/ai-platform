from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.mcp.catalog import McpToolDiscoveryError, StreamableHttpMcpToolDiscoveryAdapter
from app.mcp.domain.tool_references import build_mcp_tool_reference


MCP_GATEWAY_SERVICE_TOKEN_HEADER = "X-MCP-Gateway-Service-Token"
MCP_CACHE_INVALIDATION_TOKEN_HEADER = "X-AI-Platform-Callback-Token"
MCP_TOOL_CACHE_TTL_SECONDS = 24 * 60 * 60
MCP_REVISION_CACHE_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class GatewayRevisions:
    catalog_revision: int
    acl_revision: int


@dataclass(frozen=True)
class LiveMcpTool:
    tool_id: str
    server_id: str
    public_tool_name: str
    label: str
    description: str
    cached: bool = False
    write_capable: bool = True
    risk_level: str = "high"

    def public_payload(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "label": self.label,
            "description": self.description,
            "category": "mcp",
            "server": self.server_id,
            "cached": self.cached,
        }


@dataclass(frozen=True)
class LiveMcpServerResult:
    server_id: str
    tools: tuple[LiveMcpTool, ...]
    unavailable_reason: str | None = None


def _bounded_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("mcp_gateway_revision_invalid")
    return value


def _revision_payload(value: object) -> GatewayRevisions:
    if not isinstance(value, dict):
        raise ValueError("mcp_gateway_revision_invalid")
    return GatewayRevisions(
        catalog_revision=_bounded_revision(value.get("catalog_revision")),
        acl_revision=_bounded_revision(value.get("acl_revision")),
    )


def _version_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("mcp_gateway_revision_endpoint_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/internal/cache-revisions", "", ""))


class LiveMcpCatalogService:
    """Resolve user-effective Gateway tools without creating a platform catalog."""

    def __init__(
        self,
        *,
        redis_provider: Callable[[], Any],
        target_resolver: Callable[[str, str], Awaitable[Any]],
        target_validator: Callable[[str], Awaitable[Any]],
        service_token_provider: Callable[[], str],
        discovery: StreamableHttpMcpToolDiscoveryAdapter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis_provider = redis_provider
        self._target_resolver = target_resolver
        self._target_validator = target_validator
        self._service_token_provider = service_token_provider
        self._discovery = discovery or StreamableHttpMcpToolDiscoveryAdapter()
        self._clock = clock

    @staticmethod
    def _revision_key(tenant_id: str, server_id: str) -> str:
        return f"ai-platform:mcp:revisions:v1:{tenant_id}:{server_id}"

    @staticmethod
    def _revision_latest_key(tenant_id: str, server_id: str) -> str:
        return f"ai-platform:mcp:revisions-latest:v1:{tenant_id}:{server_id}"

    @staticmethod
    def _tool_key(
        tenant_id: str,
        user_id: str,
        server_id: str,
        revisions: GatewayRevisions,
    ) -> str:
        return (
            f"ai-platform:mcp:tools:v1:{tenant_id}:{user_id}:{server_id}:"
            f"{revisions.catalog_revision}:{revisions.acl_revision}"
        )

    @staticmethod
    def _latest_tool_key(tenant_id: str, user_id: str, server_id: str) -> str:
        return f"ai-platform:mcp:tools-latest:v1:{tenant_id}:{user_id}:{server_id}"

    async def _redis_get(self, key: str) -> str | None:
        client = self._redis_provider()
        try:
            value = await client.get(key)
            return value if isinstance(value, str) else None
        finally:
            await client.aclose()

    async def _redis_set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        client = self._redis_provider()
        try:
            await client.set(key, value, ex=max(1, ttl_seconds))
        finally:
            await client.aclose()

    async def _safe_redis_get(self, key: str) -> str | None:
        try:
            return await self._redis_get(key)
        except Exception:
            return None

    async def _safe_redis_set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        try:
            await self._redis_set(key, value, ttl_seconds=ttl_seconds)
        except Exception:
            return

    async def _cached_revisions(self, tenant_id: str, server_id: str) -> GatewayRevisions | None:
        raw = await self._safe_redis_get(self._revision_key(tenant_id, server_id))
        if raw is None:
            return None
        try:
            return _revision_payload(json.loads(raw))
        except (ValueError, json.JSONDecodeError):
            return None

    async def _query_revisions(self, endpoint: str) -> GatewayRevisions | None:
        token = self._service_token_provider().strip()
        if not token:
            return None
        try:
            validated = await self._target_validator(_version_url(endpoint))
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(
                    validated.connect_url,
                    headers={
                        MCP_GATEWAY_SERVICE_TOKEN_HEADER: token,
                        "Host": validated.host_header,
                    },
                    extensions={"sni_hostname": validated.sni_hostname},
                )
            if response.status_code != 200 or len(response.content) > 16_384:
                return None
            return _revision_payload(response.json())
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, AttributeError):
            return None

    async def _revisions(self, tenant_id: str, server_id: str, endpoint: str) -> GatewayRevisions | None:
        cached = await self._cached_revisions(tenant_id, server_id)
        if cached is not None:
            return cached
        revisions = await self._query_revisions(endpoint)
        if revisions is None:
            return None
        payload = json.dumps(revisions.__dict__, separators=(",", ":"), sort_keys=True)
        await asyncio.gather(
            self._safe_redis_set(
                self._revision_key(tenant_id, server_id),
                payload,
                ttl_seconds=MCP_REVISION_CACHE_TTL_SECONDS,
            ),
            self._safe_redis_set(
                self._revision_latest_key(tenant_id, server_id),
                payload,
                ttl_seconds=MCP_TOOL_CACHE_TTL_SECONDS,
            ),
        )
        return revisions

    @staticmethod
    def _decode_tools(raw: str | None, *, cached: bool) -> tuple[LiveMcpTool, ...] | None:
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                return None
            tools = []
            for item in payload:
                if not isinstance(item, dict):
                    return None
                reference = build_mcp_tool_reference(item["server_id"], item["public_tool_name"])
                tools.append(
                    LiveMcpTool(
                        tool_id=reference,
                        server_id=str(item["server_id"]),
                        public_tool_name=str(item["public_tool_name"]),
                        label=str(item["label"]),
                        description=str(item["description"]),
                        cached=cached,
                        write_capable=bool(item.get("write_capable", True)),
                        risk_level=str(item.get("risk_level") or "high"),
                    )
                )
            return tuple(tools)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _encode_tools(tools: tuple[LiveMcpTool, ...]) -> str:
        return json.dumps(
            [
                {
                    "server_id": tool.server_id,
                    "public_tool_name": tool.public_tool_name,
                    "label": tool.label,
                    "description": tool.description,
                    "write_capable": tool.write_capable,
                    "risk_level": tool.risk_level,
                }
                for tool in tools
            ],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def _latest_tools(self, tenant_id: str, user_id: str, server_id: str) -> tuple[LiveMcpTool, ...] | None:
        return self._decode_tools(
            await self._safe_redis_get(self._latest_tool_key(tenant_id, user_id, server_id)),
            cached=True,
        )

    async def list_server_tools(
        self,
        *,
        tenant_id: str,
        user_id: str,
        server_id: str,
        jwt: str,
    ) -> LiveMcpServerResult:
        try:
            target = await self._target_resolver(tenant_id, server_id)
        except Exception:
            stale = await self._latest_tools(tenant_id, user_id, server_id)
            return LiveMcpServerResult(server_id, stale or (), None if stale is not None else "discovery_failed")

        revisions = await self._revisions(tenant_id, server_id, target.endpoint)
        if revisions is not None:
            cached = self._decode_tools(
                await self._safe_redis_get(self._tool_key(tenant_id, user_id, server_id, revisions)),
                cached=True,
            )
            if cached is not None:
                return LiveMcpServerResult(server_id, cached)
        try:
            definitions = await self._discovery.discover_definitions(
                target.endpoint,
                static_headers=target.static_headers,
                jwt_authorization=f"Bearer {jwt}",
            )
            tools = tuple(
                LiveMcpTool(
                    tool_id=build_mcp_tool_reference(server_id, definition["name"]),
                    server_id=server_id,
                    public_tool_name=definition["name"],
                    label=definition["name"],
                    description=definition["description"],
                    write_capable=not bool(
                        isinstance(definition.get("annotations"), dict)
                        and definition["annotations"].get("readOnlyHint") is True
                    ),
                    risk_level=(
                        "low"
                        if isinstance(definition.get("annotations"), dict)
                        and definition["annotations"].get("readOnlyHint") is True
                        else "high"
                    ),
                )
                for definition in definitions
            )
        except (McpToolDiscoveryError, ValueError, KeyError, TypeError):
            stale = await self._latest_tools(tenant_id, user_id, server_id)
            return LiveMcpServerResult(server_id, stale or (), None if stale is not None else "discovery_failed")

        encoded = self._encode_tools(tools)
        writes = [
            self._safe_redis_set(
                self._latest_tool_key(tenant_id, user_id, server_id),
                encoded,
                ttl_seconds=MCP_TOOL_CACHE_TTL_SECONDS,
            )
        ]
        if revisions is not None:
            writes.append(
                self._safe_redis_set(
                    self._tool_key(tenant_id, user_id, server_id, revisions),
                    encoded,
                    ttl_seconds=MCP_TOOL_CACHE_TTL_SECONDS,
                )
            )
        await asyncio.gather(*writes)
        return LiveMcpServerResult(server_id, tools)

    async def invalidate(
        self,
        *,
        tenant_id: str,
        server_id: str,
        revisions: GatewayRevisions,
        event_id: str,
    ) -> bool:
        event_key = f"ai-platform:mcp:invalidation-event:v1:{event_id}"
        revision_key = self._revision_key(tenant_id, server_id)
        latest_key = self._revision_latest_key(tenant_id, server_id)
        client = self._redis_provider()
        try:
            result = await client.eval(
                """
                if redis.call('GET', KEYS[1]) then return 0 end
                local catalog = tonumber(ARGV[1])
                local acl = tonumber(ARGV[2])
                local raw = redis.call('GET', KEYS[3])
                if raw then
                  local ok, existing = pcall(cjson.decode, raw)
                  if ok and type(existing) == 'table' then
                    catalog = math.max(catalog, tonumber(existing['catalog_revision']) or 0)
                    acl = math.max(acl, tonumber(existing['acl_revision']) or 0)
                  end
                end
                local payload = cjson.encode({catalog_revision=catalog, acl_revision=acl})
                redis.call('SET', KEYS[1], '1', 'EX', ARGV[3])
                redis.call('SET', KEYS[2], payload, 'EX', ARGV[4])
                redis.call('SET', KEYS[3], payload, 'EX', ARGV[3])
                return 1
                """,
                3,
                event_key,
                revision_key,
                latest_key,
                revisions.catalog_revision,
                revisions.acl_revision,
                MCP_TOOL_CACHE_TTL_SECONDS,
                MCP_REVISION_CACHE_TTL_SECONDS,
            )
            return int(result or 0) == 1
        finally:
            await client.aclose()


def service_token_matches(configured: str, supplied: str | None) -> bool:
    expected = str(configured or "").strip()
    raw = str(supplied or "").strip()
    return bool(expected and raw and secrets.compare_digest(expected, raw))


async def read_cached_live_mcp_tool(
    *,
    redis_provider: Callable[[], Any],
    tenant_id: str,
    user_id: str,
    server_id: str,
    public_tool_name: str,
) -> LiveMcpTool | None:
    """Read one same-user ephemeral discovery result without falling back across identities."""

    try:
        client = redis_provider()
        raw = await client.get(LiveMcpCatalogService._latest_tool_key(tenant_id, user_id, server_id))
    except Exception:
        return None
    finally:
        if "client" in locals():
            try:
                await client.aclose()
            except Exception:
                pass
    tools = LiveMcpCatalogService._decode_tools(raw if isinstance(raw, str) else None, cached=True)
    if tools is None:
        return None
    return next((tool for tool in tools if tool.public_tool_name == public_tool_name), None)

"""Bounded RAGFlow dataset-catalog adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.knowledge.domain import (
    KnowledgeError,
    ProviderCatalogSnapshot,
    ProviderSourceRecord,
    canonical_origin,
)


_MAX_CATALOG_PAGES = 100
_CATALOG_PAGE_SIZE = 100
_MAX_CATALOG_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_METADATA_FIELDS = ("description", "document_count", "chunk_count", "update_date")
_BLOCKED_EXACT_IPS = {ipaddress.ip_address("169.254.169.254")}


@dataclass(frozen=True)
class _ValidatedTarget:
    origin: str
    hostname: str
    host_header: str
    address: str


class _PinnedOriginTransport(httpx.AsyncBaseTransport):
    """Connect to the address that passed policy validation, preserving TLS SNI."""

    def __init__(
        self,
        *,
        target: _ValidatedTarget,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self._target = target
        self._owns_transport = transport is None
        self._transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = request.headers.copy()
        headers["host"] = self._target.host_header
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self._target.hostname
        pinned_request = httpx.Request(
            request.method,
            request.url.copy_with(host=self._target.address),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()


class RagFlowCatalogProvider:
    provider_key = "ragflow"

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Any],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._transport = transport

    async def _validated_origin(self, base_url: str) -> _ValidatedTarget:
        origin = canonical_origin(base_url)
        parsed = urlsplit(origin)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        allowed_hosts = {
            item.strip().rstrip(".").lower()
            for item in str(
                self._settings_provider().knowledge_connection_allowed_hosts or ""
            ).split(",")
            if item.strip()
        }
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise KnowledgeError("knowledge_connection_dns_unavailable") from exc
        addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
        if not addresses:
            raise KnowledgeError("knowledge_connection_dns_unavailable")
        explicitly_allowed = hostname in allowed_hosts
        if not explicitly_allowed:
            raise KnowledgeError("knowledge_connection_endpoint_forbidden")
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise KnowledgeError("knowledge_connection_dns_unavailable") from exc
            if (
                address in _BLOCKED_EXACT_IPS
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            ):
                raise KnowledgeError("knowledge_connection_endpoint_forbidden")
        return _ValidatedTarget(
            origin=origin,
            hostname=hostname,
            host_header=parsed.netloc,
            address=addresses[0],
        )

    async def _page(
        self,
        *,
        client: httpx.AsyncClient,
        credential: str,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        try:
            async with client.stream(
                    "GET",
                    "/api/v1/datasets",
                    params={"page": page, "page_size": page_size},
                    headers={"Authorization": f"Bearer {credential}"},
                ) as response:
                if response.status_code in {401, 403}:
                    raise KnowledgeError("knowledge_connection_auth_invalid")
                if response.status_code == 429:
                    raise KnowledgeError("knowledge_provider_rate_limited")
                if response.status_code != 200:
                    raise KnowledgeError("knowledge_provider_unavailable")
                if "application/json" not in response.headers.get("content-type", "").lower():
                    raise KnowledgeError("knowledge_provider_catalog_invalid")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except ValueError as exc:
                        raise KnowledgeError("knowledge_provider_catalog_invalid") from exc
                    if parsed_length > _MAX_CATALOG_RESPONSE_BYTES:
                        raise KnowledgeError("knowledge_provider_catalog_limit_exceeded")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_CATALOG_RESPONSE_BYTES:
                        raise KnowledgeError("knowledge_provider_catalog_limit_exceeded")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise KnowledgeError("knowledge_provider_unavailable") from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeError("knowledge_provider_catalog_invalid") from exc
        if not isinstance(payload, dict) or payload.get("code") not in {0, "0"}:
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        data = payload.get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        return data

    async def check(self, *, base_url: str, credential: str) -> None:
        target = await self._validated_origin(base_url)
        timeout = float(self._settings_provider().knowledge_provider_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=target.origin,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            transport=_PinnedOriginTransport(target=target, transport=self._transport),
        ) as client:
            await self._page(client=client, credential=credential, page=1, page_size=1)

    async def list_sources(
        self,
        *,
        base_url: str,
        credential: str,
    ) -> ProviderCatalogSnapshot:
        target = await self._validated_origin(base_url)
        timeout = float(self._settings_provider().knowledge_provider_timeout_seconds)
        collected: list[ProviderSourceRecord] = []
        identities: set[str] = set()
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    base_url=target.origin,
                    timeout=httpx.Timeout(timeout),
                    follow_redirects=False,
                    trust_env=False,
                    transport=_PinnedOriginTransport(target=target, transport=self._transport),
                ) as client:
                    for page in range(1, _MAX_CATALOG_PAGES + 1):
                        rows = await self._page(
                            client=client,
                            credential=credential,
                            page=page,
                            page_size=_CATALOG_PAGE_SIZE,
                        )
                        for row in rows:
                            resource_id = row.get("id")
                            name = row.get("name")
                            if not isinstance(resource_id, str) or not isinstance(name, str):
                                raise KnowledgeError("knowledge_provider_catalog_invalid")
                            if resource_id in identities:
                                raise KnowledgeError("knowledge_provider_catalog_invalid")
                            identities.add(resource_id)
                            metadata = {
                                key: row[key]
                                for key in _ALLOWED_METADATA_FIELDS
                                if key in row
                                and isinstance(row[key], (str, int, float, bool, type(None)))
                            }
                            collected.append(
                                ProviderSourceRecord(
                                    provider_resource_id=resource_id,
                                    provider_name=name,
                                    provider_metadata=metadata,
                                )
                            )
                        if len(rows) < _CATALOG_PAGE_SIZE:
                            return ProviderCatalogSnapshot(
                                records=tuple(collected),
                                page_count=page,
                            )
        except TimeoutError as exc:
            raise KnowledgeError("knowledge_provider_unavailable") from exc
        raise KnowledgeError("knowledge_provider_catalog_limit_exceeded")

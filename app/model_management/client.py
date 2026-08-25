"""Bounded, DNS-pinned HTTP client for the configured model endpoint."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Iterator, Mapping

from app.validation import assert_upstream_model_id

from .security import ValidatedEndpoint, tls_context, validate_endpoint


@dataclass(frozen=True)
class UpstreamResponse:
    status: int
    content_type: str
    body: bytes


class ModelUpstreamError(RuntimeError):
    """Stable failure raised by bounded model-endpoint operations."""


_PROXY_PATHS = {
    "openai": frozenset({"/v1/chat/completions", "/v1/responses"}),
    "anthropic": frozenset({"/v1/messages", "/v1/messages/count_tokens"}),
}


@dataclass
class UpstreamStream:
    status: int
    content_type: str
    response: http.client.HTTPResponse
    connection: http.client.HTTPConnection
    max_response_bytes: int

    def body(self) -> Iterator[bytes]:
        total = 0
        try:
            while chunk := self.response.read(64 * 1024):
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise ModelUpstreamError("model_upstream_response_too_large")
                yield chunk
        finally:
            self.response.close()
            self.connection.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, endpoint: ValidatedEndpoint, ip: str, *, timeout: float) -> None:
        super().__init__(
            endpoint.hostname,
            endpoint.port,
            timeout=timeout,
            context=tls_context(),
        )
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def open_upstream_stream(
    *,
    base_url: str,
    allowed_internal_hosts: str,
    api_key: str,
    method: str,
    path: str,
    provider: str,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 3600.0,
    max_response_bytes: int = 16 * 1024 * 1024,
) -> UpstreamStream:
    endpoint = validate_endpoint(base_url, allowed_internal_hosts=allowed_internal_hosts)
    if method != "POST" or not path.startswith("/") or ".." in path.split("/"):
        raise ModelUpstreamError("model_upstream_request_invalid")
    if provider not in _PROXY_PATHS or path not in _PROXY_PATHS[provider]:
        raise ModelUpstreamError("model_upstream_request_invalid")
    outbound = _outbound_headers(
        endpoint=endpoint,
        provider=provider,
        api_key=api_key,
        headers=headers,
    )
    last_error: Exception | None = None
    for ip in endpoint.ips:
        connection = _connection(endpoint, ip, timeout_seconds=timeout_seconds)
        try:
            connection.request(method, path, body=body, headers=outbound)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                response.close()
                raise ModelUpstreamError("model_upstream_redirect_rejected")
            return UpstreamStream(
                status=response.status,
                content_type=str(
                    response.getheader("content-type") or "application/octet-stream"
                ),
                response=response,
                connection=connection,
                max_response_bytes=max_response_bytes,
            )
        except ModelUpstreamError:
            connection.close()
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            connection.close()
    raise ModelUpstreamError("model_upstream_unavailable") from last_error


def _outbound_headers(
    *,
    endpoint: ValidatedEndpoint,
    provider: str,
    api_key: str,
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    outbound = {
        str(name).lower(): str(value)
        for name, value in (headers or {}).items()
        if str(name).lower() in {"accept", "content-type", "anthropic-version", "user-agent"}
    }
    if provider == "anthropic":
        outbound["x-api-key"] = api_key
    else:
        outbound["authorization"] = f"Bearer {api_key}"
    outbound["host"] = (
        endpoint.hostname
        if endpoint.port in {80, 443}
        else f"{endpoint.hostname}:{endpoint.port}"
    )
    return outbound


def _connection(
    endpoint: ValidatedEndpoint,
    ip: str,
    *,
    timeout_seconds: float,
) -> http.client.HTTPConnection:
    if endpoint.scheme == "https":
        return _PinnedHTTPSConnection(endpoint, ip, timeout=timeout_seconds)
    return http.client.HTTPConnection(ip, endpoint.port, timeout=timeout_seconds)


def request_upstream(
    *,
    base_url: str,
    allowed_internal_hosts: str,
    api_key: str,
    method: str,
    path: str,
    provider: str,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 20.0,
    max_response_bytes: int = 16 * 1024 * 1024,
) -> UpstreamResponse:
    endpoint = validate_endpoint(base_url, allowed_internal_hosts=allowed_internal_hosts)
    if method not in {"GET", "POST"} or not path.startswith("/") or ".." in path.split("/"):
        raise ModelUpstreamError("model_upstream_request_invalid")
    if provider not in {"catalog", "openai", "anthropic"}:
        raise ModelUpstreamError("model_upstream_provider_invalid")
    outbound = _outbound_headers(
        endpoint=endpoint,
        provider=provider,
        api_key=api_key,
        headers=headers,
    )
    last_error: Exception | None = None
    for ip in endpoint.ips:
        connection = _connection(endpoint, ip, timeout_seconds=timeout_seconds)
        try:
            connection.request(method, path, body=body, headers=outbound)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ModelUpstreamError("model_upstream_redirect_rejected")
            payload = response.read(max_response_bytes + 1)
            if len(payload) > max_response_bytes:
                raise ModelUpstreamError("model_upstream_response_too_large")
            return UpstreamResponse(
                status=response.status,
                content_type=str(response.getheader("content-type") or "application/octet-stream"),
                body=payload,
            )
        except ModelUpstreamError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            connection.close()
    raise ModelUpstreamError("model_upstream_unavailable") from last_error


def parse_model_ids(response: UpstreamResponse) -> list[str]:
    if response.status != 200:
        if response.status in {401, 403}:
            raise ModelUpstreamError("model_connection_authentication_failed")
        if response.status == 429:
            raise ModelUpstreamError("model_connection_rate_limited")
        raise ModelUpstreamError("model_connection_catalog_failed")
    try:
        payload = json.loads(response.body)
        items = payload["data"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ModelUpstreamError("model_connection_catalog_invalid") from exc
    if not isinstance(items, list):
        raise ModelUpstreamError("model_connection_catalog_invalid")
    values: list[str] = []
    for item in items:
        value = item.get("id") if isinstance(item, dict) else None
        if not isinstance(value, str):
            raise ModelUpstreamError("model_connection_catalog_invalid")
        try:
            assert_upstream_model_id(value)
        except ValueError as exc:
            raise ModelUpstreamError("model_connection_catalog_invalid") from exc
        if value not in values:
            values.append(value)
    if not values:
        raise ModelUpstreamError("model_connection_catalog_empty")
    return values

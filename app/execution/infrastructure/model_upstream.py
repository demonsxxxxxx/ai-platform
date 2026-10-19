"""Bounded, DNS-pinned HTTP client for the configured model endpoint."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Mapping

import httpx

from app.execution.domain.model_catalog import validate_upstream_model_id
from app.execution.domain.runtime_debug import normalize_execution_debug

from .model_security import ValidatedEndpoint, endpoint_parts, tls_context, validate_endpoint


@dataclass(frozen=True)
class UpstreamResponse:
    status: int
    content_type: str
    body: bytes
    debug: dict[str, str | int] = field(default_factory=dict)


class ModelUpstreamError(RuntimeError):
    """Stable failure raised by bounded model-endpoint operations."""

    def __init__(self, code: str, *, debug: object = None) -> None:
        super().__init__(code)
        self.debug = normalize_execution_debug(debug)


def _failure_class(error: BaseException | None) -> str:
    if isinstance(error, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(error, httpx.ConnectError):
        return "connect_error"
    if isinstance(error, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(error, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(error, httpx.ProtocolError):
        return "protocol_error"
    return "transport_error"


_PROXY_PATHS = {
    "openai": frozenset({"/v1/chat/completions", "/v1/responses"}),
    "anthropic": frozenset({"/v1/messages", "/v1/messages/count_tokens"}),
}


@dataclass
class UpstreamStream:
    status: int
    content_type: str
    response: httpx.Response
    client: httpx.Client
    max_response_bytes: int
    debug: dict[str, str | int] = field(default_factory=dict)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        """Release the owned transport, even before body iteration starts."""
        if self._closed:
            return
        self._closed = True
        try:
            self.response.close()
        finally:
            self.client.close()

    def body(self) -> Iterator[bytes]:
        total = 0
        try:
            # No chunk_size: HTTPX's fixed-size chunker buffers small SSE events.
            # Raw bytes also avoid transparent decompression before our bound.
            for chunk in self.response.iter_raw():
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise ModelUpstreamError("model_upstream_response_too_large", debug={**self.debug, "phase": "stream", "failure_class": "size_limit", "observed_bytes": total, "limit_bytes": self.max_response_bytes})
                yield chunk
        except (httpx.HTTPError, OSError) as exc:
            exc.debug = normalize_execution_debug({
                **self.debug, "phase": "stream", "failure_class": _failure_class(exc),
            })
            raise
        finally:
            self.close()


def _new_client(timeout_seconds: float) -> httpx.Client:
    # One operation owns one client. An IP-keyed shared pool could reuse a TLS
    # connection verified for a different original hostname on the same IP.
    transport = httpx.HTTPTransport(
        verify=tls_context(), trust_env=False, retries=0, http2=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    return httpx.Client(
        transport=transport, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
    )


def _open_response(
    *, endpoint: ValidatedEndpoint, method: str, path: str, query: str,
    body: bytes, headers: Mapping[str, str], timeout_seconds: float,
    max_response_bytes: int,
) -> UpstreamStream:
    origin = httpx.URL(endpoint.base_url)
    last_error: Exception | None = None
    attempts = 0
    for attempts, ip in enumerate(endpoint.ips, start=1):
        client = _new_client(timeout_seconds)
        response = None
        transferred = False
        try:
            url = origin.copy_with(host=ip, path=path, query=query.encode("ascii"))
            request = client.build_request(
                method, url, content=body, headers=headers,
                extensions={"sni_hostname": endpoint.hostname},
            )
            response = client.send(request, stream=True)
            if 300 <= response.status_code < 400:
                raise ModelUpstreamError("model_upstream_redirect_rejected")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                # The proxy does not forward Content-Encoding. Do not silently
                # decode an unbounded compressed body or forward unusable bytes.
                raise ModelUpstreamError("model_upstream_unavailable")
            stream = UpstreamStream(
                status=response.status_code,
                content_type=response.headers.get("content-type") or "application/octet-stream",
                response=response, client=client, max_response_bytes=max_response_bytes,
                debug={"component": "model_proxy", "phase": "response", "http_status": response.status_code, "attempt_count": attempts},
            )
            transferred = True
            return stream
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Only connection establishment failures prove no POST was sent.
            # Read/write/protocol failures may follow an accepted, billed call.
            last_error = exc
        except (httpx.HTTPError, OSError) as exc:
            raise ModelUpstreamError("model_upstream_unavailable", debug={
                "component": "model_proxy", "phase": "response", "failure_class": _failure_class(exc), "attempt_count": attempts,
            }) from exc
        finally:
            if not transferred:
                try:
                    if response is not None:
                        response.close()
                finally:
                    client.close()
    raise ModelUpstreamError("model_upstream_unavailable", debug={
        "component": "model_proxy", "phase": "connect", "failure_class": _failure_class(last_error), "attempt_count": attempts,
    }) from last_error


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
    query: str = "",
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
    return _open_response(
        endpoint=endpoint, method=method, path=path, query=query, body=body,
        headers=outbound, timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
    )


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
        if str(name).lower() in (
            {"accept", "content-type", "anthropic-version", "anthropic-beta", "user-agent"}
            if provider == "anthropic"
            else {"accept", "content-type", "user-agent"}
        )
    }
    if provider == "anthropic":
        outbound["x-api-key"] = api_key
    else:
        outbound["authorization"] = f"Bearer {api_key}"
    outbound["host"] = endpoint_parts(endpoint).netloc
    outbound["accept-encoding"] = "identity"
    return outbound


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
    query: str = "",
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
    stream = _open_response(
        endpoint=endpoint, method=method, path=path, query=query, body=body,
        headers=outbound, timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    try:
        payload = b"".join(stream.body())
        return UpstreamResponse(
            status=stream.status, content_type=stream.content_type, body=payload, debug=stream.debug,
        )
    except (httpx.HTTPError, OSError) as exc:
        raise ModelUpstreamError("model_upstream_unavailable", debug=getattr(exc, "debug", None)) from exc


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
            validate_upstream_model_id(value)
        except ValueError as exc:
            raise ModelUpstreamError("model_connection_catalog_invalid") from exc
        if value not in values:
            values.append(value)
    if not values:
        raise ModelUpstreamError("model_connection_catalog_empty")
    return values


class ModelUpstreamAdapter:
    request = staticmethod(request_upstream)
    parse_model_ids = staticmethod(parse_model_ids)
    open_stream = staticmethod(open_upstream_stream)

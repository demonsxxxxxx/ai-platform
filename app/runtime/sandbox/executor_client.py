import ipaddress
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.runtime.sandbox.contracts import ExecutorTaskDispatchReceipt, ExecutorTaskRequest
from app.sandbox import api as sandbox_api
from app.sandbox.api import runtime_diagnostics_rejection
from app.settings import get_settings


PostJson = Callable[..., Awaitable[dict[str, Any]]]
RequestJson = Callable[..., Awaitable[dict[str, Any]]]
EXECUTOR_CONNECT_BASE_URL_METADATA = "X-AI-Platform-Internal-Executor-Connect-Base-Url"
_MAX_EXECUTOR_HTTP_ERROR_BODY_BYTES = 4096


def _executor_http_error(response: httpx.Response) -> sandbox_api.SandboxExecutorHttpError:
    payload: dict[str, Any] = {}
    runtime_diagnostics: object = None
    if len(response.content) <= _MAX_EXECUTOR_HTTP_ERROR_BODY_BYTES:
        try:
            candidate = response.json()
        except ValueError:
            candidate = None
        if isinstance(candidate, dict):
            payload = candidate
            runtime_diagnostics = candidate.get("runtime_diagnostics")
        elif response.content:
            runtime_diagnostics = runtime_diagnostics_rejection(
                reason="invalid_payload",
                field="http_error_body",
            )
    else:
        runtime_diagnostics = runtime_diagnostics_rejection(
            reason="truncated",
            field="http_error_body",
        )
    return sandbox_api.SandboxExecutorHttpError(
        status_code=response.status_code,
        error_code=payload.get("error_code"),
        detail=payload.get("detail"),
        runtime_diagnostics=runtime_diagnostics,
    )


def prepare_executor_http_request(
    logical_url: str,
    headers: dict[str, str] | None,
) -> tuple[str, dict[str, str]]:
    """Build a pinned executor request without transmitting private connection metadata."""

    private_headers = dict(headers or {})
    connect_base_url = str(private_headers.pop(EXECUTOR_CONNECT_BASE_URL_METADATA, "") or "").strip()
    outgoing_headers = dict(private_headers)
    if not connect_base_url:
        return logical_url, outgoing_headers

    try:
        logical = urlsplit(logical_url)
        connect = urlsplit(connect_base_url)
        connect_ip = ipaddress.ip_address(connect.hostname or "")
        logical_port = logical.port
        connect_port = connect.port
    except ValueError as exc:
        raise ValueError("invalid executor connect metadata") from exc
    if not (
        logical.scheme == "http"
        and connect.scheme == "http"
        and logical.hostname
        and logical_port
        and not logical.username
        and not logical.password
        and connect_ip.version == 4
        and not connect_ip.is_unspecified
        and (connect_ip.is_private or connect_ip.is_loopback)
        and connect_port == logical_port
        and not connect.username
        and not connect.password
        and connect.path in {"", "/"}
        and not connect.query
        and not connect.fragment
    ):
        raise ValueError("invalid executor connect metadata")

    outgoing_headers["Host"] = f"{logical.hostname}:{logical_port}"
    connect_netloc = f"{connect_ip}:{connect_port}"
    return urlunsplit((logical.scheme, connect_netloc, logical.path, logical.query, logical.fragment)), outgoing_headers


async def _default_request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            url,
            json=payload,
            headers=dict(headers or {}),
        )
        if not 200 <= response.status_code < 300:
            raise _executor_http_error(response)
        data = response.json()
    return data if isinstance(data, dict) else {}


async def _default_post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = await _default_request_json("POST", url, payload, timeout, headers)
    return data or {"status": "accepted"}


class SandboxExecutorClient:
    def __init__(
        self,
        post_json: PostJson | None = None,
        timeout_seconds: float | None = None,
        request_json: RequestJson | None = None,
    ) -> None:
        self._post_json = post_json or _default_post_json
        self._request_json = request_json or _default_request_json
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        executor_url: str,
        request: ExecutorTaskRequest,
        *,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logical_url = f"{executor_url.rstrip('/')}/v2/tasks"
        url, outgoing_headers = prepare_executor_http_request(logical_url, executor_headers)
        timeout_seconds = self._timeout_seconds if self._timeout_seconds is not None else _default_timeout_seconds()
        response = await self._post_json(url, request.model_dump(), timeout_seconds, outgoing_headers)
        try:
            receipt = ExecutorTaskDispatchReceipt.model_validate(
                {
                    **response,
                    "run_id": response.get("run_id", request.run_id),
                    "attempt_id": response.get("attempt_id", request.attempt_id),
                }
            )
        except (TypeError, ValueError):
            raise sandbox_api.SandboxExecutorHttpError(
                status_code=502,
                error_code="executor_protocol_invalid",
            ) from None
        if receipt.run_id != request.run_id or receipt.attempt_id != request.attempt_id:
            raise sandbox_api.SandboxExecutorHttpError(
                status_code=502,
                error_code="executor_protocol_invalid",
            )
        return receipt.model_dump()

    async def get_status(
        self,
        executor_url: str,
        *,
        run_id: str,
        attempt_id: str,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logical_url = f"{executor_url.rstrip('/')}/v2/tasks/{run_id}/{attempt_id}"
        url, outgoing_headers = prepare_executor_http_request(logical_url, executor_headers)
        timeout_seconds = self._timeout_seconds if self._timeout_seconds is not None else _default_timeout_seconds()
        return await self._request_json("GET", url, None, timeout_seconds, outgoing_headers)

    async def cancel(
        self,
        executor_url: str,
        *,
        run_id: str,
        attempt_id: str,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logical_url = f"{executor_url.rstrip('/')}/v2/tasks/{run_id}/{attempt_id}/cancel"
        url, outgoing_headers = prepare_executor_http_request(logical_url, executor_headers)
        timeout_seconds = self._timeout_seconds if self._timeout_seconds is not None else _default_timeout_seconds()
        return await self._post_json(url, {}, timeout_seconds, outgoing_headers)


def _default_timeout_seconds(request: ExecutorTaskRequest | None = None) -> float:
    """Bound only the short asynchronous dispatch request."""

    _ = request
    settings = get_settings()
    return float(getattr(settings, "sandbox_executor_dispatch_timeout_seconds", 30.0))

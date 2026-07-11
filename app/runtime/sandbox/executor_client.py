import ipaddress
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.runtime.sandbox.contracts import ExecutorTaskRequest
from app.settings import get_settings


PostJson = Callable[..., Awaitable[dict[str, Any]]]
EXECUTOR_CONNECT_BASE_URL_METADATA = "X-AI-Platform-Internal-Executor-Connect-Base-Url"


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


async def _default_post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        request_headers = dict(headers or {})
        if request_headers:
            response = await client.post(url, json=payload, headers=request_headers)
        else:
            response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {"status": "accepted"}


class SandboxExecutorClient:
    def __init__(self, post_json: PostJson | None = None, timeout_seconds: float | None = None) -> None:
        self._post_json = post_json or _default_post_json
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else _default_timeout_seconds()

    async def execute(
        self,
        executor_url: str,
        request: ExecutorTaskRequest,
        *,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logical_url = f"{executor_url.rstrip('/')}/v1/tasks/execute"
        url, outgoing_headers = prepare_executor_http_request(logical_url, executor_headers)
        return await self._post_json(url, request.model_dump(), self._timeout_seconds, outgoing_headers)


def _default_timeout_seconds() -> float:
    settings = get_settings()
    sdk_timeout = float(getattr(settings, "claude_agent_sdk_timeout_seconds", 120.0) or 120.0)
    return max(30.0, sdk_timeout + 10.0)

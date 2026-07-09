from typing import Any, Awaitable, Callable

import httpx

from app.runtime.sandbox.contracts import ExecutorTaskRequest
from app.settings import get_settings


PostJson = Callable[..., Awaitable[dict[str, Any]]]


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
        url = f"{executor_url.rstrip('/')}/v1/tasks/execute"
        return await self._post_json(url, request.model_dump(), self._timeout_seconds, executor_headers)


def _default_timeout_seconds() -> float:
    settings = get_settings()
    sdk_timeout = float(getattr(settings, "claude_agent_sdk_timeout_seconds", 120.0) or 120.0)
    return max(30.0, sdk_timeout + 10.0)

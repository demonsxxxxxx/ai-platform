from typing import Any, Awaitable, Callable

import httpx

from app.runtime.sandbox.contracts import ExecutorTaskRequest


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
    def __init__(self, post_json: PostJson | None = None, timeout_seconds: float = 30.0) -> None:
        self._post_json = post_json or _default_post_json
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        executor_url: str,
        request: ExecutorTaskRequest,
        *,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{executor_url.rstrip('/')}/v1/tasks/execute"
        return await self._post_json(url, request.model_dump(), self._timeout_seconds, executor_headers)

"""Task-scoped access to platform-owned MCP credentials through a fixed callback."""

import asyncio
import json

from httpx import AsyncClient, HTTPError

from app.mcp.domain.errors import McpRuntimeContextError


class McpCredentialCallbackClient:
    def __init__(self, *, callback_url, callback_token, callback_token_id, run_id, attempt_id):
        self._url = callback_url
        self._token = callback_token
        self._binding = {"run_id": run_id, "attempt_id": attempt_id, "callback_token_id": callback_token_id}

    async def get(self, server_id: str) -> str:
        try:
            async with asyncio.timeout(5):
                async with AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
                    async with client.stream(
                        "POST", self._url,
                        headers={"X-AI-Platform-Callback-Token": self._token,
                                 "Accept": "application/json", "Accept-Encoding": "identity"},
                        json={**self._binding, "server_id": server_id},
                    ) as response:
                        if response.status_code in {401, 403, 409}:
                            raise McpRuntimeContextError("mcp_principal_jwt_missing", status_code=401)
                        if (response.status_code != 200
                            or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                            or response.headers.get("content-encoding", "identity").lower() != "identity"):
                            raise ValueError("invalid credential response")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 32 * 1024:
                                raise ValueError("credential response too large")
                        result = json.loads(body)
                        if (not isinstance(result, dict) or set(result) != {"jwt"}
                            or not isinstance(result["jwt"], str) or not 1 <= len(result["jwt"]) <= 16_384):
                            raise ValueError("invalid credential response")
                        return result["jwt"]
        except McpRuntimeContextError:
            raise
        except (HTTPError, TimeoutError, ValueError):
            raise McpRuntimeContextError("mcp_principal_jwt_unavailable", status_code=503) from None

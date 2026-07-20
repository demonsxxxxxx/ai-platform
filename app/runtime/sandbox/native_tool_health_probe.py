from __future__ import annotations

import hmac
import os

import httpx

from app.runtime.sandbox.native_tool_app import NATIVE_TOOL_AUTH_HEADER


NATIVE_TOOL_CONTAINER_SOCKET = "/workspace/.ai-platform/native-tool.sock"


def _authenticated_health_probe() -> bool:
    """Probe the authenticated sidecar health endpoint without emitting secrets."""

    token = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_TOKEN") or "")
    socket_path = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET") or "")
    if len(token) < 32 or not hmac.compare_digest(
        socket_path,
        NATIVE_TOOL_CONTAINER_SOCKET,
    ):
        return False
    try:
        transport = httpx.HTTPTransport(uds=NATIVE_TOOL_CONTAINER_SOCKET)
        with httpx.Client(
            transport=transport,
            base_url="http://native-tool",
            timeout=1.0,
        ) as client:
            response = client.get(
                "/health",
                headers={NATIVE_TOOL_AUTH_HEADER: token},
            )
        return response.status_code == 200 and response.json() == {"status": "ok"}
    except Exception:
        return False


def main() -> int:
    """Return only a process status for Docker exec readiness admission."""

    return 0 if _authenticated_health_probe() else 1


if __name__ == "__main__":
    raise SystemExit(main())

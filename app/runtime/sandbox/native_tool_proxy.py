from __future__ import annotations

import base64
import os
import sys
from typing import Any

import httpx


NATIVE_TOOL_AUTH_HEADER = "X-AI-Platform-Native-Tool-Token"
NATIVE_TOOL_MAX_COMMAND_BYTES = 64 * 1024
NATIVE_TOOL_MAX_TIMEOUT_MS = 600_000


def _validated_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("native_tool_result_invalid")
    returncode = payload.get("returncode")
    stdout = payload.get("stdout", "")
    stderr = payload.get("stderr", "")
    output_truncated = payload.get("output_truncated", False)
    timed_out = payload.get("timed_out", False)
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or not isinstance(output_truncated, bool)
        or not isinstance(timed_out, bool)
    ):
        raise ValueError("native_tool_result_invalid")
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": output_truncated,
        "timed_out": timed_out,
    }


def _decode_command(encoded: str) -> str:
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        command = raw.decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("native_tool_command_invalid") from exc
    if not command or len(raw) > NATIVE_TOOL_MAX_COMMAND_BYTES:
        raise ValueError("native_tool_command_invalid")
    return command


def _decode_timeout(raw: str) -> int:
    try:
        timeout_ms = int(raw)
    except ValueError as exc:
        raise ValueError("native_tool_timeout_invalid") from exc
    if str(timeout_ms) != raw or not 1 <= timeout_ms <= NATIVE_TOOL_MAX_TIMEOUT_MS:
        raise ValueError("native_tool_timeout_invalid")
    return timeout_ms


def main() -> int:
    """Proxy one encoded Bash command to the isolated native-tool sidecar."""

    if len(sys.argv) != 3:
        print("native Skill command proxy received invalid input", file=sys.stderr)
        return 64
    socket_path = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET") or "")
    token = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_TOKEN") or "")
    if not socket_path or not token:
        print("native Skill command isolation is unavailable", file=sys.stderr)
        return 69
    try:
        command = _decode_command(sys.argv[1])
        timeout_ms = _decode_timeout(sys.argv[2])
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(
            transport=transport,
            base_url="http://native-tool",
            timeout=(timeout_ms / 1000.0) + 30.0,
        ) as client:
            response = client.post(
                "/execute",
                json={"command": command, "timeout_ms": timeout_ms},
                headers={NATIVE_TOOL_AUTH_HEADER: token},
            )
        response.raise_for_status()
        result = _validated_result(response.json())
    except Exception:
        print("native Skill command execution failed", file=sys.stderr)
        return 70
    if result["stdout"]:
        print(result["stdout"], end="")
    if result["stderr"]:
        print(result["stderr"], end="", file=sys.stderr)
    if result["output_truncated"]:
        print("\n[native Skill command output truncated]", file=sys.stderr)
    return int(result["returncode"])


if __name__ == "__main__":
    raise SystemExit(main())

"""HTTP fault injection helpers for Run lifecycle integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any


_EXECUTOR_CALLBACK_PATH = "/api/ai/runtime/callbacks/executor"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
TERMINAL_ACK_FAULT_MARKER_PATH = "/tmp/n01-terminal-ack-fault.json"


def _terminal_request_metadata(body: bytes) -> dict[str, str | None] | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    status = payload.get("status")
    terminal_result = payload.get("terminal_result")
    run_id = payload.get("run_id")
    attempt_id = payload.get("attempt_id")
    batch_id = payload.get("batch_id")
    if (
        not isinstance(status, str)
        or status not in _TERMINAL_STATUSES
        or not isinstance(terminal_result, dict)
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(attempt_id, str)
        or not attempt_id
        or (batch_id is not None and not isinstance(batch_id, str))
    ):
        return None
    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "batch_id": batch_id,
        "status": status,
    }


class DropTerminalAckOnce:
    """Drop one successful terminal executor callback ACK at the HTTP layer."""

    def __init__(self, app: Any, *, marker_path: Path) -> None:
        self.app = app
        self.marker_path = Path(marker_path)
        self._fault_lock = asyncio.Lock()
        self._faulted = False
        self._fault_record: dict[str, Any] | None = None
        self._retry_recorded = False

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != _EXECUTOR_CALLBACK_PATH
        ):
            await self.app(scope, receive, send)
            return

        request_messages: list[dict[str, Any]] = []
        request_body = bytearray()
        while True:
            message = await receive()
            request_messages.append(message)
            if message.get("type") != "http.request":
                break
            request_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        request_metadata = _terminal_request_metadata(bytes(request_body))
        if request_metadata is None:
            await self._call_with_replayed_request(
                scope,
                request_messages,
                receive,
                send,
            )
            return

        response_messages: list[dict[str, Any]] = []

        async def capture_response(message: dict[str, Any]) -> None:
            response_messages.append(message)

        try:
            await self._call_with_replayed_request(
                scope,
                request_messages,
                receive,
                capture_response,
            )
        except Exception:
            for message in response_messages:
                await send(message)
            raise

        response_start = next(
            (
                message
                for message in response_messages
                if message.get("type") == "http.response.start"
            ),
            None,
        )
        response_body_messages = [
            message
            for message in response_messages
            if message.get("type") == "http.response.body"
        ]
        response_body = b"".join(
            message.get("body", b"") for message in response_body_messages
        )
        response_complete = bool(response_body_messages) and not response_body_messages[
            -1
        ].get("more_body", False)
        response_message_types = {
            message.get("type") for message in response_messages
        }
        if (
            response_start is None
            or response_start.get("status") != 200
            or not response_complete
            or response_message_types - {"http.response.start", "http.response.body"}
        ):
            for message in response_messages:
                await send(message)
            return

        try:
            acknowledgment = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            acknowledgment = None
        if not isinstance(acknowledgment, dict) or acknowledgment.get("accepted") is not True:
            for message in response_messages:
                await send(message)
            return

        fault_record = {
            **request_metadata,
            "request_body_sha256": hashlib.sha256(request_body).hexdigest(),
            "response_body_sha256": hashlib.sha256(response_body).hexdigest(),
            "original_http_status": response_start["status"],
            "transmitted_body_length": len(response_body),
            "advertised_content_length": len(response_body) + 1,
            "fault_type": "content_length_truncation",
        }
        if not await self._claim_fault(fault_record):
            await self._record_retry(
                request_metadata,
                request_body=bytes(request_body),
                response_body=response_body,
                response_status=response_start["status"],
                acknowledgment=acknowledgment,
            )
            for message in response_messages:
                await send(message)
            return

        for message in response_messages:
            if message.get("type") != "http.response.start":
                await send(message)
                continue
            truncated_start = dict(message)
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() not in {b"content-length", b"transfer-encoding"}
            ]
            headers.append(
                (b"content-length", str(len(response_body) + 1).encode("ascii"))
            )
            truncated_start["headers"] = headers
            await send(truncated_start)

    async def _claim_fault(self, fault_record: dict[str, Any]) -> bool:
        async with self._fault_lock:
            if self._faulted:
                return False
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                marker_fd = os.open(
                    self.marker_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(marker_fd, "w", encoding="utf-8") as marker:
                    marker.write(
                        json.dumps(
                            fault_record,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    marker.write("\n")
                    marker.flush()
            except FileExistsError:
                self._faulted = True
                return False
            self._faulted = True
            self._fault_record = dict(fault_record)
            return True

    async def _record_retry(
        self,
        request_metadata: dict[str, str | None],
        *,
        request_body: bytes,
        response_body: bytes,
        response_status: int,
        acknowledgment: dict[str, Any],
    ) -> None:
        async with self._fault_lock:
            original = self._fault_record
            if original is None or self._retry_recorded:
                return
            if any(
                request_metadata.get(key) != original.get(key)
                for key in ("run_id", "attempt_id", "batch_id", "status")
            ):
                return
            if acknowledgment.get("accepted") is not True:
                return

            retry_record = {
                **request_metadata,
                "request_body_sha256": hashlib.sha256(request_body).hexdigest(),
                "response_body_sha256": hashlib.sha256(response_body).hexdigest(),
                "http_status": response_status,
                "accepted": True,
                "deduplicated": acknowledgment.get("deduplicated") is True,
            }
            try:
                marker_record = json.loads(self.marker_path.read_text(encoding="utf-8"))
                if not isinstance(marker_record, dict):
                    return
                marker_record["retry"] = retry_record
                temporary_path = self.marker_path.with_name(
                    f".{self.marker_path.name}.tmp"
                )
                marker_fd = os.open(
                    temporary_path,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                with os.fdopen(marker_fd, "w", encoding="utf-8") as marker:
                    marker.write(
                        json.dumps(marker_record, sort_keys=True, separators=(",", ":"))
                    )
                    marker.write("\n")
                    marker.flush()
                os.replace(temporary_path, self.marker_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return
            self._retry_recorded = True

    async def _call_with_replayed_request(
        self,
        scope,
        request_messages: list[dict[str, Any]],
        receive,
        send,
    ) -> None:
        message_index = 0

        async def replay_receive():
            nonlocal message_index
            if message_index < len(request_messages):
                message = request_messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

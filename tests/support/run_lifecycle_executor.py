"""Synthetic SDK runner used by the real-dependency Run lifecycle stack."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid


SYNTHETIC_MODEL_NAME = "n01.synthetic.chat"
SYNTHETIC_MODEL_API_KEY = "n01-synthetic-api-key"
TEST_ANSWER = "N01 lifecycle executor produced the synthetic answer."
SELECTED_FILES = (
    "deliverables/selected-zulu.txt",
    "deliverables/selected-alpha.txt",
)
SELECTED_FILE_CONTENTS = {
    SELECTED_FILES[0]: b"n01 synthetic zulu artifact\n",
    SELECTED_FILES[1]: b"n01 synthetic alpha artifact\n",
}
PRIVATE_FILE = "deliverables/private.txt"
PRIVATE_FILE_CONTENT = b"n01 synthetic private file; do not deliver\n"
_APP_MODULE = "app.runtime.sandbox.executor_app:create_executor_app"
_API_APP_MODULE = "app.main:create_app"
_DROP_TERMINAL_ACK_ENV = "N01_DROP_TERMINAL_ACK_ONCE"
_TERMINAL_ACK_MARKER_ENV = "N01_TERMINAL_ACK_MARKER_PATH"
_REJECT_TERMINAL_CALLBACKS_ENV = "N02_REJECT_TERMINAL_CALLBACKS"
_TERMINAL_CALLBACK_LOSS_MARKER_ENV = "N02_TERMINAL_CALLBACK_LOSS_MARKER_PATH"
_CAPTURE_EXECUTOR_TIMEOUT_CALLBACK_ENV = "N01_CAPTURE_EXECUTOR_TIMEOUT_CALLBACK"
_EXECUTOR_TIMEOUT_CALLBACK_MARKER_ENV = "N01_EXECUTOR_TIMEOUT_CALLBACK_MARKER_PATH"
_RELEASE_TIMEOUT_SECONDS = 240.0


async def deterministic_runner(request, workspace_root: Path, emit_event) -> dict[str, object]:
    """Replace only the SDK query while keeping host callbacks and storage real."""

    from app.bootstrap.execution import build_claude_session_store
    from app.execution.application.claude_agent_events import ClaudeSdkAgentEventAdapter
    from app.platform.public_payload import (
        sanitize_public_answer_text,
        sanitize_public_event_candidate,
    )
    from app.runtime.kernel_contracts import AgentEvent

    workspace_root.mkdir(parents=True, exist_ok=True)
    entered_path = workspace_root / ".n01-entered"
    release_path = workspace_root / ".n01-release"
    release_path.unlink(missing_ok=True)
    entered_path.write_text(
        json.dumps(
            {
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "resource_limits": request.config.get("resource_limits", {}),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    deadline = asyncio.get_running_loop().time() + _RELEASE_TIMEOUT_SECONDS
    while not release_path.is_file():
        if asyncio.get_running_loop().time() >= deadline:
            return {
                "status": "failed",
                "message": "Synthetic executor release was not signaled",
                "error_code": "n01_executor_release_timeout",
                "error_message": "Synthetic executor release was not signaled",
                "sdk_used": False,
            }
        await asyncio.sleep(0.05)

    if not request.sdk_session_id:
        return {
            "status": "failed",
            "message": "Provider session identity is unavailable",
            "error_code": "n01_provider_session_identity_missing",
            "error_message": "Provider session identity is unavailable",
            "sdk_used": False,
        }

    session_store = build_claude_session_store(
        callback_url=request.callback_target.provider_session_url,
        callback_token=request.callback_token,
        callback_token_id=request.callback_token_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        provider_session_id=request.sdk_session_id,
    )
    session_key = {"session_id": request.sdk_session_id}
    previous_entries = await session_store.load(session_key)
    parent_uuid = None
    if previous_entries:
        last_uuid = previous_entries[-1].get("uuid")
        if isinstance(last_uuid, str) and last_uuid:
            parent_uuid = last_uuid

    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    user_entry_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"n01-run-lifecycle:{request.run_id}:{request.attempt_id}:user",
        )
    )
    assistant_entry_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"n01-run-lifecycle:{request.run_id}:{request.attempt_id}:assistant",
        )
    )
    common_entry_fields = {
        "isSidechain": False,
        "userType": "external",
        "cwd": str(workspace_root),
        "sessionId": request.sdk_session_id,
        "version": "2.0.0",
        "gitBranch": "",
        "slug": "n01-lifecycle",
        "permissionMode": request.permission_mode,
    }
    user_entry = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": request.prompt}],
        },
        "parentUuid": parent_uuid,
        "uuid": user_entry_uuid,
        "timestamp": now,
        **common_entry_fields,
    }
    assistant_entry = {
        "type": "assistant",
        "message": {
            "id": "msg_" + hashlib.sha256(
                f"{request.run_id}:{request.attempt_id}".encode("utf-8")
            ).hexdigest()[:24],
            "type": "message",
            "role": "assistant",
            "model": SYNTHETIC_MODEL_NAME,
            "content": [{"type": "text", "text": TEST_ANSWER}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1,
                "output_tokens": len(TEST_ANSWER.split()),
            },
        },
        "parentUuid": user_entry_uuid,
        "uuid": assistant_entry_uuid,
        "timestamp": now,
        **common_entry_fields,
    }
    await session_store.append(session_key, [user_entry, assistant_entry])
    final_sequence = session_store.accepted_final_sequence
    if final_sequence is None:
        raise RuntimeError("n01_provider_session_append_not_acknowledged")

    response_files: list[str] = []
    response_file_descriptors: list[dict[str, str]] = []
    if "SELECT_FILES" in request.prompt:
        for relative_path, content in SELECTED_FILE_CONTENTS.items():
            destination = workspace_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        private_destination = workspace_root / PRIVATE_FILE
        private_destination.parent.mkdir(parents=True, exist_ok=True)
        private_destination.write_bytes(PRIVATE_FILE_CONTENT)
        response_files = list(SELECTED_FILES)
        response_file_descriptors = [
            {
                "source_path": relative_path,
                "display_name": Path(relative_path).name,
                "role": "primary" if index == 0 else "supporting",
                "description": "Synthetic explicitly selected test artifact",
            }
            for index, relative_path in enumerate(response_files)
        ]

    event_adapter = ClaudeSdkAgentEventAdapter(
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        sanitizer=sanitize_public_answer_text,
        payload_sanitizer=sanitize_public_event_candidate,
    )
    for candidates in (
        event_adapter.accept_answer_text(TEST_ANSWER),
        event_adapter.complete_answer(TEST_ANSWER),
    ):
        for candidate in candidates:
            event = AgentEvent(**candidate.as_agent_event_fields())
            if await emit_event(event) is not True:
                raise RuntimeError("n01_agent_event_callback_not_acknowledged")

    answer_receipt = event_adapter.answer_receipt
    if answer_receipt is None:
        raise RuntimeError("n01_assistant_answer_receipt_missing")
    return {
        "status": "completed",
        "message": "",
        "answer_receipt": answer_receipt,
        "provider_session_final_sequence": final_sequence,
        "response_files": response_files,
        "response_file_descriptors": response_file_descriptors,
        "sdk_session_id": request.sdk_session_id,
        "sdk_used": False,
        "tool_invocation_evidence": [],
        "capability_evidence": [],
    }


def main() -> None:
    app_module = os.environ.get("APP_MODULE")
    if (
        app_module == _API_APP_MODULE
        and os.environ.get(_CAPTURE_EXECUTOR_TIMEOUT_CALLBACK_ENV) == "true"
    ):
        import uvicorn

        from app.main import create_app
        from run_lifecycle_faults import CaptureExecutorTimeoutCallback

        marker_path = Path(os.environ[_EXECUTOR_TIMEOUT_CALLBACK_MARKER_ENV])
        uvicorn.run(
            CaptureExecutorTimeoutCallback(create_app(), marker_path=marker_path),
            host="0.0.0.0",
            port=int(os.environ.get("APP_PORT", "8020")),
            log_level="info",
        )
        return
    if (
        app_module == _API_APP_MODULE
        and os.environ.get(_REJECT_TERMINAL_CALLBACKS_ENV) == "true"
    ):
        import uvicorn

        from app.main import create_app
        from run_lifecycle_faults import RejectTerminalCallbacks

        marker_path = Path(os.environ[_TERMINAL_CALLBACK_LOSS_MARKER_ENV])
        uvicorn.run(
            RejectTerminalCallbacks(create_app(), marker_path=marker_path),
            host="0.0.0.0",
            port=int(os.environ.get("APP_PORT", "8020")),
            log_level="info",
        )
        return
    if (
        app_module == _API_APP_MODULE
        and os.environ.get(_DROP_TERMINAL_ACK_ENV) == "true"
    ):
        import uvicorn

        from app.main import create_app
        from run_lifecycle_faults import DropTerminalAckOnce

        marker_path = Path(os.environ[_TERMINAL_ACK_MARKER_ENV])
        uvicorn.run(
            DropTerminalAckOnce(create_app(), marker_path=marker_path),
            host="0.0.0.0",
            port=int(os.environ.get("APP_PORT", "8020")),
            log_level="info",
        )
        return
    if app_module == _APP_MODULE:
        import uvicorn

        from app.runtime.sandbox.executor_app import create_executor_app

        uvicorn.run(
            create_executor_app(executor_runner=deterministic_runner),
            host="0.0.0.0",
            port=18000,
            log_level="info",
        )
        return
    os.execv(
        "/app/docker-entrypoint.sh",
        ["/app/docker-entrypoint.sh", *sys.argv[1:]],
    )


if __name__ == "__main__":
    main()

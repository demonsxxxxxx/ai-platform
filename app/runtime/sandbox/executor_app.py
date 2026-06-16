from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorTaskRequest


CallbackPayload = dict[str, Any]
CallbackResult = dict[str, Any] | None
CallbackSender = Callable[[str, CallbackPayload, str], Awaitable[CallbackResult] | CallbackResult]


async def _default_callback_sender(url: str, payload: CallbackPayload, token: str) -> CallbackResult:
    headers = {"X-AI-Platform-Callback-Token": token}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {"accepted": True}


async def _dispatch_callback(
    callback_sender: CallbackSender,
    url: str,
    payload: CallbackPayload,
    token: str,
) -> CallbackResult:
    result = callback_sender(url, payload, token)
    if inspect.isawaitable(result):
        return await result
    return result


def _write_runtime_marker(workspace_root: Path, request: ExecutorTaskRequest) -> Path:
    marker_dir = workspace_root / "runtime"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_dir / f"{request.run_id}.json"
    resource_limits = request.config.get("resource_limits", {})
    if not isinstance(resource_limits, dict):
        resource_limits = {}
    safe_config = {
        "model": _safe_scalar(request.config.get("model")),
        "browser_enabled": request.config.get("browser_enabled") is True,
        "resource_limits": {
            key: value
            for key, value in resource_limits.items()
            if isinstance(value, int | float | bool | str) and "/" not in str(value) and "\\" not in str(value)
        },
        "skill_ids": _safe_id_list(request.config.get("skill_ids")),
        "mcp_tool_ids": _safe_id_list(request.config.get("mcp_tool_ids")),
        "input_files": _safe_id_list(request.config.get("input_files")),
    }
    marker_payload = {
        "session_id": request.session_id,
        "run_id": request.run_id,
        "prompt_length": len(request.prompt),
        "permission_mode": request.permission_mode,
        "config": safe_config,
    }
    marker_path.write_text(json.dumps(marker_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return marker_path


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and "/" not in value and "\\" not in value:
        return value
    return None


def _safe_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    safe_values = []
    for item in value:
        if isinstance(item, str) and "/" not in item and "\\" not in item:
            safe_values.append(item)
    return safe_values


def create_executor_app(
    workspace_root: str | Path = "/workspace",
    callback_sender: CallbackSender | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Platform Sandbox Executor", version="0.1.0")
    resolved_workspace_root = Path(workspace_root)
    resolved_callback_sender = callback_sender or _default_callback_sender

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/tasks/execute")
    async def execute_task(request: ExecutorTaskRequest) -> dict[str, Any]:
        started_at = time.monotonic()
        document_started_at = time.monotonic()
        marker_path = _write_runtime_marker(resolved_workspace_root, request)
        document_processing_latency_ms = max(int(round((time.monotonic() - document_started_at) * 1000)), 0)
        callback_errors: list[str] = []
        running_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status="running",
            progress=5,
            state_patch={"stage": "accepted"},
        )
        completed_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status="completed",
            progress=100,
            state_patch={"marker_path": f"/workspace/runtime/{marker_path.name}"},
        )

        for event in (running_event, completed_event):
            try:
                await _dispatch_callback(
                    resolved_callback_sender,
                    request.callback_url,
                    event.model_dump(),
                    request.callback_token,
                )
            except Exception:
                callback_errors.append(event.status)

        executor_model_latency_ms = max(int(round((time.monotonic() - started_at) * 1000)), 0)
        response: dict[str, Any] = {
            "status": "accepted",
            "run_id": request.run_id,
            "executor_model_latency_ms": executor_model_latency_ms,
            "document_processing_latency_ms": document_processing_latency_ms,
        }
        if callback_errors:
            response["callback_errors"] = callback_errors
        return response

    return app

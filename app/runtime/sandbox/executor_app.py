from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorTaskRequest
from app.settings import get_settings


CallbackPayload = dict[str, Any]
CallbackResult = dict[str, Any] | None
CallbackSender = Callable[[str, CallbackPayload, str], Awaitable[CallbackResult] | CallbackResult]
SdkRunner = Callable[..., Awaitable[Any] | Any]


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


async def _default_sdk_runner(**kwargs: Any) -> Any:
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk

    return await run_claude_agent_sdk(**kwargs)


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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


def _primary_skill_id(skill_ids: list[str]) -> str:
    return skill_ids[0] if skill_ids else "general-chat"


def _safe_optional_id(value: Any) -> str | None:
    if isinstance(value, str) and value and "/" not in value and "\\" not in value:
        return value
    return None


def _result_value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _resource_limit_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def create_executor_app(
    workspace_root: str | Path = "/workspace",
    callback_sender: CallbackSender | None = None,
    sdk_runner: SdkRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Platform Sandbox Executor", version="0.1.0")
    resolved_workspace_root = Path(workspace_root)
    resolved_callback_sender = callback_sender or _default_callback_sender
    resolved_sdk_runner = sdk_runner or _default_sdk_runner

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
        try:
            await _dispatch_callback(
                resolved_callback_sender,
                request.callback_url,
                running_event.model_dump(),
                request.callback_token,
            )
        except Exception:
            callback_errors.append(running_event.status)
        resource_limits = request.config.get("resource_limits", {})
        max_seconds = (
            _resource_limit_seconds(resource_limits.get("max_seconds"))
            if isinstance(resource_limits, dict)
            else None
        )
        timed_out = max_seconds is not None and max_seconds <= 0
        sdk_result: Any | None = None
        if not timed_out and getattr(get_settings(), "claude_agent_sdk_enabled", False) is True:
            skill_ids = _safe_id_list(request.config.get("skill_ids"))
            model = _safe_scalar(request.config.get("model"))

            async def on_text(text: str) -> None:
                return None

            try:
                sdk_result = await _maybe_await(
                    resolved_sdk_runner(
                        prompt=request.prompt,
                        cwd=resolved_workspace_root,
                        skill_id=_primary_skill_id(skill_ids),
                        skills=skill_ids or None,
                        model_id=model if isinstance(model, str) else None,
                        session_id=request.sdk_session_id,
                        on_text=on_text,
                    )
                )
            except Exception:
                sdk_result = {
                    "used_sdk": False,
                    "error": "claude_agent_sdk_unavailable",
                }

        sdk_error = bool(sdk_result is not None and _result_value(sdk_result, "error"))
        terminal_status = "failed" if timed_out or sdk_error else "completed"
        state_patch = (
            {"error_code": "executor_health_timeout"}
            if timed_out
            else {
                "marker_path": f"/workspace/runtime/{marker_path.name}",
            }
        )
        sdk_session_id = _safe_optional_id(_result_value(sdk_result, "session_id")) if sdk_result is not None else None
        if sdk_result is not None:
            state_patch.update(
                {
                    "sdk_used": bool(_result_value(sdk_result, "used_sdk", False)),
                    "executor_mode": "claude_agent_sdk",
                }
            )
            if sdk_session_id:
                state_patch["sdk_session_id"] = sdk_session_id
        completed_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status=terminal_status,
            progress=100 if terminal_status == "completed" else 5,
            state_patch=state_patch,
            sdk_session_id=sdk_session_id,
            error_message=(
                "Executor health timeout"
                if timed_out
                else "Claude Agent SDK execution failed"
                if sdk_error
                else None
            ),
        )

        try:
            await _dispatch_callback(
                resolved_callback_sender,
                request.callback_url,
                completed_event.model_dump(),
                request.callback_token,
            )
        except Exception:
            callback_errors.append(completed_event.status)

        executor_model_latency_ms = max(int(round((time.monotonic() - started_at) * 1000)), 0)
        response: dict[str, Any] = {
            "status": "failed" if terminal_status == "failed" else "accepted",
            "run_id": request.run_id,
            "executor_model_latency_ms": executor_model_latency_ms,
            "document_processing_latency_ms": document_processing_latency_ms,
            "sdk_used": bool(_result_value(sdk_result, "used_sdk", False)) if sdk_result is not None else False,
            "executor_mode": "claude_agent_sdk" if sdk_result is not None else "marker_smoke",
        }
        if sdk_session_id:
            response["sdk_session_id"] = sdk_session_id
        if sdk_result is not None:
            used_skill_ids = _safe_id_list(_result_value(sdk_result, "used_skills", []))
            if used_skill_ids:
                response["used_skill_ids"] = used_skill_ids
        if timed_out:
            response["error_code"] = "executor_health_timeout"
            response["error_message"] = "Executor health timeout"
        elif sdk_error:
            response["error_code"] = "claude_agent_sdk_runtime_error"
            response["error_message"] = "Claude Agent SDK execution failed"
        if callback_errors:
            response["callback_errors"] = callback_errors
        return response

    return app

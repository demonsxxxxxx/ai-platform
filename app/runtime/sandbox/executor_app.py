from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.executors.claude_agent_sdk_runner import ClaudeAgentSdkNotAvailable, run_claude_agent_sdk
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorTaskRequest, ExecutorToolPermissionRequest
from app.settings import get_settings


CallbackPayload = dict[str, Any]
CallbackResult = dict[str, Any] | None
CallbackSender = Callable[[str, CallbackPayload, str], Awaitable[CallbackResult] | CallbackResult]
ExecutorEventEmitter = Callable[[AgentEvent], Awaitable[None]]
ExecutorRunner = Callable[
    [ExecutorTaskRequest, Path, ExecutorEventEmitter],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


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


def _resource_limit_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started_at: float) -> int:
    return max(int(round((time.monotonic() - started_at) * 1000)), 0)


def _timing_value(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _task_skill_ids(request: ExecutorTaskRequest) -> list[str]:
    skill_ids = _safe_id_list(request.config.get("skill_ids"))
    return skill_ids or ["general-chat"]


def _tool_permission_callback_url(request: ExecutorTaskRequest) -> str:
    return f"{request.callback_base_url.rstrip('/')}/api/ai/runtime/callbacks/tool-permission"


def _normalize_tool_permission_response(result: CallbackResult, *, default_reason: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"allowed": False, "reason": default_reason}
    return {
        "allowed": bool(result.get("allowed")),
        "reason": str(result.get("reason") or default_reason),
        "risk_level": str(result.get("risk_level") or "high"),
        "write_capable": bool(result.get("write_capable", True)),
        "decision": str(result.get("decision") or ""),
        "permission_request_id": str(result.get("permission_request_id") or ""),
    }


async def _default_executor_runner(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
    *,
    callback_sender: CallbackSender = _default_callback_sender,
) -> dict[str, Any]:
    if getattr(get_settings(), "claude_agent_sdk_enabled", False) is not True:
        return {
            "status": "completed",
            "message": "Sandbox marker completed",
            "sdk_used": False,
            "executor_mode": "marker_fallback",
        }

    skill_ids = _task_skill_ids(request)
    model_id = str(request.config.get("model") or "") or None

    async def on_text(delta: str) -> None:
        if not delta:
            return
        await emit_event(AgentEvent(type="assistant_delta", message=delta, payload={"delta": delta}))

    async def on_tool_permission(permission_request: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(permission_request.get("tool_name") or "tool")
        tool_call_id = str(permission_request.get("tool_call_id") or "")
        reason = str(permission_request.get("reason") or "Tool use requires platform permission")
        action = str(permission_request.get("action") or "execute")
        risk_level = str(permission_request.get("risk_level") or "high")
        write_capable = bool(permission_request.get("write_capable", True))
        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "action": action,
            "risk_level": risk_level,
            "write_capable": write_capable,
            "reason": reason,
        }
        await emit_event(
            AgentEvent(
                type="tool_call_started",
                message=f"{tool_name} requested permission",
                payload=payload,
                admin_only=True,
            )
        )
        broker_request = ExecutorToolPermissionRequest(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            sdk_session_id=request.sdk_session_id,
            tool_name=tool_name,
            tool_input=permission_request.get("tool_input")
            if isinstance(permission_request.get("tool_input"), dict)
            else {},
            tool_call_id=tool_call_id,
            action=action,
            risk_level=risk_level,
            write_capable=write_capable,
            reason=reason,
        )
        try:
            broker_result = await _dispatch_callback(
                callback_sender,
                _tool_permission_callback_url(request),
                broker_request.model_dump(),
                request.callback_token,
            )
        except Exception:
            return {"allowed": False, "reason": "tool_permission_broker_failed"}
        return _normalize_tool_permission_response(broker_result, default_reason=reason)

    async def on_skill_use(skill_name: str, metadata: dict[str, Any]) -> None:
        await emit_event(
            AgentEvent(
                type="tool_call_started",
                message=f"Skill used: {skill_name}",
                payload={
                    "tool_name": "Skill",
                    "skill_name": skill_name,
                    "tool_call_id": str(metadata.get("tool_use_id") or ""),
                    "source": str(metadata.get("source") or "claude_agent_sdk_hook"),
                },
                admin_only=True,
            )
        )

    try:
        sdk_result = await run_claude_agent_sdk(
            prompt=request.prompt,
            cwd=workspace_root,
            skill_id=skill_ids[0],
            session_id=request.sdk_session_id,
            model_id=model_id,
            skills=skill_ids,
            on_text=on_text,
            on_skill_use=on_skill_use,
            on_tool_permission=on_tool_permission,
        )
    except ClaudeAgentSdkNotAvailable as exc:
        return {
            "status": "failed",
            "error_code": "claude_agent_sdk_unavailable",
            "error_message": f"Claude Agent SDK unavailable: {exc}",
            "sdk_used": False,
        }

    used_sdk = bool(getattr(sdk_result, "used_sdk", False))
    error = getattr(sdk_result, "error", None)
    response = {
        "status": "completed" if used_sdk and not error else "failed",
        "message": str(getattr(sdk_result, "message", "") or ""),
        "sdk_session_id": getattr(sdk_result, "session_id", None),
        "sdk_usage": getattr(sdk_result, "usage", {}) or {},
        "sdk_used": used_sdk,
        "executor_mode": "claude_agent_sdk",
        "used_skills": list(getattr(sdk_result, "used_skills", []) or []),
        "used_skills_source": str(getattr(sdk_result, "used_skills_source", "") or ""),
    }
    if error:
        response["error_code"] = str(error)
        response["error_message"] = str(error)
    elif not used_sdk:
        response["error_code"] = "claude_agent_sdk_disabled"
        response["error_message"] = "Claude Agent SDK is disabled"
    return response


def create_executor_app(
    workspace_root: str | Path = "/workspace",
    callback_sender: CallbackSender | None = None,
    executor_runner: ExecutorRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Platform Sandbox Executor", version="0.1.0")
    resolved_workspace_root = Path(workspace_root)
    resolved_callback_sender = callback_sender or _default_callback_sender

    async def default_executor_runner(
        request: ExecutorTaskRequest,
        runtime_workspace_root: Path,
        emit_event: ExecutorEventEmitter,
    ) -> dict[str, Any]:
        return await _default_executor_runner(
            request,
            runtime_workspace_root,
            emit_event,
            callback_sender=resolved_callback_sender,
        )

    resolved_executor_runner = executor_runner or default_executor_runner

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/tasks/execute")
    async def execute_task(request: ExecutorTaskRequest) -> dict[str, Any]:
        started_at = time.monotonic()
        document_started_at = time.monotonic()
        marker_path = _write_runtime_marker(resolved_workspace_root, request)
        document_processing_latency_ms = _elapsed_ms(document_started_at)
        callback_errors: list[str] = []
        running_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status="running",
            progress=5,
            state_patch={"stage": "accepted"},
        )
        resource_limits = request.config.get("resource_limits", {})
        max_seconds = (
            _resource_limit_seconds(resource_limits.get("max_seconds"))
            if isinstance(resource_limits, dict)
            else None
        )
        timed_out = max_seconds is not None and max_seconds <= 0
        executor_started_at = time.monotonic()
        executor_first_token_latency_ms: int | None = None
        executor_tool_call_latency_ms: int | None = None
        artifact_upload_latency_ms = 0

        async def dispatch_callback_event(event: ExecutorCallbackEvent) -> None:
            try:
                await _dispatch_callback(
                    resolved_callback_sender,
                    request.callback_url,
                    event.model_dump(),
                    request.callback_token,
                )
            except Exception:
                callback_errors.append(event.status)

        async def emit_runner_event(event: AgentEvent) -> None:
            nonlocal artifact_upload_latency_ms, executor_first_token_latency_ms, executor_tool_call_latency_ms
            agent_event = event if isinstance(event, AgentEvent) else AgentEvent.model_validate(event)
            if agent_event.type == "assistant_delta" and executor_first_token_latency_ms is None:
                executor_first_token_latency_ms = _elapsed_ms(executor_started_at)
            if agent_event.type.startswith("tool_call") and executor_tool_call_latency_ms is None:
                executor_tool_call_latency_ms = _elapsed_ms(executor_started_at)

            callback_event = ExecutorCallbackEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                callback_token_id=request.callback_token_id,
                status="running",
                progress=35 if agent_event.type.startswith("tool_call") else 60 if agent_event.type == "artifact_created" else 20,
                state_patch={"stage": agent_event.type},
                sdk_session_id=request.sdk_session_id,
                events=[agent_event],
            )
            artifact_started_at = time.monotonic() if agent_event.type == "artifact_created" else None
            await dispatch_callback_event(callback_event)
            if artifact_started_at is not None:
                artifact_upload_latency_ms += _elapsed_ms(artifact_started_at)

        await dispatch_callback_event(running_event)
        runner_result: dict[str, Any] = {}
        if not timed_out:
            try:
                raw_runner_result = resolved_executor_runner(request, resolved_workspace_root, emit_runner_event)
                if inspect.isawaitable(raw_runner_result):
                    raw_runner_result = await raw_runner_result
                runner_result = raw_runner_result if isinstance(raw_runner_result, dict) else {}
            except Exception as exc:
                runner_result = {
                    "status": "failed",
                    "error_code": "executor_runner_failed",
                    "error_message": str(exc),
                }

        runner_status = str(runner_result.get("status") or "completed")
        failed = timed_out or runner_status == "failed"
        error_code = "executor_health_timeout" if timed_out else str(runner_result.get("error_code") or "")
        error_message = (
            "Executor health timeout"
            if timed_out
            else str(runner_result.get("error_message") or runner_result.get("message") or "Executor failed")
            if failed
            else None
        )
        completed_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status="failed" if failed else "completed",
            progress=100 if not failed else 5,
            state_patch=(
                {"error_code": error_code}
                if failed
                else {"marker_path": f"/workspace/runtime/{marker_path.name}"}
            ),
            sdk_session_id=str(runner_result.get("sdk_session_id") or request.sdk_session_id or "") or None,
            error_message=error_message,
        )

        await dispatch_callback_event(completed_event)

        executor_model_latency_ms = _elapsed_ms(started_at)
        response: dict[str, Any] = {
            "status": "failed" if failed else "accepted",
            "run_id": request.run_id,
            "executor_model_latency_ms": executor_model_latency_ms,
            "document_processing_latency_ms": document_processing_latency_ms,
            "executor_first_token_latency_ms": _timing_value(
                executor_first_token_latency_ms if executor_first_token_latency_ms is not None else runner_result.get("executor_first_token_latency_ms")
            ),
            "executor_tool_call_latency_ms": _timing_value(
                executor_tool_call_latency_ms if executor_tool_call_latency_ms is not None else runner_result.get("executor_tool_call_latency_ms")
            ),
            "artifact_upload_latency_ms": _timing_value(
                artifact_upload_latency_ms or runner_result.get("artifact_upload_latency_ms")
            ),
        }
        for key in (
            "message",
            "sdk_session_id",
            "sdk_usage",
            "sdk_used",
            "executor_mode",
            "used_skills",
            "used_skills_source",
        ):
            if key in runner_result and runner_result[key] is not None:
                response[key] = runner_result[key]
        if failed:
            response["error_code"] = error_code or "executor_failed"
            response["error_message"] = error_message or "Executor failed"
        if callback_errors:
            response["callback_errors"] = callback_errors
        return response

    return app

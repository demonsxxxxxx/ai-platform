from __future__ import annotations

import asyncio
import functools
import hmac
import inspect
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI, Header, HTTPException, status

from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.context_retrieval import ContextRetrieval, TransactionalContextRetrievalRepository
from app.db import transaction
from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    run_claude_agent_sdk,
)
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.contracts import (
    EXECUTOR_AUTH_HEADER,
    CallbackTargetValidationError,
    ContextRetrievalScope,
    ExecutorCallbackEvent,
    ExecutorTaskRequest,
    build_trusted_callback_target,
)
from app.settings import get_settings
from app.storage import ObjectStorage


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
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _is_async_callable(value: object) -> bool:
    candidates = [value, getattr(value, "__call__", None)]
    for candidate in candidates:
        while isinstance(candidate, functools.partial):
            candidate = candidate.func
        if candidate is None:
            continue
        if inspect.iscoroutinefunction(candidate):
            return True
    return False


def _cancel_without_waiting(task: asyncio.Future[Any]) -> None:
    task.cancel()

    def consume_exception(done_task: asyncio.Future[Any]) -> None:
        if done_task.cancelled():
            return
        try:
            done_task.exception()
        except asyncio.CancelledError:
            pass

    task.add_done_callback(consume_exception)


async def _await_with_deadline(awaitable: Awaitable[Any], *, timeout_seconds: float) -> tuple[Any, bool]:
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        _cancel_without_waiting(task)
        raise
    if task in done:
        return task.result(), False
    _cancel_without_waiting(task)
    return None, True


def _elapsed_ms(started_at: float) -> int:
    elapsed = time.monotonic() - started_at
    if not math.isfinite(elapsed):
        return 0
    return max(int(round(elapsed * 1000)), 0)


def _timing_value(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _task_skill_ids(request: ExecutorTaskRequest) -> list[str]:
    skill_ids = _safe_id_list(request.config.get("skill_ids"))
    return skill_ids or ["general-chat"]


def _task_tool_policy_subjects(request: ExecutorTaskRequest) -> list[dict[str, Any]]:
    value = request.config.get("tool_policy_subjects")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _configured_executor_auth_token(explicit_value: str | None) -> str:
    return str(explicit_value or os.getenv("AI_PLATFORM_EXECUTOR_AUTH_TOKEN") or "").strip()


def _configured_expected_value(explicit_value: str | None, env_name: str) -> str:
    return str(explicit_value or os.getenv(env_name) or "").strip()


def _trusted_callback_target(explicit_base_url: str | None):
    configured_base_url = str(explicit_base_url or os.getenv("AI_PLATFORM_CALLBACK_BASE_URL") or "").strip()
    if not configured_base_url:
        raise CallbackTargetValidationError("trusted callback base url is not configured")
    callback_gateway = str(os.getenv("SANDBOX_CALLBACK_HOST_GATEWAY") or "").strip()
    return build_trusted_callback_target(configured_base_url, extra_hosts=[callback_gateway])


def _require_executor_credential(provided_credential: str | None, expected_credential: str) -> None:
    if not expected_credential:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="executor_auth_not_configured",
        )
    if not provided_credential or not hmac.compare_digest(str(provided_credential), expected_credential):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_executor_credential",
        )


def _validate_executor_request_scope(
    request: ExecutorTaskRequest,
    *,
    expected_session_id: str,
    expected_run_id: str,
    trusted_callback_base_url: str | None,
) -> None:
    if not expected_session_id or not expected_run_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="executor_scope_not_configured")
    if request.session_id != expected_session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_executor_scope")
    if request.run_id != expected_run_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_executor_scope")
    try:
        trusted_callback_target = _trusted_callback_target(trusted_callback_base_url)
    except CallbackTargetValidationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="executor_callback_not_configured") from exc
    if request.callback_base_url != trusted_callback_target.base_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_callback_target")
    if request.callback_url != trusted_callback_target.callback_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_callback_target")


def _context_retrieval_for_request(
    request: ExecutorTaskRequest,
) -> tuple[ContextRetrieval | None, ScopedContextRetrievalIdentity | None, str | None]:
    manifest = request.config.get("context_manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION:
        return None, None, None
    raw_scope = request.config.get("context_retrieval_scope")
    if not isinstance(raw_scope, dict):
        return None, None, "context_retrieval_scope_invalid"
    try:
        scope = ContextRetrievalScope.model_validate(raw_scope)
    except Exception:
        return None, None, "context_retrieval_scope_invalid"
    repository = TransactionalContextRetrievalRepository(transaction, storage=ObjectStorage())
    identity = ScopedContextRetrievalIdentity(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        session_id=scope.session_id,
        run_id=scope.run_id,
        agent_id=scope.agent_id,
    )
    return ContextRetrieval(repository), identity, None


async def _default_executor_runner(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
    *,
    callback_sender: CallbackSender = _default_callback_sender,
) -> dict[str, Any]:
    if getattr(get_settings(), "claude_agent_sdk_enabled", False) is not True:
        return {
            "status": "failed",
            "message": "Claude Agent SDK is disabled",
            "error_code": "claude_agent_sdk_disabled",
            "error_message": "Claude Agent SDK is disabled",
            "sdk_used": False,
            "executor_mode": "claude_agent_sdk_disabled",
        }

    skill_ids = _task_skill_ids(request)
    model_id = str(request.config.get("model") or "") or None
    context_retrieval, context_retrieval_identity, context_retrieval_error = _context_retrieval_for_request(request)
    if context_retrieval_error:
        return {
            "status": "failed",
            "message": "Context retrieval scope is invalid",
            "error_code": context_retrieval_error,
            "error_message": "Context retrieval scope is invalid",
            "sdk_used": False,
            "executor_mode": "context_retrieval_invalid",
        }

    async def on_text(delta: str) -> None:
        if not delta:
            return
        await emit_event(AgentEvent(type="assistant_delta", message=delta, payload={"delta": delta}))

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
            context_retrieval=context_retrieval,
            context_retrieval_identity=context_retrieval_identity,
            on_text=on_text,
            on_skill_use=on_skill_use,
            tool_policy_subjects=_task_tool_policy_subjects(request),
            execution_policy="sandbox_brokered",
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
    executor_auth_token: str | None = None,
    expected_session_id: str | None = None,
    expected_run_id: str | None = None,
    trusted_callback_base_url: str | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Platform Sandbox Executor", version="0.1.0")
    resolved_workspace_root = Path(workspace_root)
    resolved_callback_sender = callback_sender or _default_callback_sender
    configured_executor_auth_token = _configured_executor_auth_token(executor_auth_token)
    configured_expected_session_id = _configured_expected_value(expected_session_id, "AI_PLATFORM_SESSION_ID")
    configured_expected_run_id = _configured_expected_value(expected_run_id, "AI_PLATFORM_RUN_ID")
    execute_claimed = {"value": False}

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

    @app.get("/health/runtime-identity")
    async def runtime_identity(
        executor_credential: str | None = Header(default=None, alias=EXECUTOR_AUTH_HEADER),
    ) -> dict[str, int]:
        """Return the authenticated executor process identity without runtime metadata."""

        _require_executor_credential(executor_credential, configured_executor_auth_token)
        try:
            uid = int(os.geteuid())
            gid = int(os.getegid())
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="executor_runtime_identity_unavailable",
            ) from exc
        return {"uid": uid, "gid": gid}

    @app.post("/v1/tasks/execute")
    async def execute_task(
        request: ExecutorTaskRequest,
        executor_credential: str | None = Header(default=None, alias=EXECUTOR_AUTH_HEADER),
    ) -> dict[str, Any]:
        _require_executor_credential(executor_credential, configured_executor_auth_token)
        _validate_executor_request_scope(
            request,
            expected_session_id=configured_expected_session_id,
            expected_run_id=configured_expected_run_id,
            trusted_callback_base_url=trusted_callback_base_url,
        )
        if execute_claimed["value"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="executor_request_replayed")
        execute_claimed["value"] = True
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
        max_seconds_present = isinstance(resource_limits, dict) and "max_seconds" in resource_limits
        max_seconds = (
            _resource_limit_seconds(resource_limits.get("max_seconds"))
            if isinstance(resource_limits, dict)
            else None
        )
        invalid_max_seconds = max_seconds_present and max_seconds is None
        timed_out = max_seconds is not None and max_seconds <= 0
        executor_started_at = time.monotonic()
        deadline_started_at = executor_started_at
        executor_first_token_latency_ms: int | None = None
        executor_tool_call_latency_ms: int | None = None
        artifact_upload_latency_ms = 0
        runner_events_open = {"value": True}

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
            if not runner_events_open["value"]:
                return
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
        if invalid_max_seconds:
            runner_result = {
                "status": "failed",
                "error_code": "executor_invalid_max_seconds",
                "error_message": "Executor max_seconds must be a finite number",
            }
        elif not timed_out:
            if max_seconds is not None and not _is_async_callable(resolved_executor_runner):
                runner_result = {
                    "status": "failed",
                    "error_code": "executor_deadline_requires_async_runner",
                    "error_message": "Positive executor deadlines require an async runner",
                }
            else:
                try:
                    deadline_started_at = time.monotonic()
                    raw_runner_result = resolved_executor_runner(request, resolved_workspace_root, emit_runner_event)
                    if inspect.isawaitable(raw_runner_result):
                        if max_seconds is not None:
                            raw_runner_result, timed_out = await _await_with_deadline(
                                raw_runner_result,
                                timeout_seconds=max_seconds,
                            )
                        else:
                            raw_runner_result = await raw_runner_result
                    runner_result = raw_runner_result if isinstance(raw_runner_result, dict) else {}
                except Exception as exc:
                    runner_result = {
                        "status": "failed",
                        "error_code": "executor_runner_failed",
                        "error_message": str(exc),
                    }
        runner_events_open["value"] = False

        runner_status = str(runner_result.get("status") or "completed")
        failed = timed_out or runner_status == "failed"
        positive_deadline_exceeded = timed_out and max_seconds is not None and max_seconds > 0
        error_code = (
            "executor_deadline_exceeded"
            if positive_deadline_exceeded
            else "executor_health_timeout"
            if timed_out
            else str(runner_result.get("error_code") or "")
        )
        error_message = (
            "Executor deadline exceeded"
            if positive_deadline_exceeded
            else "Executor health timeout"
            if timed_out
            else str(runner_result.get("error_message") or runner_result.get("message") or "Executor failed")
            if failed
            else None
        )
        timeout_observation = (
            {
                "requested_max_seconds": max_seconds,
                "timeout_elapsed_ms": _elapsed_ms(deadline_started_at),
            }
            if timed_out
            else {}
        )
        execution_observation = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            callback_token_id=request.callback_token_id,
            status="running",
            progress=99,
            state_patch=(
                {"stage": "executor_finished", "error_code": error_code, **timeout_observation}
                if failed
                else {
                    "stage": "executor_finished",
                    "marker_path": f"/workspace/runtime/{marker_path.name}",
                }
            ),
            sdk_session_id=str(runner_result.get("sdk_session_id") or request.sdk_session_id or "") or None,
            error_message=error_message,
        )

        await dispatch_callback_event(execution_observation)

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
            response.update(timeout_observation)
        if callback_errors:
            response["callback_errors"] = callback_errors
        return response

    return app

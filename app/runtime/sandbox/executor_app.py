from __future__ import annotations

import asyncio
import functools
import hmac
import inspect
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI, Header, HTTPException, status

from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.context_retrieval import ContextRetrievalDenied
from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    _translation_target_language,
    run_claude_agent_sdk,
)
from app.file_parser_contracts import (
    AttachmentPreprocessingError,
    ParsedAttachmentContext,
    attachment_requirements_from_contract,
    dispatched_context_file_ids,
    parse_xlsx_attachment,
)
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.context_retrieval_client import PlatformContextRetrievalClient
from app.runtime.sandbox.contracts import (
    EXECUTOR_AUTH_HEADER,
    CallbackTargetValidationError,
    ContextRetrievalScope,
    ExecutorCallbackEvent,
    ExecutorTaskRequest,
    build_trusted_callback_target,
)
from app.settings import get_settings


CallbackPayload = dict[str, Any]
CallbackResult = dict[str, Any] | None
CallbackSender = Callable[[str, CallbackPayload, str], Awaitable[CallbackResult] | CallbackResult]
ExecutorEventEmitter = Callable[[AgentEvent], Awaitable[None]]
ExecutorRunner = Callable[
    [ExecutorTaskRequest, Path, ExecutorEventEmitter],
    Awaitable[dict[str, Any]] | dict[str, Any],
]

_CONTROLLED_FILE_SKILLS = {"baoyu-translate", "qa-file-reviewer"}
_CONTROLLED_FILE_SKILL_CAPABILITIES = {
    # These exactly mirror the server-owned builtin declarations in skills.pinning.
    "baoyu-translate": frozenset({"Bash", "Write"}),
    "qa-file-reviewer": frozenset({"Bash", "Write"}),
}
_CONTROLLED_RUNNER_TIMEOUT_SECONDS = 900.0
_CONTROLLED_RUNNER_TERMINATION_GRACE_SECONDS = 5.0
_EXECUTOR_CLEANUP_TIMEOUT_SECONDS = 5.0


class _ExecutorCleanupError(RuntimeError):
    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message


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


def _observe_detached_task(task: asyncio.Future[Any]) -> None:
    def consume_result(completed_task: asyncio.Future[Any]) -> None:
        try:
            completed_task.result()
        except (asyncio.CancelledError, Exception):
            pass

    task.add_done_callback(consume_result)


async def _await_task_completion(
    task: asyncio.Future[Any],
    *,
    timeout_seconds: float,
    timeout_message: str,
) -> Any:
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task not in done:
        _observe_detached_task(task)
        raise TimeoutError(timeout_message)
    return task.result()


async def _cancel_and_await(task: asyncio.Future[Any]) -> None:
    task.cancel()
    try:
        await _await_task_completion(
            task,
            timeout_seconds=_EXECUTOR_CLEANUP_TIMEOUT_SECONDS,
            timeout_message="Executor cleanup exceeded its deadline",
        )
    except asyncio.CancelledError:
        if task.cancelled():
            return
        raise
    except _ExecutorCleanupError:
        raise
    except TimeoutError as exc:
        raise _ExecutorCleanupError(
            "executor_cleanup_timeout",
            "Executor cleanup exceeded its deadline",
        ) from exc
    except Exception as exc:
        raise _ExecutorCleanupError(
            "executor_cleanup_failed",
            "Executor cleanup failed",
        ) from exc


async def _await_with_deadline(
    awaitable: Awaitable[Any],
    *,
    timeout_seconds: float,
    on_timeout: Callable[[], None] | None = None,
) -> tuple[Any, bool]:
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        if on_timeout is not None:
            on_timeout()
        await _cancel_and_await(task)
        raise
    if task in done:
        return task.result(), False
    if on_timeout is not None:
        on_timeout()
    await _cancel_and_await(task)
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


def _authorized_capability_subject(subject: dict[str, Any]) -> bool:
    return all(
        subject.get(field) is True
        for field in (
            "registered",
            "declared",
            "active",
            "distributed",
            "identity_authorized",
            "object_authorized",
            "parameters_authorized",
        )
    )


def _attachment_stage_subject_authorized(request: ExecutorTaskRequest) -> bool:
    identity = "mcp__ai-platform-context__stage_context_file_to_workspace"
    return any(
        str(subject.get("identity") or "") == identity
        and _authorized_capability_subject(subject)
        and bool(subject.get("write_capable"))
        and {"file_id", "max_bytes"}.issubset(
            {str(key) for key in subject.get("allowed_parameter_keys") or []}
        )
        for subject in _task_tool_policy_subjects(request)
    )


def _selected_authorized_file_skill_id(request: ExecutorTaskRequest) -> tuple[str | None, str | None]:
    """Return a controlled Skill only with its canonical builtin execution identities."""

    selected_skill_ids = _task_skill_ids(request)
    selected_skill_id = selected_skill_ids[0] if selected_skill_ids else ""
    if selected_skill_id not in _CONTROLLED_FILE_SKILLS:
        return None, None
    subjects = _task_tool_policy_subjects(request)
    skill_authorized = any(
        str(subject.get("identity") or "") == "Skill"
        and _authorized_capability_subject(subject)
        and selected_skill_id in _safe_id_list(subject.get("allowed_skill_names"))
        for subject in subjects
    )
    if not skill_authorized:
        return None, "controlled_skill_authorization_incomplete"
    required_identities = _CONTROLLED_FILE_SKILL_CAPABILITIES[selected_skill_id]
    authorized_identities = {
        str(subject.get("identity") or "")
        for subject in subjects
        if _authorized_capability_subject(subject)
    }
    if not required_identities.issubset(authorized_identities):
        return None, "controlled_skill_authorization_incomplete"
    return selected_skill_id, None


def _resolved_workspace_file(workspace_root: Path, candidate: Path) -> Path | None:
    try:
        workspace = workspace_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or candidate.is_symlink():
        return None
    return resolved


async def _preprocess_typed_attachments(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
    *,
    retrieval: PlatformContextRetrievalClient | None,
    identity: ScopedContextRetrievalIdentity | None,
) -> tuple[list[ParsedAttachmentContext], str | None]:
    """Stage and parse server-required attachments through the scoped broker."""

    manifest = request.config.get("context_manifest")
    raw_contract = manifest.get("attachment_preprocessing") if isinstance(manifest, dict) else None
    try:
        requirements = attachment_requirements_from_contract(raw_contract)
    except AttachmentPreprocessingError as exc:
        return [], exc.code
    if not requirements:
        return [], None
    manifest_file_ids = dispatched_context_file_ids(manifest)
    if any(requirement.file_id not in manifest_file_ids for requirement in requirements):
        return [], "attachment_parser_manifest_file_mismatch"
    if not _attachment_stage_subject_authorized(request):
        return [], "attachment_parser_staging_not_authorized"
    if retrieval is None or identity is None:
        return [], "attachment_parser_context_retrieval_unavailable"
    contexts: list[ParsedAttachmentContext] = []
    for requirement in requirements:
        if not requirement.supported:
            return contexts, "attachment_parser_unsupported"
        await emit_event(
            AgentEvent(
                type="tool_call_started",
                message=f"Platform attachment parser started: {requirement.parser_id}",
                payload={
                    "tool_name": "AttachmentParser",
                    "file_id": requirement.file_id,
                    "parser_id": requirement.parser_id,
                    "parser_version": requirement.parser_version,
                    "source": "platform_attachment_preprocessor",
                },
                admin_only=True,
            )
        )
        try:
            staged = await retrieval.stage_context_file_to_workspace(
                file_id=requirement.file_id,
                workspace_root=str(workspace_root),
                max_bytes=requirement.max_bytes,
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                run_id=identity.run_id,
            )
        except ContextRetrievalDenied:
            return contexts, "attachment_parser_staging_denied"
        except Exception:
            return contexts, "attachment_parser_staging_failed"
        if int(staged.get("bytes_staged") or -1) < 0 or int(staged.get("bytes_staged") or -1) > requirement.max_bytes:
            return contexts, "attachment_parser_file_too_large"
        workspace_path = str(staged.get("workspace_path") or "").replace("\\", "/")
        staged_path = _resolved_workspace_file(workspace_root, workspace_root / workspace_path)
        if staged_path is None:
            return contexts, "attachment_parser_staged_file_invalid"
        try:
            parsed = parse_xlsx_attachment(path=staged_path, requirement=requirement)
        except AttachmentPreprocessingError as exc:
            return contexts, exc.code
        contexts.append(parsed)
        await emit_event(
            AgentEvent(
                type="tool_call_completed",
                message=f"Platform attachment parser completed: {requirement.parser_id}",
                payload={
                    "tool_name": "AttachmentParser",
                    **parsed.evidence.model_dump(mode="json"),
                    "source": "platform_attachment_preprocessor",
                },
                admin_only=True,
            )
        )
    return contexts, None


def _user_message_from_skill_prompt(prompt: str) -> str:
    _, marker, remainder = str(prompt or "").partition("User request: ")
    if not marker:
        return ""
    return remainder.partition("\nWorkspace files:\n")[0]


def _safe_materialized_basename(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    candidate = Path(value)
    if candidate.is_absolute() or candidate.name != value or any(separator in value for separator in ("/", "\\")):
        return None
    return value


def _ordered_materialized_docx(request: ExecutorTaskRequest, workspace_root: Path) -> tuple[Path | None, str | None]:
    file_names = request.config.get("materialized_file_names")
    if not isinstance(file_names, list) or not file_names:
        return None, "controlled_skill_input_order_missing"
    for raw_name in file_names:
        name = _safe_materialized_basename(raw_name)
        if name is None:
            return None, "controlled_skill_input_name_invalid"
        materialized = _resolved_workspace_file(workspace_root, workspace_root / name)
        if materialized is None:
            return None, "controlled_skill_input_file_invalid"
        if materialized.suffix.lower() == ".docx":
            return materialized, None
    return None, "controlled_skill_input_docx_missing"


def _controlled_file_skill_command(
    request: ExecutorTaskRequest,
    skill_id: str,
    workspace_root: Path,
    *,
    user_message: str,
) -> tuple[list[str] | None, str | None]:
    workspace = workspace_root.resolve(strict=False)
    input_path, input_error = _ordered_materialized_docx(request, workspace)
    if input_path is None:
        return None, input_error or "controlled_skill_input_docx_missing"
    output_dir = workspace / "output"
    if output_dir.exists() and output_dir.is_symlink():
        return None, "controlled_skill_output_path_invalid"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.resolve(strict=True).relative_to(workspace.resolve(strict=True))
    except (OSError, ValueError):
        return None, "controlled_skill_output_path_invalid"
    script_name = "run_translation.py" if skill_id == "baoyu-translate" else "run_qa_review.py"
    script = _resolved_workspace_file(
        workspace,
        workspace / ".claude" / "skills" / skill_id / "scripts" / script_name,
    )
    if script is None:
        return None, "controlled_skill_runner_missing"
    command = [sys.executable, str(script), str(input_path), str(output_dir)]
    if skill_id == "baoyu-translate":
        command.extend(["--target-language", _translation_target_language(user_message)])
    else:
        command.append("--with-comments")
    command.extend(["--original-filename", input_path.name])
    return command, None


def _controlled_runner_environment(workspace_root: Path) -> dict[str, str]:
    workspace = workspace_root.resolve(strict=True)
    home = workspace / ".home"
    temp = workspace / ".tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    environment = {
        "HOME": str(home),
        "PATH": os.defpath,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TMP": str(temp),
        "TEMP": str(temp),
        "TMPDIR": str(temp),
    }
    if os.name == "nt":
        for name in ("SystemRoot", "WINDIR", "COMSPEC"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
    else:
        environment["LANG"] = "C.UTF-8"
    return environment


def _controlled_runner_process_kwargs() -> dict[str, object]:
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": creationflags} if creationflags else {}
    return {"start_new_session": True}


def _assign_windows_process_job(process: asyncio.subprocess.Process) -> object | None:
    """Attach the controlled process tree to a kill-on-close Windows job object."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        transport = getattr(process, "_transport", None)
        popen = transport.get_extra_info("subprocess") if transport is not None else None
        process_handle = getattr(popen, "_handle", None)
        if not process_handle:
            return None

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process_handle)):
            kernel32.CloseHandle(job)
            return None
        return job
    except (AttributeError, OSError):
        return None


def _close_windows_process_job(process: asyncio.subprocess.Process) -> None:
    job = getattr(process, "_controlled_job_handle", None)
    if not job:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(job):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed for controlled process job")
    finally:
        setattr(process, "_controlled_job_handle", None)


async def _wait_for_controlled_process_exit(process: asyncio.subprocess.Process) -> None:
    wait_task = asyncio.ensure_future(process.wait())
    await _await_task_completion(
        wait_task,
        timeout_seconds=_CONTROLLED_RUNNER_TERMINATION_GRACE_SECONDS,
        timeout_message="Controlled process cleanup exceeded its deadline",
    )


async def _stop_controlled_process(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        if getattr(process, "_controlled_job_handle", None):
            _close_windows_process_job(process)
        elif process.returncode is None:
            interrupt = getattr(signal, "CTRL_BREAK_EVENT", None)
            try:
                if interrupt is not None:
                    process.send_signal(interrupt)
                else:
                    process.terminate()
            except ProcessLookupError:
                return
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        await _wait_for_controlled_process_exit(process)
    except TimeoutError:
        if os.name == "nt":
            try:
                process.kill()
            except ProcessLookupError:
                return
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        await _wait_for_controlled_process_exit(process)


async def _cleanup_controlled_process(process: asyncio.subprocess.Process) -> None:
    try:
        await _stop_controlled_process(process)
    except asyncio.CancelledError:
        raise
    except _ExecutorCleanupError:
        raise
    except TimeoutError as exc:
        raise _ExecutorCleanupError(
            "executor_cleanup_timeout",
            "Executor cleanup exceeded its deadline",
        ) from exc
    except Exception as exc:
        raise _ExecutorCleanupError(
            "executor_cleanup_failed",
            "Executor cleanup failed",
        ) from exc


async def _run_selected_authorized_file_skill(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
) -> dict[str, Any] | None:
    skill_id, authorization_error = _selected_authorized_file_skill_id(request)
    if authorization_error:
        return {
            "status": "failed",
            "message": "Selected file Skill is not authorized for controlled execution",
            "error_code": authorization_error,
            "error_message": "Selected file Skill is not authorized for controlled execution",
            "sdk_used": False,
            "executor_mode": "platform_controlled_runner",
            "used_skills": [],
            "used_skills_source": "none",
        }
    if skill_id is None:
        return None
    command, command_error = _controlled_file_skill_command(
        request,
        skill_id,
        workspace_root,
        user_message=_user_message_from_skill_prompt(request.prompt),
    )
    if command is None:
        return {
            "status": "failed",
            "message": "Selected file Skill cannot be prepared in the sandbox workspace",
            "error_code": command_error or "controlled_skill_runner_unavailable",
            "error_message": "Selected file Skill cannot be prepared in the sandbox workspace",
            "sdk_used": False,
            "executor_mode": "platform_controlled_runner",
            "used_skills": [],
            "used_skills_source": "none",
        }
    await emit_event(
        AgentEvent(
            type="tool_call_started",
            message=f"Controlled file Skill started: {skill_id}",
            payload={
                "tool_name": "Skill",
                "skill_name": skill_id,
                "source": "platform_controlled_runner",
            },
            admin_only=True,
        )
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace_root),
            env=_controlled_runner_environment(workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_controlled_runner_process_kwargs(),
        )
    except OSError:
        return {
            "status": "failed",
            "message": "Selected file Skill failed to start",
            "error_code": "controlled_skill_runner_start_failed",
            "error_message": "Selected file Skill failed to start",
            "sdk_used": False,
            "executor_mode": "platform_controlled_runner",
            "used_skills": [],
            "used_skills_source": "none",
        }
    if os.name == "nt":
        job = _assign_windows_process_job(process)
        if job is None:
            await _cleanup_controlled_process(process)
            return {
                "status": "failed",
                "message": "Selected file Skill process group is unavailable",
                "error_code": "controlled_skill_process_group_unavailable",
                "error_message": "Selected file Skill process group is unavailable",
                "sdk_used": False,
                "executor_mode": "platform_controlled_runner",
                "used_skills": [],
                "used_skills_source": "none",
            }
        setattr(process, "_controlled_job_handle", job)
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=_CONTROLLED_RUNNER_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        await _cleanup_controlled_process(process)
        raise
    except TimeoutError:
        await _cleanup_controlled_process(process)
        return {
            "status": "failed",
            "message": "Selected file Skill exceeded its execution deadline",
            "error_code": "controlled_skill_execution_timeout",
            "error_message": "Selected file Skill exceeded its execution deadline",
            "sdk_used": False,
            "executor_mode": "platform_controlled_runner",
            "used_skills": [],
            "used_skills_source": "none",
        }
    if process.returncode != 0:
        await _cleanup_controlled_process(process)
        return {
            "status": "failed",
            "message": "Selected file Skill failed",
            "error_code": "controlled_skill_execution_failed",
            "error_message": "Selected file Skill failed",
            "sdk_used": False,
            "executor_mode": "platform_controlled_runner",
            "used_skills": [],
            "used_skills_source": "none",
        }
    await _cleanup_controlled_process(process)
    return {
        "status": "completed",
        "message": stdout.decode("utf-8", errors="replace").strip()
        or "Controlled file Skill completed.",
        "sdk_used": False,
        "executor_mode": "platform_controlled_runner",
        "used_skills": [skill_id],
        "used_skills_source": "platform_controlled_runner",
    }


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
) -> tuple[PlatformContextRetrievalClient | None, ScopedContextRetrievalIdentity | None, str | None]:
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
    if scope.session_id != request.session_id or scope.run_id != request.run_id:
        return None, None, "context_retrieval_scope_invalid"
    try:
        callback_target = _trusted_callback_target(request.callback_base_url)
    except CallbackTargetValidationError:
        return None, None, "context_retrieval_scope_invalid"
    retrieval = PlatformContextRetrievalClient(
        callback_url=callback_target.context_retrieval_url,
        callback_token_id=request.callback_token_id,
        callback_token=request.callback_token,
        scope=scope,
    )
    identity = ScopedContextRetrievalIdentity(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        session_id=scope.session_id,
        run_id=scope.run_id,
        agent_id=scope.agent_id,
    )
    return retrieval, identity, None


async def _default_executor_runner(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
    *,
    callback_sender: CallbackSender = _default_callback_sender,
) -> dict[str, Any]:
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
    attachment_contexts, attachment_error = await _preprocess_typed_attachments(
        request,
        workspace_root,
        emit_event,
        retrieval=context_retrieval,
        identity=context_retrieval_identity,
    )
    parser_evidence = [context.evidence.model_dump(mode="json") for context in attachment_contexts]
    if attachment_error:
        return {
            "status": "failed",
            "message": "Platform attachment preprocessing failed",
            "error_code": attachment_error,
            "error_message": "Platform attachment preprocessing failed",
            "sdk_used": False,
            "executor_mode": "platform_attachment_preprocessor",
            "attachment_parser_evidence": parser_evidence,
        }

    if not attachment_contexts:
        controlled_result = await _run_selected_authorized_file_skill(
            request,
            workspace_root,
            emit_event,
        )
        if controlled_result is not None:
            controlled_result["attachment_parser_evidence"] = parser_evidence
            return controlled_result
    if getattr(get_settings(), "claude_agent_sdk_enabled", False) is not True:
        return {
            "status": "failed",
            "message": "Claude Agent SDK is disabled",
            "error_code": "claude_agent_sdk_disabled",
            "error_message": "Claude Agent SDK is disabled",
            "sdk_used": False,
            "executor_mode": "claude_agent_sdk_disabled",
            "attachment_parser_evidence": parser_evidence,
        }

    skill_ids = _task_skill_ids(request)
    model_id = str(request.config.get("model") or "") or None

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
            attachment_contexts=attachment_contexts,
        )
    except ClaudeAgentSdkNotAvailable as exc:
        return {
            "status": "failed",
            "error_code": "claude_agent_sdk_unavailable",
            "error_message": f"Claude Agent SDK unavailable: {exc}",
            "sdk_used": False,
            "attachment_parser_evidence": parser_evidence,
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
        "attachment_parser_evidence": parser_evidence,
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
                                on_timeout=lambda: runner_events_open.update(value=False),
                            )
                        else:
                            raw_runner_result = await raw_runner_result
                    runner_result = raw_runner_result if isinstance(raw_runner_result, dict) else {}
                except _ExecutorCleanupError as exc:
                    runner_result = {
                        "status": "failed",
                        "error_code": exc.error_code,
                        "error_message": exc.error_message,
                    }
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
            "attachment_parser_evidence",
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

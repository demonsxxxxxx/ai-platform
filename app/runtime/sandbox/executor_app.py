# ruff: noqa: B004, BLE001, RUF046, S110

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import inspect
import json
import logging
import math
import os
import random
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NamedTuple

import httpx
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    _translation_target_language,
    run_claude_agent_sdk,
)
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    PublicExecutionPhasePublisher,
    PublicExecutionV2Projector,
    public_execution_phase_progress_payload,
)
from app.required_tool_contract import (
    REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY,
    REQUIRED_CAPABILITY_EVIDENCE_KEY,
    SANDBOX_EFFECTFUL_TOOL_IDENTITIES,
    SANDBOX_READ_ONLY_TOOL_IDENTITIES,
    TOOL_INVOCATION_EVIDENCE_KEY,
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    ToolInvocationEvidence,
    canonical_tool_call_id,
    declaration_from_payload,
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
    executor_callback_receipt_event_count,
)
from app.settings import get_settings
from app.skills.execution_profiles import PLATFORM_CONTROLLED
from app.validation import MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS

CallbackPayload = dict[str, Any]
CallbackResult = dict[str, Any] | None
CallbackSender = Callable[[str, CallbackPayload, str], Awaitable[CallbackResult] | CallbackResult]
CallbackRetrySleep = Callable[[float], Awaitable[None]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CallbackRetryPolicy:
    max_attempts: int = 4
    attempt_timeout_seconds: float = 10.0
    initial_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("callback retry max_attempts must be positive")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("callback retry attempt timeout must be positive")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("callback retry backoff must not be negative")
        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("callback retry initial backoff exceeds maximum")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("callback retry jitter ratio must be between zero and one")

    def delay_after_attempt(self, attempt: int) -> float:
        base_delay = min(
            self.initial_backoff_seconds * (2 ** max(attempt - 1, 0)),
            self.max_backoff_seconds,
        )
        return base_delay + random.uniform(0, base_delay * self.jitter_ratio)


class _CallbackDeliveryError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class _CallbackBatchIdFactory:
    """Allocate callback IDs that do not collide after an executor restart."""

    def __init__(self, namespace: str | None = None) -> None:
        self._namespace = namespace or uuid.uuid4().hex
        self._sequence = 0

    def next_id(self) -> str:
        self._sequence += 1
        return f"callback-{self._namespace}-{self._sequence}"


@dataclass(frozen=True)
class _CallbackBatchContent:
    batch_id: str
    serialized_payload: str
    payload_digest: str
    item_indexes: tuple[int, ...]

    @classmethod
    def freeze(cls, payload: CallbackPayload) -> _CallbackBatchContent:
        batch_id = str(payload.get("batch_id") or "")
        if not batch_id:
            raise ValueError("callback batch ID is required")
        serialized_payload = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        events = payload.get("events")
        event_count = len(events) if isinstance(events, list) else 0
        return cls(
            batch_id=batch_id,
            serialized_payload=serialized_payload,
            payload_digest=hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest(),
            item_indexes=tuple(range(event_count)),
        )

    def payload(self) -> CallbackPayload:
        return json.loads(self.serialized_payload)


@dataclass
class _CallbackBatchDelivery:
    content: _CallbackBatchContent
    state: str = "created"
    attempts: int = 0
    error_code: str | None = None

    def begin_attempt(self) -> None:
        if self.state not in {"created", "sending"}:
            raise RuntimeError(f"callback batch cannot send from {self.state}")
        self.state = "sending"
        self.attempts += 1

    def accept(self) -> None:
        if self.state != "sending":
            raise RuntimeError(f"callback batch cannot accept from {self.state}")
        self.state = "accepted"

    def exhaust(self, error_code: str) -> None:
        if self.state != "sending":
            raise RuntimeError(f"callback batch cannot exhaust from {self.state}")
        self.state = "exhausted"
        self.error_code = error_code

    def cancel(self) -> None:
        if self.state in {"accepted", "exhausted", "cancelled"}:
            return
        self.state = "cancelled"
        self.error_code = "executor_cancelled"


class _PrivateExecutionFact(NamedTuple):
    """Private runner fact paired with an optional public capability event."""

    fact: dict[str, object] | None
    public_event: AgentEvent | None = None

    @property
    def type(self) -> str:
        """Keep in-process lifecycle observers on the public event vocabulary."""

        return self.public_event.type if self.public_event is not None else "execution_step"

    def public_events(self, projector: PublicExecutionV2Projector) -> list[AgentEvent]:
        """Project this private fact into one ordered, public-safe callback batch."""

        events = []
        if self.public_event is not None:
            events.append(AgentEvent.model_validate(self.public_event.model_dump()))
        timeline = projector.project(self.fact)
        if timeline is not None:
            events.append(
                AgentEvent(
                    type=timeline.event_type,
                    message="",
                    payload=timeline.payload_json,
                )
            )
        return events


class _PlatformExecutionPhaseFact(NamedTuple):
    """One server-owned phase transition with no caller-provided presentation."""

    phase: str
    lifecycle: str

    @property
    def type(self) -> str:
        return "execution_step"

    def public_events(
        self,
        publisher: PublicExecutionPhasePublisher,
    ) -> list[AgentEvent]:
        step = publisher.project(phase=self.phase, lifecycle=self.lifecycle)
        if step is None:
            return []
        progress = public_execution_phase_progress_payload(
            phase=self.phase,
            lifecycle=self.lifecycle,
            step_id=step.step_id,
        )
        if progress is None:
            return []
        return [
            AgentEvent(type=step.event_type, message="", payload=step.payload_json),
            AgentEvent(
                type=PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
                message="",
                payload=progress,
            ),
        ]


ExecutorEvent = AgentEvent | ExecutorCallbackEvent | _PrivateExecutionFact | _PlatformExecutionPhaseFact
ExecutorEventEmitter = Callable[[ExecutorEvent], Awaitable[bool]]


class _SealableExecutorEventEmitter(NamedTuple):
    """Pair runner event emission with synchronous capability-failure sealing."""

    emit_event: ExecutorEventEmitter
    seal_capability_failure: Callable[[], None]

    async def __call__(self, event: ExecutorEvent) -> bool:
        return await self.emit_event(event)


ExecutorRunner = Callable[
    [ExecutorTaskRequest, Path, ExecutorEventEmitter],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
_PUBLIC_TOOL_LIFECYCLE_NAMES = frozenset(
    {
        "Skill",
        "MCP",
        "Read",
        "Glob",
        "Grep",
        "LS",
        "Bash",
        "Python",
        "Write",
        "Edit",
        "NotebookEdit",
        "Agent",
        "Task",
        "Artifact",
        "Validate",
        "Adjust",
    }
)


def _callback_acknowledges_exact_batch(
    result: object,
    *,
    batch_id: str,
    event_count: int,
) -> bool:
    """Accept only the runtime-callback receipt for this immutable input batch."""

    acknowledged_count = result.get("event_count") if isinstance(result, dict) else None
    acknowledged = isinstance(result, dict) and (
        result.get("accepted") is True or result.get("deduplicated") is True
    )
    return (
        acknowledged
        and result.get("batch_id") == batch_id
        and type(acknowledged_count) is int
        and acknowledged_count
        == executor_callback_receipt_event_count(input_event_count=event_count)
    )


def _private_capability_fact(
    *,
    evidence: RequiredCapabilityEvidence,
    callback_label: str,
    timeline_label: str,
) -> _PrivateExecutionFact:
    """Build both public views from one verified capability lifecycle fact."""

    callback_status, timeline_status = {"invocation_requested": ("invoking", "started"),
                                        "completed": ("completed", "completed"),
                                        "failed": ("failed", "failed")}[evidence.lifecycle_phase]
    tool_name = {"skill": "Skill", "mcp": "MCP"}.get(evidence.capability_kind)
    return _PrivateExecutionFact(
        fact=(
            {
                "invocation_id": str(evidence.tool_call_id),
                "tool_name": tool_name,
                "lifecycle": timeline_status,
                "safe_label": timeline_label,
            }
            if tool_name is not None
            else None
        ),
        public_event=AgentEvent(
            type=f"capability_{callback_status}",
            message="Capability lifecycle update",
            payload={"capability": {
                "kind": evidence.capability_kind, "name": callback_label, "status": callback_status,
            }},
        ),
    )


_CONTROLLED_FILE_SKILLS = {"baoyu-translate", "qa-file-reviewer"}
_CONTROLLED_FILE_SKILL_CAPABILITIES = {
    # These exactly mirror the server-owned builtin declarations in skills.pinning.
    "baoyu-translate": frozenset({"Bash", "Write"}),
    "qa-file-reviewer": frozenset({"Bash", "Write"}),
}
_CONTROLLED_RUNNER_TIMEOUT_SECONDS = 900.0
_CONTROLLED_RUNNER_TERMINATION_GRACE_SECONDS = 5.0
_EXECUTOR_CLEANUP_TIMEOUT_SECONDS = 5.0
_ACTIVE_PROGRESS_INTERVAL_SECONDS = 12.0
_SDK_PRESERVED_FAILURE_CODES = frozenset(
    {
        "claude_agent_sdk_disabled",
        "claude_agent_sdk_unavailable",
        "claude_agent_sdk_missing_structured_terminal",
        "claude_agent_sdk_selected_skill_not_invoked",
        "claude_agent_sdk_selected_skill_hook_failed",
        "claude_agent_sdk_selected_skill_not_authorized",
        "claude_agent_sdk_turn_limit_exceeded",
        "claude_agent_sdk_timeout",
        "claude_agent_sdk_tool_admission_failed",
        "claude_agent_sdk_upstream_error",
    }
)
_SDK_TURN_LIMIT_ERROR_PATTERN = re.compile(r"Reached maximum number of turns \(\d+\)")


class _ServerOwnedSystemPromptConfig(BaseModel):
    """Validate the one private executor config value allowed to reach the SDK system channel."""

    model_config = ConfigDict(extra="forbid", strict=True)

    system_prompt: str = Field(max_length=MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS)


class _ServerOwnedSystemPromptError(ValueError):
    """Classify a malformed private system prompt without retaining its content."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _server_owned_system_prompt(request: ExecutorTaskRequest) -> str | None:
    """Return only a strictly validated private system prompt from trusted task config."""

    if "system_prompt" not in request.config:
        return None
    try:
        return _ServerOwnedSystemPromptConfig.model_validate(
            {"system_prompt": request.config["system_prompt"]}
        ).system_prompt
    except ValidationError as exc:
        error_code = (
            "executor_system_prompt_too_large"
            if any(item.get("type") == "string_too_long" for item in exc.errors())
            else "executor_system_prompt_invalid"
        )
        raise _ServerOwnedSystemPromptError(error_code) from exc


def _public_capability_label(
    *,
    capability_kind: str,
    canonical_identity: str,
    subjects: list[dict[str, Any]],
) -> str:
    """Return only a server-owned public label for an exact authorized identity."""

    if capability_kind == "skill":
        skill_subject = next(
            (
                subject
                for subject in subjects
                if subject.get("identity") == "Skill"
                and canonical_identity in (subject.get("allowed_skill_names") or [])
            ),
            None,
        )
        if skill_subject is None:
            return ""
        public_labels = skill_subject.get("public_skill_labels")
        if isinstance(public_labels, dict):
            label = public_labels.get(canonical_identity)
            if isinstance(label, str) and label.strip():
                return label.strip()[:120]
        return "Skill"
    if capability_kind == "mcp":
        matching = [
            subject
            for subject in subjects
            if subject.get("identity") == canonical_identity
            and str(subject.get("mcp_server") or "") != "ai-platform-context"
        ]
        if len(matching) != 1:
            return ""
        label = matching[0].get("public_tool_label")
        return label.strip()[:120] if isinstance(label, str) and label.strip() else ""
    return ""


def _evidence_binding(request: ExecutorTaskRequest) -> dict[str, str]:
    """Return the authoritative request binding required by evidence factories."""

    return {
        "tenant_id": request.tenant_id,
        "workspace_id": request.workspace_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "run_id": request.run_id,
        "attempt_id": request.attempt_id,
    }


class _ExecutorCleanupError(RuntimeError):
    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message


def _expand_sdk_error_message(raw_error: str, sdk_result: object) -> str:
    """Expand a structured SDK failure into a concrete, non-generic message.

    The error_code stays machine-stable (e.g. claude_agent_sdk_timeout); the
    error_message carries the actionable details a user or support needs to
    understand exactly what happened: terminal class, recommended action,
    output volume, and any tools the policy denied.
    """

    diagnostics = getattr(sdk_result, "turn_diagnostics", None)
    if not isinstance(diagnostics, dict):
        return raw_error
    parts = [f"error={raw_error}"]
    terminal_class = str(diagnostics.get("terminal_class") or "").strip()
    if terminal_class:
        parts.append(f"terminal_class={terminal_class}")
    action = str(diagnostics.get("action") or "").strip()
    if action:
        parts.append(f"action={action}")
    counters = diagnostics.get("counters")
    if isinstance(counters, dict):
        assistant_messages = counters.get("assistant_messages")
        if assistant_messages is not None:
            parts.append(f"assistant_messages={assistant_messages}")
        denied_count = counters.get("tool_policy_denials")
        if denied_count is not None:
            parts.append(f"tool_policy_denials={denied_count}")
    detail = diagnostics.get("tool_policy_denials_detail")
    if isinstance(detail, list) and detail:
        denied = ", ".join(
            f"{item.get('tool_name')}({item.get('reason')})"
            for item in detail[:8]
            if isinstance(item, dict)
        )
        if denied:
            parts.append(f"denied_tools={denied}")
    return " | ".join(parts)


def _canonical_sdk_failure_code(raw_error: str, *, used_sdk: bool) -> str:
    """Keep known SDK terminal codes while classifying post-start SDK failures."""

    if raw_error in _SDK_PRESERVED_FAILURE_CODES:
        return raw_error
    if raw_error.startswith("claude_agent_sdk_unavailable"):
        return "claude_agent_sdk_unavailable"
    if used_sdk and _SDK_TURN_LIMIT_ERROR_PATTERN.fullmatch(raw_error):
        return "claude_agent_sdk_turn_limit_exceeded"
    if used_sdk:
        return "claude_agent_sdk_runtime_error"
    return raw_error


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


def _callback_error_is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {408, 429} or status_code >= 500
    return isinstance(exc, (httpx.TransportError, TimeoutError, OSError))


def _callback_error_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "attempt_timeout"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    if isinstance(exc, OSError):
        return "os_error"
    return "hard_rejection"


async def _deliver_nonterminal_callback(
    callback_sender: CallbackSender,
    url: str,
    batch: _CallbackBatchDelivery,
    token: str,
    *,
    retry_policy: _CallbackRetryPolicy,
    retry_sleep: CallbackRetrySleep,
) -> None:
    event_count = len(batch.content.item_indexes)
    try:
        for attempt in range(1, retry_policy.max_attempts + 1):
            batch.begin_attempt()
            try:
                result = await asyncio.wait_for(
                    _dispatch_callback(
                        callback_sender,
                        url,
                        batch.content.payload(),
                        token,
                    ),
                    timeout=retry_policy.attempt_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = _callback_error_reason(exc)
                log_context = {
                    "callback_batch_id": batch.content.batch_id,
                    "callback_batch_digest": batch.content.payload_digest,
                    "callback_attempt": attempt,
                    "callback_reason": reason,
                }
                if not _callback_error_is_retryable(exc):
                    batch.exhaust("stream_delivery_rejected")
                    _logger.warning("sandbox_callback_delivery_rejected", extra=log_context)
                    raise _CallbackDeliveryError("stream_delivery_rejected") from exc
                if attempt >= retry_policy.max_attempts:
                    batch.exhaust("stream_delivery_exhausted")
                    _logger.error("sandbox_callback_delivery_exhausted", extra=log_context)
                    raise _CallbackDeliveryError("stream_delivery_exhausted") from exc
                _logger.warning("sandbox_callback_delivery_retry", extra=log_context)
                await retry_sleep(retry_policy.delay_after_attempt(attempt))
                continue
            if not _callback_acknowledges_exact_batch(
            result,
            batch_id=batch.content.batch_id,
            event_count=event_count,
        ):
                batch.exhaust("stream_delivery_rejected")
                _logger.warning(
                    "sandbox_callback_delivery_rejected",
                    extra={
                        "callback_batch_id": batch.content.batch_id,
                        "callback_batch_digest": batch.content.payload_digest,
                        "callback_attempt": attempt,
                        "callback_reason": "invalid_receipt",
                    },
                )
                raise _CallbackDeliveryError("stream_delivery_rejected")
            batch.accept()
            return
    except asyncio.CancelledError:
        batch.cancel()
        _logger.info(
            "sandbox_callback_batch_cancelled",
            extra={
                "callback_batch_id": batch.content.batch_id,
                "callback_batch_digest": batch.content.payload_digest,
                "callback_attempt": batch.attempts,
                "callback_batch_state": batch.state,
            },
        )
        raise


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
        "attempt_id": request.attempt_id,
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
    return _safe_id_list(request.config.get("skill_ids"))


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


def _selected_authorized_file_skill_id(request: ExecutorTaskRequest) -> tuple[str | None, str | None]:
    """Return a controlled Skill only with its canonical builtin execution identities."""

    selected_skill_ids = _task_skill_ids(request)
    selected_skill_id = selected_skill_ids[0] if selected_skill_ids else ""
    subjects = _task_tool_policy_subjects(request)
    skill_subject = next(
        (
            subject
            for subject in subjects
            if str(subject.get("identity") or "") == "Skill"
            and selected_skill_id in _safe_id_list(subject.get("allowed_skill_names"))
        ),
        None,
    )
    if not isinstance(skill_subject, dict):
        if selected_skill_id in _CONTROLLED_FILE_SKILLS:
            return None, "controlled_skill_authorization_incomplete"
        return None, None
    if selected_skill_id not in _CONTROLLED_FILE_SKILLS:
        if str(skill_subject.get("execution_strategy") or "") == PLATFORM_CONTROLLED:
            return None, "controlled_skill_identity_invalid"
        return None, None
    execution_strategy = str(skill_subject.get("execution_strategy") or "")
    if execution_strategy and execution_strategy != PLATFORM_CONTROLLED:
        return None, None
    if not _authorized_capability_subject(skill_subject):
        return None, "controlled_skill_authorization_incomplete"
    required_identities = _CONTROLLED_FILE_SKILL_CAPABILITIES[selected_skill_id]
    authorized_identities = {
        str(subject.get("identity") or "")
        for subject in subjects
        if _authorized_capability_subject(subject)
    }
    if not required_identities.issubset(authorized_identities):
        return None, "controlled_skill_authorization_incomplete"
    # Empty strategy is a compatibility path for already queued, canonical
    # builtin pins. Uploaded legacy pins never carry the required identities.
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


def _user_message_from_skill_prompt(prompt: str) -> str:
    _, marker, remainder = str(prompt or "").partition("User request: ")
    if not marker:
        return ""
    for workspace_marker in (
        "\nWorkspace input files (under inputs/):\n",
        "\nWorkspace files:\n",
    ):
        user_message, separator, _workspace = remainder.partition(workspace_marker)
        if separator:
            return user_message
    return remainder


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
        materialized = _resolved_workspace_file(
            workspace_root,
            workspace_root / "inputs" / name,
        )
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
        process._controlled_job_handle = None


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


def _controlled_skill_result(
    *,
    status: str,
    message: str,
    error_code: str | None = None,
    capability_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "message": message,
        "sdk_used": False,
        "executor_mode": "platform_controlled_runner",
        "used_skills": [],
        "used_skills_source": "none",
        TOOL_INVOCATION_EVIDENCE_KEY: [],
    }
    if error_code:
        result.update(error_code=error_code, error_message=message)
    if capability_evidence is not None:
        result["capability_evidence"] = capability_evidence
    return result


async def _run_selected_authorized_file_skill(
    request: ExecutorTaskRequest,
    workspace_root: Path,
    emit_event: ExecutorEventEmitter,
) -> dict[str, Any] | None:
    skill_id, authorization_error = _selected_authorized_file_skill_id(request)
    if authorization_error:
        return _controlled_skill_result(
            status="failed",
            message="Selected file Skill is not authorized for controlled execution",
            error_code=authorization_error,
        )
    if skill_id is None:
        return None
    command, command_error = _controlled_file_skill_command(
        request,
        skill_id,
        workspace_root,
        user_message=_user_message_from_skill_prompt(request.prompt),
    )
    if command is None:
        return _controlled_skill_result(
            status="failed",
            message="Selected file Skill cannot be prepared in the sandbox workspace",
            error_code=command_error or "controlled_skill_runner_unavailable",
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
        return _controlled_skill_result(
            status="failed",
            message="Selected file Skill failed to start",
            error_code="controlled_skill_runner_start_failed",
        )
    if os.name == "nt":
        job = _assign_windows_process_job(process)
        if job is None:
            await _cleanup_controlled_process(process)
            return _controlled_skill_result(
                status="failed",
                message="Selected file Skill process group is unavailable",
                error_code="controlled_skill_process_group_unavailable",
            )
        process._controlled_job_handle = job
    invocation_id = f"controlled-{uuid.uuid4().hex}"
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity=skill_id,
    )
    capability_evidence: list[dict[str, Any]] = []

    async def publish_lifecycle(lifecycle_phase: str) -> bool:
        evidence = RequiredCapabilityEvidence.from_controlled_runner(
            declaration=declaration,
            binding=_evidence_binding(request),
            tool_call_id=invocation_id,
            lifecycle_phase=lifecycle_phase,
        )
        acknowledged = await emit_event(
            _private_capability_fact(
                evidence=evidence,
                callback_label="Skill",
                timeline_label="Authorized file processing",
            )
        )
        if acknowledged is not True:
            return False
        capability_evidence.append(asdict(evidence))
        return True

    if not await publish_lifecycle("invocation_requested"):
        await _cleanup_controlled_process(process)
        return _controlled_skill_result(
            status="failed",
            message="Capability lifecycle callback was not acknowledged",
            error_code="capability_callback_not_acknowledged",
            capability_evidence=[],
        )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=_CONTROLLED_RUNNER_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        await _cleanup_controlled_process(process)
        raise
    except TimeoutError:
        await _cleanup_controlled_process(process)
        await publish_lifecycle("failed")
        return _controlled_skill_result(
            status="failed",
            message="Selected file Skill exceeded its execution deadline",
            error_code="controlled_skill_execution_timeout",
            capability_evidence=capability_evidence,
        )
    if process.returncode != 0:
        await _cleanup_controlled_process(process)
        await publish_lifecycle("failed")
        return _controlled_skill_result(
            status="failed",
            message="Selected file Skill failed",
            error_code="controlled_skill_execution_failed",
            capability_evidence=capability_evidence,
        )
    await _cleanup_controlled_process(process)
    if not await publish_lifecycle("completed"):
        return _controlled_skill_result(
            status="failed",
            message="Capability lifecycle callback was not acknowledged",
            error_code="capability_callback_not_acknowledged",
            capability_evidence=[],
        )
    return {
        "status": "completed",
        "message": stdout.decode("utf-8", errors="replace").strip()
        or "Controlled file Skill completed.",
        "sdk_used": False,
        "executor_mode": "platform_controlled_runner",
        "used_skills": [skill_id],
        "used_skills_source": "platform_controlled_runner",
        "capability_evidence": capability_evidence,
        TOOL_INVOCATION_EVIDENCE_KEY: [],
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
    expected_attempt_id: str,
    trusted_callback_base_url: str | None,
) -> None:
    if not expected_session_id or not expected_run_id or not expected_attempt_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="executor_scope_not_configured")
    if request.session_id != expected_session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_executor_scope")
    if request.run_id != expected_run_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_executor_scope")
    if request.attempt_id != expected_attempt_id:
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
        attempt_id=request.attempt_id,
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
    callback_batch_ids = _CallbackBatchIdFactory()
    try:
        system_prompt = _server_owned_system_prompt(request)
    except _ServerOwnedSystemPromptError as exc:
        return {
            "status": "failed",
            "message": "Executor system prompt configuration is invalid",
            "error_code": exc.error_code,
            "error_message": "Executor system prompt configuration is invalid",
            "sdk_used": False,
            "executor_mode": "system_prompt_config_invalid",
        }
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
    await emit_event(
        _PlatformExecutionPhaseFact("attachment_materialization", "completed")
    )

    controlled_result = await _run_selected_authorized_file_skill(
        request,
        workspace_root,
        emit_event,
    )
    if controlled_result is not None:
        return controlled_result
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
    if skill_ids:
        await emit_event(_PlatformExecutionPhaseFact("skill_staging", "started"))
        await emit_event(_PlatformExecutionPhaseFact("skill_staging", "completed"))
    model_id = str(request.config.get("model") or "") or None
    bash_subject = next(
        (
            subject
            for subject in _task_tool_policy_subjects(request)
            if subject.get("identity") == "Bash"
        ),
        None,
    )
    try:
        required_capability_declaration = declaration_from_payload(
            (bash_subject or {}).get(REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY)
        )
    except RequiredToolContractError:
        return {
            "status": "failed",
            "message": "Required capability declaration is invalid",
            "error_code": "required_tool_declaration_mismatch",
            "error_message": "Required capability declaration is invalid",
            "sdk_used": False,
            "executor_mode": "required_capability_declaration_invalid",
        }
    required_tool_invocation_states: dict[tuple[str, str], str] = {}
    required_capability_evidence: dict[str, Any] | None = None
    tool_invocation_evidence: list[dict[str, Any]] = []
    bound_capability_evidence: list[dict[str, Any]] = []
    invocation_states: dict[tuple[str, str, str], str] = {}
    invocation_owners: dict[str, str] = {}
    capability_evidence_error = {"code": ""}
    capability_evidence_lock = asyncio.Lock()

    def reject_capability_evidence(error_code: str) -> bool:
        capability_evidence_error["code"] = capability_evidence_error["code"] or error_code
        return False

    def claim_invocation_id(invocation_id: str, owner: str, error_code: str) -> bool:
        current_owner = invocation_owners.get(invocation_id)
        if current_owner is not None and current_owner != owner:
            return reject_capability_evidence(error_code)
        invocation_owners[invocation_id] = owner
        return True

    async def on_text(delta: str) -> None:
        if not delta or capability_evidence_error["code"]:
            return
        await emit_event(AgentEvent(type="assistant_delta", message=delta, payload={"delta": delta}))

    async def on_agent_event(candidates: tuple[Any, ...]) -> bool:
        if capability_evidence_error["code"] or not candidates:
            return False
        try:
            events = [AgentEvent(**candidate.as_agent_event_fields()) for candidate in candidates]
        except Exception:  # noqa: BLE001
            events = []
        if not events:
            reject_capability_evidence("agent_event_callback_not_acknowledged")
            return False
        callback_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            batch_id=callback_batch_ids.next_id(),
            status="running",
            progress=20,
            state_patch={"stage": "agent_event"},
            sdk_session_id=request.sdk_session_id,
            events=events,
        )
        try:
            acknowledged = await emit_event(callback_event)
        except Exception:  # noqa: BLE001
            acknowledged = False
        if acknowledged is not True:
            reject_capability_evidence("agent_event_callback_not_acknowledged")
            if isinstance(emit_event, _SealableExecutorEventEmitter):
                emit_event.seal_capability_failure()
            return False
        return True

    async def on_skill_use(skill_name: str, metadata: dict[str, Any]) -> None:
        del skill_name, metadata

    async def bind_tool_lifecycle(fact: dict[str, str]) -> bool:
        nonlocal required_capability_evidence

        if capability_evidence_error["code"]:
            return False
        tool_name = str(fact.get("tool_name") or "")
        if tool_name not in _PUBLIC_TOOL_LIFECYCLE_NAMES:
            return False
        invocation_id = canonical_tool_call_id(fact.get("invocation_id")) or ""
        lifecycle = str(fact.get("lifecycle") or "")
        is_required_tool = (
            required_capability_declaration is not None
            and tool_name == required_capability_declaration.canonical_identity
        )
        lifecycle_error = (
            "required_tool_completion_evidence_mismatch"
            if is_required_tool
            else "tool_invocation_evidence_mismatch"
        )
        is_governed_bash = bash_subject is not None and tool_name == "Bash"
        is_read_only_tool = tool_name in SANDBOX_READ_ONLY_TOOL_IDENTITIES
        is_strict_tool = (
            is_required_tool
            or tool_name in SANDBOX_EFFECTFUL_TOOL_IDENTITIES
            or tool_name == "MCP"
        )
        is_lifecycle_tool = is_strict_tool or is_read_only_tool
        if invocation_id and is_strict_tool and not claim_invocation_id(
            invocation_id,
            f"tool:{tool_name}",
            lifecycle_error,
        ):
            if is_governed_bash:
                required_capability_evidence = None
            return False
        if is_lifecycle_tool and (
            not invocation_id or lifecycle not in {"started", "completed", "failed"}
        ):
            if is_strict_tool:
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
            return False
        try:
            acknowledged = await emit_event(
                _PrivateExecutionFact(
                    fact={
                        "invocation_id": invocation_id,
                        "tool_name": tool_name,
                        "lifecycle": lifecycle,
                    },
                )
            )
        except Exception:
            if is_strict_tool:
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
            return False
        if not is_lifecycle_tool:
            return acknowledged is True
        if acknowledged is not True or not invocation_id:
            if is_strict_tool:
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
            return False
        if not is_strict_tool:
            return True
        invocation_key = (tool_name, invocation_id)
        current_state = required_tool_invocation_states.get(invocation_key)
        if lifecycle == "started":
            if current_state is not None:
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
                return False
            required_tool_invocation_states[invocation_key] = "started"
        elif lifecycle in {"completed", "failed"}:
            if current_state != "started":
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
                return False
            required_tool_invocation_states[invocation_key] = lifecycle
            if lifecycle == "completed" and is_required_tool:
                required_capability_evidence = asdict(
                    RequiredCapabilityEvidence.from_executor_private_payload(
                        declaration=required_capability_declaration,
                        binding=_evidence_binding(request),
                        tool_call_id=invocation_id,
                    )
                )
        if is_governed_bash:
            try:
                tool_invocation_evidence.append(
                    asdict(
                        ToolInvocationEvidence.from_executor_private_payload(
                            binding=_evidence_binding(request),
                            tool_call_id=invocation_id,
                            canonical_identity=tool_name,
                            lifecycle_phase=lifecycle,
                        )
                    )
                )
            except RequiredToolContractError:
                required_capability_evidence = None
                reject_capability_evidence(lifecycle_error)
                return False
        if lifecycle == "failed" and is_required_tool:
            required_capability_evidence = None
            reject_capability_evidence("required_tool_completion_evidence_mismatch")
            return False
        return True

    async def on_tool_lifecycle(fact: dict[str, str]) -> bool:
        """Bind and forward a mapped lifecycle fact under the shared call-id fence."""

        try:
            async with capability_evidence_lock:
                return await bind_tool_lifecycle(fact)
        except asyncio.CancelledError:
            poison_capability_evidence()
            raise

    def poison_capability_evidence() -> None:
        # No await: one event-loop turn invalidates a suspended lock owner before it can commit.
        reject_capability_evidence("capability_callback_not_acknowledged")
        bound_capability_evidence.clear()
        invocation_states.clear()
        if isinstance(emit_event, _SealableExecutorEventEmitter):
            emit_event.seal_capability_failure()

    async def bind_capability_evidence(raw: dict[str, str]) -> bool:
        """Bind SDK-hook facts to this request and emit only a safe public event."""

        if capability_evidence_error["code"]:
            return False
        try:
            declaration = RequiredCapabilityDeclaration.from_authorized_subject(
                capability_kind=str(raw.get("capability_kind") or ""),
                canonical_identity=str(raw.get("canonical_identity") or ""),
            )
            if declaration.declaration_sha256 != raw.get("declaration_sha256"):
                return reject_capability_evidence("capability_lifecycle_sequence_invalid")
            evidence = RequiredCapabilityEvidence.from_sdk_hook(
                declaration=declaration,
                binding=_evidence_binding(request),
                tool_call_id=str(raw.get("tool_call_id") or ""),
                lifecycle_phase=str(raw.get("lifecycle_phase") or ""),
            )
        except (AttributeError, RequiredToolContractError):
            return reject_capability_evidence("capability_lifecycle_sequence_invalid")
        label = _public_capability_label(
            capability_kind=evidence.capability_kind,
            canonical_identity=evidence.canonical_identity,
            subjects=_task_tool_policy_subjects(request),
        )
        if not label:
            return reject_capability_evidence("capability_lifecycle_sequence_invalid")
        invocation_key = (
            evidence.capability_kind,
            evidence.canonical_identity,
            str(evidence.tool_call_id or ""),
        )
        if not claim_invocation_id(
            str(evidence.tool_call_id or ""),
            f"capability:{evidence.capability_kind}:{evidence.canonical_identity}",
            "capability_lifecycle_sequence_invalid",
        ):
            return False
        current_state = invocation_states.get(invocation_key)
        invalid_sequence = (
            evidence.lifecycle_phase == "invocation_requested" and current_state is not None
        ) or (
            evidence.lifecycle_phase != "invocation_requested" and current_state != "invoking"
        )
        if invalid_sequence:
            invocation_states[invocation_key] = "rejected"
            return reject_capability_evidence("capability_lifecycle_sequence_invalid")
        try:
            acknowledged = await emit_event(
                _private_capability_fact(
                    evidence=evidence,
                    callback_label=label,
                    timeline_label=label,
                )
            )
        except Exception:
            invocation_states[invocation_key] = "rejected"
            return reject_capability_evidence("capability_callback_not_acknowledged")
        if capability_evidence_error["code"]:
            return False
        if acknowledged is not True:
            invocation_states[invocation_key] = "rejected"
            return reject_capability_evidence("capability_callback_not_acknowledged")
        bound_capability_evidence.append(asdict(evidence))
        invocation_states[invocation_key] = (
            "invoking" if evidence.lifecycle_phase == "invocation_requested" else "terminal"
        )
        return True

    async def on_capability_evidence(raw: dict[str, str]) -> bool:
        try:
            async with capability_evidence_lock:
                return await bind_capability_evidence(raw)
        except asyncio.CancelledError:
            poison_capability_evidence()
            raise

    await emit_event(_PlatformExecutionPhaseFact("model_wait", "started"))
    try:
        sdk_kwargs = {
            "prompt": request.prompt,
            "cwd": workspace_root,
            "skill_id": skill_ids[0] if skill_ids else None,
            "session_id": request.sdk_session_id,
            "model_id": model_id,
            "skills": skill_ids,
            "context_retrieval": context_retrieval,
            "context_retrieval_identity": context_retrieval_identity,
            "on_text": on_text,
            "on_agent_event": on_agent_event,
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "on_skill_use": on_skill_use,
            "on_capability_evidence": on_capability_evidence,
            "on_tool_lifecycle": on_tool_lifecycle,
            "tool_policy_subjects": _task_tool_policy_subjects(request),
            "execution_policy": "sandbox_brokered",
            "execution_profile": str(request.config.get("sdk_execution_profile") or ""),
            "require_selected_skill_invocation": request.config.get(
                "require_selected_skill_invocation", True
            ) is not False,
        }
        if system_prompt is not None:
            sdk_kwargs["system_prompt"] = system_prompt
        sdk_result = await run_claude_agent_sdk(
            **sdk_kwargs,
        )
    except ClaudeAgentSdkNotAvailable:
        await emit_event(_PlatformExecutionPhaseFact("model_wait", "failed"))
        return {
            "status": "failed",
            "error_code": "claude_agent_sdk_unavailable",
            "error_message": "Claude Agent SDK is unavailable",
            "sdk_used": False,
        }

    used_sdk = bool(getattr(sdk_result, "used_sdk", False))
    error = getattr(sdk_result, "error", None)
    received_structured_terminal = bool(
        getattr(sdk_result, "received_structured_terminal", False)
    )
    if used_sdk and not error and not received_structured_terminal:
        error = "claude_agent_sdk_missing_structured_terminal"
    if used_sdk and not error:
        # Only a successful SDK run may be downgraded by missing completion
        # evidence.  When the SDK already failed (timeout, cancelled, upstream
        # error, ...) preserve that structured error so callers see the real
        # terminal cause instead of a misleading evidence mismatch.
        if required_capability_declaration is not None:
            required_tool_states = set(required_tool_invocation_states.values())
            if "started" in required_tool_states or "completed" not in required_tool_states:
                required_capability_evidence = None
                reject_capability_evidence("required_tool_completion_evidence_mismatch")
        elif any(state == "started" for state in required_tool_invocation_states.values()):
            reject_capability_evidence("tool_invocation_evidence_mismatch")
    await emit_event(
        _PlatformExecutionPhaseFact(
            "model_wait",
            "completed" if used_sdk and not error else "failed",
        )
    )
    response = {
        "status": "completed" if used_sdk and not error else "failed",
        "message": str(getattr(sdk_result, "message", "") or ""),
        "sdk_session_id": getattr(sdk_result, "session_id", None),
        "sdk_usage": getattr(sdk_result, "usage", {}) or {},
        "sdk_used": used_sdk,
        "sdk_received_structured_terminal": received_structured_terminal,
        "sdk_terminal_reason": getattr(sdk_result, "terminal_reason", None),
        "executor_mode": "claude_agent_sdk",
        "used_skills": list(getattr(sdk_result, "used_skills", []) or []),
        "used_skills_source": str(getattr(sdk_result, "used_skills_source", "") or ""),
        "sdk_turn_diagnostics": dict(getattr(sdk_result, "turn_diagnostics", {}) or {}),
        "capability_evidence": bound_capability_evidence,
        TOOL_INVOCATION_EVIDENCE_KEY: tool_invocation_evidence,
    }
    if required_capability_evidence is not None:
        response[REQUIRED_CAPABILITY_EVIDENCE_KEY] = required_capability_evidence
    if error:
        raw_error = str(error)
        response["error_code"] = _canonical_sdk_failure_code(raw_error, used_sdk=used_sdk)
        response["error_message"] = _expand_sdk_error_message(raw_error, sdk_result)
    elif not used_sdk:
        response["error_code"] = "claude_agent_sdk_disabled"
        response["error_message"] = "Claude Agent SDK is disabled"
    if capability_evidence_error["code"]:
        response["status"] = "failed"
        response["message"] = ""
        response["error_code"] = capability_evidence_error["code"]
        if capability_evidence_error["code"] == "capability_callback_not_acknowledged":
            response["error_message"] = "Capability lifecycle callback was not acknowledged"
        elif capability_evidence_error["code"] == "required_tool_completion_evidence_mismatch":
            response["error_message"] = "Required capability completion evidence is invalid"
        else:
            response["error_message"] = "Capability lifecycle sequence is invalid"
        response["capability_evidence"] = []
        response[TOOL_INVOCATION_EVIDENCE_KEY] = []
        response.pop(REQUIRED_CAPABILITY_EVIDENCE_KEY, None)
    return response


def create_executor_app(
    workspace_root: str | Path = "/workspace",
    callback_sender: CallbackSender | None = None,
    executor_runner: ExecutorRunner | None = None,
    executor_auth_token: str | None = None,
    expected_session_id: str | None = None,
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
    trusted_callback_base_url: str | None = None,
    dispatch_in_background: bool = True,
    terminal_callback_retry_seconds: float = 300.0,
    heartbeat_interval_seconds: float = 15.0,
    nonterminal_callback_retry_policy: _CallbackRetryPolicy | None = None,
    callback_retry_sleep: CallbackRetrySleep | None = None,
) -> FastAPI:
    task_state: dict[str, Any] = {
        "status": "idle",
        "result": None,
        "task": None,
        "run_id": None,
        "attempt_id": None,
        "delivery_error": None,
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        task = task_state.get("task")
        if not isinstance(task, asyncio.Task) or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=30.0)
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            task.cancel()

    app = FastAPI(
        title="AI Platform Sandbox Executor",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.dispatch_in_background = dispatch_in_background
    resolved_workspace_root = Path(workspace_root)
    resolved_callback_sender = callback_sender or _default_callback_sender
    resolved_nonterminal_callback_retry_policy = nonterminal_callback_retry_policy or _CallbackRetryPolicy()
    resolved_callback_retry_sleep = callback_retry_sleep or asyncio.sleep
    configured_executor_auth_token = _configured_executor_auth_token(executor_auth_token)
    configured_expected_session_id = _configured_expected_value(expected_session_id, "AI_PLATFORM_SESSION_ID")
    configured_expected_run_id = _configured_expected_value(expected_run_id, "AI_PLATFORM_RUN_ID")
    configured_expected_attempt_id = _configured_expected_value(expected_attempt_id, "AI_PLATFORM_ATTEMPT_ID")
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

    async def execute_claimed_task(
        request: ExecutorTaskRequest,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        document_started_at = time.monotonic()
        try:
            marker_path = _write_runtime_marker(resolved_workspace_root, request)
        except OSError:
            error_message = "Executor runtime marker write failed"
            return {
                "status": "failed",
                "run_id": request.run_id,
                "message": error_message,
                "error_code": "executor_runtime_marker_write_failed",
                "error_message": error_message,
                "sdk_used": False,
                "executor_mode": "runtime_marker_write_failed",
            }
        document_processing_latency_ms = _elapsed_ms(document_started_at)
        callback_errors: list[str] = []
        callback_batch_ids = _CallbackBatchIdFactory()

        running_event = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            batch_id=callback_batch_ids.next_id(),
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
        capability_callback_failed = {"value": False}
        stream_delivery_failure: dict[str, str | None] = {"error_code": None}
        public_execution_projector = PublicExecutionV2Projector()
        public_execution_phase_publisher = PublicExecutionPhasePublisher()
        runner_event_lock = asyncio.Lock()
        active_progress_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        progress_tasks: set[asyncio.Task[None]] = set()

        def active_progress_identity(
            event: _PrivateExecutionFact | _PlatformExecutionPhaseFact,
        ) -> tuple[
            tuple[str, str],
            _PrivateExecutionFact | _PlatformExecutionPhaseFact,
            str,
        ] | None:
            if isinstance(event, _PlatformExecutionPhaseFact):
                if event.lifecycle not in {"started", "progress", "completed", "failed"}:
                    return None
                return (
                    ("phase", event.phase),
                    _PlatformExecutionPhaseFact(event.phase, "progress"),
                    event.lifecycle,
                )
            fact = event.fact
            if not isinstance(fact, dict):
                return None
            invocation_id = str(fact.get("invocation_id") or "")
            tool_name = str(fact.get("tool_name") or "")
            lifecycle = str(fact.get("lifecycle") or "")
            if (
                not invocation_id
                or tool_name not in _PUBLIC_TOOL_LIFECYCLE_NAMES
                or lifecycle not in {"started", "progress", "completed", "failed"}
            ):
                return None
            progress_fact: dict[str, object] = {
                "invocation_id": invocation_id,
                "tool_name": tool_name,
                "lifecycle": "progress",
            }
            if "safe_label" in fact:
                progress_fact["safe_label"] = fact["safe_label"]
            return (
                ("tool", invocation_id),
                _PrivateExecutionFact(fact=progress_fact),
                lifecycle,
            )

        def stop_active_progress(identity: tuple[str, str]) -> None:
            task = active_progress_tasks.pop(identity, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()

        def stop_all_active_progress() -> None:
            active_progress_tasks.clear()
            for task in tuple(progress_tasks):
                task.cancel()

        def seal_runner_events_after_capability_failure() -> None:
            capability_callback_failed["value"] = True
            runner_events_open["value"] = False
            stop_all_active_progress()

        def seal_runner_events_after_delivery_failure(error_code: str) -> None:
            if stream_delivery_failure["error_code"] is None:
                stream_delivery_failure["error_code"] = error_code
            runner_events_open["value"] = False
            stop_all_active_progress()

        def seal_runner_events_after_delivery_cancellation() -> None:
            runner_events_open["value"] = False
            stop_all_active_progress()

        async def dispatch_callback_event(event: ExecutorCallbackEvent) -> bool:
            if stream_delivery_failure["error_code"] is not None:
                return False
            batch = _CallbackBatchDelivery(
                content=_CallbackBatchContent.freeze(event.model_dump())
            )
            try:
                await _deliver_nonterminal_callback(
                    resolved_callback_sender,
                    request.callback_url,
                    batch,
                    request.callback_token,
                    retry_policy=resolved_nonterminal_callback_retry_policy,
                    retry_sleep=resolved_callback_retry_sleep,
                )
            except asyncio.CancelledError:
                seal_runner_events_after_delivery_cancellation()
                raise
            except _CallbackDeliveryError as exc:
                callback_errors.append(event.status)
                seal_runner_events_after_delivery_failure(exc.error_code)
                return False
            return True

        def apply_stream_delivery_failure(result: dict[str, Any]) -> bool:
            error_code = stream_delivery_failure["error_code"]
            if capability_callback_failed["value"] or error_code is None:
                return False
            result["status"] = "failed"
            result["message"] = ""
            result["error_code"] = error_code
            result["error_message"] = "Public stream callback delivery failed"
            return True

        async def emit_runner_event_locked(event: ExecutorEvent) -> bool:
            nonlocal artifact_upload_latency_ms, executor_first_token_latency_ms, executor_tool_call_latency_ms
            if capability_callback_failed["value"] or not runner_events_open["value"]:
                return False
            if isinstance(event, ExecutorCallbackEvent):
                return await dispatch_callback_event(event)
            if isinstance(event, _PrivateExecutionFact):
                agent_event = event.public_event
                agent_events = event.public_events(public_execution_projector)
                if not agent_events:
                    return False
                event_type = event.type
                active_progress = active_progress_identity(event)
                if active_progress is not None and active_progress[2] in {"completed", "failed"}:
                    stop_active_progress(active_progress[0])
            elif isinstance(event, _PlatformExecutionPhaseFact):
                agent_event = None
                agent_events = event.public_events(public_execution_phase_publisher)
                if not agent_events:
                    return True
                event_type = event.type
                active_progress = active_progress_identity(event)
                if active_progress is not None and active_progress[2] in {"completed", "failed"}:
                    stop_active_progress(active_progress[0])
            else:
                agent_event = event if isinstance(event, AgentEvent) else AgentEvent.model_validate(event)
                raw_payload = dict(agent_event.payload)
                if agent_event.type in {
                    "execution_step", "execution_progress", "execution_step_completed", "execution_step_failed"
                }:
                    return False
                if agent_event.type.startswith("tool_call") and {
                    "command", "args", "arguments", "result", "output", "tool_input", "tool_output",
                    "private_payload", "executor_private_payload",
                } & set(raw_payload):
                    return True
                agent_events = [AgentEvent(
                    type=agent_event.type,
                    message=agent_event.message,
                    payload=raw_payload,
                    admin_only=agent_event.admin_only,
                    event_id=agent_event.event_id,
                    run_id=agent_event.run_id,
                    message_id=agent_event.message_id,
                    causation_event_id=agent_event.causation_event_id,
                )]
                event_type = agent_event.type
            if event_type == "assistant_delta" and executor_first_token_latency_ms is None:
                executor_first_token_latency_ms = _elapsed_ms(executor_started_at)
            if event_type and event_type.startswith("tool_call") and executor_tool_call_latency_ms is None:
                executor_tool_call_latency_ms = _elapsed_ms(executor_started_at)

            callback_event = ExecutorCallbackEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                callback_token_id=request.callback_token_id,
                batch_id=callback_batch_ids.next_id(),
                status="running",
                progress=35 if event_type and event_type.startswith("tool_call") else 60 if event_type == "artifact_created" else 20,
                state_patch={"stage": event_type or "execution_step"},
                sdk_session_id=request.sdk_session_id,
                events=agent_events,
            )
            artifact_started_at = time.monotonic() if event_type == "artifact_created" else None
            is_capability_event = agent_event is not None and agent_event.type.startswith("capability_")
            acknowledged = await dispatch_callback_event(callback_event)
            if is_capability_event and not acknowledged:
                seal_runner_events_after_capability_failure()
            elif isinstance(event, (_PrivateExecutionFact, _PlatformExecutionPhaseFact)) and active_progress is not None:
                identity, progress_fact, lifecycle = active_progress
                if lifecycle == "started" and acknowledged:
                    task = asyncio.create_task(emit_active_progress(identity, progress_fact))
                    active_progress_tasks[identity] = task
                    progress_tasks.add(task)
                    _observe_detached_task(task)

                    def forget_progress_task(completed_task: asyncio.Task[None]) -> None:
                        progress_tasks.discard(completed_task)
                        if active_progress_tasks.get(identity) is completed_task:
                            active_progress_tasks.pop(identity, None)

                    task.add_done_callback(forget_progress_task)
                elif lifecycle == "progress" and not acknowledged:
                    stop_active_progress(identity)
            if artifact_started_at is not None:
                artifact_upload_latency_ms += _elapsed_ms(artifact_started_at)
            return acknowledged

        async def emit_runner_event(event: ExecutorEvent) -> bool:
            is_capability_event = str(getattr(event, "type", "")).startswith("capability_")
            try:
                async with runner_event_lock:
                    try:
                        return await emit_runner_event_locked(event)
                    except asyncio.CancelledError:
                        if is_capability_event:
                            seal_runner_events_after_capability_failure()
                        raise
            except asyncio.CancelledError:
                if is_capability_event:
                    seal_runner_events_after_capability_failure()
                raise

        async def emit_active_progress(
            identity: tuple[str, str],
            progress_fact: _PrivateExecutionFact | _PlatformExecutionPhaseFact,
        ) -> None:
            current_task = asyncio.current_task()
            while (
                runner_events_open["value"]
                and not capability_callback_failed["value"]
                and active_progress_tasks.get(identity) is current_task
            ):
                await asyncio.sleep(_ACTIVE_PROGRESS_INTERVAL_SECONDS)
                if (
                    not runner_events_open["value"]
                    or capability_callback_failed["value"]
                    or active_progress_tasks.get(identity) is not current_task
                ):
                    return
                acknowledged = await emit_runner_event(progress_fact)
                if not acknowledged:
                    return

        async def drain_active_progress() -> None:
            stop_all_active_progress()
            pending = tuple(progress_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        runner_event_emitter = _SealableExecutorEventEmitter(
            emit_event=emit_runner_event,
            seal_capability_failure=seal_runner_events_after_capability_failure,
        )

        await emit_runner_event(
            _PlatformExecutionPhaseFact("sandbox_preparation", "started")
        )
        await dispatch_callback_event(running_event)
        await emit_runner_event(
            _PlatformExecutionPhaseFact("sandbox_preparation", "completed")
        )
        await emit_runner_event(
            _PlatformExecutionPhaseFact("sandbox_submission", "started")
        )
        runner_result: dict[str, Any] = {}
        try:
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
                        raw_runner_result = resolved_executor_runner(
                            request,
                            resolved_workspace_root,
                            runner_event_emitter,
                        )
                        if inspect.isawaitable(raw_runner_result):
                            if max_seconds is not None:
                                raw_runner_result, timed_out = await _await_with_deadline(
                                    raw_runner_result,
                                    timeout_seconds=max_seconds,
                                    on_timeout=lambda: None,
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
        finally:
            await drain_active_progress()

        if capability_callback_failed["value"]:
            runner_result["status"] = "failed"
            runner_result["message"] = ""
            runner_result["error_code"] = "capability_callback_not_acknowledged"
            runner_result["error_message"] = "Capability lifecycle callback was not acknowledged"
            runner_result["capability_evidence"] = []
        else:
            apply_stream_delivery_failure(runner_result)

        runner_status = str(runner_result.get("status") or "").strip().lower()
        failed = timed_out or runner_status not in {"completed", "succeeded"}
        if runner_events_open["value"]:
            phase_lifecycle = "failed" if failed else "completed"
            await emit_runner_event(
                _PlatformExecutionPhaseFact("sandbox_submission", phase_lifecycle)
            )
            await emit_runner_event(
                _PlatformExecutionPhaseFact("artifact_validation", "started")
            )
            await emit_runner_event(
                _PlatformExecutionPhaseFact("artifact_validation", phase_lifecycle)
            )
        if apply_stream_delivery_failure(runner_result):
            runner_status = "failed"
            failed = True
        runner_events_open["value"] = False
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
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            batch_id=callback_batch_ids.next_id(),
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
        if apply_stream_delivery_failure(runner_result):
            runner_status = "failed"
            failed = True
            error_code = str(runner_result["error_code"])
            error_message = str(runner_result["error_message"])

        executor_model_latency_ms = _elapsed_ms(started_at)
        response: dict[str, Any] = {
            "status": runner_status if not failed else "failed",
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
            "sdk_received_structured_terminal",
            "sdk_terminal_reason",
            "executor_mode",
            "used_skills",
            "used_skills_source",
            "sdk_turn_diagnostics",
            "capability_evidence",
            REQUIRED_CAPABILITY_EVIDENCE_KEY,
            TOOL_INVOCATION_EVIDENCE_KEY,
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

    async def deliver_terminal_callback(
        request: ExecutorTaskRequest,
        result: dict[str, Any],
    ) -> None:
        result_status = str(result.get("status") or "failed").strip().lower()
        if result_status in {"completed", "succeeded"}:
            callback_status = "completed"
            progress = 100
        elif result_status in {"cancelled", "canceled"}:
            callback_status = "cancelled"
            progress = 100
            result["status"] = "cancelled"
        else:
            callback_status = "failed"
            progress = 100
            result["status"] = "failed"
        callback = ExecutorCallbackEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            batch_id=f"terminal-{uuid.uuid4().hex}",
            status=callback_status,
            progress=progress,
            sdk_session_id=str(result.get("sdk_session_id") or request.sdk_session_id or "") or None,
            error_message=str(result.get("error_message") or "") or None,
            terminal_result=result,
        )
        deadline = time.monotonic() + terminal_callback_retry_seconds
        delay = 0.5
        while True:
            try:
                acknowledged = resolved_callback_sender(
                    request.callback_url,
                    callback.model_dump(exclude_none=True),
                    request.callback_token,
                )
                if inspect.isawaitable(acknowledged):
                    acknowledged = await acknowledged
                if isinstance(acknowledged, dict) and acknowledged.get("accepted") is True:
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task_state["delivery_error"] = (
                    f"{type(exc).__name__}: {str(exc)}"[:512]
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("executor_terminal_callback_not_acknowledged")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)

    async def send_supervisor_heartbeats(request: ExecutorTaskRequest) -> None:
        while True:
            await asyncio.sleep(heartbeat_interval_seconds)
            heartbeat = ExecutorCallbackEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                callback_token_id=request.callback_token_id,
                batch_id=f"heartbeat-{uuid.uuid4().hex}",
                status="running",
                progress=5,
                state_patch={"executor_heartbeat": True},
            )
            try:
                acknowledged = resolved_callback_sender(
                    request.callback_url,
                    heartbeat.model_dump(exclude_none=True),
                    request.callback_token,
                )
                if inspect.isawaitable(acknowledged):
                    await acknowledged
            except asyncio.CancelledError:
                raise
            except Exception:
                # Heartbeats are best-effort liveness hints. Runner events and
                # terminal delivery keep their own acknowledgement semantics.
                continue

    async def supervise_task(request: ExecutorTaskRequest) -> None:
        task_state["status"] = "running"
        heartbeat_task = asyncio.create_task(send_supervisor_heartbeats(request))
        try:
            result = await execute_claimed_task(request)
        except asyncio.CancelledError:
            result = {
                "status": "cancelled",
                "run_id": request.run_id,
                "message": "Task cancelled",
                "error_code": "executor_cancelled",
                "error_message": "Task cancelled",
            }
        except Exception:
            result = {
                "status": "failed",
                "run_id": request.run_id,
                "message": "Executor failed",
                "error_code": "executor_runner_failed",
                "error_message": "Executor failed",
            }
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        task_state["result"] = result
        task_state["status"] = str(result.get("status") or "failed")
        try:
            await deliver_terminal_callback(request, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            task_state["status"] = "callback_failed"

    def validate_control_scope(run_id: str, attempt_id: str) -> None:
        expected_run = configured_expected_run_id or task_state.get("run_id")
        expected_attempt = configured_expected_attempt_id or task_state.get("attempt_id")
        if run_id != expected_run or attempt_id != expected_attempt:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="invalid_executor_scope")

    @app.post("/v2/tasks")
    async def dispatch_task(
        request: ExecutorTaskRequest,
        response: Response = None,  # type: ignore[assignment]
        executor_credential: str | None = Header(default=None, alias=EXECUTOR_AUTH_HEADER),
    ) -> dict[str, Any]:
        _require_executor_credential(executor_credential, configured_executor_auth_token)
        _validate_executor_request_scope(
            request,
            expected_session_id=configured_expected_session_id,
            expected_run_id=configured_expected_run_id,
            expected_attempt_id=configured_expected_attempt_id,
            trusted_callback_base_url=trusted_callback_base_url,
        )
        if execute_claimed["value"]:
            if response is not None:
                response.status_code = status.HTTP_202_ACCEPTED
            return {
                "status": "accepted",
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
            }
        execute_claimed["value"] = True
        task_state["run_id"] = request.run_id
        task_state["attempt_id"] = request.attempt_id
        if not app.state.dispatch_in_background:
            result = await execute_claimed_task(request)
            task_state["result"] = result
            task_state["status"] = str(result.get("status") or "failed")
            return result
        task = asyncio.create_task(supervise_task(request))
        task_state["task"] = task
        task_state["status"] = "accepted"
        if response is not None:
            response.status_code = status.HTTP_202_ACCEPTED
        return {
            "status": "accepted",
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
        }

    @app.get("/v2/tasks/{run_id}/{attempt_id}")
    async def get_task_status(
        run_id: str,
        attempt_id: str,
        executor_credential: str | None = Header(default=None, alias=EXECUTOR_AUTH_HEADER),
    ) -> dict[str, Any]:
        _require_executor_credential(executor_credential, configured_executor_auth_token)
        validate_control_scope(run_id, attempt_id)
        response: dict[str, Any] = {
            "status": task_state["status"],
            "run_id": run_id,
            "attempt_id": attempt_id,
        }
        if task_state["result"] is not None:
            response["terminal_result"] = task_state["result"]
        if task_state["delivery_error"] is not None:
            response["error_message"] = task_state["delivery_error"]
        return response

    @app.post("/v2/tasks/{run_id}/{attempt_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_task(
        run_id: str,
        attempt_id: str,
        executor_credential: str | None = Header(default=None, alias=EXECUTOR_AUTH_HEADER),
    ) -> dict[str, Any]:
        _require_executor_credential(executor_credential, configured_executor_auth_token)
        validate_control_scope(run_id, attempt_id)
        task = task_state.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
        return {"status": "cancel_requested", "run_id": run_id, "attempt_id": attempt_id}

    return app

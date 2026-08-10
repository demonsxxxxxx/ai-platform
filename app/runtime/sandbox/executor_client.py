import ipaddress
import math
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.runtime.sandbox.contracts import ExecutorTaskRequest
from app.settings import get_settings
from app.tool_permission_lifecycle import tool_permission_budget


PostJson = Callable[..., Awaitable[dict[str, Any]]]
EXECUTOR_CONNECT_BASE_URL_METADATA = "X-AI-Platform-Internal-Executor-Connect-Base-Url"
_MAX_EXECUTOR_HTTP_ERROR_BODY_BYTES = 4096
_GENERIC_EXECUTOR_HTTP_ERROR_CODE = "executor_http_failure"
_EXECUTOR_FAILURE_MILLISECOND_FIELDS = (
    "executor_first_token_latency_ms",
    "executor_tool_call_latency_ms",
    "executor_model_latency_ms",
    "document_processing_latency_ms",
    "artifact_upload_latency_ms",
    "timeout_elapsed_ms",
)
_MAX_EXECUTOR_FAILURE_MILLISECONDS = 86_400_000
_MAX_EXECUTOR_FAILURE_SECONDS = 86_400
_EXECUTOR_HTTP_ERROR_MESSAGES = {
    "executor_auth_not_configured": "Executor authentication is unavailable",
    "invalid_executor_credential": "Executor authentication failed",
    "executor_scope_not_configured": "Executor scope is unavailable",
    "invalid_executor_scope": "Executor scope was rejected",
    "executor_callback_not_configured": "Executor callback is unavailable",
    "invalid_callback_target": "Executor callback target was rejected",
    "executor_runtime_identity_unavailable": "Executor runtime identity is unavailable",
    "executor_request_replayed": "Executor request was already claimed",
}
_EXECUTOR_REPORTED_FAILURE_CODES = frozenset(
    {
        *_EXECUTOR_HTTP_ERROR_MESSAGES,
        "attachment_parser_context_retrieval_unavailable",
        "attachment_parser_file_too_large",
        "attachment_parser_manifest_file_mismatch",
        "attachment_parser_staged_file_invalid",
        "attachment_parser_staging_denied",
        "attachment_parser_staging_failed",
        "attachment_parser_staging_not_authorized",
        "attachment_parser_unsupported",
        "capability_callback_not_acknowledged",
        "claude_agent_sdk_cancelled",
        "claude_agent_sdk_disabled",
        "claude_agent_sdk_missing_structured_terminal",
        "claude_agent_sdk_required",
        "claude_agent_sdk_runtime_error",
        "claude_agent_sdk_selected_skill_hook_failed",
        "claude_agent_sdk_selected_skill_not_authorized",
        "claude_agent_sdk_selected_skill_not_invoked",
        "claude_agent_sdk_timeout",
        "claude_agent_sdk_tool_admission_failed",
        "claude_agent_sdk_turn_limit_exceeded",
        "claude_agent_sdk_unavailable",
        "claude_agent_sdk_upstream_error",
        "context_retrieval_invalid",
        "context_retrieval_registration_failed",
        "context_retrieval_registration_unavailable",
        "context_retrieval_scope_invalid",
        "controlled_skill_authorization_incomplete",
        "controlled_skill_execution_failed",
        "controlled_skill_execution_timeout",
        "controlled_skill_identity_invalid",
        "controlled_skill_input_docx_missing",
        "controlled_skill_input_file_invalid",
        "controlled_skill_input_name_invalid",
        "controlled_skill_input_order_missing",
        "controlled_skill_output_path_invalid",
        "controlled_skill_process_group_unavailable",
        "controlled_skill_runner_missing",
        "controlled_skill_runner_start_failed",
        "controlled_skill_runner_unavailable",
        "executor_cancelled",
        "executor_cleanup_failed",
        "executor_cleanup_timeout",
        "executor_deadline_exceeded",
        "executor_deadline_requires_async_runner",
        "executor_failed",
        "executor_health_timeout",
        "executor_invalid_max_seconds",
        "executor_missing_structured_terminal",
        "executor_reported_failure",
        "executor_runner_failed",
        "executor_system_prompt_invalid",
        "executor_system_prompt_too_large",
    }
)


def _allowlisted_executor_http_error(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    return value if value in _EXECUTOR_REPORTED_FAILURE_CODES else None


def canonical_executor_reported_failure_code(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        return "executor_reported_failure"
    return value if value in _EXECUTOR_REPORTED_FAILURE_CODES else "executor_reported_failure"


def executor_reported_failure_message(error_code: str) -> str:
    if error_code in _EXECUTOR_HTTP_ERROR_MESSAGES:
        return _EXECUTOR_HTTP_ERROR_MESSAGES[error_code]
    return {
        "executor_cancelled": "Executor cancelled",
        "executor_deadline_exceeded": "Executor deadline exceeded",
        "executor_health_timeout": "Executor health timeout",
    }.get(error_code, "Executor reported failure")


def normalize_executor_reported_failure(
    response: dict[str, Any],
    *,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    if str(response.get("status") or "").strip().lower() != "failed":
        return response
    safe_code = canonical_executor_reported_failure_code(response.get("error_code"))
    safe_message = executor_reported_failure_message(safe_code)
    normalized: dict[str, Any] = {
        "status": "failed",
        "error_code": safe_code,
        "error_message": safe_message,
    }
    if expected_run_id is not None and response.get("run_id") == expected_run_id:
        normalized["run_id"] = expected_run_id
    if "message" in response:
        normalized["message"] = safe_message
    if "sdk_error" in response:
        normalized["sdk_error"] = safe_code
    if "detail" in response:
        safe_detail = _allowlisted_executor_http_error(response.get("detail"))
        if safe_detail is not None:
            normalized["detail"] = safe_detail
    if type(response.get("sdk_used")) is bool:
        normalized["sdk_used"] = response["sdk_used"]
    requested_max_seconds = response.get("requested_max_seconds")
    if (
        type(requested_max_seconds) in {int, float}
        and math.isfinite(requested_max_seconds)
        and 0 <= requested_max_seconds <= _MAX_EXECUTOR_FAILURE_SECONDS
    ):
        normalized["requested_max_seconds"] = requested_max_seconds
    for field_name in _EXECUTOR_FAILURE_MILLISECOND_FIELDS:
        value = response.get(field_name)
        if type(value) is int and 0 <= value <= _MAX_EXECUTOR_FAILURE_MILLISECONDS:
            normalized[field_name] = value
    return normalized


class SandboxExecutorHttpError(RuntimeError):
    """A bounded public projection of an executor HTTP failure."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: object = None,
        detail: object = None,
    ) -> None:
        self.status_code = int(status_code)
        safe_error_code = _allowlisted_executor_http_error(error_code)
        safe_detail = _allowlisted_executor_http_error(detail)
        self.error_code = safe_error_code or safe_detail or _GENERIC_EXECUTOR_HTTP_ERROR_CODE
        self.detail = safe_detail
        public_message = (
            "Executor request failed"
            if self.error_code == _GENERIC_EXECUTOR_HTTP_ERROR_CODE
            else executor_reported_failure_message(self.error_code)
        )
        self.public_message = f"{public_message} (HTTP {self.status_code})"
        super().__init__(self.public_message)


def _executor_http_error(response: httpx.Response) -> SandboxExecutorHttpError:
    payload: dict[str, Any] = {}
    if len(response.content) <= _MAX_EXECUTOR_HTTP_ERROR_BODY_BYTES:
        try:
            candidate = response.json()
        except ValueError:
            candidate = None
        if isinstance(candidate, dict):
            payload = candidate
    return SandboxExecutorHttpError(
        status_code=response.status_code,
        error_code=payload.get("error_code"),
        detail=payload.get("detail"),
    )


def prepare_executor_http_request(
    logical_url: str,
    headers: dict[str, str] | None,
) -> tuple[str, dict[str, str]]:
    """Build a pinned executor request without transmitting private connection metadata."""

    private_headers = dict(headers or {})
    connect_base_url = str(private_headers.pop(EXECUTOR_CONNECT_BASE_URL_METADATA, "") or "").strip()
    outgoing_headers = dict(private_headers)
    if not connect_base_url:
        return logical_url, outgoing_headers

    try:
        logical = urlsplit(logical_url)
        connect = urlsplit(connect_base_url)
        connect_ip = ipaddress.ip_address(connect.hostname or "")
        logical_port = logical.port
        connect_port = connect.port
    except ValueError as exc:
        raise ValueError("invalid executor connect metadata") from exc
    if not (
        logical.scheme == "http"
        and connect.scheme == "http"
        and logical.hostname
        and logical_port
        and not logical.username
        and not logical.password
        and connect_ip.version == 4
        and not connect_ip.is_unspecified
        and (connect_ip.is_private or connect_ip.is_loopback)
        and connect_port == logical_port
        and not connect.username
        and not connect.password
        and connect.path in {"", "/"}
        and not connect.query
        and not connect.fragment
    ):
        raise ValueError("invalid executor connect metadata")

    outgoing_headers["Host"] = f"{logical.hostname}:{logical_port}"
    connect_netloc = f"{connect_ip}:{connect_port}"
    return urlunsplit((logical.scheme, connect_netloc, logical.path, logical.query, logical.fragment)), outgoing_headers


async def _default_post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        request_headers = dict(headers or {})
        if request_headers:
            response = await client.post(url, json=payload, headers=request_headers)
        else:
            response = await client.post(url, json=payload)
        if not 200 <= response.status_code < 300:
            raise _executor_http_error(response)
        data = response.json()
    return data if isinstance(data, dict) else {"status": "accepted"}


class SandboxExecutorClient:
    def __init__(self, post_json: PostJson | None = None, timeout_seconds: float | None = None) -> None:
        self._post_json = post_json or _default_post_json
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        executor_url: str,
        request: ExecutorTaskRequest,
        *,
        executor_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logical_url = f"{executor_url.rstrip('/')}/v1/tasks/execute"
        url, outgoing_headers = prepare_executor_http_request(logical_url, executor_headers)
        timeout_seconds = self._timeout_seconds if self._timeout_seconds is not None else _default_timeout_seconds(request)
        return await self._post_json(url, request.model_dump(), timeout_seconds, outgoing_headers)


def _default_timeout_seconds(request: ExecutorTaskRequest | None = None) -> float:
    """Use the normal bounded executor timeout; runtime approval never extends it."""

    settings = get_settings()
    sdk_timeout = float(getattr(settings, "claude_agent_sdk_timeout_seconds", 120.0) or 120.0)
    _ = request
    return tool_permission_budget(sdk_timeout).normal_outer_executor_timeout_seconds

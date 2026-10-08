"""Public-safe normalization of executor-reported failures."""

import math
import re
from typing import Any

from app.sandbox.domain.runtime_diagnostics import normalize_sdk_runtime_diagnostics

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
    "executor_protocol_invalid": "Executor returned an invalid protocol response",
    "model_proxy_forbidden": "Model proxy authorization was rejected",
    "model_proxy_attempt_required": "Model proxy attempt binding is missing",
    "model_proxy_capability_invalid": "Model proxy capability was rejected",
    "model_proxy_path_not_allowed": "Model proxy path was rejected",
    "model_proxy_query_not_allowed": "Model proxy query was rejected",
    "model_proxy_header_duplicate": "Model proxy headers were rejected",
    "model_proxy_anthropic_version_not_allowed": "Model proxy protocol version was rejected",
    "model_proxy_anthropic_beta_not_allowed": "Model proxy protocol option was rejected",
    "model_proxy_run_binding_invalid": "Model proxy run binding was rejected",
    "model_capacity_missing": "Model capacity configuration is unavailable",
    "model_proxy_conversation_mode_invalid": "Model conversation mode was rejected",
    "model_proxy_body_invalid": "Model proxy request was invalid",
    "model_proxy_max_tokens_invalid": "Model token budget was rejected",
    "model_proxy_count_tokens_failed": "Model token counting failed",
    "model_proxy_count_tokens_unavailable": "Model token counting is unavailable",
    "model_proxy_count_tokens_invalid": "Model token counting returned an invalid response",
}
_STRUCTURED_EXECUTOR_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _structured_executor_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if _STRUCTURED_EXECUTOR_ERROR_CODE.fullmatch(value) else None


def canonical_executor_reported_failure_code(value: object) -> str:
    return _structured_executor_error_code(value) or "executor_reported_failure"


def executor_reported_failure_message(error_code: str) -> str:
    if error_code in _EXECUTOR_HTTP_ERROR_MESSAGES:
        return _EXECUTOR_HTTP_ERROR_MESSAGES[error_code]
    return {
        "executor_cancelled": "Executor cancelled",
        "executor_deadline_exceeded": "Executor deadline exceeded",
        "executor_health_timeout": "Executor health timeout",
        "capability_callback_not_acknowledged": "Capability lifecycle callback was not acknowledged",
        "capability_lifecycle_sequence_invalid": "Capability lifecycle sequence was invalid",
        "required_tool_admin_bypass_forbidden": "Required capability cannot bypass authorization",
        "required_tool_completion_evidence_missing": "Required capability completion evidence was missing",
        "required_tool_completion_evidence_mismatch": "Required capability completion evidence was invalid",
        "required_tool_declaration_mismatch": "Required capability declaration was invalid",
        "required_tool_not_currently_authorized": "Required capability was not authorized",
        "required_tool_scope_mismatch": "Required capability scope was invalid",
        "required_tool_unavailable": "Required capability was unavailable",
        "tool_invocation_evidence_mismatch": "Tool invocation evidence was incomplete",
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
    runtime_diagnostics = normalize_sdk_runtime_diagnostics(
        response.get("runtime_diagnostics")
    )
    if runtime_diagnostics:
        normalized["runtime_diagnostics"] = runtime_diagnostics
    if "detail" in response:
        safe_detail = _structured_executor_error_code(response.get("detail"))
        if safe_detail == safe_code:
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
    """A safe public error plus an optional bounded private diagnostic carrier."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: object = None,
        detail: object = None,
        runtime_diagnostics: object = None,
    ) -> None:
        self.status_code = int(status_code)
        safe_error_code = _structured_executor_error_code(error_code)
        safe_detail = _structured_executor_error_code(detail)
        self.error_code = (
            safe_error_code
            or (safe_detail if safe_detail in _EXECUTOR_HTTP_ERROR_MESSAGES else None)
            or _GENERIC_EXECUTOR_HTTP_ERROR_CODE
        )
        self.detail = safe_detail if safe_detail == self.error_code else None
        public_message = (
            "Executor request failed"
            if self.error_code == _GENERIC_EXECUTOR_HTTP_ERROR_CODE
            else executor_reported_failure_message(self.error_code)
        )
        self.public_message = f"{public_message} (HTTP {self.status_code})"
        self.runtime_diagnostics = (
            normalize_sdk_runtime_diagnostics(runtime_diagnostics)
            if runtime_diagnostics is not None
            else None
        )
        super().__init__(self.public_message)

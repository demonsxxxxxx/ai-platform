from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.control_plane_contracts import normalize_thinking_effort
from app.mcp.api import assert_mcp_tool_reference
from app.persistence_limits import RUN_RESULT_MAX_BYTES, ensure_json_size
from app.runtime.kernel_contracts import AgentEvent
from app.tool_permission_lifecycle import TOOL_PERMISSION_REQUEST_TTL_SECONDS
from app.sandbox.api import AssistantAnswerReceipt
from app.validation import (
    MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS,
    assert_safe_id,
    assert_safe_principal_user_id,
)


SandboxMode = Literal["ephemeral", "persistent"]
ContainerProviderName = Literal["fake", "docker", "opensandbox"]
CallbackStatus = Literal["running", "completed", "failed", "cancelled"]
TerminalCallbackStatus = Literal["completed", "failed", "cancelled"]
EXECUTOR_AUTH_HEADER = "X-AI-Platform-Executor-Credential"
EXECUTOR_CALLBACK_PATH = "/api/ai/runtime/callbacks/executor"
EXECUTOR_TOOL_PERMISSION_CALLBACK_PATH = "/api/ai/runtime/callbacks/tool-permission"
EXECUTOR_CONTEXT_RETRIEVAL_CALLBACK_PATH = "/api/ai/runtime/callbacks/context-retrieval"
EXECUTOR_PROVIDER_SESSION_CALLBACK_PATH = "/api/ai/runtime/callbacks/provider-session"
PROFILE_DRIVE_STAGE_TOOL = "stage_profile_drive_file_to_workspace"
PROFILE_DRIVE_STAGE_IDENTITY = f"mcp__ai-platform-context__{PROFILE_DRIVE_STAGE_TOOL}"
PROFILE_DRIVE_READ_TEXT_IDENTITY = "mcp__ProfileDriveMCPServer__read_text_file"
PROFILE_DRIVE_STAGE_MAX_BYTES = 512 * 1024 * 1024
PROFILE_DRIVE_STAGE_LEASE_FLAG = "profile_drive_file_staging_authorized"
_TRUSTED_CALLBACK_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "gateway.docker.internal",
}
_TRUSTED_CALLBACK_SUFFIXES = (".test", ".localhost", ".invalid", ".internal")
_TRUSTED_CALLBACK_PORTS = {80, 443, 8000, 8020, 18043, 18443}
_PUBLIC_SKILL_METADATA_FIELDS = {"name", "version", "availability"}
_PUBLIC_SKILL_AVAILABILITIES = {
    "available",
    "unavailable_dependency",
    "unavailable_materialization",
}


def _validated_public_skill_metadata(
    value: object,
    *,
    skill_ids: object,
) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("public_skill_metadata_invalid")
    authorized_skill_ids = {
        item for item in skill_ids if isinstance(item, str)
    } if isinstance(skill_ids, list) else set()
    validated: dict[str, dict[str, str]] = {}
    for raw_skill_id, raw_metadata in value.items():
        if not isinstance(raw_skill_id, str):
            raise ValueError("public_skill_metadata_invalid")
        skill_id = assert_safe_id(raw_skill_id, "public_skill_metadata")
        if skill_id not in authorized_skill_ids or not isinstance(raw_metadata, dict):
            raise ValueError("public_skill_metadata_invalid")
        if set(raw_metadata) != _PUBLIC_SKILL_METADATA_FIELDS:
            raise ValueError("public_skill_metadata_invalid")
        name = raw_metadata.get("name")
        version = raw_metadata.get("version")
        availability = raw_metadata.get("availability")
        if (
            not isinstance(name, str)
            or not name
            or not name.isprintable()
            or len(name.encode("utf-8")) > 256
            or not isinstance(version, str)
            or not version
            or not version.isprintable()
            or len(version.encode("utf-8")) > 128
            or not isinstance(availability, str)
            or availability not in _PUBLIC_SKILL_AVAILABILITIES
        ):
            raise ValueError("public_skill_metadata_invalid")
        validated[skill_id] = {
            "name": name,
            "version": version,
            "availability": availability,
        }
    return validated


def executor_callback_receipt_event_count(*, input_event_count: int) -> int:
    """Count one callback envelope plus the sender's immutable input events."""

    if type(input_event_count) is not int or input_event_count < 0:
        raise ValueError("executor callback input event count must be non-negative")
    return 1 + input_event_count


class CallbackTargetValidationError(ValueError):
    """Raised when a callback base URL violates the sandbox trusted-target policy."""

    pass


@dataclass(frozen=True)
class TrustedCallbackTarget:
    """Normalized platform callback endpoints derived from a trusted base URL."""

    base_url: str
    callback_url: str
    tool_permission_url: str
    context_retrieval_url: str
    provider_session_url: str
    host: str


def _normalize_host(host: str) -> str:
    return str(host or "").strip().lower().rstrip(".")


def _is_ipv6_host(host: str) -> bool:
    return ":" in host and not host.startswith("[")


def _trusted_callback_netloc(host: str, port: int | None) -> str:
    formatted_host = f"[{host}]" if _is_ipv6_host(host) else host
    return f"{formatted_host}:{port}" if port is not None else formatted_host


def is_trusted_callback_host(host: str, *, extra_hosts: Iterable[str] = ()) -> bool:
    """Return true only for internal callback hosts explicitly allowed by policy."""

    normalized = _normalize_host(host)
    if not normalized:
        return False
    if normalized in _TRUSTED_CALLBACK_HOSTS:
        return True
    if normalized.endswith(_TRUSTED_CALLBACK_SUFFIXES):
        return True
    normalized_extra_hosts = {_normalize_host(item) for item in extra_hosts if str(item or "").strip()}
    if normalized in normalized_extra_hosts:
        return True
    try:
        parsed_ip = ip_address(normalized)
    except ValueError:
        return False
    if parsed_ip.is_link_local or parsed_ip.is_multicast or parsed_ip.is_unspecified or parsed_ip.is_reserved:
        return False
    return parsed_ip.is_loopback or parsed_ip.is_private


def build_trusted_callback_target(
    base_url: str,
    *,
    extra_hosts: Iterable[str] = (),
) -> TrustedCallbackTarget:
    """Validate and normalize the platform callback base URL for sandbox use."""

    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise CallbackTargetValidationError("callback scheme must be http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CallbackTargetValidationError("callback url must not include credentials, query, or fragment")
    host = _normalize_host(parsed.hostname or "")
    if not is_trusted_callback_host(host, extra_hosts=extra_hosts):
        raise CallbackTargetValidationError("callback host is not in the trusted allowlist")
    if parsed.path not in {"", "/"}:
        raise CallbackTargetValidationError("callback base url must not include a path prefix")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CallbackTargetValidationError("callback port is invalid") from exc
    effective_port = port if port is not None else 443 if parsed.scheme == "https" else 80
    if effective_port not in _TRUSTED_CALLBACK_PORTS:
        raise CallbackTargetValidationError("callback port is not in the trusted allowlist")
    normalized_base_url = urlunsplit((parsed.scheme, _trusted_callback_netloc(host, port), "", "", ""))
    return TrustedCallbackTarget(
        base_url=normalized_base_url,
        callback_url=f"{normalized_base_url}{EXECUTOR_CALLBACK_PATH}",
        tool_permission_url=f"{normalized_base_url}{EXECUTOR_TOOL_PERMISSION_CALLBACK_PATH}",
        context_retrieval_url=f"{normalized_base_url}{EXECUTOR_CONTEXT_RETRIEVAL_CALLBACK_PATH}",
        provider_session_url=f"{normalized_base_url}{EXECUTOR_PROVIDER_SESSION_CALLBACK_PATH}",
        host=host,
    )


class ProviderSessionCallbackRequest(BaseModel):
    """Private callback envelope for the opaque Claude SessionStore mirror."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["load", "append", "list_subkeys"]
    run_id: str
    attempt_id: str
    callback_token_id: str
    provider_session_id: str = Field(min_length=1, max_length=128)
    subpath: str | None = Field(default=None, max_length=512)
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    expected_sequence: int | None = Field(default=None, ge=1)

    @field_validator("run_id", "attempt_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action == "append" and (not self.entries or self.expected_sequence is None):
            raise ValueError("provider_session_append_sequence_required")
        if self.action != "append" and (self.entries or self.expected_sequence is not None):
            raise ValueError("provider_session_append_fields_forbidden")
        return self


class ProviderSessionCallbackResponse(BaseModel):
    """Private callback receipt carrying no platform scope claims."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["load", "append", "list_subkeys"]
    entries: list[dict[str, Any]] = Field(default_factory=list)
    subpaths: list[str] = Field(default_factory=list, max_length=4096)
    accepted: bool = True
    entry_count: int = Field(default=0, ge=0)
    next_sequence: int = Field(ge=1)
    last_sequence: int | None = Field(default=None, ge=1)


class ContextRetrievalScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id", "agent_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)


class ModelTokenLimits(BaseModel):
    """Run-frozen model budget accepted by the sandbox transport."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_input_tokens: int = Field(gt=0, le=10_000_000)
    max_output_tokens: int = Field(gt=0, le=10_000_000)


class SandboxRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    attempt_id: str
    owner_generation: int = Field(default=1, ge=1)
    agent_id: str
    skill_ids: list[str] = Field(default_factory=list)
    public_skill_metadata: dict[str, dict[str, str]] = Field(default_factory=dict)
    mcp_tool_ids: list[str] = Field(default_factory=list)
    tool_policy_subjects: list[dict[str, Any]] = Field(default_factory=list)
    input_message: str
    system_prompt: str = Field(default="", max_length=MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS)
    file_ids: list[str] = Field(default_factory=list)
    materialized_file_names: list[str] = Field(default_factory=list)
    sandbox_mode: SandboxMode
    browser_enabled: bool = False
    model: str
    model_token_limits: ModelTokenLimits | None = None
    thinking_effort: str = "auto"
    model_gateway: Literal["new-api"] = "new-api"
    permissions: list[str] = Field(default_factory=list)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    queue_wait_ms: int = Field(default=0, ge=0)
    trace_id: str = ""
    callback_url: str
    callback_token_id: str
    context_manifest: dict[str, Any] = Field(default_factory=dict)
    context_retrieval_scope: ContextRetrievalScope | None = None
    sdk_session_id: str | None = None
    provider_session_resume_required: bool = False
    governed_permission_wait: bool = False
    reconciliation_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id", "attempt_id", "agent_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)

    @field_validator("skill_ids", "file_ids")
    @classmethod
    def validate_list_ids(cls, values: list[str], info):
        return [assert_safe_id(value, info.field_name) for value in values]

    @field_validator("public_skill_metadata", mode="before")
    @classmethod
    def validate_public_skill_metadata(cls, value: object, info):
        return _validated_public_skill_metadata(
            value,
            skill_ids=info.data.get("skill_ids", []),
        )

    @field_validator("mcp_tool_ids")
    @classmethod
    def validate_mcp_tool_ids(cls, values: list[str]):
        # The code-owned RAGFlow capability is the sole retained legacy reference.
        return [
            value
            if value == "ragflow-knowledge-search"
            else assert_mcp_tool_reference(value)
            for value in values
        ]

    @field_validator("trace_id")
    @classmethod
    def validate_optional_trace_id(cls, value: str):
        return assert_safe_id(value, "trace_id") if value else value

    @field_validator("thinking_effort")
    @classmethod
    def validate_thinking_effort(cls, value: str):
        return normalize_thinking_effort(value)

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value


class WorkspaceLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    host_root: str
    workspace_host_path: str
    workspace_container_path: str = "/workspace"
    inputs_host_path: str
    logs_host_path: str

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)

    def user_visible_payload(self) -> dict[str, str]:
        return {
            "workspace": self.workspace_container_path,
            "inputs": f"{self.workspace_container_path}/inputs",
        }


class ContainerLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str
    container_name: str
    provider: ContainerProviderName
    executor_url: str
    executor_headers: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    sandbox_mode: SandboxMode
    browser_enabled: bool
    workspace_host_path: str
    workspace_container_path: str = "/workspace"
    labels: dict[str, str] = Field(default_factory=dict)
    timings: dict[str, int] = Field(default_factory=dict)

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)

    def platform_labels(self) -> dict[str, str]:
        labels = dict(self.labels)
        labels.update(
            {
                "ai-platform.owner": "sandbox-runtime",
                "ai-platform.tenant_id": self.tenant_id,
                "ai-platform.workspace_id": self.workspace_id,
                "ai-platform.user_id": self.user_id,
                "ai-platform.session_id": self.session_id,
                "ai-platform.run_id": self.run_id,
                "ai-platform.sandbox_mode": self.sandbox_mode,
                "ai-platform.browser_enabled": "true" if self.browser_enabled else "false",
            }
        )
        return labels


class ContainerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str
    container_name: str
    provider: ContainerProviderName
    status: str
    tenant_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    sandbox_mode: SandboxMode | None = None
    browser_enabled: bool = False
    executor_url: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None, info):
        return assert_safe_id(value, info.field_name) if value else value

    @field_validator("user_id")
    @classmethod
    def validate_optional_user_id(cls, value: str | None):
        return assert_safe_principal_user_id(value) if value else value


class StopResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str
    status: Literal["stopped", "not_found", "failed"]
    message: str = ""


class ExecutorTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    attempt_id: str
    prompt: str
    callback_url: str
    callback_token_id: str
    callback_token: str
    callback_base_url: str
    sdk_session_id: str | None = None
    permission_mode: Literal["default", "plan", "acceptEdits", "bypassPermissions"] = "default"
    governed_permission_wait: bool = False
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def callback_target(self) -> TrustedCallbackTarget:
        return build_trusted_callback_target(self.callback_base_url)

    @field_validator(
        "tenant_id",
        "workspace_id",
        "session_id",
        "run_id",
        "attempt_id",
        "callback_token_id",
    )
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]):
        if "thinking_effort" in value:
            normalize_thinking_effort(value["thinking_effort"])
        if "model_token_limits" in value:
            try:
                ModelTokenLimits.model_validate(value["model_token_limits"])
            except Exception as exc:
                raise ValueError("model_token_limits_invalid") from exc
        if "public_skill_metadata" in value:
            value = dict(value)
            value["public_skill_metadata"] = _validated_public_skill_metadata(
                value["public_skill_metadata"],
                skill_ids=value.get("skill_ids", []),
            )
        return value

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value


class ExecutorTaskDispatchReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    run_id: str
    attempt_id: str

    @field_validator("run_id", "attempt_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return assert_safe_id(value, str(info.field_name))


class ResponseFileDescriptor(BaseModel):
    """Presentation metadata for one explicitly declared response file."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(max_length=1_024)
    display_name: str | None = Field(default=None, max_length=255)
    role: Literal["primary", "supporting"] | None = None
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        if not value or "\x00" in value:
            raise ValueError("response_file_path_invalid")
        path = PurePosixPath(value.replace("\\", "/"))
        windows_path = PureWindowsPath(value)
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/"))
        ):
            raise ValueError("response_file_path_invalid")
        return path.as_posix()

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "\x00" in normalized
            or "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
        ):
            raise ValueError("response_file_display_name_invalid")
        return normalized

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("response_file_description_invalid")
        return value.strip() or None


class ExecutorTerminalResult(BaseModel):
    """Authoritative terminal response returned through the callback channel."""

    model_config = ConfigDict(extra="allow")

    status: Literal["completed", "succeeded", "failed", "cancelled", "canceled"]
    run_id: str
    message: str = Field(default="", max_length=200_000)
    answer_receipt: AssistantAnswerReceipt | None = None
    response_files: list[str] = Field(default_factory=list, max_length=128)
    response_file_descriptors: list[ResponseFileDescriptor] | None = Field(
        default=None,
        max_length=128,
    )
    error_code: str | None = Field(default=None, max_length=256)
    error_message: str | None = Field(default=None, max_length=4_096)
    provider_session_final_sequence: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("answer_receipt", mode="before")
    @classmethod
    def validate_answer_receipt(cls, value: object):
        return None if value is None else AssistantAnswerReceipt.model_validate(value)

    @field_validator("response_files")
    @classmethod
    def validate_response_files(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 1024 or "\x00" in value:
                raise ValueError("response_file_path_invalid")
            path = PurePosixPath(value.replace("\\", "/"))
            windows_path = PureWindowsPath(value)
            if (
                path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/"))
            ):
                raise ValueError("response_file_path_invalid")
            canonical = path.as_posix()
            if canonical in normalized:
                raise ValueError("response_file_path_duplicate")
            normalized.append(canonical)
        return normalized

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return assert_safe_id(value, "run_id")

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "ExecutorTerminalResult":
        if self.status in {"completed", "succeeded"}:
            if self.answer_receipt is None and not self.message.strip():
                raise ValueError(
                    "successful terminal result requires a non-empty message or answer receipt"
                )
            if self.answer_receipt is not None and self.message != "":
                raise ValueError(
                    "successful terminal result must contain either a message or answer receipt"
                )
            if self.response_file_descriptors and [
                item.source_path for item in self.response_file_descriptors
            ] != self.response_files:
                raise ValueError(
                    "response file descriptors must match response files"
                )
        else:
            if self.answer_receipt is not None:
                raise ValueError(
                    "failed or cancelled terminal result must not contain an answer receipt"
                )
            if self.response_files:
                raise ValueError(
                    "failed or cancelled terminal result must not contain response files"
                )
            if self.response_file_descriptors:
                raise ValueError(
                    "failed or cancelled terminal result must not contain response file descriptors"
                )
            if not str(self.error_code or "").strip() or not str(self.error_message or "").strip():
                raise ValueError("failed or cancelled terminal result requires structured error fields")
        return self


_EXECUTOR_TERMINAL_RECEIPT_FIELDS = frozenset(
    {
        "status",
        "run_id",
        "message",
        "answer_receipt",
        "response_files",
        "response_file_descriptors",
        "provider_session_final_sequence",
        "error_code",
        "error_message",
        "executor_model_latency_ms",
        "document_processing_latency_ms",
        "executor_first_token_latency_ms",
        "executor_tool_call_latency_ms",
        "artifact_upload_latency_ms",
        "timeout_elapsed_ms",
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
        "required_capability_evidence",
        "tool_invocation_evidence",
        "callback_errors",
        "diagnostics",
    }
)


def executor_terminal_receipt_payload(
    value: ExecutorTerminalResult | dict[str, Any],
) -> dict[str, Any]:
    """Persist only the bounded reconciliation contract, excluding private diagnostics."""

    raw = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, ExecutorTerminalResult)
        else dict(value)
    )
    receipt = {
        key: _without_runtime_diagnostics(raw[key])
        for key in _EXECUTOR_TERMINAL_RECEIPT_FIELDS
        if key in raw
    }
    ensure_json_size(
        receipt,
        max_bytes=RUN_RESULT_MAX_BYTES,
        code="executor_terminal_receipt_too_large",
    )
    return receipt


def _without_runtime_diagnostics(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_runtime_diagnostics(item)
            for key, item in value.items()
            if str(key) != "runtime_diagnostics"
        }
    if isinstance(value, list):
        return [_without_runtime_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_runtime_diagnostics(item) for item in value)
    return value


def normalize_executor_terminal_status(
    task_status: object,
    result_status: object,
) -> TerminalCallbackStatus | None:
    if not isinstance(task_status, str) or not isinstance(result_status, str):
        return None
    aliases: dict[str, TerminalCallbackStatus] = {
        "completed": "completed",
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    normalized_result = aliases.get(result_status.strip().lower())
    if normalized_result is None:
        return None
    normalized_task = task_status.strip().lower()
    if normalized_task == "callback_failed":
        return normalized_result
    if aliases.get(normalized_task) == normalized_result:
        return normalized_result
    return None


class ExecutorCallbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    attempt_id: str
    callback_token_id: str
    batch_id: str | None = None
    status: CallbackStatus
    progress: int = Field(ge=0, le=100)
    new_message: dict[str, Any] | None = None
    state_patch: dict[str, Any] = Field(default_factory=dict)
    sdk_session_id: str | None = None
    error_message: str | None = None
    events: list[AgentEvent] = Field(default_factory=list, max_length=100)
    terminal_result: ExecutorTerminalResult | None = None

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "ExecutorCallbackEvent":
        terminal = self.status in {"completed", "failed", "cancelled"}
        if terminal != (self.terminal_result is not None):
            raise ValueError("terminal_result must be present only for terminal callbacks")
        if self.terminal_result is None:
            return self
        if self.terminal_result.run_id != self.run_id:
            raise ValueError("terminal_result run_id must match callback run_id")
        if (
            normalize_executor_terminal_status(
                self.status,
                self.terminal_result.status,
            )
            is None
        ):
            raise ValueError("terminal callback status must match terminal result")
        return self

    @field_validator("session_id", "run_id", "attempt_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("batch_id")
    @classmethod
    def validate_optional_batch_id(cls, value: str | None):
        return assert_safe_id(value, "batch_id") if value is not None else value

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value


class ExecutorContextRetrievalRequest(BaseModel):
    """One snapshot-scoped retrieval request from an ephemeral executor."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    attempt_id: str
    callback_token_id: str
    action: Literal[
        "read_run_artifact",
        "stage_context_file_to_workspace",
        "stage_run_artifact_to_workspace",
        "stage_profile_drive_file_to_workspace",
        "search_memory",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "run_id", "attempt_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)


class ExecutorToolPermissionRequest(BaseModel):
    """Sandbox executor callback payload for brokered Claude SDK tool permissions."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    attempt_id: str
    callback_token_id: str
    sdk_session_id: str | None = None
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str = ""
    action: str = "execute"
    risk_level: str = "high"
    write_capable: bool = True
    reason: str = "Claude SDK tool permission required"
    permission_wait_seconds: float | None = Field(default=None, ge=0, le=TOOL_PERMISSION_REQUEST_TTL_SECONDS)

    @field_validator("session_id", "run_id", "attempt_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value

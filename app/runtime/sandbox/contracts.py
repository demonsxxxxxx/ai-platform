from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.runtime.kernel_contracts import AgentEvent
from app.validation import assert_safe_id


SandboxMode = Literal["ephemeral", "persistent"]
ContainerProviderName = Literal["fake", "docker", "opensandbox"]
CallbackStatus = Literal["running", "completed", "failed", "cancelled"]


class SandboxRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str
    skill_ids: list[str] = Field(default_factory=list)
    mcp_tool_ids: list[str] = Field(default_factory=list)
    input_message: str
    file_ids: list[str] = Field(default_factory=list)
    sandbox_mode: SandboxMode
    browser_enabled: bool = False
    model: str
    model_gateway: Literal["new-api"] = "new-api"
    permissions: list[str] = Field(default_factory=list)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    queue_wait_ms: int = Field(default=0, ge=0)
    callback_url: str
    callback_token_id: str
    sdk_session_id: str | None = None

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "agent_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("skill_ids", "mcp_tool_ids", "file_ids")
    @classmethod
    def validate_list_ids(cls, values: list[str], info):
        return [assert_safe_id(value, info.field_name) for value in values]

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

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

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

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

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

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None, info):
        return assert_safe_id(value, info.field_name) if value else value


class StopResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str
    status: Literal["stopped", "not_found", "failed"]
    message: str = ""


class ExecutorTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    prompt: str
    callback_url: str
    callback_token_id: str
    callback_token: str
    callback_base_url: str
    sdk_session_id: str | None = None
    permission_mode: Literal["default", "plan", "acceptEdits", "bypassPermissions"] = "default"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "run_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value


class ExecutorCallbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    callback_token_id: str
    status: CallbackStatus
    progress: int = Field(ge=0, le=100)
    new_message: dict[str, Any] | None = None
    state_patch: dict[str, Any] = Field(default_factory=dict)
    sdk_session_id: str | None = None
    error_message: str | None = None
    events: list[AgentEvent] = Field(default_factory=list)

    @field_validator("session_id", "run_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value


class ExecutorToolPermissionRequest(BaseModel):
    """Sandbox executor callback payload for brokered Claude SDK tool permissions."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    callback_token_id: str
    sdk_session_id: str | None = None
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str = ""
    action: str = "execute"
    risk_level: str = "high"
    write_capable: bool = True
    reason: str = "Claude SDK tool permission required"

    @field_validator("session_id", "run_id", "callback_token_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("sdk_session_id")
    @classmethod
    def validate_optional_sdk_session_id(cls, value: str | None):
        return assert_safe_id(value, "sdk_session_id") if value else value

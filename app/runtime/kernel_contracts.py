from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.validation import assert_safe_id, assert_safe_principal_user_id


SUPPORTED_AGENT_EVENT_TYPES = {
    "run_queued",
    "run_started",
    "runtime_container_started",
    "assistant_delta",
    "tool_call_started",
    "tool_call_delta",
    "tool_call_completed",
    "tool_permission_requested",
    "tool_permission_authorized",
    "tool_permission_denied",
    "browser_snapshot",
    "workspace_file_changed",
    "artifact_created",
    "checkpoint_created",
    "subagent_started",
    "subagent_completed",
    "subagent_failed",
    "agent_step_started",
    "agent_step_reused",
    "agent_step_completed",
    "agent_step_blocked",
    "agent_step_failed",
    "run_failed",
    "run_completed",
    "run_cancelled",
}


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str
    skill_ids: list[str] = Field(default_factory=list)
    mcp_tool_ids: list[str] = Field(default_factory=list)
    model: str
    model_gateway: str = "new-api"
    input_message: str = ""
    file_ids: list[str] = Field(default_factory=list)
    sandbox_mode: str
    browser_enabled: bool = False
    permissions: list[str] = Field(default_factory=list)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "workspace_id", "session_id", "run_id", "agent_id")
    @classmethod
    def validate_required_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        return assert_safe_principal_user_id(value)

    @field_validator("skill_ids", "mcp_tool_ids", "file_ids")
    @classmethod
    def validate_list_ids(cls, values: list[str], info):
        return [assert_safe_id(value, info.field_name) for value in values]

    @field_validator("model_gateway")
    @classmethod
    def validate_model_gateway(cls, value: str):
        if value != "new-api":
            raise ValueError("model_gateway must be new-api")
        return value

    @field_validator("sandbox_mode")
    @classmethod
    def validate_sandbox_mode(cls, value: str):
        if value not in {"none", "ephemeral", "persistent"}:
            raise ValueError("sandbox_mode must be one of none, ephemeral, persistent")
        return value


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    admin_only: bool = False

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str):
        if value not in SUPPORTED_AGENT_EVENT_TYPES:
            raise ValueError(f"Unsupported agent event type: {value}")
        return value


def artifact_storage_prefix(context: RunContext) -> str:
    return (
        f"tenants/{context.tenant_id}"
        f"/workspaces/{context.workspace_id}"
        f"/sessions/{context.session_id}"
        f"/runs/{context.run_id}"
    )

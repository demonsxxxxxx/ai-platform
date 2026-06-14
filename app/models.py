from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.control_plane_contracts import RUN_PAYLOAD_SCHEMA_VERSION
from app.skills.release_policy import validate_release_decision_lock, validate_release_decision_payload

from app.validation import assert_safe_id


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = "default"
    workspace_id: str = "default"
    user_id: str | None = None
    agent_id: str
    capability_id: str | None = None
    skill_id: str | None = None
    session_id: str | None = None
    title: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    file_ids: list[str] = Field(default_factory=list)

    @field_validator("tenant_id", "workspace_id", "agent_id")
    @classmethod
    def validate_required_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("capability_id", "skill_id")
    @classmethod
    def validate_optional_selector_ids(cls, value: str | None, info):
        return assert_safe_id(value, info.field_name) if value else value

    @field_validator("user_id")
    @classmethod
    def validate_optional_user_id(cls, value: str | None):
        return assert_safe_id(value, "user_id") if value else value

    @field_validator("session_id")
    @classmethod
    def validate_optional_session_id(cls, value: str | None):
        if value is None:
            return value
        return assert_safe_id(value, "session_id")

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, value: list[str]):
        return [assert_safe_id(item, "file_ids") for item in value]


class CreateRunResponse(BaseModel):
    run_id: str
    session_id: str
    status: Literal["queued"]


class RunControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str | None = None
    status: str
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None


class MultiAgentDispatchClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str

    @field_validator("step_key")
    @classmethod
    def validate_step_key(cls, value: str):
        return assert_safe_id(value, "step_key")


class MultiAgentDispatchClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    run_id: str
    step_key: str
    step_id: str
    status: Literal["claimed"]
    dispatch_id: str
    event_id: str
    audit_id: str
    step: dict[str, Any]


class MultiAgentDispatchHandoffResponse(BaseModel):
    """Admin response for turning a claimed dispatch step into a queued child run."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    parent_run_id: str
    dispatch_id: str
    step_key: str
    step_id: str
    status: Literal["queued"]
    child_run_id: str
    session_id: str
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None
    event_id: str
    child_event_id: str
    audit_id: str


class MultiAgentDispatchTickResponse(BaseModel):
    """Admin response for one bounded multi-agent dispatch tick."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    parent_run_id: str
    dispatch_id: str
    step_key: str
    step_id: str
    status: Literal["queued"]
    child_run_id: str
    session_id: str
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None
    claim_event_id: str
    claim_audit_id: str
    handoff_event_id: str
    child_event_id: str
    handoff_audit_id: str


class AgentApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str
    name: str
    mode: Literal["chat", "file", "chat_file"]
    default_skill_id: str
    allowed_input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    status: Literal["active", "disabled"] = "active"


class AgentAppProjection(AgentApp):
    pass


class AgentAppsResponse(BaseModel):
    agent_apps: list[AgentAppProjection]


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    version: str
    executor_type: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "disabled"] = "active"


class RunResponse(BaseModel):
    run_id: str
    session_id: str
    agent_id: str
    skill_id: str | None = None
    capability_id: str | None = None
    trace_id: str = ""
    contract_version: str = ""
    executor_schema_version: str | None = None
    status: str
    progress: int = 0
    input: dict[str, Any]
    result: dict[str, Any]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None
    cancel_requested_at: Any | None = None
    cancel_requested_by: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class RunEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    event_id: str
    sequence: int = 0
    run_id: str
    trace_id: str = ""
    type: str
    stage: str
    message: str = ""
    severity: Literal["info", "warning", "error"] = "info"
    visible_to_user: bool = True
    error_code: str | None = None
    latency_ms: int | None = None
    token_counts: dict[str, int] = Field(default_factory=dict)
    cost: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: Any | None = None


class ContextSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_kind: Literal["executor", "replay", "resume"] = "executor"
    included_message_ids: list[str] = Field(default_factory=list)
    included_file_ids: list[str] = Field(default_factory=list)
    included_artifact_ids: list[str] = Field(default_factory=list)
    included_memory_record_ids: list[str] = Field(default_factory=list)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("included_message_ids", "included_file_ids", "included_artifact_ids", "included_memory_record_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: list[str], info):
        return [assert_safe_id(item, info.field_name) for item in value]


class MemoryRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    agent_id: str | None = None
    session_id: str | None = None
    record_type: Literal["session_summary", "user_preference", "task_note"] = "session_summary"
    content: str = Field(min_length=1, max_length=16000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("workspace_id")
    @classmethod
    def validate_memory_workspace_id(cls, value: str):
        return assert_safe_id(value, "workspace_id")

    @field_validator("agent_id", "session_id")
    @classmethod
    def validate_optional_memory_ids(cls, value: str | None, info):
        return assert_safe_id(value, info.field_name) if value else value


class MemoryPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    agent_id: str | None = None
    memory_enabled: bool = True
    long_term_memory_enabled: bool = False
    retention_days: int = Field(default=90, ge=1, le=3650)
    redaction_mode: Literal["standard", "strict"] = "standard"
    reason: str = Field(default="", max_length=2000)

    @field_validator("workspace_id")
    @classmethod
    def validate_policy_workspace_id(cls, value: str):
        return assert_safe_id(value, "workspace_id")

    @field_validator("agent_id")
    @classmethod
    def validate_policy_agent_id(cls, value: str | None):
        return assert_safe_id(value, "agent_id") if value else value


class MemoryRedactionPreviewRequest(BaseModel):
    """Admin-only request for previewing memory redaction output without persistence."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    agent_id: str | None = None
    redaction_mode: Literal["standard", "strict"] = "standard"
    content: str = Field(default="", max_length=16000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)

    @field_validator("workspace_id")
    @classmethod
    def validate_preview_workspace_id(cls, value: str):
        return assert_safe_id(value, "workspace_id")

    @field_validator("agent_id")
    @classmethod
    def validate_preview_agent_id(cls, value: str | None):
        return assert_safe_id(value, "agent_id") if value else value


class ToolPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    tool_call_id: str
    action: str = Field(default="execute", min_length=1, max_length=80)
    risk_level: Literal["low", "medium", "high"] = "low"
    write_capable: bool = False
    reason: str = Field(default="", max_length=2000)
    request_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_id", "tool_call_id")
    @classmethod
    def validate_tool_permission_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)


class ToolPermissionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow_once", "deny", "allow_for_run"]
    reason: str = Field(default="", max_length=2000)
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    expires_in_seconds: int = Field(default=900, ge=30, le=86400)


class AdminToolPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"] = "disabled"
    risk_level: Literal["low", "medium", "high"] = "low"
    write_capable: bool = False
    visible_to_user: bool = True
    reason: str = Field(default="", max_length=2000)


class SandboxLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_mode: Literal["ephemeral", "persistent"]
    provider: Literal["fake", "docker"] = "fake"
    browser_enabled: bool = False
    ttl_seconds: int = Field(default=1800, ge=30, le=86400)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    lease_payload: dict[str, Any] = Field(default_factory=dict)


class SandboxLeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int = Field(default=1800, ge=30, le=86400)


class SandboxLeaseReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="released", max_length=200)


class ArtifactCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_type: str
    label: str
    content_type: str
    size_bytes: int
    download_url: str
    preview_url: str | None = None
    status: Literal["available", "failed"] = "available"
    lineage: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    created_at: Any | None = None


class UploadFileResponse(BaseModel):
    file_id: str
    sha256: str
    size_bytes: int


class QueueRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str
    skill_id: str
    file_ids: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    executor_type: str
    skill_version: str | None = None
    release_decision: dict[str, Any] = Field(default_factory=dict)
    skill_manifests: list[dict[str, Any]] = Field(default_factory=list)
    context_snapshot_id: str | None = None
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = RUN_PAYLOAD_SCHEMA_VERSION

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "agent_id", "skill_id", "executor_type")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("context_snapshot_id")
    @classmethod
    def validate_optional_context_snapshot_id(cls, value: str | None):
        return assert_safe_id(value, "context_snapshot_id") if value else value

    @field_validator("file_ids")
    @classmethod
    def validate_queue_file_ids(cls, value: list[str]):
        return [assert_safe_id(item, "file_ids") for item in value]

    @field_validator("release_decision")
    @classmethod
    def validate_release_decision(cls, value: dict[str, Any]):
        return validate_release_decision_payload(value)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str):
        if value != RUN_PAYLOAD_SCHEMA_VERSION:
            raise ValueError("run_payload_schema_version_invalid")
        return value

    @model_validator(mode="after")
    def validate_release_decision_matches_skill_version(self):
        validate_release_decision_lock(
            release_decision=self.release_decision,
            skill_version=self.skill_version,
            skill_id=self.skill_id,
            skill_manifests=self.skill_manifests,
        )
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_name: str = Field(min_length=1, validation_alias=AliasChoices("user_name", "username"))
    password: str = Field(min_length=1)


class PrincipalResponse(BaseModel):
    user_id: str
    user_name: str = ""
    display_name: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    is_admin: bool = False
    source: str = ""


class CapabilitySuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    label: str
    reason: str


class IntentDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["selected", "needs_confirmation"]
    intent: str
    confidence: float
    reason: str
    selected_capability: str | None = None
    agent_id: str | None = None
    skill_id: str | None = None
    confirmed_by_user: bool = False
    suggestions: list[CapabilitySuggestionResponse] = Field(default_factory=list)


class ChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    agent_id: str
    title: str = ""

    @field_validator("workspace_id", "agent_id")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)


class ChatSessionResponse(BaseModel):
    session_id: str
    workspace_id: str
    agent_id: str
    title: str
    created_at: Any | None = None
    updated_at: Any | None = None


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionResponse]


class ChatMessageResponse(BaseModel):
    message_id: str
    session_id: str
    run_id: str | None = None
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Any | None = None


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageResponse]


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = "default"
    session_id: str | None = None
    agent_id: str | None = None
    skill_id: str | None = None
    message: str = Field(min_length=1)
    file_ids: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    agent_options: dict[str, bool | str | int | float] | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    disabled_skills: list[str] = Field(default_factory=list)
    enabled_skills: list[str] | None = None
    disabled_mcp_tools: list[str] = Field(default_factory=list)
    persona_preset_id: str | None = None
    user_timezone: str | None = None
    confirmed_capability_id: str | None = None

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("agent_id", "skill_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None, info):
        return assert_safe_id(value, info.field_name) if value else value

    @field_validator("session_id")
    @classmethod
    def validate_optional_session(cls, value: str | None):
        return assert_safe_id(value, "session_id") if value else value

    @field_validator("file_ids")
    @classmethod
    def validate_chat_file_ids(cls, value: list[str]):
        return [assert_safe_id(item, "file_ids") for item in value]


class ChatStreamResponse(BaseModel):
    session_id: str | None = None
    run_id: str | None = None
    status: Literal["queued", "needs_confirmation"]
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None
    intent_decision: IntentDecisionResponse | None = None
    suggestions: list[CapabilitySuggestionResponse] = Field(default_factory=list)


class AdminRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str
    user_id: str | None = None
    workspace_id: str
    status: str
    agent_id: str
    skill_id: str
    created_at: Any | None = None
    queued_at: Any | None = None
    started_at: Any | None = None
    finished_at: Any | None = None
    cancel_requested_at: Any | None = None
    cancel_requested_by: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    queue_position: int | None = None
    queue_insight: dict[str, Any] | None = None


class AdminRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[AdminRunSummaryResponse] = Field(default_factory=list)
    limit: int


class AdminRunDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: dict[str, Any]
    events: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    sandbox_leases: list[dict[str, Any]] = Field(default_factory=list)
    skill_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    audit: list[dict[str, Any]] = Field(default_factory=list)


class AdminSkillVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    version: str
    content_hash: str = ""
    description: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    dependency_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    created_by: str | None = None
    created_at: Any | None = None


class AdminSkillDependencyDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    status: str
    reason: str
    public: bool = False
    internal_dependency: bool = False
    available: bool = False


class AdminSkillDependencyPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    public: bool = False
    internal_dependency: bool = False
    dependency_ids: list[str] = Field(default_factory=list)
    dependency_details: list[AdminSkillDependencyDetailResponse] = Field(default_factory=list)


class AdminSkillDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: dict[str, Any]
    dependency_policy: AdminSkillDependencyPolicyResponse | None = None
    release_policy: dict[str, Any] | None = None
    versions: list[AdminSkillVersionResponse] = Field(default_factory=list)
    recent_snapshots: list[dict[str, Any]] = Field(default_factory=list)


class AdminSkillSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    synced: list[AdminSkillVersionResponse] = Field(default_factory=list)


class AdminSkillUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uploaded: AdminSkillVersionResponse


class AdminSkillVersionDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    from_version: str
    to_version: str
    content_hash_changed: bool = False
    description_changed: bool = False
    source_changed: bool = False
    dependency_added: list[str] = Field(default_factory=list)
    dependency_removed: list[str] = Field(default_factory=list)


class AdminSkillPromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    channel: Literal["stable"] = "stable"
    rollout_percent: int = Field(default=100, ge=0, le=100)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str):
        return assert_safe_id(value, "version")


class AdminSkillRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    channel: Literal["stable"] = "stable"

    @field_validator("version")
    @classmethod
    def validate_rollback_version(cls, value: str):
        return assert_safe_id(value, "version")


class AdminSkillReleasePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    channel: str = "stable"
    current_version: str
    previous_version: str | None = None
    rollout_percent: int = 100
    status: str = "active"

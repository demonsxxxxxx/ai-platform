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


class ShareContextSnapshotRequest(BaseModel):
    """Request a governed share/fork context snapshot for another owned session."""

    model_config = ConfigDict(extra="forbid")

    share_kind: Literal["share", "fork", "import"] = "share"
    target_session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_session_id")
    @classmethod
    def validate_target_session_id(cls, value: str):
        return assert_safe_id(value, "target_session_id")


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
    model_id: str | None = None
    model_value: str | None = None
    schema_version: str = RUN_PAYLOAD_SCHEMA_VERSION

    @field_validator("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "agent_id", "skill_id", "executor_type")
    @classmethod
    def validate_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)

    @field_validator("context_snapshot_id")
    @classmethod
    def validate_optional_context_snapshot_id(cls, value: str | None):
        return assert_safe_id(value, "context_snapshot_id") if value else value

    @field_validator("model_id")
    @classmethod
    def validate_optional_model_id(cls, value: str | None):
        return assert_safe_id(value, "model_id") if value else value

    @field_validator("model_value")
    @classmethod
    def validate_optional_model_value(cls, value: str | None):
        return assert_safe_id(value, "model_value") if value else value

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


class PublicSkillResponse(BaseModel):
    """User-facing skill catalog item for the Phase 1 Skills surface."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    enabled: bool = True
    file_count: int = 0
    installed_from: Literal["manual", "marketplace"] = "marketplace"
    published_marketplace_name: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    is_published: bool = True
    marketplace_is_active: bool = True


class PublicSkillsResponse(BaseModel):
    """Paginated public Skills response consumed by the frontend shell."""

    model_config = ConfigDict(extra="forbid")

    skills: list[PublicSkillResponse] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 50
    available_tags: list[str] = Field(default_factory=list)
    effective_permissions: list[str] = Field(default_factory=list)


class PublicSkillDetailResponse(BaseModel):
    """Public skill detail with file paths and tenant availability."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)
    enabled: bool = True
    skill_name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    is_published: bool = True
    marketplace_is_active: bool = True


class PublicSkillFileResponse(BaseModel):
    """Public skill file content response with binary-safe metadata."""

    model_config = ConfigDict(extra="forbid")

    content: str
    is_binary: bool = False
    url: str | None = None
    mime_type: str | None = None
    size: int | None = None


class PublicSkillToggleRequest(BaseModel):
    """Request body for enabling or disabling a tenant-visible skill."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class PublicSkillToggleResponse(BaseModel):
    """Toggle result for the public Skills surface."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    enabled: bool
    message: str


class PublicSkillFileUpdateRequest(BaseModel):
    """Request body for frontend file-write attempts, currently permission-gated."""

    model_config = ConfigDict(extra="forbid")

    content: str


class PublicSkillFileMutationResponse(BaseModel):
    """Result for tenant/user scoped public Skill file mutations."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    file_path: str
    message: str
    size: int | None = None


class PublicSkillImportPreviewItem(BaseModel):
    """Preview of one Skill package before user-scoped import persistence."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    file_count: int
    files: list[str] = Field(default_factory=list)
    already_exists: bool = False


class PublicSkillImportPreviewResponse(BaseModel):
    """ZIP Skill import preview response for the public Skills surface."""

    model_config = ConfigDict(extra="forbid")

    skill_count: int
    skills: list[PublicSkillImportPreviewItem] = Field(default_factory=list)


class PublicSkillImportCreatedItem(BaseModel):
    """One successfully imported public Skill package."""

    model_config = ConfigDict(extra="forbid")

    name: str
    file_count: int


class PublicSkillImportErrorItem(BaseModel):
    """One rejected public Skill package import item."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str


class PublicSkillImportUploadResponse(BaseModel):
    """ZIP Skill import result for current user overlays."""

    model_config = ConfigDict(extra="forbid")

    message: str
    created: list[PublicSkillImportCreatedItem] = Field(default_factory=list)
    errors: list[PublicSkillImportErrorItem] = Field(default_factory=list)
    skill_count: int


class PublishToMarketplaceRequest(BaseModel):
    """User-facing publish request accepted by the public Skills contract."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    version: str | None = None


class MarketplaceSkillResponse(BaseModel):
    """Marketplace catalog item for frontend browsing and preview."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    version: str
    created_at: Any | None = None
    updated_at: Any | None = None
    created_by: str | None = None
    created_by_username: str | None = None
    is_active: bool = True
    is_owner: bool = False
    file_count: int = 0


class MarketplaceListResponse(BaseModel):
    """Paginated marketplace response consumed by the frontend shell."""

    model_config = ConfigDict(extra="forbid")

    skills: list[MarketplaceSkillResponse] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 50
    available_tags: list[str] = Field(default_factory=list)
    effective_permissions: list[str] = Field(default_factory=list)


class MarketplaceSkillFilesResponse(BaseModel):
    """Marketplace skill file listing."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)


class MarketplaceInstallResponse(BaseModel):
    """Marketplace install/update result for tenant skill availability."""

    model_config = ConfigDict(extra="forbid")

    message: str
    skill_name: str
    file_count: int


class MarketplaceTagsResponse(BaseModel):
    """Available marketplace tag projection."""

    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(default_factory=list)


class PersonaStarterPromptResponse(BaseModel):
    """Starter prompt projected with a persona preset."""

    model_config = ConfigDict(extra="forbid")

    icon: str | None = None
    text: str | dict[str, str]


class PersonaPresetResponse(BaseModel):
    """Public persona preset projection for the post-login workbench."""

    model_config = ConfigDict(extra="forbid")

    id: str
    scope: Literal["global", "user"] = "global"
    owner_user_id: str | None = None
    name: str
    description: str = ""
    avatar: str | None = None
    tags: list[str] = Field(default_factory=list)
    system_prompt: str
    starter_prompts: list[PersonaStarterPromptResponse] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    visibility: Literal["public", "private"] = "public"
    status: Literal["draft", "published", "archived"] = "published"
    source_preset_id: str | None = None
    copied_from_version: int | None = None
    version: int = 1
    usage_count: int = 0
    is_favorite: bool = False
    is_pinned: bool = False
    last_used_at: Any | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: Any
    updated_at: Any


class PersonaPresetListResponse(BaseModel):
    """Paginated persona preset response consumed by the frontend."""

    model_config = ConfigDict(extra="forbid")

    presets: list[PersonaPresetResponse] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 12


class PersonaPresetPreferenceRequest(BaseModel):
    """User-local persona preset preference update."""

    model_config = ConfigDict(extra="forbid")

    is_favorite: bool | None = None
    is_pinned: bool | None = None


class PersonaPresetSnapshotResponse(BaseModel):
    """Persona preset snapshot applied to a chat session."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str
    name: str
    system_prompt: str
    starter_prompts: list[PersonaStarterPromptResponse] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    missing_skill_names: list[str] = Field(default_factory=list)
    version: int = 1
    avatar: str | None = None


class RevealedFileCardPreviewResponse(BaseModel):
    """Optional card preview metadata for revealed files."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["image", "text", "code", "markdown", "project", "document", "fallback"] = "fallback"
    title: str | None = None
    subtitle: str | None = None
    text: str | None = None
    lines: list[str] | None = None
    language: str | None = None
    image_url: str | None = None
    badge: str | None = None
    accent: str | None = None


class RevealedFileItemResponse(BaseModel):
    """Safe file/artifact projection for the post-login files workbench."""

    model_config = ConfigDict(extra="forbid")

    id: str
    file_key: str
    file_name: str
    file_type: Literal["image", "video", "document", "code", "project", "other"] = "other"
    mime_type: str | None = None
    file_size: int = 0
    url: str | None = None
    session_id: str
    session_name: str | None = None
    trace_id: str
    project_id: str | None = None
    user_id: str
    source: Literal["reveal_file", "reveal_project"] = "reveal_file"
    description: str | None = None
    original_path: str | None = None
    created_at: Any
    is_favorite: bool = False
    card_preview: RevealedFileCardPreviewResponse | None = None
    project_meta: dict[str, Any] | None = None


class RevealedFileListResponse(BaseModel):
    """Paginated revealed file list."""

    model_config = ConfigDict(extra="forbid")

    items: list[RevealedFileItemResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class RevealedFileSessionGroupResponse(BaseModel):
    """One session group in the revealed files workbench."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    session_name: str | None = None
    file_count: int = 0
    files: list[RevealedFileItemResponse] = Field(default_factory=list)


class RevealedFileGroupedListResponse(BaseModel):
    """Paginated session-grouped revealed file list."""

    model_config = ConfigDict(extra="forbid")

    sessions: list[RevealedFileSessionGroupResponse] = Field(default_factory=list)
    total_sessions: int = 0
    page: int = 1
    page_size: int = 20


class RevealedFileSessionResponse(BaseModel):
    """Session summary for revealed file filters."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    session_name: str | None = None
    file_count: int = 0


class PublicChannelResponse(BaseModel):
    """Secret-safe channel catalog item for governed workbench routes."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    workspace_id: str = "default"
    display_name: str
    channel_type: str
    enabled: bool = False
    capabilities: list[str] = Field(default_factory=list)
    connection_state: str = "not_configured"
    redaction_policy: str = "secrets_never_projected"
    retention_policy: str = "tenant_default"
    last_actor: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class PublicChannelsResponse(BaseModel):
    """Tenant-scoped public channel catalog response."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str = "default"
    channels: list[PublicChannelResponse] = Field(default_factory=list)
    total: int = 0


class ChannelAdminTestRequest(BaseModel):
    """Admin channel test request without credential material."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True


class ChannelAdminCreateRequest(BaseModel):
    """Admin channel creation request without inline credentials."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    enabled: bool = True


class ChannelAdminCredentialsRequest(BaseModel):
    """Credential update request that accepts references but never projects them."""

    model_config = ConfigDict(extra="forbid")

    credential_ref: str | None = None
    webhook_url: str | None = None


class ChannelAdminRetentionRequest(BaseModel):
    """Retention policy update request for channel audit metadata."""

    model_config = ConfigDict(extra="forbid")

    retention_policy: str


class ChannelAdminOperationResponse(BaseModel):
    """Audited admin channel operation projection."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    workspace_id: str = "default"
    operation: str
    status: str
    audit_id: str
    message: str


class WorkbenchGovernanceResponse(BaseModel):
    """Governance metadata shared by post-login workbench projections."""

    model_config = ConfigDict(extra="forbid")

    projection: str
    tenant_id: str
    workspace_id: str = "default"
    degraded: bool = False
    audit_required: bool = False
    rollback_available: bool = False
    secret_material_projected: bool = False


class WorkbenchAuditResponse(BaseModel):
    """Safe audit reference for queued admin workbench operations."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    status: str = "queued"


class WorkbenchOperationResponse(BaseModel):
    """Audited admin workbench operation response."""

    model_config = ConfigDict(extra="forbid")

    target_type: str
    target_id: str
    operation: str
    status: str = "queued"
    audit_id: str
    message: str


class RoleGovernanceRoleResponse(BaseModel):
    """Secret-safe role directory item for the frontend role governance surface."""

    model_config = ConfigDict(extra="forbid")

    role_id: str
    name: str
    description: str = ""
    requestable: bool = False
    assignable: bool = False
    scope: Literal["tenant", "department", "workspace"] = "tenant"
    capabilities: list[str] = Field(default_factory=list)


class RoleGovernanceRoleDirectoryResponse(BaseModel):
    """Role directory projection without raw permission leakage."""

    model_config = ConfigDict(extra="forbid")

    roles: list[RoleGovernanceRoleResponse] = Field(default_factory=list)


class RoleGovernanceDepartmentResponse(BaseModel):
    """Tenant-scoped department projection for role governance."""

    model_config = ConfigDict(extra="forbid")

    department_id: str
    name: str
    current_user_member: bool = False
    requestable: bool = True


class RoleGovernanceWorkspaceResponse(BaseModel):
    """Workspace projection for role and department access governance."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    name: str
    current: bool = False
    requestable: bool = True


class RoleGovernanceSkillAvailabilityResponse(BaseModel):
    """Inherited Skill availability projected for role governance."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    availability_state: Literal["enabled", "disabled", "inherited", "requestable"] = "inherited"
    inherited_from: Literal["tenant", "department", "workspace"] = "tenant"
    scope_id: str


class RoleGovernanceScopeResponse(BaseModel):
    """Department/workspace scope projection for the role governance page."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    current_department_id: str = ""
    departments: list[RoleGovernanceDepartmentResponse] = Field(default_factory=list)
    workspaces: list[RoleGovernanceWorkspaceResponse] = Field(default_factory=list)
    skill_availability: list[RoleGovernanceSkillAvailabilityResponse] = Field(default_factory=list)


class RoleGovernanceRequestItemResponse(BaseModel):
    """Safe request/approval workflow item."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    requester_id: str
    target_type: Literal["role", "department_agent"]
    target_id: str
    status: Literal["pending", "approved", "rejected", "queued"] = "pending"
    reason: str = ""
    approver_id: str | None = None
    created_at: Any | None = None
    decided_at: Any | None = None
    audit_id: str | None = None


class RoleGovernanceAuditItemResponse(BaseModel):
    """Safe audit and rollback projection for role governance."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    target_type: str
    target_id: str
    actor_id: str
    source: str = "role_governance_projection"
    status: str = "recorded"
    rollback_available: bool = False
    created_at: Any | None = None


class RoleGovernanceOverviewResponse(BaseModel):
    """Complete frontend role governance overview projection."""

    model_config = ConfigDict(extra="forbid")

    governance: WorkbenchGovernanceResponse
    role_directory: RoleGovernanceRoleDirectoryResponse
    scope: RoleGovernanceScopeResponse
    requests: list[RoleGovernanceRequestItemResponse] = Field(default_factory=list)
    audit: list[RoleGovernanceAuditItemResponse] = Field(default_factory=list)


class RoleGovernanceRequestCreateRequest(BaseModel):
    """Ordinary-user request for governed role or department-agent access."""

    model_config = ConfigDict(extra="forbid")

    target_type: Literal["role", "department_agent"]
    target_id: str
    reason: str = Field(default="", max_length=2000)
    workspace_id: str = "default"

    @field_validator("workspace_id")
    @classmethod
    def validate_role_governance_workspace_id(cls, value: str):
        return assert_safe_id(value, "workspace_id")


class RoleGovernanceDecisionRequest(BaseModel):
    """Admin approval or rejection note for queued role governance requests."""

    model_config = ConfigDict(extra="forbid")

    decision_note: str = Field(default="", max_length=2000)
    rollback_id: str | None = None

    @field_validator("rollback_id")
    @classmethod
    def validate_rollback_id(cls, value: str | None):
        return assert_safe_id(value, "rollback_id") if value else value


class RoleGovernanceRollbackRequest(BaseModel):
    """Admin rollback request for a role governance audit item."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


class WorkbenchUserResponse(BaseModel):
    """Safe company user-directory projection."""

    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    email: str | None = None
    full_name: str
    is_active: bool = True
    is_superuser: bool = False
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    tenant_id: str
    department_id: str = ""
    created_at: Any | None = None
    updated_at: Any | None = None


class WorkbenchUserListResponse(BaseModel):
    """Paginated safe user-directory response."""

    model_config = ConfigDict(extra="forbid")

    users: list[WorkbenchUserResponse] = Field(default_factory=list)
    items: list[WorkbenchUserResponse] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 50
    governance: WorkbenchGovernanceResponse


class WorkbenchUserWriteRequest(BaseModel):
    """User lifecycle request payload with secret-bearing fields forbidden."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    full_name: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    is_active: bool | None = None


class WorkbenchSettingItemResponse(BaseModel):
    """Safe settings projection item."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any
    type: str
    category: str
    label: str
    description: str = ""
    is_public: bool = True
    is_secret: bool = False
    audit_required: bool = False
    rollback_available: bool = False
    updated_at: Any | None = None


class WorkbenchSettingGroupResponse(BaseModel):
    """Grouped settings projection."""

    model_config = ConfigDict(extra="forbid")

    category: str
    items: list[WorkbenchSettingItemResponse] = Field(default_factory=list)


class WorkbenchSettingsResponse(BaseModel):
    """Safe personal/system settings split."""

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, WorkbenchSettingGroupResponse]
    governance: WorkbenchGovernanceResponse


class WorkbenchSettingUpdateRequest(BaseModel):
    """Admin settings update request."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    rollback_id: str | None = None


class WorkbenchSettingWriteResponse(BaseModel):
    """Masked settings write response."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any
    status: str = "queued"
    audit: WorkbenchAuditResponse


class WorkbenchSettingResetResponse(BaseModel):
    """Settings reset operation response."""

    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    status: str = "queued"
    reset_count: int = 1
    audit_id: str


class WorkbenchFeedbackItemResponse(BaseModel):
    """Safe aggregate feedback desk item."""

    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    username: str
    session_id: str
    run_id: str
    rating: str
    comment: str | None = None
    assignment_state: str = "unassigned"
    assignee_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    status: str = "open"
    audit_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: Any | None = None


class WorkbenchFeedbackStatsResponse(BaseModel):
    """Feedback aggregate stats."""

    model_config = ConfigDict(extra="forbid")

    total_count: int = 0
    up_count: int = 0
    down_count: int = 0
    up_percentage: float = 0.0


class WorkbenchFeedbackListResponse(BaseModel):
    """Feedback desk list response."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkbenchFeedbackItemResponse] = Field(default_factory=list)
    total: int = 0
    stats: WorkbenchFeedbackStatsResponse
    governance: WorkbenchGovernanceResponse


class WorkbenchFeedbackUpdateRequest(BaseModel):
    """Feedback assignment/closure/label update request."""

    model_config = ConfigDict(extra="forbid")

    assignee_id: str | None = None
    assignment_state: str | None = None
    status: str | None = None
    labels: list[str] = Field(default_factory=list)


class WorkbenchI18nTextResponse(BaseModel):
    """Localized notification text."""

    model_config = ConfigDict(extra="forbid")

    en: str = ""
    zh: str = ""
    ja: str = ""
    ko: str = ""
    ru: str = ""


class WorkbenchNotificationResponse(BaseModel):
    """Safe notification projection."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title_i18n: WorkbenchI18nTextResponse
    content_i18n: WorkbenchI18nTextResponse
    type: str = "info"
    start_time: Any | None = None
    end_time: Any | None = None
    expires_at: Any | None = None
    is_active: bool = True
    read_state: str | None = None
    audience: dict[str, Any] | None = None
    audit_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: Any | None = None
    updated_at: Any | None = None
    created_by: str = "system"


class WorkbenchNotificationListResponse(BaseModel):
    """Notification management projection."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkbenchNotificationResponse] = Field(default_factory=list)
    total: int = 0
    governance: WorkbenchGovernanceResponse


class WorkbenchNotificationWriteRequest(BaseModel):
    """Notification management write request."""

    model_config = ConfigDict(extra="forbid")

    title_i18n: dict[str, str] = Field(default_factory=dict)
    content_i18n: dict[str, str] = Field(default_factory=dict)
    type: str = "info"
    start_time: Any | None = None
    end_time: Any | None = None
    expires_at: Any | None = None
    is_active: bool = True
    audience: dict[str, Any] = Field(default_factory=dict)
    replay: bool = False


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

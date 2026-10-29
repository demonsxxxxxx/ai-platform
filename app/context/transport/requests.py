"""HTTP request contracts owned by Context and memory governance."""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTEXT_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _assert_context_request_id(value: str, field_name: str) -> str:
    """Preserve the established Context HTTP identifier grammar and errors."""
    if not _CONTEXT_REQUEST_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


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
        return [_assert_context_request_id(item, info.field_name) for item in value]


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
        return _assert_context_request_id(value, "target_session_id")


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
        return _assert_context_request_id(value, "workspace_id")

    @field_validator("agent_id", "session_id")
    @classmethod
    def validate_optional_memory_ids(cls, value: str | None, info):
        return _assert_context_request_id(value, info.field_name) if value else value


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
        return _assert_context_request_id(value, "workspace_id")

    @field_validator("agent_id")
    @classmethod
    def validate_policy_agent_id(cls, value: str | None):
        return _assert_context_request_id(value, "agent_id") if value else value


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
        return _assert_context_request_id(value, "workspace_id")

    @field_validator("agent_id")
    @classmethod
    def validate_preview_agent_id(cls, value: str | None):
        return _assert_context_request_id(value, "agent_id") if value else value

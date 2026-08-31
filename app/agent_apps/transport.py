from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.control_plane_contracts import ThinkingEffort
from app.validation import assert_safe_id


class AgentAppRunRequest(BaseModel):
    """Strict dedicated submission surface without client-owned capability selectors."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=100_000)
    submission_id: UUID
    file_ids: list[str] = Field(default_factory=list, max_length=32)
    user_timezone: str | None = Field(default=None, max_length=128)
    thinking_effort: ThinkingEffort = "off"

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, value: list[str]):
        normalized = [assert_safe_id(item, "file_ids") for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("file_ids contains duplicates")
        return normalized

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _AgentProfilePresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    description: str = ""
    welcome_message: str = ""
    starter_prompts: list[str] = Field(default_factory=list)
    capability_summary: str = ""
    recommended_tasks: list[str] = Field(default_factory=list)
    supported_input_types: list[Literal["text", "file"]] = Field(default_factory=lambda: ["text"])
    supported_file_types: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    permissions_and_data_access_notice: str = ""
    avatar_ref: Literal[
        "builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"
    ] = "builtin:agent"
    avatar_seed: str = ""
    category: Literal["general", "support", "writing", "research", "operations"] = "general"
    published_at: Any | None = None


class AgentProfilePublicProjection(_AgentProfilePresentation):
    """Ordinary-user market projection without executable configuration."""

    expected_revision: int


class AgentConversationIdentity(_AgentProfilePresentation):
    """Safe immutable Agent identity retained for conversation recovery."""

    revision: int = Field(ge=1)

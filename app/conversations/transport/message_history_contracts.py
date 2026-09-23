from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_apps.api import AgentProfileAvatarRef


class AgentConversationIdentity(BaseModel):
    """Only safe immutable Agent identity retained in public conversation recovery."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    revision: int = Field(ge=1)
    name: str
    description: str = ""
    starter_prompts: list[str] = Field(default_factory=list)
    avatar_ref: AgentProfileAvatarRef = "builtin:agent"
    avatar_seed: str = ""
    published_at: Any | None = None


class ChatSessionResponse(BaseModel):
    session_id: str
    workspace_id: str
    agent_id: str
    title: str
    purpose: Literal["conversation", "builder_test"] = "conversation"
    agent_conversation: AgentConversationIdentity | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class SessionRenameRequest(BaseModel):
    """Public rename payload for an active Session."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionResponse]
    next_cursor: str | None = None


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
    next_cursor: str | None = None

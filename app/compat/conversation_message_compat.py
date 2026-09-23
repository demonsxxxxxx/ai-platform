"""Compatibility exports for conversation message-history contracts."""

from app.conversations.transport.message_history_contracts import (
    AgentConversationIdentity as AgentConversationIdentity,
    ChatMessageResponse as ChatMessageResponse,
    ChatMessagesResponse as ChatMessagesResponse,
    ChatSessionResponse as ChatSessionResponse,
    ChatSessionsResponse as ChatSessionsResponse,
    SessionRenameRequest as SessionRenameRequest,
)

__all__ = [
    "AgentConversationIdentity",
    "ChatMessageResponse",
    "ChatMessagesResponse",
    "ChatSessionResponse",
    "ChatSessionsResponse",
    "SessionRenameRequest",
]

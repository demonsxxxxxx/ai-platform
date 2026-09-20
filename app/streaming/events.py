"""Public Streaming event registries for cross-context producers."""

from app.streaming.domain.protocol_v4 import (
    PUBLIC_APPLICATION_EVENT_TYPES,
    PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES,
)

EXECUTOR_CALLBACK_APPLICATION_EVENT_TYPES = PUBLIC_APPLICATION_EVENT_TYPES - {
    "agent.progress",
    "thinking.started",
    "thinking.delta",
    "thinking.completed",
}

__all__ = [
    "EXECUTOR_CALLBACK_APPLICATION_EVENT_TYPES",
    "PUBLIC_APPLICATION_EVENT_TYPES",
    "PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES",
]

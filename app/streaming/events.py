"""Public Streaming event registries for cross-context producers."""

from app.streaming.domain.protocol_v4 import (
    PUBLIC_APPLICATION_EVENT_TYPES,
    PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES,
)
from app.streaming.domain.run_events import EVENT_ENVELOPE_SCHEMA_VERSION

# Pre-bridge AgentEvent names. This parser set is intentionally broader than the
# durable callback publication subset: platform-owned candidates remain private
# callback evidence and are projected only by their owning platform authority.
AGENT_EVENT_PUBLIC_CANDIDATE_TYPES = PUBLIC_APPLICATION_EVENT_TYPES - {
    "agent.progress",
    "thinking.started",
    "thinking.delta",
    "thinking.completed",
}

__all__ = [
    "EVENT_ENVELOPE_SCHEMA_VERSION",
    "AGENT_EVENT_PUBLIC_CANDIDATE_TYPES",
    "PUBLIC_APPLICATION_EVENT_TYPES",
    "PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES",
]

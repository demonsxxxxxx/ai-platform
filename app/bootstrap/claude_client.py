"""Assemble the Claude client's protocol completion boundary."""

from app.execution.infrastructure.harness.claude_client_lifecycle import (
    ClaudeClientCloseBoundary,
    ClaudeInjectedCallbackTracker,
)
from app.execution.infrastructure.harness.claude.interaction import ClaudeRunInteractionActor
from app.execution.infrastructure.harness.claude.assistant_text_sources import AssistantTextSourceBuffer
from app.execution.infrastructure.harness.claude.typed_blocks import ClaudeTypedBlockObservations


def prepare_claude_typed_observations():
    return ClaudeTypedBlockObservations()


def prepare_claude_text_sources():
    return AssistantTextSourceBuffer()


def prepare_claude_client_close(client):
    return ClaudeClientCloseBoundary(client)


def prepare_claude_callback_tracker():
    return ClaudeInjectedCallbackTracker()


def prepare_claude_run_interaction(port, *, run_id, attempt_id, sanitize_text):
    return ClaudeRunInteractionActor(
        port, run_id=run_id, attempt_id=attempt_id, sanitize_text=sanitize_text,
    )

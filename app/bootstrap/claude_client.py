"""Assemble the Claude client's protocol completion boundary."""

from app.execution.infrastructure.harness.claude_client_lifecycle import ClaudeClientCloseBoundary


def prepare_claude_client_close(client, on_protocol_closed):
    return ClaudeClientCloseBoundary(client, on_protocol_closed)

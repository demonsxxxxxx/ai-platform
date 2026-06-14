from __future__ import annotations

from typing import Any


DOCUMENT_WORKER_SKILLS = {"baoyu-translate", "qa-file-reviewer"}
HEAVY_SANDBOX_SIGNALS = {
    "browser",
    "playwright",
    "selenium",
    "script",
    "python script",
    "shell",
    "terminal",
    "execute code",
    "run code",
}


def _message_text(input_payload: dict[str, Any] | None) -> str:
    payload = input_payload if isinstance(input_payload, dict) else {}
    message = payload.get("message")
    if isinstance(message, str):
        return message.lower()
    return ""


def _explicit_sandbox_requested(input_payload: dict[str, Any] | None) -> bool:
    payload = input_payload if isinstance(input_payload, dict) else {}
    sandbox_mode = payload.get("sandbox_mode")
    return sandbox_mode in {"ephemeral", "persistent"}


def route_office_execution_tier(
    *,
    agent_id: str,
    skill_id: str,
    input_payload: dict[str, Any] | None,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Classify office work into a public execution tier without starting runtime resources."""
    _ = file_ids
    if _explicit_sandbox_requested(input_payload) or any(
        signal in _message_text(input_payload) for signal in HEAVY_SANDBOX_SIGNALS
    ):
        return {
            "execution_tier": "heavy_sandbox",
            "uses_sandbox_by_default": True,
            "reason": "explicit_sandbox_or_risky_tooling",
        }
    if skill_id in DOCUMENT_WORKER_SKILLS or agent_id in {"qa-word-review", "document-review", "baoyu-translate"}:
        return {
            "execution_tier": "document_worker",
            "uses_sandbox_by_default": False,
            "reason": "document_processing_skill",
        }
    return {
        "execution_tier": "sdk_only_writing",
        "uses_sandbox_by_default": False,
        "reason": "lightweight_office_writing",
    }

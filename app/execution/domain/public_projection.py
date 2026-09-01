from __future__ import annotations

PUBLIC_ANSWER_FAILURE_REASONS = frozenset(
    {
        "answer_too_large",
        "invalid_configuration",
        "invalid_input",
        "private_replacement_invalid",
        "private_token_already_published",
        "private_token_boundary_conflict",
        "private_token_prefix_overflow",
        "sanitizer_bound_exceeded",
        "sanitizer_failed",
        "sanitizer_rejected",
        "terminal_text_mismatch",
        "upstream_projection_failed",
    }
)


_SELECTED_SKILL_INVOCATION_ERRORS = frozenset(
    {
        "claude_agent_sdk_selected_skill_not_invoked",
        "claude_agent_sdk_selected_skill_hook_failed",
        "claude_agent_sdk_selected_skill_not_authorized",
    }
)
_CLAUDE_SDK_ACTIONABLE_FAILURE_CODES = frozenset(
    {
        *_SELECTED_SKILL_INVOCATION_ERRORS,
        "claude_agent_sdk_cancelled",
        "claude_agent_sdk_missing_structured_terminal",
        "claude_agent_sdk_turn_limit_exceeded",
        "claude_agent_sdk_timeout",
        "claude_agent_sdk_public_projection_failed",
        "claude_agent_sdk_tool_admission_failed",
        "claude_agent_sdk_upstream_error",
    }
)


def claude_sdk_failure_code(sdk_result: object) -> str:
    if sdk_result is None:
        return "claude_agent_sdk_disabled"
    error_text = str(getattr(sdk_result, "error", "") or "")
    if error_text in _CLAUDE_SDK_ACTIONABLE_FAILURE_CODES:
        return error_text
    if error_text.startswith("claude_agent_sdk_unavailable"):
        return "claude_agent_sdk_unavailable"
    if getattr(sdk_result, "used_sdk", False):
        return "claude_agent_sdk_runtime_error"
    if error_text == "claude_agent_sdk_disabled":
        return "claude_agent_sdk_disabled"
    return "claude_agent_sdk_required"


def claude_sdk_failure_message(sdk_result: object) -> str:
    error_code = claude_sdk_failure_code(sdk_result)
    if error_code in _SELECTED_SKILL_INVOCATION_ERRORS:
        return "The selected capability did not complete its required Skill execution. Please retry."
    messages = {
        "claude_agent_sdk_cancelled": "This run was cancelled before completion.",
        "claude_agent_sdk_turn_limit_exceeded": (
            "This run reached its turn limit. Continue in the same session or narrow the request."
        ),
        "claude_agent_sdk_timeout": "This run timed out. Retry or split the request.",
        "claude_agent_sdk_missing_structured_terminal": (
            "The executor ended without an authoritative terminal result. Please retry."
        ),
        "claude_agent_sdk_public_projection_failed": (
            "The executor could not safely project the final answer."
        ),
        "claude_agent_sdk_tool_admission_failed": (
            "The selected capability or tool was not admitted by platform policy."
        ),
        "claude_agent_sdk_upstream_error": (
            "The execution service failed. Please retry later."
        ),
        "claude_agent_sdk_disabled": "Claude Agent SDK is required for this run.",
        "claude_agent_sdk_required": "Claude Agent SDK is required for this run.",
        "claude_agent_sdk_unavailable": "Claude Agent SDK is required for this run.",
    }
    return messages.get(error_code, "Claude Agent SDK execution failed")


def public_answer_failure_reason(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and value in PUBLIC_ANSWER_FAILURE_REASONS
        else None
    )


def projected_public_answer_failure_reason(
    error_code: object,
    diagnostics: object,
) -> str | None:
    if error_code != "claude_agent_sdk_public_projection_failed":
        return None
    raw = diagnostics if isinstance(diagnostics, dict) else {}
    return public_answer_failure_reason(raw.get("projection_failure_reason"))

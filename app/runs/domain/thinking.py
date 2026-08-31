"""Run-scoped Thinking controls owned by Runs."""

from typing import Literal

RUN_THINKING_EFFORT_INPUT_KEY = "_thinking_effort"
ThinkingEffort = Literal["off", "low", "medium", "high"]
THINKING_EFFORT_LEVELS = frozenset({"off", "low", "medium", "high"})


def normalize_thinking_effort(value: object) -> str:
    if value is None:
        return "off"
    if not isinstance(value, str) or value not in THINKING_EFFORT_LEVELS:
        raise ValueError("thinking_effort_invalid")
    return value

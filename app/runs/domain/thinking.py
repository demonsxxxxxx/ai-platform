"""Run-scoped Thinking controls owned by Runs."""

from typing import Literal

RUN_THINKING_EFFORT_INPUT_KEY = "_thinking_effort"
ThinkingEffort = Literal["auto", "low", "medium", "high"]
THINKING_EFFORT_LEVELS = frozenset({"auto", "low", "medium", "high"})
_LEGACY_THINKING_EFFORT_ALIASES = {"off": "auto"}


def normalize_thinking_effort(value: object) -> str:
    if value is None:
        return "auto"
    if not isinstance(value, str):
        raise ValueError("thinking_effort_invalid")
    normalized = _LEGACY_THINKING_EFFORT_ALIASES.get(value, value)
    if normalized not in THINKING_EFFORT_LEVELS:
        raise ValueError("thinking_effort_invalid")
    return normalized

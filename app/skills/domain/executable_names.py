"""Stable Skill names accepted by the configured execution runtime."""

from __future__ import annotations

import re


_EXECUTABLE_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def is_valid_executable_skill_name(value: object) -> bool:
    """Return whether a Skill name can be represented by the SDK Skill tool."""

    return (
        isinstance(value, str)
        and _EXECUTABLE_SKILL_NAME_PATTERN.fullmatch(value) is not None
    )

"""Pure field predicates shared by the closed v4 payload validators.

The producer and public projection keep separate shape rules, semantic checks,
validation order, and exception types. Only identical field-level facts live
here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeGuard


_SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


def exact_enum_member(value: object, options: Iterable[object]) -> bool:
    """Match JSON enum values without Python's bool/int equality overlap."""

    return any(type(value) is type(option) and value == option for option in options)


def safe_public_ref(value: object) -> bool:
    return isinstance(value, str) and _SAFE_REF_PATTERN.fullmatch(value) is not None


def bounded_public_string(
    value: object, *, minimum: int, maximum: int
) -> TypeGuard[str]:
    return isinstance(value, str) and minimum <= len(value) <= maximum


def bounded_public_integer(
    value: object, *, minimum: int, maximum: int | None
) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def safe_public_filename_characters(value: str) -> bool:
    """Check filename characters after the caller's context-specific bounds."""

    return not any(
        ord(character) < 32
        or ord(character) == 127
        or character in "/\\"
        for character in value
    )

"""Public in-process conversation contracts used by legacy delivery code."""

from __future__ import annotations

import re
from uuid import uuid4


_SAFE_SUBMISSION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def safe_submission_code(
    value: object,
    fallback: str = "chat_submission_rejected",
) -> str:
    return (
        value
        if isinstance(value, str) and _SAFE_SUBMISSION_CODE_PATTERN.fullmatch(value)
        else fallback
    )


def new_submission_diagnostic_id() -> str:
    return f"diag_{uuid4().hex[:16]}"


__all__ = ["new_submission_diagnostic_id", "safe_submission_code"]

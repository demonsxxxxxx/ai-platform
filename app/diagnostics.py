from __future__ import annotations

import json
import logging
import re
from types import TracebackType
from typing import Any
from uuid import uuid4

_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9_]{0,95}$")


def new_diagnostic_id() -> str:
    """Return an opaque reference that is safe to expose to an end user."""

    return f"diag_{uuid4().hex[:16]}"


def _application_frames(traceback: TracebackType | None) -> list[str]:
    frames: list[str] = []
    cursor = traceback
    while cursor is not None:
        module_name = str(cursor.tb_frame.f_globals.get("__name__") or "unknown")
        if module_name == "app" or module_name.startswith("app."):
            frames.append(
                f"{module_name}:{cursor.tb_lineno}:{cursor.tb_frame.f_code.co_name}"
            )
        cursor = cursor.tb_next
    return frames[-8:]


def log_safe_exception(
    logger: logging.Logger,
    *,
    event: str,
    phase: str,
    diagnostic_id: str,
    exc: BaseException,
    identifiers: dict[str, Any] | None = None,
) -> None:
    """Log bounded correlation facts without serializing exception text or data."""

    payload: dict[str, Any] = {
        "event": event,
        "diagnostic_id": diagnostic_id,
        "phase": phase,
        "exception_type": type(exc).__name__,
        "frames": _application_frames(exc.__traceback__),
    }
    for key, value in (identifiers or {}).items():
        if isinstance(key, str) and isinstance(value, str | int | float | bool | type(None)):
            payload[key] = value
    logger.error(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def log_safe_failure(
    logger: logging.Logger,
    *,
    event: str,
    phase: str,
    diagnostic_id: str,
    error_code: str,
    identifiers: dict[str, Any] | None = None,
) -> None:
    """Log a structured terminal failure without accepting upstream prose."""

    canonical_error_code = (
        error_code if _SAFE_ERROR_CODE.fullmatch(error_code) is not None else "internal_error"
    )
    payload: dict[str, Any] = {
        "event": event,
        "diagnostic_id": diagnostic_id,
        "phase": phase,
        "error_code": canonical_error_code,
    }
    for key, value in (identifiers or {}).items():
        if isinstance(key, str) and isinstance(value, str | int | float | bool | type(None)):
            payload[key] = value
    logger.error(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))

"""Safe public Chat submission failure projection and correlated diagnostics."""

import logging
import re
from uuid import uuid4
from typing import Any

from app.queue_payload_validation import queue_payload_invalid_detail
from app.required_tool_contract import public_required_tool_detail


# Preserve the existing operator log category while moving its safe projection.
logger = logging.getLogger("app.routes.chat")
_SAFE_SUBMISSION_DETAIL_CODES = frozenset({"required_capability_unavailable"})
_SAFE_SUBMISSION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def safe_submission_code(value: object, fallback: str = "chat_submission_rejected") -> str:
    return value if isinstance(value, str) and _SAFE_SUBMISSION_CODE_PATTERN.fullmatch(value) else fallback


def new_submission_diagnostic_id() -> str:
    return f"diag_{uuid4().hex[:16]}"


def log_safe_submission_exception(
    *,
    phase: str,
    diagnostic_id: str,
    exc: BaseException,
) -> None:
    frames: list[str] = []
    traceback_cursor = exc.__traceback__
    while traceback_cursor is not None:
        module_name = str(traceback_cursor.tb_frame.f_globals.get("__name__") or "unknown")
        if module_name == "app" or module_name.startswith("app."):
            frames.append(f"{module_name}:{traceback_cursor.tb_lineno}:{traceback_cursor.tb_frame.f_code.co_name}")
        traceback_cursor = traceback_cursor.tb_next
    logger.error(
        "chat submission failure diagnostic_id=%s phase=%s exception_type=%s frames=%s",
        diagnostic_id, phase, type(exc).__name__, ",".join(frames[-8:]) or "none",
    )


def submission_error_detail(
    *,
    code: str,
    rejected_before_persist: bool,
    diagnostic_id: str | None = None,
) -> dict[str, str]:
    detail = {"code": safe_submission_code(code)}
    if rejected_before_persist:
        detail["submission_disposition"] = "rejected_before_persist"
    if diagnostic_id is not None:
        detail["diagnostic_id"] = diagnostic_id
    return detail


def submission_error_code(detail: object, fallback: str = "chat_submission_rejected") -> str:
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return safe_submission_code(detail["code"], fallback)
    if isinstance(detail, dict) and detail.get("detail_code") in _SAFE_SUBMISSION_DETAIL_CODES:
        return safe_submission_code(detail["detail_code"], fallback)
    if isinstance(detail, str):
        return safe_submission_code(detail, fallback)
    return fallback


def submission_rejection_detail(
    *,
    code: str,
    diagnostic_id: str | None = None,
    validation_error: BaseException | None = None,
) -> dict[str, Any]:
    """Project an owner-confirmed rejection; never infer transaction outcome."""

    detail: dict[str, Any] = submission_error_detail(
        code=code, rejected_before_persist=True, diagnostic_id=diagnostic_id,
    )
    if detail["code"] == "required_capability_unavailable":
        detail.update(public_required_tool_detail("unavailable"))
    if detail["code"] == "queue_payload_invalid" and isinstance(validation_error, ValueError):
        detail.update(queue_payload_invalid_detail(validation_error))
    return detail

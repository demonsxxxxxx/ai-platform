"""Safe public Chat submission failure projection and correlated diagnostics."""

import logging
import re
from uuid import uuid4

from app.platform.tracing import safe_exception_log_fields


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
    try:
        fields = safe_exception_log_fields(exc)
        chain = fields["exception_chain"]
        safe_phase = phase if type(phase) is str and phase in {
            "resolver", "rejection_ledger", "admission_transaction",
        } else "unknown"
        safe_id = diagnostic_id if type(diagnostic_id) is str and re.fullmatch(
            r"diag_[0-9a-f]{16}", diagnostic_id,
        ) else "unknown"
        logger.error(
            "chat submission failure diagnostic_id=%s phase=%s exception_type=%s frames=%s "
            "exception_chain=%s exception_chain_end=%s",
            safe_id, safe_phase, chain[0]["exception_type"],
            ",".join(chain[0]["frames"]) or "none",
            chain, fields["exception_chain_end"],
        )
    except Exception:
        # Logging cannot replace the submission's original failure.
        pass


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

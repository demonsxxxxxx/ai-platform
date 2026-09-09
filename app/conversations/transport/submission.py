"""Compatibility exports for the platform-owned submission response models."""

from app.models import (
    ChatSubmissionPreLedgerAbsenceResponse as ChatSubmissionPreLedgerAbsenceResponse,
    ChatSubmissionResponse as ChatSubmissionResponse,
)

__all__ = [
    "ChatSubmissionPreLedgerAbsenceResponse",
    "ChatSubmissionResponse",
]

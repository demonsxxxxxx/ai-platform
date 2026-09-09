"""HTTP response models owned by Conversations transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.models import ChatStreamResponse


class ChatSubmissionResponse(BaseModel):
    """Principal-scoped durable resolution of one keyed chat submission."""

    submission_id: str
    state: Literal[
        "queued",
        "accepted_pending_enqueue",
        "admission_rejected",
        "enqueue_failed",
        "needs_confirmation",
        "rejected_before_persist",
    ]
    submission_disposition: Literal["rejected_before_persist"] | None = None
    rejection_code: str | None = None
    outcome: ChatStreamResponse | None = None
    run_status: Literal[
        "queued", "running", "succeeded", "failed", "cancelled"
    ] | None = None


class ChatSubmissionPreLedgerAbsenceResponse(BaseModel):
    """Versioned proof that this principal has no durable submission ledger row."""

    protocol_version: Literal["chat_submission_resolution.v2"] = "chat_submission_resolution.v2"
    submission_id: str
    state: Literal["absent_before_ledger"] = "absent_before_ledger"

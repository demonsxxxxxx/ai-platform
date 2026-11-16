"""Versioned native coverage and the deliberately small public continuation view."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.context.domain.conversation_authority import (
    SCHEMA_VERSION as AUTHORITY_V2,
    make_authority_receipt,
    validate_authority_receipt,
)

AUTHORITY_V3 = "ai-platform.conversation-authority.v3"
RECOVERY_SCHEMA_VERSION = "ai-platform.conversation-recovery.v1"
ACCEPTED_MESSAGE_COVERAGE = "native_accepted_messages"
PUBLIC_REASONS = frozenset({
    "settling", "native_ready", "interrupted", "controlled_failure", "fault_candidate",
    "writer_unverified", "workspace_unsaved", "native_unavailable", "capacity_exhausted",
    "external_effect_unknown", "unavailable",
})


def make_provider_coverage_authority_receipt(
    *, coverage: Mapping[str, Any], scope: Mapping[str, str],
    through_session_generation: int, current_message_id: str | None,
) -> dict[str, Any]:
    """Admission conversion: old v2 counts remain successful business messages only."""
    receipt = make_authority_receipt(
        scope=scope, through_session_generation=through_session_generation,
        current_message_id=current_message_id, message_count=coverage["message_count"],
        source_sha256=coverage["source_sha256"],
    )
    version = coverage.get("authority_schema_version", AUTHORITY_V2)
    if version == AUTHORITY_V2:
        return receipt
    if version != AUTHORITY_V3:
        raise ValueError("conversation_authority_schema_invalid")
    return {
        **{key: value for key, value in receipt.items() if key != "message_count"},
        "schema_version": AUTHORITY_V3,
        "coverage_kind": ACCEPTED_MESSAGE_COVERAGE,
        "accepted_message_count": receipt["message_count"],
    }


def validate_provider_authority_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize for internal consumers without changing the persisted v2 validator."""
    if receipt.get("schema_version") != AUTHORITY_V3:
        return validate_authority_receipt(receipt)
    if (receipt.get("coverage_kind") != ACCEPTED_MESSAGE_COVERAGE
        or "message_count" in receipt):
        raise ValueError("conversation_authority_coverage_invalid")
    validated = validate_authority_receipt({
        **receipt, "schema_version": AUTHORITY_V2,
        "message_count": receipt.get("accepted_message_count"),
    })
    return {**validated, "schema_version": AUTHORITY_V3,
            "coverage_kind": ACCEPTED_MESSAGE_COVERAGE}


def provider_restore_size(epoch: Mapping[str, Any], recovery: object) -> tuple[Any, Any]:
    """Capacity is the fixed ACK prefix copied next, including all child paths."""
    count, size = epoch.get("entry_count"), epoch.get("transcript_bytes")
    if isinstance(recovery, Mapping) and recovery.get("state") in ("resumable", "recovery_candidate"):
        count = recovery.get("confirmed_entry_count", count)
        size = recovery.get("confirmed_transcript_bytes", size)
    return count, size


def public_continuation(recovery: object, *, settling: bool = False,
                        capacity_exhausted: bool = False) -> dict[str, Any]:
    """Allowlist projection; private receipt IDs and paths never leave Context."""
    if settling:
        state, reason, saved = "settling", "settling", False
    elif isinstance(recovery, Mapping) and recovery.get("schema_version") == RECOVERY_SCHEMA_VERSION:
        state = recovery.get("state")
        reason = recovery.get("reason_code")
        saved = recovery.get("workspace_saved") is True
        if (not isinstance(state, str) or state not in {"resumable", "recovery_candidate", "blocked"}
            or not isinstance(reason, str) or reason not in PUBLIC_REASONS):
            state, reason, saved = "blocked", "native_unavailable", False
        elif capacity_exhausted and state != "blocked":
            state, reason = "capacity_exhausted", "capacity_exhausted"
    elif capacity_exhausted:
        state, reason, saved = "capacity_exhausted", "capacity_exhausted", False
    else:
        state, reason, saved = "unavailable", "unavailable", False
    action = ("wait" if state == "settling" else "continue"
              if state in {"resumable", "recovery_candidate"} else "new_conversation")
    return {"state": state, "action": action, "reason_code": reason, "workspace_saved": saved}

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubmissionRejectionPorts:
    transaction: Any
    ensure_submission_principal: Any
    get_authorized_session: Any
    claim_chat_submission: Any
    finalize_chat_submission: Any
    canonical_fingerprint: Any
    is_preledger_tombstone: Any
    submission_error: Any
    conflict_error: Any


def log_safe_submission_exception(
    logger: Any,
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
            frames.append(
                f"{module_name}:{traceback_cursor.tb_lineno}:"
                f"{traceback_cursor.tb_frame.f_code.co_name}"
            )
        traceback_cursor = traceback_cursor.tb_next
    logger.error(
        "chat submission failure diagnostic_id=%s phase=%s exception_type=%s frames=%s",
        diagnostic_id,
        phase,
        type(exc).__name__,
        ",".join(frames[-8:]) or "none",
    )


async def persist_pre_persistence_rejection(
    *,
    request: Any,
    principal: Any,
    submission_id: str | None,
    query_agent_id: str | None,
    workspace_id: str | None,
    session_id: str | None,
    code: str,
    preledger_rejection_code: str,
    required_capability_unavailable_code: str,
    ports: SubmissionRejectionPorts,
) -> None:
    """Record a deterministic rejection after the mutation transaction rolled back."""

    if submission_id is None:
        return
    request_fingerprint = ports.canonical_fingerprint(
        request=request,
        principal=principal,
        query_agent_id=query_agent_id,
        code=code,
    )
    async with ports.transaction() as conn:
        await ports.ensure_submission_principal(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name,
        )
        effective_workspace_id = workspace_id
        if session_id:
            continuation_session = await ports.get_authorized_session(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
            )
            saved_workspace_id = (
                continuation_session.get("workspace_id") if continuation_session else None
            )
            if isinstance(saved_workspace_id, str) and saved_workspace_id:
                effective_workspace_id = saved_workspace_id
        row, created = await ports.claim_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            workspace_id=effective_workspace_id,
            request_fingerprint_sha256=request_fingerprint,
        )
        if not created and ports.is_preledger_tombstone(row, principal=principal):
            raise ports.submission_error(
                status_code=409,
                code=preledger_rejection_code,
            )
        if not created and row.get("request_fingerprint_sha256") != request_fingerprint:
            raise ports.conflict_error(
                status_code=409,
                detail="submission_payload_mismatch",
            )
        if not created and row.get("state") == "rejected_before_persist":
            if (
                code == required_capability_unavailable_code
                and row.get("rejection_code") == code
            ):
                return
            raise ports.submission_error(
                status_code=409,
                code=str(row.get("rejection_code") or "chat_submission_rejected"),
            )
        if created or row.get("state") == "resolving":
            await ports.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="rejected_before_persist",
                workspace_id=effective_workspace_id,
                submission_disposition="rejected_before_persist",
                rejection_code=code,
            )


__all__ = [
    "SubmissionRejectionPorts",
    "log_safe_submission_exception",
    "persist_pre_persistence_rejection",
]

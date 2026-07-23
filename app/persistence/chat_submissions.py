"""Durable chat-submission persistence operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg import AsyncConnection

from app.persistence import RepositoryNotFoundError


def chat_submission_fingerprint(
    request_payload: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
) -> str:
    """Hash the complete client-visible chat intent in a principal scope."""

    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "request": request_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_chat_submission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Load exactly one durable submission without crossing a principal scope."""

    cursor = await conn.execute(
        f"""
        select *
        from chat_submissions
        where tenant_id = %s and user_id = %s and submission_id = %s::uuid
        {"for update" if for_update else ""}
        """,
        (tenant_id, user_id, submission_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def claim_chat_submission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    workspace_id: str | None,
    request_fingerprint_sha256: str,
) -> tuple[dict[str, Any], bool]:
    """Atomically claim a client key or return the already-committed record."""

    cursor = await conn.execute(
        """
        insert into chat_submissions(
          tenant_id, user_id, submission_id, workspace_id,
          request_fingerprint_sha256, state, outcome_json
        )
        values (%s, %s, %s::uuid, %s, %s, 'resolving', '{}'::jsonb)
        on conflict (tenant_id, user_id, submission_id) do nothing
        returning *
        """,
        (tenant_id, user_id, submission_id, workspace_id, request_fingerprint_sha256),
    )
    created = await cursor.fetchone()
    if created is not None:
        return dict(created), True
    existing = await get_chat_submission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        for_update=True,
    )
    if existing is None:
        raise RepositoryNotFoundError("chat_submission_not_found")
    return existing, False


async def finalize_chat_submission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    state: str,
    workspace_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    submission_disposition: str | None = None,
    rejection_code: str | None = None,
    outcome_json: dict[str, Any] | None = None,
    queue_position: int | None = None,
    queue_admission_ordinal: int | None = None,
    queue_message_id: str | None = None,
) -> None:
    """Finalize one claimed submission without ever changing its request hash."""

    await conn.execute(
        """
        update chat_submissions
        set state = %s,
            workspace_id = coalesce(workspace_id, %s),
            session_id = coalesce(%s, session_id),
            run_id = coalesce(%s, run_id),
            submission_disposition = %s,
            rejection_code = %s,
            outcome_json = %s::jsonb,
            queue_position = coalesce(%s, queue_position),
            queue_admission_ordinal = coalesce(%s, queue_admission_ordinal),
            queue_message_id = coalesce(%s, queue_message_id),
            updated_at = now()
        where tenant_id = %s and user_id = %s and submission_id = %s::uuid
        """,
        (
            state,
            workspace_id,
            session_id,
            run_id,
            submission_disposition,
            rejection_code,
            json.dumps(outcome_json or {}, ensure_ascii=False),
            queue_position,
            queue_admission_ordinal,
            queue_message_id,
            tenant_id,
            user_id,
            submission_id,
        ),
    )

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app import agent_conversation_repository, repositories
from app.agent_profiles import get_public_profile
from app.auth import AuthPrincipal, require_principal
from app.chat_session_projection import session_response
from app.db import transaction
from app.models import ChatSessionsResponse
from app.validation import assert_safe_id

router = APIRouter()
_SESSION_CURSOR_VERSION = 1


def _encode_session_cursor(row: dict[str, object]) -> str:
    """Encode a non-secret keyset boundary without repository details."""

    def timestamp(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and value:
            return value
        raise ValueError("session_cursor_invalid")

    payload = {
        "v": _SESSION_CURSOR_VERSION,
        "updated_at": timestamp(row.get("updated_at")),
        "created_at": timestamp(row.get("created_at")),
        "session_id": str(row.get("id") or ""),
    }
    if not payload["session_id"]:
        raise ValueError("session_cursor_invalid")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_session_cursor(value: str) -> tuple[datetime, datetime, str]:
    """Decode and validate one opaque Agent conversation keyset cursor."""

    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
        )
        if not isinstance(payload, dict) or payload.get("v") != _SESSION_CURSOR_VERSION:
            raise ValueError
        updated_at = datetime.fromisoformat(
            str(payload["updated_at"]).replace("Z", "+00:00")
        )
        created_at = datetime.fromisoformat(
            str(payload["created_at"]).replace("Z", "+00:00")
        )
        session_id = str(payload["session_id"])
        if (
            updated_at.tzinfo is None
            or created_at.tzinfo is None
            or not session_id
            or len(session_id) > 200
        ):
            raise ValueError
        return updated_at, created_at, session_id
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as exc:
        raise HTTPException(status_code=400, detail="session_cursor_invalid") from exc


@router.get(
    "/chat/sessions",
    response_model=ChatSessionsResponse,
    response_model_exclude_none=True,
)
async def list_sessions(
    agent_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatSessionsResponse:
    """List generic Sessions or one authorized Agent/revision history page."""

    agent_scope_requested = (
        agent_id is not None or revision is not None or cursor is not None
    )
    if not agent_scope_requested:
        async with transaction() as conn:
            rows = await repositories.list_authorized_sessions(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        return ChatSessionsResponse(sessions=[session_response(row) for row in rows])

    if agent_id is None or revision is None:
        raise HTTPException(
            status_code=400, detail="agent_conversation_scope_incomplete"
        )
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    boundary = _decode_session_cursor(cursor) if cursor is not None else None
    async with transaction() as conn:
        # Immutable N history remains readable after N+1, but a withdrawn or
        # unpublished current Agent must still fail closed at the API boundary.
        await get_public_profile(conn, principal=principal, agent_id=safe_agent_id)
        rows = await agent_conversation_repository.list_authorized_agent_conversations(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            agent_id=safe_agent_id,
            revision=revision,
            cursor=boundary,
            limit=limit + 1,
        )
    page = rows[:limit]
    next_cursor = (
        _encode_session_cursor(page[-1]) if len(rows) > limit and page else None
    )
    return ChatSessionsResponse(
        sessions=[session_response(row) for row in page],
        next_cursor=next_cursor,
    )

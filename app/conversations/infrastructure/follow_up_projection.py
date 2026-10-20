"""Public turn projection for follow-up questions, owned by Conversations infrastructure."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.platform.public_payload import sanitize_public_text

PUBLIC_FOLLOW_UP_ROLES = frozenset({"user", "assistant"})


def public_follow_up_turn(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Keep one ordered public turn; drop private roles and empty text."""

    role = str(row.get("role") or "")
    if role not in PUBLIC_FOLLOW_UP_ROLES:
        return None
    text = sanitize_public_text(row.get("content")).strip()
    return (role, text) if text else None


def public_follow_up_turns(
    rows: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """Project authorized message rows into ordered, redacted public turns."""

    turns: list[tuple[str, str]] = []
    for row in rows:
        turn = public_follow_up_turn(row)
        if turn is not None:
            turns.append(turn)
    return turns

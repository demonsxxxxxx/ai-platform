"""Scoped, ready conversation checkpoint ancestry for Snapshot and Worker."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.context.domain.conversation_authority import validate_authority_receipt


def _boundary(row: dict[str, Any], prefix: str) -> dict[str, str]:
    value = row[f"{prefix}_created_at"]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("conversation_checkpoint_range_invalid")
    return {"created_at": value.astimezone(UTC).isoformat(), "id": row[f"{prefix}_id"]}


def _ready_chain(rows: list[dict[str, Any]], scope: dict[str, str]) -> dict[str, Any]:
    if not rows or len(rows) > 1024 or rows[-1]["predecessor_checkpoint_id"] is not None:
        raise ValueError("conversation_checkpoint_chain_invalid")
    for index, row in enumerate(rows):
        if (row["state"] != "ready" or any(row[key] != scope[key] for key in scope)
            or row["predecessor_checkpoint_id"] != (rows[index + 1]["id"] if index + 1 < len(rows) else None)
            or type(row["covered_message_count"]) is not int or row["covered_message_count"] < 1
            or type(row["covered_turn_count"]) is not int or row["covered_turn_count"] < 1
            or not isinstance(row["summary_text"], str) or not row["summary_text"]
            or hashlib.sha256(row["summary_text"].encode("utf-8")).hexdigest() != row["summary_sha256"]):
            raise ValueError("conversation_checkpoint_chain_invalid")
        validate_authority_receipt({
            "schema_version": "ai-platform.conversation-authority.v2", "scope": scope,
            "through_session_generation": row["through_session_generation"],
            "range_start": _boundary(row, "range_start"),
            "range_end": _boundary(row, "range_end"),
            "message_count": row["covered_message_count"],
            "source_sha256": row["source_sha256"],
            "base_checkpoint_id": None, "base_checkpoint_source_sha256": None,
            "base_checkpoint_summary_sha256": None,
            "tail_message_count": row["covered_message_count"],
            "tail_sha256": "0" * 64, "current_message_id": None,
        })
        if index + 1 < len(rows):
            previous = rows[index + 1]
            if ((row["range_start_created_at"], row["range_start_id"])
                <= (previous["range_end_created_at"], previous["range_end_id"])):
                raise ValueError("conversation_checkpoint_chain_invalid")
    first, root = rows[0], rows[-1]
    total = sum(row["covered_message_count"] for row in rows)
    return {
        "id": first["id"], "scope": scope,
        "owner_run_id": first["owner_run_id"],
        "source_snapshot_id": first["source_snapshot_id"],
        "predecessor_checkpoint_id": first["predecessor_checkpoint_id"],
        "range_start": _boundary(root, "range_start"),
        "range_end": _boundary(first, "range_end"),
        "message_count": total, "source_sha256": first["source_sha256"],
        "summary_text": first["summary_text"], "summary_sha256": first["summary_sha256"],
        "through_session_generation": first["through_session_generation"],
    }


async def load_ready_checkpoint(
    conn: AsyncConnection, *, scope: dict[str, str], run_id: str,
    checkpoint_id: str | None = None,
) -> dict[str, Any] | None:
    """Use a concrete checkpoint ID or the latest ready predecessor of this Run."""
    cursor = await conn.execute(
        """
        with recursive chosen as (
          select checkpoint.*
          from conversation_context_checkpoints checkpoint
          join runs current on current.tenant_id = checkpoint.tenant_id
            and current.workspace_id = checkpoint.workspace_id
            and current.user_id = checkpoint.user_id
            and current.session_id = checkpoint.session_id
            and current.agent_id = checkpoint.agent_id
          where checkpoint.tenant_id = %s and checkpoint.workspace_id = %s
            and checkpoint.user_id = %s and checkpoint.session_id = %s
            and checkpoint.agent_id = %s and current.id = %s
            and checkpoint.state = 'ready'
            and checkpoint.through_session_generation <= current.session_generation
            and (%s::text is null or checkpoint.id = %s)
          order by checkpoint.range_end_created_at desc, checkpoint.range_end_id desc
          limit 1
        ), ancestry as (
          select chosen.*, 1 as depth, array[chosen.id] as visited from chosen
          union all
          select prior.*, ancestry.depth + 1, ancestry.visited || prior.id
          from ancestry
          join conversation_context_checkpoints prior
            on prior.id = ancestry.predecessor_checkpoint_id
            and prior.tenant_id = ancestry.tenant_id
            and prior.workspace_id = ancestry.workspace_id
            and prior.user_id = ancestry.user_id
            and prior.session_id = ancestry.session_id
            and prior.agent_id = ancestry.agent_id
          where ancestry.depth < 1024 and not prior.id = any(ancestry.visited)
        )
        select * from ancestry order by depth asc
        """,
        (*[scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")],
         run_id, checkpoint_id, checkpoint_id),
    )
    rows = list(await cursor.fetchall())
    if not rows:
        return None
    if checkpoint_id is not None and rows[0]["id"] != checkpoint_id:
        raise ValueError("conversation_checkpoint_identity_invalid")
    return _ready_chain(rows, scope)

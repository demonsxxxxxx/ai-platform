"""Compatibility check for historical ready conversation checkpoints."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.context.infrastructure.checkpoints_postgres import load_ready_checkpoint

_SCOPE = {
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "user_id": "user-a",
    "session_id": "session-a",
    "agent_id": "agent-a",
}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params):
        assert sql.count("%s") == len(params)
        return Cursor(self.rows)


def _row(checkpoint_id, predecessor, start, end, summary):
    return {
        **_SCOPE,
        "id": checkpoint_id,
        "predecessor_checkpoint_id": predecessor,
        "owner_run_id": "run-source",
        "source_snapshot_id": "ctx-source",
        "state": "ready",
        "range_start_created_at": start,
        "range_start_id": "msg-001" if predecessor is None else "msg-003",
        "range_end_created_at": end,
        "range_end_id": "msg-002" if predecessor is None else "msg-004",
        "covered_message_count": 2,
        "covered_turn_count": 1,
        "through_session_generation": 4,
        "summary_text": summary,
        "summary_sha256": hashlib.sha256(summary.encode()).hexdigest(),
        "source_sha256": "a" * 64,
    }


@pytest.mark.asyncio
async def test_historical_ready_checkpoint_remains_readable():
    now = datetime(2026, 9, 15, tzinfo=UTC)
    newest = _row(
        "ccp-new", "ccp-old", now + timedelta(minutes=2),
        now + timedelta(minutes=3), "summary of both turns",
    )
    root = _row("ccp-old", None, now, now + timedelta(minutes=1), "summary of first turn")

    result = await load_ready_checkpoint(
        Connection([newest, root]),
        scope=_SCOPE,
        run_id="run-current",
        checkpoint_id="ccp-new",
    )

    assert result["message_count"] == 4
    assert result["summary_text"] == "summary of both turns"

"""Assemble bounded public answer ledgers and SDK stop facts, never arbitrary results."""
from typing import Any

from app.runs.application.admin_run_monitor import build_admin_worker_execution
from app.streaming.api import project_persisted_assistant_text_messages
from app.runs.domain.diagnostics import sanitize_run_diagnostic_text


def build_admin_public_result(evidence: dict[str, Any], *, tenant_id: str, run_id: str) -> dict[str, Any]:
    rows = [row for row in evidence["rows"]
            if "__stream_v4" not in row["payload_json"] or row.get("v4_attempt_authorized") is True]
    events = [{"event_id": row["id"], "type": row["event_type"], "sequence": row["sequence"],
               "visible_to_user": row["visible_to_user"], "payload": row["payload_json"],
               "created_at": row.get("created_at")} for row in rows]
    result = build_admin_worker_execution(events,
        sanitize_text=lambda text: sanitize_run_diagnostic_text(text, max_bytes=512 * 1024) or "",
        part_messages=project_persisted_assistant_text_messages(rows, tenant_id=tenant_id, run_id=run_id))
    result["coverage"] = {key: value for key, value in evidence.items() if key != "rows"}
    return result

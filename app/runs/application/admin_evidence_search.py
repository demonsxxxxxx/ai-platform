"""Bounded search after administrator projection; never search private source text."""
import json
from typing import Any

from app.runs.application.admin_trajectory import project_admin_trajectory_page
from app.runs.domain.diagnostics import sanitize_run_diagnostic_text


def search_admin_evidence(*, rows: list[dict[str, Any]], query: str,
                          attempt_id: str | None, limit: int, diagnostics: dict[str, Any] | None, match_offset: int = 0) -> dict[str, Any]:
    page = project_admin_trajectory_page(rows, sanitize_text=sanitize_run_diagnostic_text)
    events = [event for event in page["events"] if not attempt_id or event.get("attempt_id") == attempt_id]
    term = query.casefold()
    matches = [{"kind": "event", "sequence": event["sequence"], "event_id": event["event_id"],
                "attempt_id": event.get("attempt_id"), "summary": event.get("summary") or event.get("outcome") or event["source_type"],
                "context_after_sequence": max(0, event["sequence"] - 10)}
               for event in events if term in json.dumps(event, ensure_ascii=False).casefold()]
    if diagnostics:
        observations = diagnostics.get("details", {}).get("observations", [])
        for observation in observations:
            if attempt_id and observation.get("attempt_id") != attempt_id:
                continue
            safe = {name: observation.get(name) for name in ("observation_id", "parent_observation_id", "attempt_id", "source", "stage", "error_code")}
            sdk = observation.get("sdk") or {}
            safe["exception_type"] = sdk.get("exception_type")
            safe["exception_message"] = sdk.get("exception_message")
            if term in json.dumps(safe, ensure_ascii=False, default=str).casefold():
                matches.append({"kind": "diagnostic", "observation_id": observation.get("observation_id"),
                                "attempt_id": observation.get("attempt_id"), "summary": observation.get("error_code") or "已保留诊断证据"})
    remaining = matches[match_offset:]
    return {"matches": remaining[:limit], "matches_truncated": len(remaining) > limit,
            "next_match_offset": match_offset + min(len(remaining), limit),
            "omitted": page["omitted"], "searched_source_count": len(rows),
            "scope": "disclosable_projection", "excluded": ["prompt", "message_chunks", "private_payload", "unretained_evidence"]}

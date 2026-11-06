"""Content-free execution configuration and persisted Run relationship projections."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.runs.domain.diagnostics import sanitize_run_diagnostic_text
from app.runs.domain.thinking import RUN_THINKING_EFFORT_INPUT_KEY, THINKING_EFFORT_LEVELS
from app.runs.domain.trigger_source import read_run_trigger

ADMIN_RUN_INSPECTION_VERSION = "ai-platform.admin-run-inspection.v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")


def _identity(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _number(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def build_admin_run_inspection(
    run: Mapping[str, Any], *, skills: Sequence[Mapping[str, Any]],
    relationships: Sequence[Mapping[str, Any]], input_value: object = None,
    attachments: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Never expose arbitrary input/result mappings, prompts, schemas or file paths."""
    supplied = input_value if isinstance(input_value, Mapping) else {}
    inputs: dict[str, object] = {}
    execution_input = supplied.get("input") if isinstance(supplied.get("input"), Mapping) else supplied
    effort = execution_input.get(RUN_THINKING_EFFORT_INPUT_KEY)
    if isinstance(effort, str) and effort in THINKING_EFFORT_LEVELS:
        inputs["thinking_effort"] = effort
    input_files = supplied.get("file_ids")
    if isinstance(input_files, list):
        inputs["attachment_count"] = len(input_files)
    links = []
    for row in relationships[:50]:
        source = _identity(row.get("source_run_id"))
        child = _identity(row.get("child_run_id"))
        if not source or not child or run.get("run_id") not in {source, child}:
            continue
        action = row.get("action")
        links.append({
            "source_run_id": source, "child_run_id": child,
            "action": action if action in {"retry", "resume", "copy"} else "unknown",
            "status": row.get("status") if row.get("status") in {"queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"} else "unknown",
            "created_at": row.get("created_at"),
            "user_id": _identity(row.get("user_id")),
        })
    trigger = read_run_trigger(dict(supplied)) or next((item["action"] for item in links if item["child_run_id"] == run.get("run_id") and item["action"] != "unknown"), None)
    profile_hash = run.get("admitted_agent_profile_hash")
    profile_hash = profile_hash if isinstance(profile_hash, str) and re.fullmatch(r"[0-9a-f]{64}", profile_hash) else None
    return {
        "contract_version": ADMIN_RUN_INSPECTION_VERSION,
        "run_id": run.get("run_id"),
        "input": {"fields": inputs, "coverage": "partial" if inputs or attachments else "not_collected",
                  "attachments": [{"file_id": _identity(item.get("file_id")),
                                   "name": sanitize_run_diagnostic_text(item.get("original_name"), max_bytes=512),
                                   "content_type": sanitize_run_diagnostic_text(item.get("content_type"), max_bytes=256),
                                   "size_bytes": _number(item.get("size_bytes")),
                                   "availability": "active" if item.get("lifecycle_state") == "active" else "unavailable"}
                                  for item in attachments[:100]], "attachments_truncated": len(attachments) > 100,
                  "excluded": ["prompt", "private_executor_payload", "tool_arguments"]},
        "configuration": {
            "source": "persisted_run_snapshot",
            "model_id": _identity(run.get("model_id")),
            "model_value": sanitize_run_diagnostic_text(run.get("model_value"), max_bytes=256) or None,
            "model_gateway_revision": _number(run.get("model_gateway_revision")),
            "auto_compact_window": _number(run.get("auto_compact_window")),
            "max_output_tokens": _number(run.get("max_output_tokens")),
            "profile_revision": _number(run.get("admitted_agent_profile_revision")),
            "profile_hash": profile_hash,
            "skills": [{"skill_id": _identity(item.get("skill_id")),
                        "version": sanitize_run_diagnostic_text(item.get("skill_version"), max_bytes=128) or None,
                        "content_hash": item.get("content_hash") if isinstance(item.get("content_hash"), str) and re.fullmatch(r"[0-9a-f]{64}", item["content_hash"]) else None}
                       for item in skills[:100]],
            "skills_truncated": len(skills) > 100,
        },
        "history": {"relationships": links, "truncated": len(relationships) > 50,
                    "source_run_id": next((item["source_run_id"] for item in links if item["child_run_id"] == run.get("run_id")), None)},
        "trigger": {"kind": trigger, "coverage": "recorded" if trigger else "not_collected"},
    }

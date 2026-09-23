"""Read-only, bounded trajectory projection of committed public Run events.

The source of order and durability is ``run_events``. This read model never
re-executes an action and never promotes private executor payloads to events.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.streaming.api import (
    V4_METADATA_KEY,
    V4_METADATA_VERSION,
    V4_PUBLIC_STAGE,
    V4ProjectionError,
    validate_public_application_payload_v4,
)

ADMIN_TRAJECTORY_CONTRACT_VERSION = "ai-platform.admin-run-trajectory.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
_KINDS = {
    "message.started": "message",
    "message.delta": "message",
    "message.completed": "message",
    "commentary.delta": "message",
    "tool.started": "action",
    "tool.completed": "observation",
    "tool.failed": "error",
    "tool.denied": "error",
    "agent.progress": "observation",
    "model.completed": "observation",
    "run.failed": "error",
}


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def _recorded_at(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and len(value) <= 64 else None


def project_admin_trajectory_page(
    rows: Sequence[Mapping[str, Any]],
    *,
    sanitize_text: Callable[[str], str],
) -> dict[str, object]:
    """Project one source page; cursor advancement belongs to the route.

    An invalid, legacy or private source row is counted, never interpreted as a
    generic JSON event. Message text is intentionally absent: arbitrary chunks
    can split a secret and must only be shown after whole-message redaction.
    """

    events: list[dict[str, object]] = []
    omitted = {"private": 0, "unsupported": 0, "invalid": 0}
    for row in rows:
        if row.get("visible_to_user") is not True:
            omitted["private"] += 1
            continue
        source_type = row.get("event_type")
        if source_type not in _KINDS:
            omitted["unsupported"] += 1
            continue
        raw_payload = row.get("payload_json")
        if not isinstance(raw_payload, Mapping):
            omitted["invalid"] += 1
            continue
        metadata = raw_payload.get(V4_METADATA_KEY)
        if row.get("stage") != V4_PUBLIC_STAGE or not isinstance(metadata, Mapping) or metadata.get("version") != V4_METADATA_VERSION:
            omitted["unsupported"] += 1
            continue
        payload = {key: value for key, value in raw_payload.items() if key != V4_METADATA_KEY}
        try:
            payload = validate_public_application_payload_v4(source_type, payload)
        except V4ProjectionError:
            omitted["invalid"] += 1
            continue
        sequence = row.get("sequence")
        event_id = _safe_id(row.get("id"))
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1 or event_id is None:
            omitted["invalid"] += 1
            continue
        correlation = metadata
        item: dict[str, object] = {
            "event_id": event_id,
            "sequence": sequence,
            "kind": _KINDS[source_type],
            "source_type": source_type,
            "recorded_at": _recorded_at(row.get("created_at")),
            "attempt_id": _safe_id(correlation.get("attempt_id")),
            "message_id": _safe_id(correlation.get("message_id")),
            "causation_event_id": _safe_id(correlation.get("causation_event_id")),
            "operation_id": _safe_id(payload.get("operation_id")),
        }
        if source_type in {"message.delta", "commentary.delta"}:
            item["text_length"] = len(payload["delta"])
            item["summary"] = "Agent 输出片段" if source_type == "message.delta" else "Agent 过程说明片段"
        elif source_type == "message.completed":
            item["text_length"] = payload["text_length"]
            item["summary"] = "Agent 输出完成"
        elif source_type == "message.started":
            item["summary"] = "Agent 开始输出"
        elif source_type.startswith("tool."):
            item["category"] = payload["category"]
            summary = sanitize_text(payload["display_name"])
            item["summary"] = summary[:128] if isinstance(summary, str) else "工具"
            if source_type == "tool.completed":
                item["duration_ms"] = payload["duration_ms"]
                item["outcome"] = "completed"
            elif source_type == "tool.failed":
                item["duration_ms"] = payload["duration_ms"]
                item["outcome"] = payload["failure_category"]
            elif source_type == "tool.denied":
                item["outcome"] = payload["denial_code"]
        elif source_type == "agent.progress":
            item["summary"] = payload["message"]
            item["stage"] = payload["phase"]
            item["outcome"] = payload["lifecycle"]
        elif source_type == "model.completed":
            item["summary"] = "模型调用结束"
            item["duration_ms"] = payload["duration_ms"]
            item["outcome"] = payload["stop_category"]
        elif source_type == "run.failed":
            item["summary"] = "运行失败"
            code = sanitize_text(payload["code"])
            item["outcome"] = code[:128] if isinstance(code, str) else "run_failed"
        events.append(item)
    return {"events": events, "omitted": omitted}

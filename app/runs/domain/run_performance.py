"""Run-owned latency measurements; elapsed time and SDK work never share a sample."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.runs.domain.diagnostics import (
    _validated_payload,
    serialize_run_diagnostics_payload,
    sanitize_run_diagnostic_text,
)
from app.runs.domain.run_status import is_terminal_run_status

BROWSER_LATENCY_MEASUREMENT = "browser_dom_commit.v1"
BROWSER_LATENCY_MAX_MS = 24 * 60 * 60 * 1_000
_PUBLIC_TEXT_TYPES = frozenset({"message.delta", "message.part.delta"})


def timestamp(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if isinstance(value, datetime) and value.utcoffset() is not None else None


def elapsed_ms(start: object, end: object) -> int | None:
    start_at, end_at = timestamp(start), timestamp(end)
    if start_at is None or end_at is None or end_at < start_at:
        return None
    return int((end_at - start_at).total_seconds() * 1_000)


def browser_latency_ms(value: object) -> int | None:
    if not isinstance(value, Mapping) or value.get("measurement") != BROWSER_LATENCY_MEASUREMENT:
        return None
    duration = value.get("duration_ms")
    if value.get("visibility") != "visible" or type(duration) is not int:
        return None
    return duration if 0 <= duration <= BROWSER_LATENCY_MAX_MS else None


def merge_browser_latency(current: object, measurement: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Keep the first browser observation without turning it into a failure."""
    if browser_latency_ms(measurement) is None:
        raise ValueError("invalid_browser_latency")
    payload = _validated_payload(current)
    if browser_latency_ms(payload.get("browser_latency")) is not None:
        return payload, False
    payload["browser_latency"] = {
        "measurement": BROWSER_LATENCY_MEASUREMENT,
        "duration_ms": browser_latency_ms(measurement),
        "visibility": "visible",
        "received_at": timestamp(measurement.get("received_at")),
    }
    payload["browser_latency"]["received_at"] = (
        payload["browser_latency"]["received_at"].isoformat()
        if payload["browser_latency"]["received_at"] is not None else None
    )
    serialize_run_diagnostics_payload(payload)
    return payload, True


def first_public_text_at(events: Sequence[Mapping[str, Any]], *, run_created_at: object) -> datetime | None:
    created_at = timestamp(run_created_at)
    candidates: list[datetime] = []
    for event in events:
        payload = event.get("payload", event.get("payload_json"))
        payload = payload if isinstance(payload, Mapping) else {}
        recorded_at = timestamp(event.get("created_at"))
        if (
            event.get("type", event.get("event_type")) in _PUBLIC_TEXT_TYPES
            and event.get("visible_to_user") is True
            and isinstance(payload.get("delta"), str)
            and payload["delta"].strip()
            and recorded_at is not None
            and created_at is not None
            and recorded_at >= created_at
        ):
            candidates.append(recorded_at)
    return min(candidates) if candidates else None


def build_run_latency(
    run: Mapping[str, Any],
    *,
    first_text_at: object = None,
    browser_measurement: object = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or datetime.now(timezone.utc)
    terminal = is_terminal_run_status(run.get("status"))
    finished_at = run.get("finished_at") if terminal else None
    if timestamp(finished_at) is not None and timestamp(finished_at) > observed_at:
        finished_at = None
    text_at = timestamp(first_text_at)
    if text_at is not None and (
        elapsed_ms(run.get("created_at"), text_at) is None
        or text_at > observed_at
        or (timestamp(finished_at) is not None and text_at > timestamp(finished_at))
    ):
        text_at = None
    started_at = timestamp(run.get("started_at"))
    if started_at is not None and started_at > observed_at:
        started_at = None
    return {
        "total_ms": elapsed_ms(run.get("created_at"), finished_at),
        "queue_ms": elapsed_ms(run.get("queued_at"), started_at),
        "execution_ms": elapsed_ms(started_at, finished_at),
        "server_first_text_ms": elapsed_ms(run.get("created_at"), text_at),
        "browser_first_text_ms": browser_latency_ms(browser_measurement),
        "elapsed_ms": elapsed_ms(run.get("created_at"), finished_at if terminal else observed_at),
        "first_public_text_at": text_at,
        "browser_measurement": BROWSER_LATENCY_MEASUREMENT if browser_latency_ms(browser_measurement) is not None else None,
        "observed_at": observed_at,
    }


def build_run_investigation(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    attempts: Sequence[Mapping[str, Any]] = (),
    browser_measurement: object = None,
    first_text_at: object = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Project receipt-time intervals separately from measured SDK/tool durations."""
    observed_at = observed_at or datetime.now(timezone.utc)
    latency = build_run_latency(
        run, first_text_at=first_text_at if first_text_at is not None else first_public_text_at(events, run_created_at=run.get("created_at")),
        browser_measurement=browser_measurement, observed_at=observed_at,
    )
    stages: list[dict[str, Any]] = []

    def stage(
        stage_id: str, label: str, start: object, end: object, *,
        duration: int | None = None, source: str = "run_lifecycle",
        status: str = "recorded", attempt_id: str | None = None,
        operation_id: str | None = None, event_id: str | None = None,
        causation_event_id: str | None = None,
    ) -> dict[str, Any]:
        start_at, end_at = timestamp(start), timestamp(end)
        if start_at is not None and start_at > observed_at:
            start_at = None
        if end_at is not None and end_at > observed_at:
            end_at = None
        row = {
            "stage_id": stage_id, "label": label, "source": source,
            "status": status, "attempt_id": attempt_id, "operation_id": operation_id,
            "event_id": event_id, "causation_event_id": causation_event_id,
            "started_at": start_at, "finished_at": end_at,
            "offset_ms": elapsed_ms(run.get("created_at"), start_at),
            "receipt_duration_ms": elapsed_ms(start_at, end_at),
            "duration_ms": duration if source != "run_lifecycle" else elapsed_ms(start_at, end_at),
        }
        stages.append(row)
        return row

    stage("admission", "提交与入队", run.get("created_at"), run.get("queued_at"))
    stage("queue", "排队等待", run.get("queued_at"), run.get("started_at"), status="waiting" if run.get("status") == "queued" else "recorded")
    stage("execution", "执行与结果落库", run.get("started_at"), run.get("finished_at"), status=str(run.get("status") or "unknown"))
    if latency["first_public_text_at"] is not None:
        stage("first_text", "首段公开文本入库", latency["first_public_text_at"], latency["first_public_text_at"], source="platform_ledger", duration=0)

    owned_attempts = {str(item["attempt_id"]) for item in attempts if item.get("attempt_id")}
    for attempt in attempts:
        attempt_id = str(attempt.get("attempt_id") or "") or None
        stage(
            f"attempt:{attempt_id}", f"第 {attempt.get('ordinal', '?')} 次执行尝试",
            attempt.get("started_at"), attempt.get("finished_at"),
            source="run_attempt", duration=elapsed_ms(attempt.get("started_at"), attempt.get("finished_at")),
            status=str(attempt.get("status") or "unknown"), attempt_id=attempt_id,
        )

    tools: dict[tuple[str | None, str], dict[str, Any]] = {}
    unbound_event_count = 0
    for event in events:
        if event.get("visible_to_user") is not True:
            continue
        event_type = event.get("type", event.get("event_type"))
        payload = event.get("payload", event.get("payload_json"))
        payload = payload if isinstance(payload, Mapping) else {}
        attempt_id = event.get("attempt_id")
        if isinstance(attempt_id, str) and attempt_id and attempt_id not in owned_attempts:
            unbound_event_count += 1
            continue
        attempt_id = attempt_id if isinstance(attempt_id, str) and attempt_id in owned_attempts else None
        event_id = str(event.get("event_id", event.get("id")) or "") or None
        cause = event.get("causation_event_id")
        cause = cause if isinstance(cause, str) else None
        recorded_at = event.get("created_at")
        duration = payload.get("duration_ms")
        duration = duration if type(duration) is int and duration >= 0 else None
        if event_type == "model.completed":
            stage(
                f"sdk:{event_id}", "SDK 执行观测", None, recorded_at,
                source="sdk_reported", duration=duration, attempt_id=attempt_id,
                event_id=event_id, causation_event_id=cause,
            )
            continue
        if event_type not in {"tool.started", "tool.completed", "tool.failed", "tool.denied"}:
            continue
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            continue
        key = (attempt_id, operation_id if attempt_id is not None else f"{operation_id}:{event_id}")
        row = tools.get(key)
        if row is None:
            label = sanitize_run_diagnostic_text(payload.get("display_name"), max_bytes=256) or "工具调用"
            row = stage(
                f"tool:{attempt_id}:{operation_id}" if attempt_id else f"tool:unknown:{operation_id}:{event_id}", label,
                recorded_at if event_type == "tool.started" else None, None,
                source="sdk_tool_hooks", status="running", attempt_id=attempt_id,
                operation_id=operation_id, event_id=event_id, causation_event_id=cause,
            )
            tools[key] = row
        if event_type != "tool.started":
            finished_at = timestamp(recorded_at)
            if finished_at is not None and finished_at > observed_at:
                finished_at = None
            row.update(
                finished_at=finished_at, duration_ms=duration,
                receipt_duration_ms=elapsed_ms(row["started_at"], finished_at),
                status={"tool.completed": "succeeded", "tool.failed": "failed", "tool.denied": "denied"}[event_type],
                event_id=event_id, causation_event_id=cause,
            )

    return {
        **latency,
        "stages": stages,
        "missing_measurements": [
            name for name in ("total_ms", "queue_ms", "execution_ms", "server_first_text_ms", "browser_first_text_ms")
            if latency[name] is None
        ],
        "timeline_source": "platform_receipts",
        "durations_additive": False,
        "unbound_event_count": unbound_event_count,
    }

from __future__ import annotations

from typing import Any

from app.execution.application.worker_result_projection import int_payload_value


def step_key_from_event(payload: dict[str, Any]) -> str:
    explicit = payload.get("step_key")
    if explicit:
        return str(explicit)
    role = str(payload.get("role") or "agent").strip() or "agent"
    step_index = int_payload_value(payload, "step_index", 1)
    return f"{role}-{step_index}"


def _normalize_step_status(status: object) -> str:
    value = str(status or "")
    return "cancelled" if value == "canceled" else value


def multi_agent_result_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    summary_steps = []
    reused_step_keys = []
    completed_step_outputs = {}
    for row in steps:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict):
            payload = {}
        step_key = str(row["step_key"])
        output = payload.get("output")
        checkpoint_reused = bool(payload.get("checkpoint_reused"))
        status = _normalize_step_status(row.get("status"))
        if checkpoint_reused:
            reused_step_keys.append(step_key)
        if output is not None and status == "succeeded":
            completed_step_outputs[step_key] = str(output)
        summary_step = {
            "step_key": step_key,
            "status": status,
            "role": row.get("role"),
            "sequence": int_payload_value(row, "sequence", 0),
            "depends_on": list(payload.get("depends_on") or []),
            "checkpoint_reused": checkpoint_reused,
            "output": str(output) if output is not None else None,
            "error_code": (
                str(payload["error_code"])
                if payload.get("error_code") is not None
                else None
            ),
            "error": str(payload["error"])
            if payload.get("error") is not None
            else None,
            "missing_dependencies": [
                str(item) for item in payload.get("missing_dependencies") or []
            ],
        }
        if isinstance(payload.get("skill_ids"), list):
            summary_step["skill_ids"] = [str(item) for item in payload["skill_ids"]]
        if isinstance(payload.get("mcp_tool_ids"), list):
            summary_step["mcp_tool_ids"] = [
                str(item) for item in payload["mcp_tool_ids"]
            ]
        if isinstance(payload.get("resource_limits"), dict):
            summary_step["resource_limits"] = dict(payload["resource_limits"])
        if payload.get("sandbox_mode") is not None:
            summary_step["sandbox_mode"] = str(payload["sandbox_mode"])
        if isinstance(payload.get("browser_enabled"), bool):
            summary_step["browser_enabled"] = payload["browser_enabled"]
        summary_steps.append(summary_step)
    counts = {
        "total": len(summary_steps),
        "pending": sum(1 for item in summary_steps if item["status"] == "pending"),
        "succeeded": sum(1 for item in summary_steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in summary_steps if item["status"] == "failed"),
        "running": sum(1 for item in summary_steps if item["status"] == "running"),
        "cancelled": sum(1 for item in summary_steps if item["status"] == "cancelled"),
        "reused": sum(1 for item in summary_steps if item["checkpoint_reused"]),
        "blocked": sum(1 for item in summary_steps if item["missing_dependencies"]),
    }
    return {
        "steps": summary_steps,
        "reused_step_keys": reused_step_keys,
        "completed_step_outputs": completed_step_outputs,
        "counts": counts,
    }

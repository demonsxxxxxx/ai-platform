from typing import Any

from app.control_plane_contracts import (
    RUN_EXECUTION_KIND_SKILL,
    RUN_CONTRACT_VERSION,
)


def intent_event_specs(decision: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        {
            "event_type": "intent_detected",
            "stage": "intent",
            "message": "已识别处理方式",
            "payload": {
                "visible_to_user": True,
                "severity": "info",
                "intent": decision.get("intent"),
                "confidence": decision.get("confidence"),
                "selected_capability": decision.get("selected_capability"),
                "reason": decision.get("reason"),
            },
        }
    ]
    if decision.get("confirmed_by_user"):
        events.append(
            {
                "event_type": "intent_confirmed",
                "stage": "intent",
                "message": "已按确认的能力开始处理",
                "payload": {
                    "visible_to_user": True,
                    "severity": "info",
                    "selected_capability": decision.get("selected_capability"),
                },
            }
        )
    return events


def initial_run_event_specs(
    *,
    agent_id: str,
    skill_id: str | None,
    skill_version: str | None,
    executor_type: str,
    file_ids: list[str],
    source: str,
    execution_kind: str = RUN_EXECUTION_KIND_SKILL,
) -> list[dict[str, Any]]:
    base_payload = {
        "agent_id": agent_id,
        "execution_kind": execution_kind,
        "executor_type": executor_type,
        "contract_version": RUN_CONTRACT_VERSION,
        "source": source,
        "severity": "info",
        "visible_to_user": True,
    }
    if execution_kind == RUN_EXECUTION_KIND_SKILL:
        base_payload.update(
            {
                "skill_id": skill_id,
                "skill_version": skill_version,
            }
        )
    events: list[dict[str, Any]] = [
        {
            "event_type": "queued",
            "stage": "queue",
            "message": "任务已进入队列",
            "payload": base_payload,
        }
    ]
    if execution_kind == RUN_EXECUTION_KIND_SKILL:
        events.append(
            {
                "event_type": "skill_selected",
                "stage": "planning",
                "message": "已选择后台能力",
                "payload": base_payload,
            }
        )
    if file_ids:
        events.append(
            {
                "event_type": "file_bound",
                "stage": "input",
                "message": f"已绑定 {len(file_ids)} 个文件",
                "payload": {
                    **base_payload,
                    "file_ids": file_ids,
                    "file_count": len(file_ids),
                },
            }
        )
    return events

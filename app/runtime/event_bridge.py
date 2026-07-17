from app.runtime.kernel_contracts import AgentEvent


EVENT_STAGE_MAP = {
    "run_queued": "queue",
    "run_started": "runtime",
    "runtime_container_started": "runtime",
    "assistant_delta": "message",
    "tool_call_started": "tool",
    "tool_call_delta": "tool",
    "tool_call_completed": "tool",
    "tool_permission_requested": "tool_policy",
    "tool_permission_authorized": "tool_policy",
    "tool_permission_denied": "tool_policy",
    "browser_snapshot": "browser",
    "workspace_file_changed": "workspace",
    "artifact_created": "artifact",
    "checkpoint_created": "checkpoint",
    "subagent_started": "subagent",
    "subagent_completed": "subagent",
    "subagent_failed": "subagent",
    "agent_step_started": "agent",
    "agent_step_reused": "agent",
    "agent_step_completed": "agent",
    "agent_step_blocked": "agent",
    "agent_step_failed": "agent",
    "run_failed": "runtime",
    "run_completed": "runtime",
    "run_cancelled": "control",
}


def agent_event_to_executor_event(event: AgentEvent) -> dict[str, object]:
    payload = dict(event.payload)
    if event.admin_only:
        payload["visible_to_user"] = False
        payload["admin_only"] = True
    else:
        payload.setdefault("visible_to_user", True)

    return {
        "event_type": event.type,
        "stage": EVENT_STAGE_MAP[event.type],
        "message": event.message,
        "payload": payload,
    }

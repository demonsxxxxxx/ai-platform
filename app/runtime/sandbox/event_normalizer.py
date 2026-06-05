from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.contracts import ContainerLease, ExecutorCallbackEvent


def container_started_event(lease: ContainerLease) -> AgentEvent:
    return AgentEvent(
        type="runtime_container_started",
        message="Sandbox executor container started",
        admin_only=True,
        payload={
            "container_id": lease.container_id,
            "container_name": lease.container_name,
            "provider": lease.provider,
            "sandbox_mode": lease.sandbox_mode,
            "browser_enabled": lease.browser_enabled,
        },
    )


def callback_event_to_run_events(callback: ExecutorCallbackEvent) -> list[AgentEvent]:
    events: list[AgentEvent] = []

    if callback.status == "running":
        message = callback.new_message or {}
        delta = message.get("delta") or message.get("text")
        if delta:
            events.append(
                AgentEvent(
                    type="assistant_delta",
                    message=str(delta),
                    payload={"delta": str(delta)},
                )
            )

        current_step = callback.state_patch.get("current_step")
        if current_step:
            events.append(
                AgentEvent(
                    type="tool_call_delta",
                    message=str(current_step),
                    payload={"current_step": str(current_step)},
                )
            )

    if callback.status == "completed":
        events.append(
            AgentEvent(
                type="run_completed",
                message="Executor completed",
                payload={"progress": callback.progress},
            )
        )
    elif callback.status == "failed":
        error_message = callback.error_message or "Executor failed"
        events.append(
            AgentEvent(
                type="run_failed",
                message=error_message,
                payload={"error_message": error_message},
            )
        )
    elif callback.status == "cancelled":
        events.append(
            AgentEvent(
                type="run_cancelled",
                message="Executor cancelled",
                payload={"progress": callback.progress},
            )
        )

    events.extend(callback.events)
    return events

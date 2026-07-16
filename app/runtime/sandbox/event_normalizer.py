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

    # The executor has no authority to publish a run terminal fact.  The
    # worker emits one only after its final repository transaction succeeds.
    events.extend(
        event
        for event in callback.events
        if event.type not in {"run_completed", "run_failed", "run_cancelled"}
    )
    return events

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
    mirrored_delta: str | None = None

    if callback.status in {"running", "completed"}:
        message = callback.new_message or {}
        delta = message.get("delta") or message.get("text")
        if delta:
            mirrored_delta = str(delta)
            events.append(
                AgentEvent(
                    type="assistant_delta",
                    message=mirrored_delta,
                    payload={"delta": mirrored_delta},
                )
            )

    if callback.status == "running":
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
    mirror_consumed = False
    for event in callback.events:
        if event.type in {"run_completed", "run_failed", "run_cancelled"}:
            continue
        if (
            not mirror_consumed
            and mirrored_delta is not None
            and event.type == "assistant_delta"
            and not event.admin_only
            and event.message == mirrored_delta
            and event.payload == {"delta": mirrored_delta}
        ):
            mirror_consumed = True
            continue
        events.append(event)
    return events

import concurrent.futures

import pytest

from app.executors.base import RunPayload
from app.executors.embedded_poco import EmbeddedPocoAdapter, build_run_context, build_step_sandbox_request
from app.runtime.embedded_poco_kernel import AgentStepExecutionContext
from app.runtime.event_bridge import EVENT_STAGE_MAP, agent_event_to_executor_event
from app.runtime.kernel_contracts import SUPPORTED_AGENT_EVENT_TYPES, AgentEvent
from app.runtime.sandbox.callback_tokens import CallbackTokenBinding, callback_token_id_for_binding


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {"skill_id": skill_id, "content_hash": version}


def test_agent_event_to_executor_event_maps_runtime_event_to_worker_shape():
    event = AgentEvent(
        type="assistant_delta",
        message="partial answer",
        payload={"delta": "partial answer"},
    )

    converted = agent_event_to_executor_event(event)

    assert converted == {
        "event_type": "assistant_delta",
        "stage": "message",
        "message": "partial answer",
        "payload": {
            "delta": "partial answer",
            "visible_to_user": True,
        },
    }


def test_agent_event_to_executor_event_hides_admin_only_events_from_ordinary_users():
    event = AgentEvent(
        type="browser_snapshot",
        message="browser state captured",
        payload={"url": "https://example.test", "visible_to_user": True},
        admin_only=True,
    )

    converted = agent_event_to_executor_event(event)

    assert converted["event_type"] == "browser_snapshot"
    assert converted["stage"] == "browser"
    assert converted["payload"]["visible_to_user"] is False
    assert converted["payload"]["admin_only"] is True


def test_every_supported_agent_event_type_has_a_stage_mapping():
    assert SUPPORTED_AGENT_EVENT_TYPES == set(EVENT_STAGE_MAP)


@pytest.mark.parametrize("event_type", ["intent_detected", "skill_selected", "skill_used"])
def test_executor_progress_types_are_not_kernel_agent_events(event_type):
    with pytest.raises(ValueError, match=f"Unsupported agent event type: {event_type}"):
        AgentEvent(type=event_type)


def test_checkpoint_and_subagent_events_have_stable_stage_mapping():
    checkpoint = agent_event_to_executor_event(
        AgentEvent(type="checkpoint_created", message="checkpoint saved", payload={"checkpoint_id": "checkpoint-a"})
    )
    subagent = agent_event_to_executor_event(
        AgentEvent(type="subagent_completed", message="reviewer done", payload={"subagent_id": "subagent-a"})
    )

    assert checkpoint["stage"] == "checkpoint"
    assert checkpoint["payload"] == {"checkpoint_id": "checkpoint-a", "visible_to_user": True}
    assert subagent["stage"] == "subagent"
    assert subagent["payload"] == {"subagent_id": "subagent-a", "visible_to_user": True}


def run_payload(**overrides):
    skill_id = overrides.get("skill_id", "general-chat")
    version = overrides.get("skill_version") or f"hash-{skill_id}"
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "agent_id": "general-agent",
        "skill_id": skill_id,
        "file_ids": [],
        "input": {"message": "hello", "model": "deepseek-v4-flash"},
        "skill_version": version,
        "release_decision": release_decision(version),
        "skill_manifests": [primary_manifest(skill_id, version)],
    }
    values.update(overrides)
    if "release_decision" not in overrides:
        values["release_decision"] = release_decision(values["skill_version"])
    return RunPayload(**values)


def test_build_run_context_uses_platform_identity_and_new_api_model_gateway():
    context = build_run_context(run_payload())

    assert context.tenant_id == "tenant-a"
    assert context.workspace_id == "workspace-a"
    assert context.user_id == "user-a"
    assert context.session_id == "session-a"
    assert context.run_id == "run-a"
    assert context.skill_ids == ["general-chat"]
    assert context.model == "deepseek-v4-flash"
    assert context.model_gateway == "new-api"
    assert context.permissions == ["chat.respond"]
    assert context.metadata["authoritative_attempt_id"] == "attempt-a"


def test_multi_agent_step_attempts_are_parent_bound_parallel_safe_and_retry_stable(monkeypatch):
    class StubSettings:
        sandbox_callback_base_url = "http://platform.test/"

    monkeypatch.setattr("app.settings.get_settings", lambda: StubSettings())
    context = build_run_context(
        run_payload(
            attempt_id="queue-attempt-7",
            input={"message": "hello", "model": "deepseek-v4-flash", "execution_mode": "multi_agent"},
        )
    )

    def step(key: str, index: int) -> AgentStepExecutionContext:
        return AgentStepExecutionContext(
            step_key=key,
            role=key,
            step_index=index,
            depends_on=[],
            skill_ids=["general-chat"],
            mcp_tool_ids=[],
            resource_limits={"max_seconds": 30},
            sandbox_mode="ephemeral",
            browser_enabled=False,
        )

    first = step("coding", 1)
    second = step("testing", 2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        coding, testing = list(
            pool.map(
                lambda item: build_step_sandbox_request(context=context, step=item),
                (first, second),
            )
        )
    coding_retry = build_step_sandbox_request(context=context, step=first)

    assert coding.attempt_id == coding_retry.attempt_id
    assert coding.attempt_id != testing.attempt_id
    assert coding.attempt_id.startswith("step-") and len(coding.attempt_id) == 69
    assert coding.callback_token_id == callback_token_id_for_binding(
        CallbackTokenBinding(run_id="run-a", attempt_id=coding.attempt_id)
    )
    assert testing.callback_token_id == callback_token_id_for_binding(
        CallbackTokenBinding(run_id="run-a", attempt_id=testing.attempt_id)
    )

    missing_parent = context.model_copy(update={"metadata": {}})
    with pytest.raises(ValueError, match="authoritative parent attempt_id"):
        build_step_sandbox_request(context=missing_parent, step=first)


def test_embedded_adapter_message_aggregates_multiple_deltas():
    from app.executors.embedded_poco import _message_from_events

    events = [
        AgentEvent(type="assistant_delta", message="Hel", payload={"delta": "Hel"}),
        AgentEvent(type="assistant_delta", message="lo", payload={"delta": "lo"}),
    ]

    assert _message_from_events(events) == "Hello"


def test_embedded_poco_adapter_accepts_sandbox_runtime_injection():
    runtime = object()

    adapter = EmbeddedPocoAdapter(sandbox_runtime=runtime)

    assert adapter._sandbox_runtime is runtime


@pytest.mark.asyncio
async def test_embedded_poco_adapter_returns_executor_result_and_streams_events():
    events = []
    adapter = EmbeddedPocoAdapter()

    result = await adapter.submit_run(
        run_payload(),
        event_sink=lambda **kwargs: events.append(kwargs),
    )

    assert result.status == "succeeded"
    assert result.executor_type == "embedded-poco-kernel"
    assert result.capabilities["streaming"] is True
    assert result.result["message"] == "hello"
    assert [event["event_type"] for event in events] == ["run_started", "assistant_delta", "run_completed"]
    assert events[1]["payload"]["delta"] == "hello"


@pytest.mark.asyncio
async def test_embedded_poco_adapter_requires_user_identity():
    adapter = EmbeddedPocoAdapter()

    result = await adapter.submit_run(run_payload(user_id=None))

    assert result.status == "failed"
    assert result.result["error_code"] == "missing_user_id"


@pytest.mark.asyncio
async def test_embedded_adapter_uses_sandbox_runtime_for_ephemeral_mode(monkeypatch):
    class StubSettings:
        sandbox_callback_base_url = "http://platform.test/"

    monkeypatch.setattr("app.settings.get_settings", lambda: StubSettings())

    submitted = []
    events = []

    class FakeRuntime:
        async def submit(self, request, event_sink=None):
            submitted.append(request)
            if event_sink is not None:
                await event_sink(AgentEvent(type="run_started", message="sandbox started"))
                await event_sink(
                    AgentEvent(
                        type="assistant_delta",
                        message="sandbox delta",
                        payload={"delta": "sandbox delta"},
                    )
                )
                await event_sink(AgentEvent(type="run_completed", message="sandbox completed"))
            return type(
                "SandboxSubmitResult",
                (),
                {
                    "status": "accepted",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {"status": "accepted"},
                },
            )()

    adapter = EmbeddedPocoAdapter(sandbox_runtime=FakeRuntime())

    result = await adapter.submit_run(
        run_payload(
            file_ids=["file-a"],
            input={
                "message": "use sandbox",
                "model": "deepseek-v4-flash",
                "sandbox_mode": "ephemeral",
                "browser_enabled": True,
                "mcp_tool_ids": ["knowledge.search"],
                "max_tool_calls": 9,
            },
        ),
        event_sink=lambda **event: events.append(event),
    )

    assert result.status == "succeeded"
    assert result.executor_type == "embedded-poco-kernel"
    assert result.executor_version == "sandbox-runtime/0.1.0"
    assert result.capabilities["streaming"] is True
    assert result.capabilities["sandbox"] is True
    assert result.result == {"message": "Sandbox run accepted", "sandbox_mode": "ephemeral"}
    assert len(submitted) == 1
    assert submitted[0].attempt_id == "attempt-a"
    assert submitted[0].sandbox_mode == "ephemeral"
    assert submitted[0].browser_enabled is True
    assert submitted[0].permissions == ["chat.respond", "sandbox.execute"]
    assert submitted[0].resource_limits["max_tool_calls"] == 9
    assert submitted[0].file_ids == ["file-a"]
    assert submitted[0].callback_url == "http://platform.test/api/ai/runtime/callbacks/executor"
    assert submitted[0].callback_token_id == "cbt:run-a:attempt-a"
    assert [event["event_type"] for event in events] == ["run_started", "assistant_delta", "run_completed"]
    assert events[1]["payload"]["delta"] == "sandbox delta"


@pytest.mark.asyncio
async def test_embedded_adapter_keeps_none_mode_in_process_and_aggregates_deltas():
    events = []
    submitted = []

    class FakeKernel:
        async def submit_run(self, context, event_sink):
            submitted.append(context)
            emitted = [
                AgentEvent(type="run_started", message="kernel started"),
                AgentEvent(type="assistant_delta", message="Hel", payload={"delta": "Hel"}),
                AgentEvent(type="assistant_delta", message="lo", payload={"delta": "lo"}),
                AgentEvent(type="run_completed", message="kernel completed", payload={"status": "succeeded"}),
            ]
            for event in emitted:
                await event_sink(event)
            return emitted

    class ForbiddenRuntime:
        async def submit(self, request, event_sink=None):
            raise AssertionError("sandbox runtime should not be used for sandbox_mode=none")

    adapter = EmbeddedPocoAdapter(kernel=FakeKernel(), sandbox_runtime=ForbiddenRuntime())

    result = await adapter.submit_run(
        run_payload(input={"message": "hello", "model": "deepseek-v4-flash", "sandbox_mode": "none"}),
        event_sink=lambda **event: events.append(event),
    )

    assert result.status == "succeeded"
    assert result.executor_version == "in-process/0.1.0"
    assert result.capabilities["sandbox"] is False
    assert result.result["message"] == "Hello"
    assert submitted[0].sandbox_mode == "none"
    assert [event["event_type"] for event in events] == [
        "run_started",
        "assistant_delta",
        "assistant_delta",
        "run_completed",
    ]

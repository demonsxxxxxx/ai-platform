"""Strict framing and message-local recovery using the locked SDK, no provider."""

import pytest

from app.executors.claude_stream_projection import ClaudeStreamProjector
from tests.test_claude_assistant_source_routing import (
    assert_answer,
    commentary,
    execute,
    finish_read,
    frame,
    terminal,
    text_source,
)
from tests.test_claude_assistant_source_routing import source_routing_settings as _source_routing_settings

source_routing_settings = _source_routing_settings


def tool_frames(sdk, *, serial=False, missing_stop=False, reuse_index=False):
    message_id = "work-message"
    events = [
        {"type": "message_start", "message": {
            "id": message_id, "role": "assistant", "stop_reason": None,
        }},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "text_delta", "text": "Checking the synthetic input. ",
        }},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {
            "type": "tool_use", "id": "synthetic-call", "name": "Read",
        }},
        {"type": "content_block_delta", "index": 1, "delta": {
            "type": "input_json_delta", "partial_json": "PRIVATE_TOOL_INPUT",
        }},
        {"type": "content_block_start", "index": 1 if reuse_index else 2,
         "content_block": {"type": "tool_use", "id": "synthetic-call-2", "name": "Read"}},
        {"type": "content_block_delta", "index": 2, "delta": {
            "type": "input_json_delta", "partial_json": "PRIVATE_SECOND_INPUT",
        }},
        {"type": "content_block_stop", "index": 2},
    ]
    if serial:
        events.insert(6, {"type": "content_block_stop", "index": 1})
    elif not missing_stop:
        events.append({"type": "content_block_stop", "index": 1})
    events += [
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    for ordinal, event in enumerate(events):
        yield frame(sdk, message_id, ordinal, event)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["serial", "overlap", "missing-stop", "reused-index"])
async def test_tool_overlap_keeps_safe_work_and_independent_final_answer(
    source_routing_settings, shape,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt
        for item in tool_frames(sdk, serial=shape == "serial", missing_stop=shape == "missing-stop", reuse_index=shape == "reused-index"):
            yield item
        await finish_read(options)
        # Even complete typed text from the damaged message cannot fill it in.
        if shape != "serial":
            yield sdk.AssistantMessage(
                content=[sdk.TextBlock(text="PRIVATE_TYPED_REPAIR")], model="model-a",
                message_id="work-message", uuid="work-repair", stop_reason="end_turn",
            )
        for item in text_source(sdk, "final-message", "Safe final answer.", typed=True):
            yield item
        yield terminal(sdk, "Safe final answer.")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Safe final answer.")
    assert commentary(events) == "Checking the synthetic input. "
    assert "".join(texts) == "Safe final answer."
    assert "PRIVATE" not in str([event.payload for event in events])
    # Exercise the durable receipt reader with the callback's source identities
    # and canonical tenant/Run message identity, including the quarantined part.
    from app.streaming.api import opaque_message_id
    from app.streaming.infrastructure.v4 import load_answer_by_receipt
    from app.streaming.application.worker_publication_v4 import AssistantAnswerReceiptError
    from tests.test_streaming_answer_receipt import _connection
    from tests.test_streaming_v4_durable import _row

    rows = []
    for sequence, event in enumerate(events, start=1):
        if not event.event_type.startswith("message."):
            continue
        row = _row(event.payload, id=f"evt4_recovery_{sequence}", sequence=sequence, event_type=event.event_type)
        row["payload_json"]["__stream_v4"].update({
            "source_event_id": event.event_id, "causation_event_id": event.causation_event_id,
        })
        rows.append(row)
    receipt = {**result.answer_receipt, "message_id": opaque_message_id("tenant-a", "run-a")}
    reconstructed = await load_answer_by_receipt(_connection(rows), tenant_id="tenant-a", run_id="run-a",
        attempt_id="attempt-a", receipt=receipt)
    assert reconstructed.text == "Safe final answer."
    with pytest.raises(AssistantAnswerReceiptError):
        await load_answer_by_receipt(_connection(rows, attempt_id="attempt-other"), tenant_id="tenant-a",
            run_id="run-a", attempt_id="attempt-a", receipt=receipt)
    if shape == "serial":
        assert "projection_failure" not in result.runtime_diagnostics
    else:
        assert set(result.runtime_diagnostics) == {
            "schema_version", "error_code", "failure_source", "failure_stage", "projection_failure",
        }
        failure = result.runtime_diagnostics["projection_failure"]
        assert failure["reason"] == "raw_frame_invalid"
        assert failure["frame_shape"]["guard"] == "block_start_state"
        assert "PRIVATE" not in str(failure)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_unfinished_text_requires_a_new_raw_message_not_typed_or_result(
    source_routing_settings, recover,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        for item in list(text_source(sdk, "broken", "Safe partial preview.", typed=False))[:3]:
            yield item
        if recover:
            for item in text_source(sdk, "fresh", "Fresh safe answer.", typed=False):
                yield item
        else:
            yield sdk.AssistantMessage(
                content=[sdk.TextBlock(text="PRIVATE_UNBOUND_REPAIR")], model="model-a",
                message_id="broken", uuid="broken-repair", stop_reason="end_turn",
            )
        yield terminal(sdk, "Fresh safe answer." if recover else "PRIVATE_UNBOUND_REPAIR")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    if recover:
        assert_answer(result, events, "Fresh safe answer.")
        assert commentary(events) == "Safe partial preview."
        assert result.runtime_diagnostics["projection_failure"]["reason"] == "unfinished_raw_stream"
        assert "".join(texts) == "Fresh safe answer."
    else:
        assert result.error == "claude_agent_sdk_output_validation_failed"
        assert result.answer_receipt is None
        assert not any(event.event_type == "message.completed" for event in events)
    assert "PRIVATE" not in str([event.payload for event in events])


@pytest.mark.asyncio
async def test_subagent_messages_cannot_poison_or_publish_into_main_text(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        items = list(text_source(sdk, "main", "Safe main answer.", typed=True))
        for item in items[:3]:
            yield item
        yield sdk.AssistantMessage(
            content=[sdk.TextBlock(text="PRIVATE_SUBAGENT_BODY")], model="model-a",
            message_id="subagent", uuid="subagent-typed", parent_tool_use_id="private-parent",
        )
        yield sdk.StreamEvent(
            event={"type": "content_block_delta", "index": 0, "delta": {
                "type": "text_delta", "text": "PRIVATE_SUBAGENT_DELTA",
            }}, uuid="subagent-raw", session_id="synthetic-session", parent_tool_use_id="private-parent",
        )
        for item in items[3:]:
            yield item
        yield terminal(sdk, "Safe main answer.")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Safe main answer.")
    assert "".join(texts) == "Safe main answer."
    assert "PRIVATE" not in str([event.payload for event in events])


@pytest.mark.asyncio
async def test_typed_end_turn_cannot_finalize_answer_before_raw_message_boundary(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        for item in list(text_source(sdk, "broken-typed", "Safe partial preview.", typed=False))[:3]:
            yield item
        yield sdk.AssistantMessage(
            content=[sdk.TextBlock(text="Safe partial preview.")], model="model-a",
            message_id="broken-typed", uuid="early-terminal", stop_reason="end_turn",
        )
        for item in text_source(sdk, "fresh-final", "Fresh safe answer.", typed=False):
            yield item
        yield terminal(sdk, "Fresh safe answer.")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Fresh safe answer.")
    assert commentary(events) == "Safe partial preview."
    assert "".join(texts) == "Fresh safe answer."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sdk-error", "cancelled", "result-conflict"])
async def test_recovery_cannot_turn_an_invalid_terminal_into_success(source_routing_settings, kind):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        for item in tool_frames(sdk, missing_stop=True):
            yield item
        for item in text_source(sdk, "fresh", "Fresh safe answer.", typed=False):
            yield item
        result = terminal(sdk, "Conflicting private result" if kind == "result-conflict" else "Fresh safe answer.")
        if kind == "sdk-error":
            result.is_error = True
            result.errors = ["synthetic upstream error"]
        if kind == "cancelled":
            result.terminal_reason = "cancelled"
        yield result

    result, events, _texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is not None
    assert result.answer_receipt is None
    assert not any(event.event_type == "message.completed" for event in events)
    assert "Conflicting private result" not in str([event.payload for event in events])


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
async def test_excluded_child_tool_id_still_redacts_main_answer(source_routing_settings, raw):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        if raw:
            yield sdk.StreamEvent(uuid="child-tool-start", session_id="synthetic-session",
                parent_tool_use_id="child-parent", event={"type": "content_block_start", "index": 1,
                    "content_block": {"type": "tool_use", "id": "PRIVATE_CHILD_CALL", "name": "Read"}})
        else:
            yield sdk.AssistantMessage(content=[sdk.ToolUseBlock(id="PRIVATE_CHILD_CALL", name="Read", input={})],
                model="model-a", message_id="child-message", uuid="child-tool", parent_tool_use_id="child-parent")
        for item in text_source(sdk, "main-safe", "Reference PRIVATE_CHILD_CALL remains private.", typed=False):
            yield item
        yield terminal(sdk, "Reference PRIVATE_CHILD_CALL remains private.")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is None
    assert "PRIVATE_CHILD_CALL" not in "".join(texts)
    assert "PRIVATE_CHILD_CALL" not in str([event.payload for event in events])



def test_quarantined_projector_requires_fresh_identity_and_excludes_private_delta():
    projector = ClaudeStreamProjector()
    start = {"type": "message_start", "message": {
        "id": "broken", "role": "assistant", "stop_reason": None,
    }}
    projector.accept(start)
    projector.accept({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use"}})
    assert projector.accept({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "PRIVATE"}}) == ()
    failure = projector.failure_frame
    assert projector.disabled
    projector.accept(start)
    assert projector.disabled
    projector.accept({**start, "message": {**start["message"], "id": "fresh"}})
    assert not projector.disabled
    assert projector.failure_frame == failure
    assert "PRIVATE" not in str(failure)


@pytest.mark.asyncio
async def test_recovered_runner_record_reaches_private_storage_with_public_success(
    source_routing_settings, tmp_path,
):
    import types
    import claude_agent_sdk as sdk
    from app.execution.application.worker_terminal_projection import project_worker_terminal_result
    from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter, PreparedSdkRun
    from app.runs.application.diagnostics import RunDiagnosticsService
    from app.sandbox.api import SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, normalize_sdk_runtime_diagnostics
    from tests.test_claude_agent_worker_adapter import sandbox_writing_payload
    from tests.test_run_diagnostics import InMemoryDiagnostics, NOW

    async def source(*, prompt, options):
        del prompt, options
        for item in list(text_source(sdk, "broken", "Safe work.", typed=False))[:3]:
            yield item
        for item in text_source(sdk, "fresh", "Safe final answer.", typed=True):
            yield item
        yield terminal(sdk, "Safe final answer.")

    sdk_result, events, _texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(sdk_result, events, "Safe final answer.")
    prepared = PreparedSdkRun(workspace=tmp_path, file_names=[], selected_skills=[],
        pinned_manifests={}, allowed_skill_names=["general-chat"], staged_skill_names=["general-chat"],
        prompt="synthetic recovery")
    result = ClaudeAgentWorkerAdapter()._executor_result_from_sandbox_runtime(
        sandbox_writing_payload(agent_id="general-agent", skill_id="general-chat"), prepared,
        types.SimpleNamespace(status="completed", provider="docker", timings={}, executor_response={
            "status": "completed", "message": sdk_result.message, "sdk_used": True,
            "answer_receipt": sdk_result.answer_receipt,
            "tool_invocation_evidence": [],
            "runtime_diagnostics": sdk_result.runtime_diagnostics,
        }),
    )
    assert result.status == "succeeded", result.result
    assert "runtime_diagnostics" not in result.result
    projection = project_worker_terminal_result(result, [], trace_id="trace-a", latency_ms=0,
        invoked_skill_ids=lambda _payload: set())
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, clock=lambda: NOW)
    for _ in range(2):
        public = await service.capture_failure_result(object(), tenant_id="tenant-a", run_id="run-a",
            attempt_id="attempt-a", source="worker_executor", stage="terminalization",
            error_code="completed", result_json=projection.result_payload)
    assert public["message"] == sdk_result.message == ""
    assert result.executor_payload["answer_receipt"] == sdk_result.answer_receipt
    assert "runtime_diagnostics" not in str(public)
    observations = persistence.payload["observations"]
    assert len(observations) == 1
    assert persistence.calls[0][1]["tenant_id"] == "tenant-a"
    assert persistence.calls[0][1]["run_id"] == "run-a"
    assert observations[0]["attempt_id"] == "attempt-a"
    assert observations[0]["runtime_diagnostics"]["projection_failure"] == sdk_result.runtime_diagnostics["projection_failure"]

"""Completed SDK blocks are the only public prose source; no real provider."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.execution.infrastructure.harness.claude.typed_blocks import (
    ClaudeTypedBlockObservations,
)
from tests.test_claude_assistant_source_routing import (
    assert_answer,
    commentary,
    execute,
    finish_read,
    frame,
    terminal,
)
from tests.test_claude_assistant_source_routing import (
    source_routing_settings as _source_routing_settings,
)

source_routing_settings = _source_routing_settings


def assistant(sdk, provider, observation, *blocks, parent=None, stop=None):
    return sdk.AssistantMessage(
        content=list(blocks),
        model="model-a",
        message_id=provider,
        uuid=observation,
        parent_tool_use_id=parent,
        stop_reason=stop,
    )


@pytest.mark.asyncio
async def test_complete_blocks_publish_before_tool_and_result_with_one_message_owner(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    captured = []

    async def source(*, prompt, options):
        del prompt
        assert options.include_partial_messages is False
        yield assistant(
            sdk, "work", "work-text", sdk.TextBlock(text="Checking synthetic input. ")
        )
        # The public part must already be ACKed before subsequent SDK messages.
        assert any(event.event_type == "message.part.delta" for event in captured)
        yield assistant(
            sdk,
            "work",
            "work-tool",
            sdk.ToolUseBlock(
                id="synthetic-call", name="Read", input={"file_path": "input.txt"}
            ),
        )
        await finish_read(options)
        yield assistant(sdk, "final", "final-1", sdk.TextBlock(text="Safe "))
        final = assistant(sdk, "final", "final-2", sdk.TextBlock(text="final answer."))
        yield final
        yield final
        yield terminal(sdk, "PRIVATE_RESULT_BODY_HAS_NO_PUBLIC_AUTHORITY")

    # execute's callback collector is replaced only to observe actual ACK order.
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    from app.required_tool_contract import with_sandbox_local_tool_capability_subjects
    from tests.support.claude_sdk import native_client_factory

    texts = []

    async def acknowledge(_fact):
        return True

    result = await run_claude_agent_sdk(
        prompt="synthetic",
        cwd=source_routing_settings,
        skill_id=None,
        execution_policy="sandbox_brokered",
        client_fn=native_client_factory(source),
        run_id="run",
        attempt_id="attempt",
        tool_policy_subjects=with_sandbox_local_tool_capability_subjects(
            [], sandbox_provider="docker", authorized_sandbox_tool_identities={"Read"}
        ),
        on_text=texts.append,
        on_agent_event=lambda batch: captured.extend(batch) or True,
        on_tool_lifecycle=acknowledge,
    )
    assert_answer(result, captured, "Safe final answer.")
    assert commentary(captured) == "Checking synthetic input. "
    assert "".join(texts) == "Safe final answer."
    assert "PRIVATE" not in str([event.payload for event in captured])
    assert {event.event_type for event in captured} >= {
        "tool.started",
        "tool.completed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", [None, "missing-stop", "overlap-tools", "duplicate-stop"]
)
async def test_raw_noise_cannot_poison_typed_answer_or_refill_result_only(
    source_routing_settings, raw
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        if raw:
            yield frame(
                sdk,
                "raw-only",
                0,
                {
                    "type": "message_start",
                    "message": {"id": "raw-only", "role": "assistant"},
                },
            )
            if raw == "overlap-tools":
                for index in range(2):
                    yield frame(
                        sdk,
                        "raw-only",
                        index + 1,
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {
                                "type": "tool_use",
                                "id": f"private-raw-tool-{index}",
                                "name": "Read",
                            },
                        },
                    )
            else:
                yield frame(
                    sdk,
                    "raw-only",
                    1,
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    },
                )
            yield frame(
                sdk,
                "raw-only",
                3,
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "PRIVATE_RAW_BODY"},
                },
            )
            if raw == "duplicate-stop":
                for ordinal in (4, 5):
                    yield frame(sdk, "raw-only", ordinal, {"type": "message_stop"})
        yield assistant(sdk, "main", "main-1", sdk.TextBlock(text="Safe typed answer."))
        yield terminal(sdk, "PRIVATE_RESULT_BODY")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Safe typed answer.")
    assert "".join(texts) == "Safe typed answer."
    assert "PRIVATE" not in str([event.payload for event in events])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape", ["result-only", "work-only", "whitespace", "empty-message"]
)
async def test_no_public_answer_is_invented_from_result(source_routing_settings, shape):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        if shape == "work-only":
            yield assistant(sdk, "work", "work-text", sdk.TextBlock(text="Checking."))
            yield assistant(
                sdk,
                "work",
                "work-tool",
                sdk.ToolUseBlock(id="private-call", name="Read", input={}),
            )
        elif shape == "whitespace":
            yield assistant(sdk, "blank", "blank-1", sdk.TextBlock(text=" \n\t"))
        elif shape == "empty-message":
            yield assistant(sdk, "empty", "empty-1")
        yield terminal(sdk, "PRIVATE_RESULT_ONLY")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.message == ""
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert set(result.runtime_diagnostics) == {
        "schema_version",
        "error_code",
        "failure_source",
        "failure_stage",
        "projection_failure",
    }
    assert result.answer_receipt is None
    assert not texts
    assert not any(event.event_type == "message.completed" for event in events)
    assert "PRIVATE" not in str([event.payload for event in events])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "replay-body",
        "replay-provider",
        "missing-id",
        "missing-uuid",
        "reopened-message",
        "invalid-text",
    ],
)
async def test_invalid_typed_identity_or_body_cannot_complete(
    source_routing_settings, fault
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(sdk, "first", "same", sdk.TextBlock(text="Safe preview. "))
        if fault == "replay-body":
            yield assistant(
                sdk, "first", "same", sdk.TextBlock(text="PRIVATE_CONFLICT")
            )
        elif fault == "replay-provider":
            yield assistant(sdk, "second", "same", sdk.TextBlock(text="Safe preview. "))
        elif fault == "reopened-message":
            yield assistant(
                sdk, "second", "second-1", sdk.TextBlock(text="Other preview.")
            )
            yield assistant(
                sdk, "first", "late-first", sdk.TextBlock(text="PRIVATE_LATE")
            )
        else:
            yield assistant(
                sdk,
                None if fault == "missing-id" else "second",
                None if fault == "missing-uuid" else "second-1",
                sdk.TextBlock(
                    text=None if fault == "invalid-text" else "Safe candidate."
                ),
                stop=None,
            )
        yield terminal(sdk, "PRIVATE_RESULT_REPAIR")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is None
    assert result.received_structured_terminal is True
    assert result.runtime_diagnostics["failure_source"] == "public_projection"
    assert result.answer_receipt is None
    assert not texts
    assert not any(event.event_type == "message.completed" for event in events)
    assert "PRIVATE" not in str([event.payload for event in events])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["sdk-error", "cancelled", "missing-result"])
async def test_terminal_control_failure_keeps_preview_but_never_finalizes(
    source_routing_settings, fault
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(sdk, "final", "final-1", sdk.TextBlock(text="Safe preview."))
        if fault == "missing-result":
            return
        result = terminal(sdk, "PRIVATE_RESULT")
        if fault == "sdk-error":
            result.is_error, result.errors = True, ["synthetic upstream error"]
        elif fault == "cancelled":
            result.terminal_reason = "cancelled"
        yield result

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is not None
    assert result.answer_receipt is None
    assert not texts
    assert any(event.event_type == "message.part.delta" for event in events)
    assert not any(event.event_type == "message.completed" for event in events)


@pytest.mark.asyncio
async def test_optional_result_uuid_has_no_public_text_authority(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(sdk, "final", "uuid-final", sdk.TextBlock(text="Safe answer."))
        result = terminal(sdk, "PRIVATE_RESULT")
        result.uuid = None
        yield result

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Safe answer.")
    assert "".join(texts) == "Safe answer."


@pytest.mark.asyncio
async def test_multiple_eligible_main_sources_preserve_answer_parts(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(sdk, "first", "uuid-first", sdk.TextBlock(text="第一部分"))
        yield assistant(sdk, "second", "uuid-second", sdk.TextBlock(text="第二部分"))
        yield terminal(sdk, "PRIVATE_RESULT")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "第一部分\n\n第二部分")
    assert "".join(texts) == "第一部分\n\n第二部分"


@pytest.mark.asyncio
async def test_recovered_assistant_error_cannot_reclassify_later_terminal(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        failed = assistant(
            sdk, "failed", "uuid-failed", sdk.TextBlock(text="PRIVATE_ERROR")
        )
        failed.error = "prompt_too_long"
        yield failed
        yield assistant(
            sdk, "recovered", "uuid-recovered", sdk.TextBlock(text="Safe preview.")
        )
        result = terminal(sdk, "PRIVATE_RESULT")
        result.is_error, result.errors = True, ["synthetic upstream error"]
        yield result

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error == "claude_agent_sdk_execution_failed"
    assert "PRIVATE" not in str([event.payload for event in events])
    assert result.answer_receipt is None
    assert not texts


@pytest.mark.asyncio
async def test_child_scope_is_excluded_but_its_tool_id_redacts_main_answer(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(sdk, "main", "main-1", sdk.TextBlock(text="Safe "))
        yield assistant(
            sdk,
            "child",
            "child-1",
            sdk.TextBlock(text="PRIVATE_CHILD_BODY"),
            parent="parent",
        )
        yield assistant(
            sdk,
            "child",
            "child-2",
            sdk.ToolUseBlock(id="PRIVATE_CALL", name="Read", input={}),
            parent="parent",
        )
        yield assistant(
            sdk, "main", "main-2", sdk.TextBlock(text="PRIVATE_CALL remains private.")
        )
        yield terminal(sdk, "PRIVATE_RESULT")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is None
    assert "PRIVATE" not in "".join(texts)
    assert "PRIVATE" not in str([event.payload for event in events])
    assert result.answer_receipt["text_length"] == len("".join(texts))


def test_observation_identity_outlives_old_replay_window_without_storing_body():
    observations = ClaudeTypedBlockObservations()
    for index in range(200):
        assert observations.accept(
            message_id=f"message-{index}",
            uuid=f"uuid-{index}",
            stop_reason=None,
            blocks=[("TextBlock", None, None, "PRIVATE_BODY")],
        )
    assert len(observations._recent) == 200
    for index in range(200):
        assert not observations.accept(
            message_id=f"message-{index}",
            uuid=f"uuid-{index}",
            stop_reason=None,
            blocks=[("TextBlock", None, None, "PRIVATE_BODY")],
        )
    assert "PRIVATE_BODY" not in str(observations._recent)


@pytest.mark.asyncio
async def test_active_message_replay_after_129_blocks_is_not_republished(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        first = assistant(sdk, "one-message", "uuid-0", sdk.TextBlock(text="0|"))
        yield first
        for index in range(1, 129):
            yield assistant(
                sdk, "one-message", f"uuid-{index}", sdk.TextBlock(text=f"{index}|")
            )
        yield first
        yield terminal(sdk, "PRIVATE_RESULT")

    expected = "".join(f"{index}|" for index in range(129))
    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, expected)
    assert "".join(texts) == expected


@pytest.mark.asyncio
async def test_160_eligible_messages_survive_without_role_eviction(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        for index in range(160):
            yield assistant(
                sdk,
                f"message-{index}",
                f"uuid-{index}",
                sdk.TextBlock(text=f"答案 {index}"),
            )
        yield terminal(sdk, "PRIVATE_RESULT")

    expected = "\n\n".join(f"答案 {index}" for index in range(160))
    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, expected)
    assert "".join(texts) == expected


def persisted_rows(events):
    """Wrap actual candidates as the existing authorized history query rows."""
    return [
        {
            "id": f"evt4_row_{sequence}",
            "tenant_id": "tenant",
            "run_id": event.run_id,
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": sequence,
            "event_type": event.event_type,
            "stage": "agent_kernel",
            "visible_to_user": True,
            "v4_attempt_authorized": True,
            "created_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
            "payload_json": {
                **event.payload,
                "__stream_v4": {
                    "version": 1,
                    "attempt_id": "attempt-1651",
                    "message_id": event.message_id,
                    "stream_incarnation": 1,
                    "authorization_epoch": 1,
                    "trace_ref": None,
                    "causation_event_id": event.causation_event_id,
                    "source_event_id": event.event_id,
                },
            },
        }
        for sequence, event in enumerate(events, 1)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "attempt", "authorization", "tenant", "run"])
async def test_actual_typed_facts_match_receipt_history_and_1659_admin(
    source_routing_settings, damage
):
    import claude_agent_sdk as sdk
    from app.streaming.api import project_persisted_assistant_text_messages
    from app.runs.application.admin_run_monitor import build_admin_worker_execution

    async def source(*, prompt, options):
        del prompt
        yield assistant(
            sdk, "work", "work-1", sdk.TextBlock(text="Checking synthetic input. ")
        )
        yield assistant(
            sdk,
            "work",
            "work-2",
            sdk.ToolUseBlock(
                id="synthetic-call", name="Read", input={"file_path": "input.txt"}
            ),
        )
        await finish_read(options)
        yield assistant(
            sdk,
            "child",
            "child-1",
            sdk.TextBlock(text="PRIVATE_CHILD"),
            parent="synthetic-call",
        )
        yield assistant(
            sdk, "final", "answer-1", sdk.TextBlock(text="正常公开synthetic-")
        )
        yield assistant(sdk, "final", "answer-2", sdk.TextBlock(text="call回答"))
        yield terminal(sdk, "PRIVATE_RESULT")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "正常公开█回答")
    assert "".join(texts) == "正常公开█回答"
    rows = persisted_rows(events)
    answer_part = next(
        event.payload["part_id"]
        for event in events
        if event.event_type == "message.part.classified"
        and event.payload["role"] == "answer"
    )
    if damage:
        rows = deepcopy(rows)
        row = next(
            row
            for row in rows
            if row["event_type"] == "message.part.delta"
            and row["payload_json"]["part_id"] == answer_part
        )
        if damage == "attempt":
            row["payload_json"]["__stream_v4"]["attempt_id"] = "foreign-attempt"
        elif damage == "authorization":
            row["v4_attempt_authorized"] = False
        elif damage == "tenant":
            row["tenant_id"] = "foreign-tenant"
        else:
            row["run_id"] = "foreign-run"
    projections = project_persisted_assistant_text_messages(
        rows, tenant_id="tenant", run_id="run-1651"
    )
    admin = build_admin_worker_execution(
        [
            {
                "event_id": row["id"],
                "type": row["event_type"],
                "sequence": row["sequence"],
                "visible_to_user": row["visible_to_user"],
                "payload": row["payload_json"],
            }
            for row in rows
        ],
        part_messages=projections,
        sanitize_text=lambda text: text,
    )
    if damage:
        assert not any(message.text for message in projections)
        assert admin["response"] == ""
        assert admin["answer_projection"]["status"] in {"invalid", "incomplete"}
    else:
        assert len(projections) == 1
        assert projections[0].status == "complete"
        assert projections[0].text == admin["response"] == "正常公开█回答"
        assert admin["answer_projection"]["status"] == "available"
    assert "PRIVATE" not in str(admin)


@pytest.mark.asyncio
@pytest.mark.parametrize("late_conflict", [False, True])
async def test_verified_write_keeps_typed_answer_and_late_conflict_survives_text_rejection(
    source_routing_settings, monkeypatch, late_conflict
):
    import sys
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    from tests.test_claude_agent_sdk_runner import (
        _subject,
        _mcp_hook_steps,
        _sandbox_brokered_settings,
        _scripted_sdk,
    )
    from tests.support.claude_mcp import install_mcp_sessions

    install_mcp_sessions(monkeypatch)
    subject = {**_subject(tool_name="write"), "write_capable": True}
    call_id = "private-write-call"
    steps = _mcp_hook_steps(subject, call_id=call_id)
    sdk = _scripted_sdk({}, steps, result_text="PRIVATE_RESULT")
    if late_conflict:
        steps.extend(
            [
                (
                    "assistant_typed",
                    {"text": None, "message_id": "invalid", "uuid": "invalid-text"},
                ),
                (
                    "assistant_typed",
                    {
                        "text": "",
                        "message_id": "late",
                        "uuid": "late-tool",
                        "content": [
                            sdk.ToolUseBlock(
                                id=call_id,
                                name=subject["identity"],
                                input={"private": "different-value"},
                            )
                        ],
                    },
                ),
            ]
        )
    steps.append(("assistant", "Verified write completed."))
    events, texts, evidence = [], [], []

    async def acknowledge(fact):
        evidence.append(dict(fact))
        return True

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings", _sandbox_brokered_settings
    )
    result = await run_claude_agent_sdk(
        prompt="synthetic",
        cwd=source_routing_settings,
        skill_id=None,
        execution_policy="sandbox_brokered",
        run_id="run-write",
        attempt_id="attempt-write",
        tool_policy_subjects=[subject],
        on_text=texts.append,
        on_agent_event=lambda batch: events.extend(batch) or True,
        on_capability_evidence=acknowledge,
    )
    assert [fact["lifecycle_phase"] for fact in evidence] == [
        "invocation_requested",
        "completed",
    ]
    if late_conflict:
        assert result.error == "mcp_execution_outcome_unknown"
        assert result.turn_diagnostics["retryable"] is False
        assert result.answer_receipt is None
        assert texts == []
        assert not any(event.event_type == "message.completed" for event in events)
    else:
        assert_answer(result, events, "Verified write completed.")
        assert "".join(texts) == "Verified write completed."


@pytest.mark.asyncio
async def test_optional_sdk_stop_string_and_empty_observation_do_not_erase_public_answer(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(
            sdk,
            "answer",
            "answer-1",
            sdk.TextBlock(text="Safe answer. "),
            stop="future_sdk_reason",
        )
        yield assistant(sdk, "empty", "empty-1")
        yield assistant(
            sdk,
            "server",
            "server-1",
            type(
                "ServerToolUseBlock",
                (),
                {"id": "private-server-call", "name": "web_search", "input": {}},
            )(),
        )
        yield terminal(sdk, "PRIVATE_RESULT", stop="future_sdk_reason")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Safe answer. ")
    assert "".join(texts) == "Safe answer. "


@pytest.mark.asyncio
async def test_late_private_identity_discovery_remains_a_safety_failure(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt, options
        yield assistant(
            sdk,
            "first",
            "first-1",
            sdk.TextBlock(text="Visible previously-unknown-call text. " * 200),
        )
        yield assistant(
            sdk,
            "child",
            "child-tool",
            sdk.ToolUseBlock(id="previously-unknown-call", name="Read", input={}),
            parent="parent",
        )
        yield terminal(sdk, "PRIVATE_RESULT")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error == "claude_agent_sdk_output_validation_failed"
    assert result.answer_receipt is None
    assert texts == []
    assert not any(event.event_type == "message.completed" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape,expected_projection",
    [("empty", "unknown"), ("work", "incomplete"), ("invalid", "incomplete")],
)
async def test_success_without_answer_is_honest_in_history_and_admin(
    source_routing_settings, shape, expected_projection
):
    import claude_agent_sdk as sdk
    from app.streaming.api import project_persisted_assistant_text_messages
    from app.runs.application.admin_run_monitor import build_admin_worker_execution

    async def source(*, prompt, options):
        del prompt, options
        if shape != "empty":
            yield assistant(
                sdk, "preview", "preview-1", sdk.TextBlock(text="Safe preview.")
            )
            if shape == "work":
                yield assistant(
                    sdk,
                    "preview",
                    "work-tool",
                    sdk.ToolUseBlock(id="private-work", name="Read", input={}),
                )
            else:
                yield assistant(
                    sdk, "invalid", "invalid-text", sdk.TextBlock(text=None)
                )
        yield terminal(sdk, "PRIVATE_RESULT_ONLY")

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert result.error is None and result.received_structured_terminal is True
    assert result.turn_diagnostics["terminal_class"] == "completed"
    assert result.turn_diagnostics["retryable"] is False
    assert result.message == "" and result.answer_receipt is None and texts == []
    rows = persisted_rows(events)
    projections = project_persisted_assistant_text_messages(
        rows, tenant_id="tenant", run_id="run-1651"
    )
    admin = build_admin_worker_execution(
        [
            {
                "event_id": row["id"],
                "type": row["event_type"],
                "sequence": row["sequence"],
                "visible_to_user": row["visible_to_user"],
                "payload": row["payload_json"],
            }
            for row in rows
        ],
        part_messages=projections,
        sanitize_text=lambda text: text,
    )
    assert not any(message.text for message in projections)
    assert admin["response"] == ""
    assert admin["answer_projection"]["status"] == expected_projection
    assert "PRIVATE" not in str(admin)


@pytest.mark.asyncio
async def test_typed_replay_identity_is_attempt_local(source_routing_settings):
    import claude_agent_sdk as sdk
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    from tests.support.claude_sdk import native_client_factory

    async def source(*, prompt, options):
        del prompt, options
        block = assistant(
            sdk, "same-provider", "same-uuid", sdk.TextBlock(text="Safe answer.")
        )
        yield block
        yield block
        yield terminal(sdk, "PRIVATE_RESULT")

    receipts = []
    for attempt_id in ("attempt-first", "attempt-second"):
        events, texts = [], []
        result = await run_claude_agent_sdk(
            prompt="synthetic",
            cwd=source_routing_settings,
            skill_id=None,
            client_fn=native_client_factory(source),
            execution_policy="sandbox_brokered",
            run_id="same-run",
            attempt_id=attempt_id,
            on_text=texts.append,
            on_agent_event=lambda batch: events.extend(batch) or True,
        )
        assert_answer(result, events, "Safe answer.")
        assert "".join(texts) == "Safe answer."
        receipts.append(result.answer_receipt)
    assert receipts[0]["message_id"] != receipts[1]["message_id"]
    assert receipts[0]["last_delta_event_id"] != receipts[1]["last_delta_event_id"]


@pytest.mark.asyncio
async def test_successful_empty_runner_record_reaches_private_storage_only(
    source_routing_settings, tmp_path
):
    import types
    import claude_agent_sdk as sdk
    from app.execution.application.worker_terminal_projection import (
        project_worker_terminal_result,
    )
    from app.executors.claude_agent_worker import (
        ClaudeAgentWorkerAdapter,
        PreparedSdkRun,
    )
    from app.runs.application.diagnostics import RunDiagnosticsService
    from app.sandbox.api import (
        SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        normalize_sdk_runtime_diagnostics,
    )
    from tests.test_claude_agent_worker_adapter import sandbox_writing_payload
    from tests.test_run_diagnostics import InMemoryDiagnostics, NOW

    async def source(*, prompt, options):
        del prompt, options
        yield terminal(sdk, "PRIVATE_RESULT")

    sdk_result, events, texts = await execute(
        source_routing_settings, source, partial=True
    )
    assert sdk_result.error is None and sdk_result.answer_receipt is None
    assert not texts and not any(
        event.event_type == "message.completed" for event in events
    )
    prepared = PreparedSdkRun(
        workspace=tmp_path,
        file_names=[],
        selected_skills=[],
        pinned_manifests={},
        allowed_skill_names=["general-chat"],
        staged_skill_names=["general-chat"],
        prompt="synthetic",
    )
    result = ClaudeAgentWorkerAdapter()._executor_result_from_sandbox_runtime(
        sandbox_writing_payload(agent_id="general-agent", skill_id="general-chat"),
        prepared,
        types.SimpleNamespace(
            status="completed",
            provider="docker",
            timings={},
            executor_response={
                "status": "completed",
                "message": sdk_result.message,
                "sdk_used": True,
                "tool_invocation_evidence": [],
                "runtime_diagnostics": sdk_result.runtime_diagnostics,
            },
        ),
    )
    assert result.status == "succeeded" and result.result["message"] == ""
    assert "runtime_diagnostics" not in result.result
    projection = project_worker_terminal_result(
        result,
        [],
        trace_id="trace-a",
        latency_ms=0,
        invoked_skill_ids=lambda _payload: set(),
    )
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    for _ in range(2):
        public = await service.capture_failure_result(
            object(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            source="worker_executor",
            stage="terminalization",
            error_code="completed",
            result_json=projection.result_payload,
        )
    assert public["message"] == ""
    assert "runtime_diagnostics" not in str(public) and "PRIVATE" not in str(public)
    observations = persistence.payload["observations"]
    assert len(observations) == 1
    assert persistence.calls[0][1]["tenant_id"] == "tenant-a"
    assert persistence.calls[0][1]["run_id"] == "run-a"
    assert observations[0]["attempt_id"] == "attempt-a"
    assert (
        observations[0]["runtime_diagnostics"]["projection_failure"]
        == sdk_result.runtime_diagnostics["projection_failure"]
    )


@pytest.mark.asyncio
async def test_legal_two_tools_share_message_role_and_keep_independent_receipts(
    source_routing_settings,
):
    import claude_agent_sdk as sdk

    async def source(*, prompt, options):
        del prompt
        yield assistant(
            sdk, "work", "work-text", sdk.TextBlock(text="Checking two inputs.")
        )
        for index in range(2):
            call_id = f"private-read-{index}"
            payload = {
                "tool_name": "Read",
                "tool_use_id": call_id,
                "tool_input": {"file_path": "input.txt"},
            }
            yield assistant(
                sdk,
                "work",
                f"work-tool-{index}",
                sdk.ToolUseBlock(id=call_id, name="Read", input=payload["tool_input"]),
            )
            admitted = await options.hooks["PreToolUse"][0].hooks[0](
                payload, call_id, {}
            )
            assert admitted["hookSpecificOutput"]["permissionDecision"] == "allow"
            await options.hooks["PostToolUse"][-1].hooks[0](payload, call_id, {})
        yield assistant(
            sdk, "final", "final-answer", sdk.TextBlock(text="Both reads completed.")
        )
        result = terminal(sdk, "PRIVATE_RESULT")
        yield result
        yield result  # terminal replay cannot produce a second public receipt

    result, events, texts = await execute(source_routing_settings, source, partial=True)
    assert_answer(result, events, "Both reads completed.")
    assert "".join(texts) == "Both reads completed."
    assert commentary(events) == "Checking two inputs."
    completed = [event for event in events if event.event_type == "tool.completed"]
    assert (
        len(completed) == 2
        and completed[0].payload["operation_id"] != completed[1].payload["operation_id"]
    )
    assert sum(event.event_type == "message.completed" for event in events) == 1


@pytest.mark.asyncio
async def test_completed_source_never_invents_raw_checkpoint(source_routing_settings):
    import claude_agent_sdk as sdk
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    from tests.support.claude_sdk import native_client_factory

    async def source(*, prompt, options):
        del prompt
        assert options.include_partial_messages is False
        yield assistant(
            sdk, "final", "final-answer", sdk.TextBlock(text="Safe answer.")
        )
        yield terminal(sdk, "PRIVATE_RESULT")

    events, checkpoints = [], []
    result = await run_claude_agent_sdk(
        prompt="synthetic",
        cwd=source_routing_settings,
        skill_id=None,
        client_fn=native_client_factory(source),
        execution_policy="sandbox_brokered",
        run_id="run",
        attempt_id="attempt",
        on_agent_event=lambda batch: events.extend(batch) or True,
        on_sdk_text=checkpoints.append,
    )
    assert_answer(result, events, "Safe answer.")
    assert checkpoints == []

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from app.public_execution import (
    PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
    PUBLIC_EXECUTION_V2_STEP_PAYLOAD_FIELDS,
    PersistablePublicExecutionStepV2,
    PublicExecutionV2Projector,
    public_execution_event_from_row,
)

PUBLIC_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "sequence",
    "run_id",
    "step_id",
    "presentation_kind",
    "kind",
    "stage",
    "status",
    "progress",
    "safe_label",
    "created_at",
}


class _FailingOffsetTimezone(tzinfo):
    def __init__(self, exception_type):
        self._exception_type = exception_type

    def dst(self, value):
        return None

    def utcoffset(self, value):
        raise self._exception_type("repository timezone failure")


def _persisted_step(*, created_at):
    return {
        "id": "evt-execution-1",
        "sequence": 9,
        "event_type": "execution_step",
        "created_at": created_at,
        "payload_json": {
            "schema_version": PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
            "step_id": "pex_execution_1",
            "presentation_kind": "read",
            "kind": "file_read",
            "stage": "read",
            "status": "running",
            "safe_label": "Reading authorized files",
            "progress": {"current": 0, "total": 1},
        },
    }


def _v2_fact(
    *,
    invocation_id="private-call-1",
    tool_name="Read",
    lifecycle="started",
    safe_label=None,
):
    fact = {
        "invocation_id": invocation_id,
        "tool_name": tool_name,
        "lifecycle": lifecycle,
    }
    if safe_label is not None:
        fact["safe_label"] = safe_label
    return fact


def _v2_row(payload, *, event_type="execution_step"):
    return {
        "id": "evt-execution-v2-1",
        "sequence": 10,
        "event_type": event_type,
        "created_at": "2026-07-31T01:02:03Z",
        "payload_json": payload,
    }


def test_public_execution_row_normalizes_timezone_aware_repository_datetime():
    event = public_execution_event_from_row(
        "run-execution-1",
        _persisted_step(
            created_at=datetime(2026, 7, 30, 12, 34, 56, 120000, tzinfo=timezone(timedelta(hours=8)))
        ),
    )

    assert event is not None
    assert event["created_at"] == "2026-07-30T04:34:56.120000Z"
    assert {"event_id", "sequence", "run_id", "step_id"} <= set(event)
    assert set(event) == PUBLIC_EVENT_FIELDS


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-30T04:34:56Z",
        None,
    ],
)
def test_public_execution_row_preserves_existing_valid_timestamp_values(created_at):
    event = public_execution_event_from_row("run-execution-1", _persisted_step(created_at=created_at))

    assert event is not None
    assert event["created_at"] == created_at


@pytest.mark.parametrize(
    "created_at",
    [
        datetime(2026, 7, 30, 4, 34, 56),
        "2026-07-30T04:34:56",
        "not-a-timestamp",
        object(),
    ],
)
def test_public_execution_row_rejects_naive_malformed_and_unsupported_timestamps(created_at):
    assert public_execution_event_from_row(
        "run-execution-1", _persisted_step(created_at=created_at)
    ) is None


def test_public_execution_row_rejects_datetime_utc_conversion_overflow():
    created_at = datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))

    assert public_execution_event_from_row(
        "run-execution-1", _persisted_step(created_at=created_at)
    ) is None


@pytest.mark.parametrize("exception_type", [ValueError, TypeError])
def test_public_execution_row_rejects_datetime_timezone_offset_errors(exception_type):
    created_at = datetime(2026, 7, 30, tzinfo=_FailingOffsetTimezone(exception_type))

    assert public_execution_event_from_row(
        "run-execution-1", _persisted_step(created_at=created_at)
    ) is None


@pytest.mark.parametrize(
    ("tool_name", "presentation_kind", "kind", "stage", "safe_label"),
    [
        ("Skill", "skill", "capability", "execution", "Document review"),
        ("MCP", "mcp", "capability", "execution", "Tenant search"),
        ("Read", "read", "file_read", "read", "Reading authorized files"),
        ("Glob", "read", "file_read", "search", "Finding authorized files"),
        ("Grep", "read", "file_read", "search", "Finding authorized files"),
        ("LS", "read", "file_read", "search", "Finding authorized files"),
        ("Write", "write", "generation", "edit", "Updating authorized files"),
        ("Edit", "write", "generation", "edit", "Updating authorized files"),
        ("NotebookEdit", "write", "generation", "edit", "Updating authorized files"),
        ("Bash", "processing", "processing", "data", "Data processing"),
        ("Agent", "agent", "collaboration", "execution", "Coordinating task"),
        ("Task", "agent", "collaboration", "execution", "Coordinating task"),
    ],
)
def test_v2_projector_owns_the_closed_server_tool_mapping(
    tool_name,
    presentation_kind,
    kind,
    stage,
    safe_label,
):
    projected = PublicExecutionV2Projector().project(
        _v2_fact(tool_name=tool_name, safe_label=safe_label if tool_name in {"Skill", "MCP"} else None)
    )

    assert isinstance(projected, PersistablePublicExecutionStepV2)
    assert projected.event_type == "execution_step"
    assert projected.payload_json == {
        "schema_version": PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
        "step_id": projected.payload_json["step_id"],
        "presentation_kind": presentation_kind,
        "kind": kind,
        "stage": stage,
        "status": "running",
        "progress": {"current": 0, "total": 1},
        **({"safe_label": safe_label} if safe_label else {}),
    }
    assert set(projected.payload_json) <= PUBLIC_EXECUTION_V2_STEP_PAYLOAD_FIELDS
    assert "private-call-1" not in str(projected.payload_json)


def test_v2_persistable_value_cannot_be_constructed_outside_the_projector():
    with pytest.raises(TypeError):
        PersistablePublicExecutionStepV2(
            event_type="execution_step",
            step_id="pex_forged",
            presentation_kind="skill",
            kind="capability",
            stage="execution",
            status="running",
            progress_current=0,
            progress_total=1,
            safe_label="Caller selected label",
        )


@pytest.mark.parametrize(
    ("terminal_lifecycle", "event_type", "terminal_status"),
    [
        ("completed", "execution_step_completed", "completed"),
        ("failed", "execution_step_failed", "failed"),
    ],
)
def test_v2_projector_preserves_one_opaque_step_across_all_three_lifecycle_states(
    terminal_lifecycle,
    event_type,
    terminal_status,
):
    projector = PublicExecutionV2Projector()

    started = projector.project(_v2_fact())
    terminal = projector.project(_v2_fact(lifecycle=terminal_lifecycle))

    assert started is not None
    assert terminal is not None
    assert started.event_type == "execution_step"
    assert terminal.event_type == event_type
    assert started.payload_json["status"] == "running"
    assert terminal.payload_json["status"] == terminal_status
    assert started.payload_json["progress"] == {"current": 0, "total": 1}
    assert terminal.payload_json["progress"] == {"current": 1, "total": 1}
    assert started.payload_json["step_id"] == terminal.payload_json["step_id"]
    assert started.payload_json["step_id"].startswith("pex_")
    assert "private-call-1" not in started.payload_json["step_id"]


def test_v2_projector_rejects_unknown_mismatched_and_duplicate_lifecycle_facts_atomically():
    projector = PublicExecutionV2Projector()

    assert projector.project(_v2_fact(tool_name="FutureTool")) is None
    assert projector.project(_v2_fact(lifecycle="completed")) is None
    assert projector.project(_v2_fact()) is not None
    assert projector.project(_v2_fact()) is None
    assert projector.project(_v2_fact(tool_name="Write", lifecycle="completed")) is None
    assert projector.project(_v2_fact(lifecycle="completed")) is not None
    assert projector.project(_v2_fact(lifecycle="completed")) is None
    assert projector.project(_v2_fact(lifecycle="failed")) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_name", ["Read"]),
        ("lifecycle", ["started"]),
        ("invocation_id", {"private": "id"}),
    ],
)
def test_v2_projector_fails_closed_for_malformed_raw_fact_types(field, value):
    projector = PublicExecutionV2Projector()

    assert projector.project({**_v2_fact(), field: value}) is None
    assert projector.project(_v2_fact()) is not None


@pytest.mark.parametrize(
    "fact",
    [
        _v2_fact(invocation_id=" private-call-1"),
        _v2_fact(
            tool_name="Skill",
            safe_label=" Document review",
        ),
    ],
)
def test_v2_projector_requires_exact_server_authorized_identity_and_safe_label(fact):
    assert PublicExecutionV2Projector().project(fact) is None


def test_v2_projector_rejects_safe_label_drift_and_labels_for_non_capabilities():
    projector = PublicExecutionV2Projector()
    started = projector.project(
        _v2_fact(tool_name="Skill", safe_label="Document review")
    )

    assert started is not None
    assert projector.project(
        _v2_fact(
            tool_name="Skill",
            lifecycle="completed",
            safe_label="Different label",
        )
    ) is None
    assert projector.project(
        _v2_fact(
            tool_name="Skill",
            lifecycle="completed",
            safe_label="Document review",
        )
    ) is not None
    assert PublicExecutionV2Projector().project(
        _v2_fact(tool_name="Bash", safe_label="Friendly command")
    ) is None
    assert PublicExecutionV2Projector().project(
        {**_v2_fact(tool_name="Skill"), "safe_label": None}
    ) is None


@pytest.mark.parametrize(
    "private_field",
    [
        "command",
        "args",
        "stdout",
        "stderr",
        "path",
        "tool_call_id",
        "endpoint",
        "token",
        "reasoning",
        "answer",
        "artifact",
        "title",
        "summary",
        "label",
        "fact_kind",
        "extension",
    ],
)
def test_v2_projector_rejects_private_or_arbitrary_raw_fact_extensions(private_field):
    projector = PublicExecutionV2Projector()
    hostile = {
        **_v2_fact(),
        private_field: "C:\\private\\secret private-token",
    }

    assert projector.project(hostile) is None
    assert projector.project(_v2_fact()) is not None


def test_public_execution_row_rejects_retired_v1_and_unversioned_payloads():
    legacy_payload = {
        "step_id": "step-old",
        "kind": "processing",
        "stage": "execution",
        "status": "running",
        "title": "Old title",
        "summary": "Old summary",
        "progress": {"current": 0, "total": 1},
    }
    for payload in (
        {"schema_version": "ai-platform.public-execution-event.v1", **legacy_payload},
        legacy_payload,
    ):
        assert public_execution_event_from_row(
            "run-execution-1",
            {**_persisted_step(created_at=None), "payload_json": payload},
        ) is None


def test_public_execution_row_projects_only_the_strict_v2_payload_plus_row_authority():
    projected = PublicExecutionV2Projector().project(_v2_fact())
    assert projected is not None

    event = public_execution_event_from_row(
        "run-execution-v2-1",
        _v2_row(projected.payload_json),
    )

    assert event == {
        **projected.payload_json,
        "event_id": "evt-execution-v2-1",
        "sequence": 10,
        "run_id": "run-execution-v2-1",
        "created_at": "2026-07-31T01:02:03Z",
    }


@pytest.mark.parametrize(
    "hostile_field",
    [
        "command",
        "args",
        "stdout",
        "stderr",
        "path",
        "tool_call_id",
        "endpoint",
        "token",
        "reasoning",
        "answer",
        "answer_delta",
        "artifact",
        "artifact_public_id",
        "safe_file_name",
        "title",
        "summary",
        "extension",
    ],
)
def test_public_execution_row_rejects_v2_private_answer_artifact_and_extension_fields(
    hostile_field,
):
    projected = PublicExecutionV2Projector().project(_v2_fact())
    assert projected is not None
    payload = {**projected.payload_json, hostile_field: "private"}

    assert public_execution_event_from_row(
        "run-execution-v2-1",
        _v2_row(payload),
    ) is None


def test_public_execution_row_rejects_explicit_null_v2_safe_label():
    projected = PublicExecutionV2Projector().project(
        _v2_fact(tool_name="Skill", safe_label="Document review")
    )
    assert projected is not None

    assert public_execution_event_from_row(
        "run-execution-v2-1",
        _v2_row({**projected.payload_json, "safe_label": None}),
    ) is None


def test_public_execution_row_fails_closed_for_malformed_event_type():
    projected = PublicExecutionV2Projector().project(_v2_fact())
    assert projected is not None

    assert public_execution_event_from_row(
        "run-execution-v2-1",
        _v2_row(projected.payload_json, event_type=["execution_step"]),
    ) is None

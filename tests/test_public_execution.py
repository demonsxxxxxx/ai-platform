from datetime import datetime, timedelta, timezone

import pytest

from app.public_execution import PublicExecutionProjector, public_execution_event_from_row

PUBLIC_STEP_PAYLOAD_FIELDS = {
    "step_id",
    "kind",
    "stage",
    "status",
    "title",
    "summary",
    "progress",
    "safe_file_name",
    "artifact_public_id",
    "created_at",
}


def _persisted_step(*, created_at):
    return {
        "id": "evt-execution-1",
        "sequence": 9,
        "event_type": "execution_step",
        "created_at": created_at,
        "payload_json": {
            "step_id": "pex_execution_1",
            "kind": "processing",
            "stage": "execution",
            "status": "running",
            "title": "Process request",
            "summary": "Running controlled processing",
            "progress": {"current": 0, "total": 1},
        },
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
    assert set(event) == {"schema_version", "event_id", "sequence", "run_id", *PUBLIC_STEP_PAYLOAD_FIELDS}


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


def test_public_execution_projector_is_the_fail_closed_owner_of_strict_opaque_events():
    projector = PublicExecutionProjector()
    private_fact = {
        "invocation_id": "private-tool-call-77",
        "fact_kind": "capability_invocation",
        "lifecycle": "started",
        "public_label": "Document review",
        "command": "powershell -Command $env:PRIVATE_TOKEN",
        "args": ["C:\\private\\workspace"],
        "stdout": "private stdout",
        "stderr": "private stderr",
        "path": "C:\\private\\workspace",
        "token": "private-token",
        "private_payload": {"secret": "private"},
    }

    started = projector.project(private_fact)
    completed = projector.project({**private_fact, "lifecycle": "completed"})

    assert started is not None
    assert completed is not None
    assert set(started) <= PUBLIC_STEP_PAYLOAD_FIELDS
    assert started["kind"] == "capability"
    assert completed["kind"] == "capability"
    assert started["status"] == "running"
    assert completed["status"] == "completed"
    assert started["step_id"] == completed["step_id"]
    assert all(value not in str(started) for value in ("private-tool-call-77", "PRIVATE_TOKEN", "private-token", "C:\\private"))
    assert projector.project({**private_fact, "fact_kind": "future_private_fact"}) is None
    assert projector.project({**private_fact, "public_label": "cmd.exe /c private"}) is None


def test_public_execution_projector_keeps_progress_monotonic_for_one_opaque_step():
    projector = PublicExecutionProjector()
    fact = {
        "invocation_id": "private-tool-call-progress",
        "fact_kind": "tool_invocation",
        "public_label": "Processing authorized data",
    }

    started = projector.project({**fact, "lifecycle": "started", "progress": {"current": 0, "total": 4}})
    progress = projector.project({**fact, "lifecycle": "progress", "progress": {"current": 2, "total": 4}})
    completed = projector.project({**fact, "lifecycle": "completed", "progress": {"current": 4, "total": 4}})

    assert started is not None
    assert progress is not None
    assert completed is not None
    assert {started["step_id"], progress["step_id"], completed["step_id"]} == {started["step_id"]}
    assert [started["progress"], progress["progress"], completed["progress"]] == [
        {"current": 0, "total": 4},
        {"current": 2, "total": 4},
        {"current": 4, "total": 4},
    ]
    assert projector.project({**fact, "lifecycle": "completed", "progress": {"current": 4, "total": 4}}) is None


def test_public_execution_projector_rejects_progress_total_changes_and_regressions():
    projector = PublicExecutionProjector()
    fact = {
        "invocation_id": "private-tool-call-reject",
        "fact_kind": "tool_invocation",
        "public_label": "Processing authorized data",
    }

    assert projector.project({**fact, "lifecycle": "started", "progress": {"current": 0, "total": 4}})
    assert projector.project({**fact, "lifecycle": "progress", "progress": {"current": 3, "total": 4}})
    assert projector.project({**fact, "lifecycle": "progress", "progress": {"current": 3, "total": 5}}) is None
    assert projector.project({**fact, "lifecycle": "failed", "progress": {"current": 2, "total": 4}}) is None
    failed = projector.project({**fact, "lifecycle": "failed", "progress": {"current": 3, "total": 4}})

    assert failed is not None
    assert failed["status"] == "failed"


def test_public_execution_projector_rejects_invalid_public_fields_without_consuming_lifecycle():
    projector = PublicExecutionProjector()
    fact = {
        "invocation_id": "private-tool-call-atomic",
        "fact_kind": "tool_invocation",
        "public_label": "Processing authorized data",
    }

    invalid_started = {**fact, "lifecycle": "started", "safe_file_name": "C:\\private\\report.txt"}
    started = {**fact, "lifecycle": "started", "progress": {"current": 0, "total": 2}}
    assert projector.project(invalid_started) is None
    assert projector.project(started) is not None

    invalid_progress = {
        **fact,
        "lifecycle": "progress",
        "progress": {"current": 2, "total": 2},
        "artifact_public_id": "private/artifact",
    }
    progress = {**fact, "lifecycle": "progress", "progress": {"current": 1, "total": 2}}
    assert projector.project(invalid_progress) is None
    assert projector.project(progress) is not None

    invalid_terminal = {
        **fact,
        "lifecycle": "completed",
        "progress": {"current": 2, "total": 2},
        "safe_file_name": "report\\draft.txt",
    }
    completed = {**fact, "lifecycle": "completed", "progress": {"current": 2, "total": 2}}
    assert projector.project(invalid_terminal) is None
    assert projector.project(completed) is not None


def test_public_execution_projector_accepts_safe_result_basename_and_rejects_paths():
    projector = PublicExecutionProjector()
    fact = {
        "invocation_id": "private-artifact-call",
        "fact_kind": "artifact_generation",
        "lifecycle": "started",
        "public_label": "Generating result file",
    }

    assert projector.project({**fact, "safe_file_name": "../private/analysis.xlsx"}) is None
    started = projector.project({**fact, "safe_file_name": "分析报告.xlsx"})

    assert started is not None
    assert started["safe_file_name"] == "分析报告.xlsx"

    for unsafe_name in ("..", "C:\\private\\analysis.xlsx", "analysis;whoami.xlsx"):
        rejected = PublicExecutionProjector().project(
            {**fact, "invocation_id": f"private-{unsafe_name}", "safe_file_name": unsafe_name}
        )
        assert rejected is None

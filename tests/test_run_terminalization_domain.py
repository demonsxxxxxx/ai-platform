from app.runs.domain.terminalization import (
    TERMINAL_RUN_STATUSES,
    RunTerminalizationProgress,
    progress_for_requested_status,
)
from app.runs.domain.attempt_lifecycle import RUN_ATTEMPT_STATUSES
from app.runs.domain.retry import RUN_RETRYABLE_STATUSES, run_retry_block_reason
from app.runs.domain.run_status import (
    RUN_STATUS_VALUES,
    is_terminal_run_status,
    normalize_public_run_status,
    normalize_run_status,
)
from app.conversations.application.submission_resolution import normalize_submission_run_status
from app.runs.api import normalize_public_run_status as API_NORMALIZE_PUBLIC_RUN_STATUS


def test_terminal_status_contract_is_exact_and_immutable():
    assert TERMINAL_RUN_STATUSES == frozenset({"succeeded", "failed", "cancelled"})


def test_run_status_classification_normalizes_aliases_and_fails_closed():
    assert normalize_run_status(" CANCELED ") == "cancelled"
    assert is_terminal_run_status("canceled")
    assert not is_terminal_run_status("completed")
    assert not is_terminal_run_status("unknown")
    assert normalize_public_run_status("canceled") == "cancelled"
    assert normalize_public_run_status("queued") == "queued"
    assert normalize_public_run_status("cancel_requested") is None
    assert normalize_public_run_status("unknown") is None
    assert normalize_submission_run_status is API_NORMALIZE_PUBLIC_RUN_STATUS
    assert normalize_submission_run_status("unknown") is None


def test_historical_retry_statuses_and_attempt_vocabulary_remain_separate():
    assert RUN_RETRYABLE_STATUSES == frozenset(
        {"failed", "dead-letter", "dead_letter", "dead-lettered"}
    )
    assert all(run_retry_block_reason(status, None) is None for status in RUN_RETRYABLE_STATUSES)
    assert {"created", "claimed", "expired"}.issubset(RUN_ATTEMPT_STATUSES)
    assert not {"created", "claimed", "expired", "completed"}.intersection(
        RUN_STATUS_VALUES
    )


def test_progress_is_truthy_only_for_a_completed_terminal_status():
    assert bool(RunTerminalizationProgress(completed=True, status="failed")) is True
    assert bool(RunTerminalizationProgress(completed=False, status="failed")) is False
    assert bool(RunTerminalizationProgress(completed=True, status="running")) is False
    assert bool(RunTerminalizationProgress(completed=True, status=None)) is False


def test_progress_requested_status_must_match_the_observed_terminal_status():
    progress = RunTerminalizationProgress(completed=True, status="cancelled")

    assert progress.is_terminal() is True
    assert progress.is_terminal("cancelled") is True
    assert progress.is_terminal("failed") is False


def test_requested_status_decision_returns_the_winning_progress_unchanged():
    progress = RunTerminalizationProgress(
        completed=True,
        status="failed",
        did_transition=True,
    )

    assert progress_for_requested_status(progress, requested_status="failed") is progress


def test_requested_status_decision_preserves_transition_fact_when_another_intent_won():
    progress = RunTerminalizationProgress(
        completed=True,
        status="cancelled",
        did_transition=True,
    )

    projected = progress_for_requested_status(progress, requested_status="failed")

    assert projected == RunTerminalizationProgress(
        completed=False,
        status="cancelled",
        did_transition=True,
    )


def test_requested_status_decision_maps_missing_progress_to_incomplete():
    assert progress_for_requested_status(None, requested_status="failed") == (
        RunTerminalizationProgress(completed=False, status=None)
    )


def test_progress_exposes_typed_facts_without_retired_mapping_compatibility():
    progress = RunTerminalizationProgress(
        completed=False,
        status="running",
    )

    assert progress.completed is False
    assert progress.status == "running"
    assert progress.did_transition is False
    assert not hasattr(progress, "get")

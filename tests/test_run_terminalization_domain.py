from app.runs.domain.terminalization import (
    TERMINAL_RUN_STATUSES,
    RunTerminalizationProgress,
    progress_for_requested_status,
)


def test_terminal_status_contract_is_exact_and_immutable():
    assert TERMINAL_RUN_STATUSES == frozenset({"succeeded", "failed", "cancelled"})


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


def test_progress_get_reads_known_and_default_values():
    progress = RunTerminalizationProgress(
        completed=False,
        status="running",
    )

    assert progress.get("status") == "running"
    assert progress.get("missing", "fallback") == "fallback"

import re
from pathlib import Path

import pytest

from app.runs.api import (
    OPEN_RUN_ATTEMPT_STATUSES,
    RUN_ATTEMPT_OWNER_KINDS,
    RUN_ATTEMPT_STATUSES,
    TERMINAL_RUN_ATTEMPT_STATUSES,
    RunAttemptTransitionError,
    decide_run_attempt_transition,
)


def _decide(current_status: str, requested_status: str, *, generation: int = 3):
    return decide_run_attempt_transition(
        current_status=current_status,
        requested_status=requested_status,
        owner_generation=generation,
        expected_owner_generation=generation,
    )


def test_run_attempt_status_sets_are_exact_and_immutable():
    assert RUN_ATTEMPT_STATUSES == frozenset(
        {
            "created",
            "queued",
            "claimed",
            "running",
            "cancel_requested",
            "expired",
            "succeeded",
            "failed",
            "cancelled",
        }
    )
    assert OPEN_RUN_ATTEMPT_STATUSES == frozenset(
        {"created", "queued", "claimed", "running", "cancel_requested", "expired"}
    )
    assert TERMINAL_RUN_ATTEMPT_STATUSES == frozenset(
        {"succeeded", "failed", "cancelled"}
    )
    assert RUN_ATTEMPT_OWNER_KINDS == frozenset(
        {"queue_worker", "reconciler", "operator"}
    )


def test_run_attempt_database_status_constraint_matches_domain_authority():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")
    start = schema.index("constraint chk_run_attempts_status")
    end = schema.index("constraint chk_run_attempts_owner_kind", start)
    persisted_statuses = frozenset(re.findall(r"'([a-z_]+)'", schema[start:end]))

    assert persisted_statuses == RUN_ATTEMPT_STATUSES


@pytest.mark.parametrize(
    ("current_status", "requested_status", "projected_run_status"),
    [
        ("created", "queued", "queued"),
        ("queued", "claimed", "running"),
        ("claimed", "running", "running"),
        ("running", "succeeded", "succeeded"),
        ("running", "failed", "failed"),
        ("running", "cancel_requested", "running"),
        ("cancel_requested", "cancelled", "cancelled"),
        ("running", "expired", "running"),
        ("expired", "failed", "failed"),
    ],
)
def test_run_attempt_legal_transition_projects_compatible_run_status(
    current_status,
    requested_status,
    projected_run_status,
):
    decision = _decide(current_status, requested_status)

    assert decision.previous_status == current_status
    assert decision.status == requested_status
    assert decision.did_transition is True
    assert decision.projected_run_status == projected_run_status
    assert decision.owner_generation == 4


def test_run_attempt_same_state_is_idempotent_for_current_owner():
    decision = _decide("running", "running")

    assert decision.did_transition is False
    assert decision.status == "running"
    assert decision.projected_run_status == "running"
    assert decision.owner_generation == 3


@pytest.mark.parametrize(
    ("current_status", "requested_status", "code"),
    [
        ("queued", "succeeded", "run_attempt_transition_invalid"),
        ("cancel_requested", "succeeded", "run_attempt_transition_invalid"),
        ("succeeded", "failed", "run_attempt_terminal_immutable"),
        ("cancelled", "running", "run_attempt_terminal_immutable"),
    ],
)
def test_run_attempt_illegal_or_terminal_rewrite_fails_closed(
    current_status,
    requested_status,
    code,
):
    with pytest.raises(RunAttemptTransitionError, match=code) as exc_info:
        _decide(current_status, requested_status)

    assert exc_info.value.code == code


def test_run_attempt_stale_owner_generation_fails_closed():
    with pytest.raises(
        RunAttemptTransitionError,
        match="run_attempt_owner_generation_stale",
    ):
        decide_run_attempt_transition(
            current_status="running",
            requested_status="succeeded",
            owner_generation=4,
            expected_owner_generation=3,
        )

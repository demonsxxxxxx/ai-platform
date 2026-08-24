"""Pure RunAttempt state transitions and Run-status projections."""

from __future__ import annotations

from dataclasses import dataclass


RUN_ATTEMPT_STATUSES = frozenset(
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
RUN_ATTEMPT_OWNER_KINDS = frozenset({"queue_worker", "reconciler", "operator"})
OPEN_RUN_ATTEMPT_STATUSES = frozenset(
    {"created", "queued", "claimed", "running", "cancel_requested", "expired"}
)
TERMINAL_RUN_ATTEMPT_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

_LEGAL_RUN_ATTEMPT_TRANSITIONS = {
    "created": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"claimed", "cancelled"}),
    "claimed": frozenset({"running", "cancel_requested", "failed", "expired"}),
    "running": frozenset({"cancel_requested", "succeeded", "failed", "expired"}),
    "cancel_requested": frozenset({"cancelled"}),
    "expired": frozenset({"failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class RunAttemptTransitionError(ValueError):
    """Stable fail-closed error for one rejected attempt transition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RunAttemptTransitionDecision:
    """One authorized transition and its compatible Run-level projection."""

    previous_status: str
    status: str
    did_transition: bool
    projected_run_status: str
    owner_generation: int


def _projected_run_status(attempt_status: str) -> str:
    if attempt_status in {"created", "queued"}:
        return "queued"
    if attempt_status in {
        "claimed",
        "running",
        "cancel_requested",
        "expired",
    }:
        return "running"
    return attempt_status


def decide_run_attempt_transition(
    *,
    current_status: str,
    requested_status: str,
    owner_generation: int,
    expected_owner_generation: int,
) -> RunAttemptTransitionDecision:
    """Authorize one exact owner-fenced RunAttempt transition."""

    if current_status not in RUN_ATTEMPT_STATUSES:
        raise RunAttemptTransitionError("run_attempt_current_status_invalid")
    if requested_status not in RUN_ATTEMPT_STATUSES:
        raise RunAttemptTransitionError("run_attempt_requested_status_invalid")
    if (
        not isinstance(owner_generation, int)
        or isinstance(owner_generation, bool)
        or owner_generation < 1
        or not isinstance(expected_owner_generation, int)
        or isinstance(expected_owner_generation, bool)
        or expected_owner_generation < 1
    ):
        raise RunAttemptTransitionError("run_attempt_owner_generation_invalid")
    if owner_generation != expected_owner_generation:
        raise RunAttemptTransitionError("run_attempt_owner_generation_stale")
    if current_status == requested_status:
        return RunAttemptTransitionDecision(
            previous_status=current_status,
            status=current_status,
            did_transition=False,
            projected_run_status=_projected_run_status(current_status),
            owner_generation=owner_generation,
        )
    if requested_status not in _LEGAL_RUN_ATTEMPT_TRANSITIONS[current_status]:
        error_code = (
            "run_attempt_terminal_immutable"
            if current_status in TERMINAL_RUN_ATTEMPT_STATUSES
            else "run_attempt_transition_invalid"
        )
        raise RunAttemptTransitionError(error_code)
    return RunAttemptTransitionDecision(
        previous_status=current_status,
        status=requested_status,
        did_transition=True,
        projected_run_status=_projected_run_status(requested_status),
        owner_generation=owner_generation + 1,
    )

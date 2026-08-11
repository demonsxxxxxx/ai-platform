"""Truthful Expert capability semantics derived from server-owned evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXACT_SKILL_INVOCATION_SOURCE = "executor_hook"


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    }


@dataclass(frozen=True)
class AgentCapabilityState:
    """Private evidence plus the fixed ordinary-user semantic projection."""

    bound: bool
    staged: bool
    sdk_registered: bool
    actually_invoked: bool
    completed: bool
    artifact_ready: bool
    optional_not_invoked_count: int
    invocation_attempt_count: int
    invocation_completed_count: int
    invocation_failed_count: int
    partial_failure: bool

    def public_projection(self) -> dict[str, bool | int]:
        return {
            "bound": self.bound,
            "staged": self.staged,
            "sdk_registered": self.sdk_registered,
            "actually_invoked": self.actually_invoked,
            "completed": self.completed,
            "artifact_ready": self.artifact_ready,
            "optional_not_invoked_count": self.optional_not_invoked_count,
            "invocation_attempt_count": self.invocation_attempt_count,
            "invocation_completed_count": self.invocation_completed_count,
            "invocation_failed_count": self.invocation_failed_count,
            "partial_failure": self.partial_failure,
        }


def exact_invoked_skills(
    executor_payload: dict[str, Any],
) -> set[str]:
    """Accept exact trusted invocation only inside the fixed staged set."""

    source = str(executor_payload.get("used_skills_source") or "").strip()
    if source != EXACT_SKILL_INVOCATION_SOURCE:
        return set()
    staged = _string_set(executor_payload.get("staged_skills"))
    return _string_set(executor_payload.get("used_skills")) & staged


def exact_attempted_skills(executor_payload: dict[str, Any]) -> set[str]:
    """Accept only worker-validated SDK invocation requests in the staged set."""

    if executor_payload.get("capability_evidence_validated") is not True:
        return set()
    staged = _string_set(executor_payload.get("staged_skills"))
    evidence = executor_payload.get("capability_evidence")
    if not isinstance(evidence, list):
        return set()
    return {
        identity
        for item in evidence
        if isinstance(item, dict)
        and item.get("capability_kind") == "skill"
        and item.get("lifecycle_phase") == "invocation_requested"
        and isinstance(item.get("canonical_identity"), str)
        and (identity := item["canonical_identity"].strip())
        and identity in staged
    }


def exact_skill_lifecycle_counts(
    executor_payload: dict[str, Any],
    *,
    skill_ids: set[str],
) -> dict[str, int]:
    """Count worker-validated SDK lifecycle facts for one staged Skill set."""

    counts = {"invocation_requested": 0, "completed": 0, "failed": 0}
    if executor_payload.get("capability_evidence_validated") is not True:
        return counts
    staged = _string_set(executor_payload.get("staged_skills"))
    eligible = staged & skill_ids
    evidence = executor_payload.get("capability_evidence")
    if not isinstance(evidence, list):
        return counts
    for item in evidence:
        if not isinstance(item, dict) or item.get("capability_kind") != "skill":
            continue
        identity = item.get("canonical_identity")
        phase = item.get("lifecycle_phase")
        if (
            isinstance(identity, str)
            and isinstance(phase, str)
            and identity.strip() in eligible
            and phase in counts
        ):
            counts[phase] += 1
    return counts


def project_agent_capability_state(
    *,
    bound_skill_ids: set[str],
    executor_payload: dict[str, Any],
    run_succeeded: bool,
    durable_artifact_count: int,
) -> AgentCapabilityState:
    staged_skills = _string_set(executor_payload.get("staged_skills"))
    invoked_skills = exact_invoked_skills(executor_payload)
    attempted_skills = exact_attempted_skills(executor_payload) | invoked_skills
    bound_staged = bound_skill_ids & staged_skills
    bound_invoked = bound_skill_ids & invoked_skills
    bound_attempted = bound_skill_ids & attempted_skills
    lifecycle_counts = exact_skill_lifecycle_counts(
        executor_payload,
        skill_ids=bound_skill_ids,
    )
    attempt_count = lifecycle_counts["invocation_requested"]
    completed_count = lifecycle_counts["completed"]
    failed_count = lifecycle_counts["failed"]
    return AgentCapabilityState(
        bound=bool(bound_skill_ids),
        staged=bool(bound_staged),
        sdk_registered=bool(bound_staged) and executor_payload.get("sdk_used") is True,
        actually_invoked=bool(bound_attempted),
        completed=run_succeeded and bool(bound_invoked),
        artifact_ready=durable_artifact_count > 0,
        optional_not_invoked_count=int(bool(bound_staged) and not bool(bound_attempted)),
        invocation_attempt_count=attempt_count,
        invocation_completed_count=completed_count,
        invocation_failed_count=failed_count,
        partial_failure=bool(completed_count and failed_count),
    )

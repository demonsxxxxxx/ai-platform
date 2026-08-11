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

    def public_projection(self) -> dict[str, bool | int]:
        return {
            "bound": self.bound,
            "staged": self.staged,
            "sdk_registered": self.sdk_registered,
            "actually_invoked": self.actually_invoked,
            "completed": self.completed,
            "artifact_ready": self.artifact_ready,
            "optional_not_invoked_count": self.optional_not_invoked_count,
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
    optional_not_invoked = bound_staged - bound_attempted
    return AgentCapabilityState(
        bound=bool(bound_skill_ids),
        staged=bool(bound_staged),
        sdk_registered=bool(bound_staged) and executor_payload.get("sdk_used") is True,
        actually_invoked=bool(bound_attempted),
        completed=run_succeeded and bool(bound_invoked),
        artifact_ready=durable_artifact_count > 0,
        optional_not_invoked_count=len(optional_not_invoked),
    )

"""Truthful Agent App capability semantics derived from server-owned evidence."""

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

    selected: bool
    staged: bool
    sdk_registered: bool
    actually_invoked: bool
    completed: bool
    artifact_ready: bool
    optional_not_invoked_count: int

    def public_projection(self) -> dict[str, bool | int]:
        return {
            "selected": self.selected,
            "staged": self.staged,
            "sdk_registered": self.sdk_registered,
            "actually_invoked": self.actually_invoked,
            "completed": self.completed,
            "artifact_ready": self.artifact_ready,
            "optional_not_invoked_count": self.optional_not_invoked_count,
        }


def exact_hook_invoked_skills(executor_payload: dict[str, Any]) -> set[str]:
    """Accept invocation only from the exact hook and only inside the staged set."""

    if str(executor_payload.get("used_skills_source") or "").strip() != EXACT_SKILL_INVOCATION_SOURCE:
        return set()
    staged = _string_set(executor_payload.get("staged_skills"))
    return _string_set(executor_payload.get("used_skills")) & staged


def project_agent_capability_state(
    *,
    required_skill_id: str,
    executor_payload: dict[str, Any],
    run_succeeded: bool,
    durable_artifact_count: int,
) -> AgentCapabilityState:
    staged_skills = _string_set(executor_payload.get("staged_skills"))
    invoked_skills = exact_hook_invoked_skills(executor_payload)
    required_staged = bool(required_skill_id and required_skill_id in staged_skills)
    required_invoked = bool(required_skill_id and required_skill_id in invoked_skills)
    optional_not_invoked = staged_skills - invoked_skills - {required_skill_id}
    return AgentCapabilityState(
        selected=bool(required_skill_id),
        staged=required_staged,
        sdk_registered=required_staged and executor_payload.get("sdk_used") is True,
        actually_invoked=required_invoked,
        completed=run_succeeded and required_invoked,
        artifact_ready=durable_artifact_count > 0,
        optional_not_invoked_count=len(optional_not_invoked),
    )

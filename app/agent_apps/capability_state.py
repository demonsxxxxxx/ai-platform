"""Truthful Agent App capability semantics derived from server-owned evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.required_tool_contract import (
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    selected_capability_completion_decision,
)


EXACT_SKILL_INVOCATION_SOURCE = "executor_hook"
PLATFORM_CONTROLLED_INVOCATION_SOURCE = "platform_controlled_runner"
_TRUSTED_CONTROLLED_SKILL_IDS_KEY = "_trusted_controlled_skill_ids"


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


def exact_invoked_skills(
    executor_payload: dict[str, Any],
    *,
    trusted_controlled_skill_ids: set[str] | None = None,
) -> set[str]:
    """Accept exact trusted invocation only inside the fixed staged set."""

    source = str(executor_payload.get("used_skills_source") or "").strip()
    if source == EXACT_SKILL_INVOCATION_SOURCE:
        trusted_source_skills = _string_set(executor_payload.get("staged_skills"))
    elif source == PLATFORM_CONTROLLED_INVOCATION_SOURCE:
        trusted_source_skills = set(trusted_controlled_skill_ids or set()) | _string_set(
            executor_payload.get(_TRUSTED_CONTROLLED_SKILL_IDS_KEY)
        )
    else:
        return set()
    staged = _string_set(executor_payload.get("staged_skills"))
    return _string_set(executor_payload.get("used_skills")) & staged & trusted_source_skills


def bind_validated_controlled_skill_evidence(
    payload: Any,
    result: Any,
    attempt_id: str,
    adapter: Any,
) -> dict[str, Any]:
    """Seal exact process-bound controlled Skill facts for downstream projections."""

    executor_payload = dict(result.executor_payload)
    executor_payload.pop(_TRUSTED_CONTROLLED_SKILL_IDS_KEY, None)
    from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter

    if type(adapter) is not ClaudeAgentWorkerAdapter:
        return executor_payload
    semantic_evidence = {**result.result, **executor_payload}
    source = str(semantic_evidence.get("used_skills_source") or "").strip()
    if source != PLATFORM_CONTROLLED_INVOCATION_SOURCE:
        return executor_payload
    used = _string_set(semantic_evidence.get("used_skills"))
    staged = _string_set(semantic_evidence.get("staged_skills"))
    authorized = {
        str(item.get("skill_id") or "").strip()
        for item in payload.skill_manifests
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }
    candidates = used & staged & authorized
    raw_evidence = executor_payload.get("capability_evidence")
    if not candidates or not isinstance(raw_evidence, list):
        return executor_payload
    try:
        records = [RequiredCapabilityEvidence.from_payload(item) for item in raw_evidence]
        controlled_records = [
            record
            for record in records
            if record.capability_kind == "skill"
            and record.canonical_identity in candidates
            and record.evidence_source == "controlled_skill_runner"
            and record.trust_basis == "process_bound_invocation"
        ]
        if {record.canonical_identity for record in controlled_records} != candidates:
            return executor_payload
        declarations = [
            RequiredCapabilityDeclaration.from_authorized_subject(
                capability_kind="skill",
                canonical_identity=skill_id,
            )
            for skill_id in sorted(candidates)
        ]
        decision = selected_capability_completion_decision(
            declarations=declarations,
            binding={
                "tenant_id": payload.tenant_id,
                "workspace_id": payload.workspace_id,
                "user_id": payload.user_id,
                "session_id": payload.session_id,
                "run_id": payload.run_id,
                "attempt_id": attempt_id,
            },
            evidence=[asdict(record) for record in controlled_records],
        )
    except RequiredToolContractError:
        return executor_payload
    if decision.allowed:
        executor_payload[_TRUSTED_CONTROLLED_SKILL_IDS_KEY] = sorted(candidates)
    return executor_payload


def project_agent_capability_state(
    *,
    required_skill_id: str,
    executor_payload: dict[str, Any],
    run_succeeded: bool,
    durable_artifact_count: int,
    trusted_controlled_skill_ids: set[str] | None = None,
) -> AgentCapabilityState:
    staged_skills = _string_set(executor_payload.get("staged_skills"))
    invoked_skills = exact_invoked_skills(
        executor_payload,
        trusted_controlled_skill_ids=trusted_controlled_skill_ids,
    )
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

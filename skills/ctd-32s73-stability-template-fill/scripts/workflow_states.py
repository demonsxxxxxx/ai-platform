from __future__ import annotations

from dataclasses import dataclass


STATE_NEW = "NEW"
STATE_WORKSPACE_PREPARED = "WORKSPACE_PREPARED"
STATE_SOURCES_REGISTERED = "SOURCES_REGISTERED"
STATE_FACT_EXTRACTION_REQUIRED = "FACT_EXTRACTION_REQUIRED"
STATE_FACT_PROJECT_PROFILE_REQUIRED = "FACT_PROJECT_PROFILE_REQUIRED"
STATE_FACT_STUDY_SHARDS_REQUIRED = "FACT_STUDY_SHARDS_REQUIRED"
STATE_FACT_PACKET_SUBMITTED = "FACT_PACKET_SUBMITTED"
STATE_FACT_PACKET_VALIDATING = "FACT_PACKET_VALIDATING"
STATE_FACT_PACKET_VALID = "FACT_PACKET_VALID"
STATE_FACT_PACKET_REVISION_REQUIRED = "FACT_PACKET_REVISION_REQUIRED"
STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED = "MISSING_EVIDENCE_RECOVERY_REQUIRED"
STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED = "MISSING_EVIDENCE_RECOVERY_EXHAUSTED"
STATE_TREND_CHARTS_REQUIRED = "TREND_CHARTS_REQUIRED"
STATE_TREND_CHARTS_SUBMITTED = "TREND_CHARTS_SUBMITTED"
STATE_TREND_CHARTS_VALIDATING = "TREND_CHARTS_VALIDATING"
STATE_TREND_CHARTS_VALID = "TREND_CHARTS_VALID"
STATE_TREND_CHARTS_REVISION_REQUIRED = "TREND_CHARTS_REVISION_REQUIRED"
STATE_BODY_SECTIONS_REQUIRED = "BODY_SECTIONS_REQUIRED"
STATE_BODY_SECTIONS_SUBMITTED = "BODY_SECTIONS_SUBMITTED"
STATE_BODY_SECTIONS_VALIDATING = "BODY_SECTIONS_VALIDATING"
STATE_BODY_SECTIONS_VALID = "BODY_SECTIONS_VALID"
STATE_BODY_SECTIONS_REVISION_REQUIRED = "BODY_SECTIONS_REVISION_REQUIRED"
STATE_BODY_SKELETON_REQUIRED = "BODY_SKELETON_REQUIRED"
STATE_BODY_SKELETON_COMPLETED = "BODY_SKELETON_COMPLETED"
STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY = "BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY"
STATE_TABLE_RENDER_READY = "TABLE_RENDER_READY"
STATE_PIPELINE_RUNNING = "PIPELINE_RUNNING"
STATE_PIPELINE_COMPLETED = "PIPELINE_COMPLETED"
STATE_FINAL_VALIDATING = "FINAL_VALIDATING"
STATE_COMPLETED_FINAL = "COMPLETED_FINAL"
STATE_COMPLETED_INTERMEDIATE = "COMPLETED_INTERMEDIATE"
STATE_PAUSED = "PAUSED"
STATE_FAILED = "FAILED"

ARTIFACT_FACT_PACKET = "fact_packet"
ARTIFACT_BODY_SKELETON_DOCX = "body_skeleton_docx"
LEGACY_FACT_SHARD_ARTIFACTS = frozenset(
    {
        "project_profile_facts",
        "long_term_stability_facts",
        "accelerated_stability_facts",
        "stress_study_facts",
    }
)
FACT_SHARD_ARTIFACTS = LEGACY_FACT_SHARD_ARTIFACTS
PROJECT_PROFILE_FACT_ARTIFACTS = frozenset({ARTIFACT_FACT_PACKET})
STUDY_FACT_SHARD_ARTIFACTS = frozenset({ARTIFACT_FACT_PACKET})

RECOVERY_NONE = "none"
RECOVERY_SUBMIT_FACT_PACKET = "submit_fact_packet"
RECOVERY_SUBMIT_FACT_SHARDS = "submit_fact_shards"
RECOVERY_SUBMIT_FACT_PACKET_AFTER_FAILED_VALIDATION = "submit_fact_packet_after_failed_validation"
RECOVERY_SUBMIT_BODY_SKELETON_DOCX = "submit_body_skeleton_docx"

TERMINAL_KIND_RUNNING = "running"
TERMINAL_KIND_PAUSED = "paused"
TERMINAL_KIND_PAUSED_RECOVERABLE = "paused_recoverable"
TERMINAL_KIND_INTERMEDIATE_NOT_DELIVERABLE = "intermediate_not_deliverable"
TERMINAL_KIND_BLOCKED_NOT_DELIVERABLE = "blocked_not_deliverable"
TERMINAL_KIND_FAILED = "failed"
TERMINAL_KIND_FINAL = "final"

DELIVERY_NOT_READY = "not_ready"
DELIVERY_NOT_DELIVERABLE = "not_deliverable"
DELIVERY_FINAL_CANDIDATE = "final_candidate"


@dataclass(frozen=True)
class StateRule:
    kind: str
    allowed_artifacts: frozenset[str] = frozenset()
    recovery_mode: str = RECOVERY_NONE


STATE_RULES: dict[str, StateRule] = {
    STATE_NEW: StateRule("running"),
    STATE_WORKSPACE_PREPARED: StateRule("running"),
    STATE_SOURCES_REGISTERED: StateRule("running"),
    STATE_FACT_PROJECT_PROFILE_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_FACT_STUDY_SHARDS_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_FACT_EXTRACTION_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_FACT_PACKET_SUBMITTED: StateRule("running"),
    STATE_FACT_PACKET_VALIDATING: StateRule("running"),
    STATE_FACT_PACKET_VALID: StateRule("running"),
    STATE_FACT_PACKET_REVISION_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET_AFTER_FAILED_VALIDATION,
    ),
    STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED: StateRule("blocked"),
    STATE_TREND_CHARTS_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_TREND_CHARTS_SUBMITTED: StateRule("running"),
    STATE_TREND_CHARTS_VALIDATING: StateRule("running"),
    STATE_TREND_CHARTS_VALID: StateRule("running"),
    STATE_TREND_CHARTS_REVISION_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_BODY_SECTIONS_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_BODY_SECTIONS_SUBMITTED: StateRule("running"),
    STATE_BODY_SECTIONS_VALIDATING: StateRule("running"),
    STATE_BODY_SECTIONS_VALID: StateRule("running"),
    STATE_BODY_SECTIONS_REVISION_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_FACT_PACKET}),
        RECOVERY_SUBMIT_FACT_PACKET,
    ),
    STATE_BODY_SKELETON_REQUIRED: StateRule(
        "paused",
        frozenset({ARTIFACT_BODY_SKELETON_DOCX}),
        RECOVERY_SUBMIT_BODY_SKELETON_DOCX,
    ),
    STATE_BODY_SKELETON_COMPLETED: StateRule("running"),
    STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY: StateRule("running"),
    STATE_TABLE_RENDER_READY: StateRule("running"),
    STATE_PIPELINE_RUNNING: StateRule("running"),
    STATE_PIPELINE_COMPLETED: StateRule("running"),
    STATE_FINAL_VALIDATING: StateRule("running"),
    STATE_COMPLETED_FINAL: StateRule("final"),
    STATE_COMPLETED_INTERMEDIATE: StateRule("intermediate"),
    STATE_PAUSED: StateRule("paused"),
    STATE_FAILED: StateRule("failed"),
}

ALL_STATES = frozenset(STATE_RULES)
SUPPORTED_ARTIFACT_KEYS = frozenset({ARTIFACT_FACT_PACKET, ARTIFACT_BODY_SKELETON_DOCX})

FACT_PACKET_INPUT_STATES = frozenset(
    {
        STATE_FACT_EXTRACTION_REQUIRED,
        STATE_FACT_PROJECT_PROFILE_REQUIRED,
        STATE_FACT_STUDY_SHARDS_REQUIRED,
        STATE_FACT_PACKET_REVISION_REQUIRED,
        STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED,
    }
)
BODY_SKELETON_INPUT_STATES = frozenset({STATE_BODY_SKELETON_REQUIRED})
FACT_PACKET_EARLY_INPUT_STATES = frozenset({STATE_NEW, STATE_WORKSPACE_PREPARED, STATE_SOURCES_REGISTERED})
FACT_PACKET_READY_STATES = frozenset(
    {
        STATE_FACT_PACKET_SUBMITTED,
        STATE_FACT_PACKET_VALIDATING,
        STATE_FACT_PACKET_VALID,
    }
)
TREND_CHARTS_INPUT_STATES = frozenset(
    {
        STATE_TREND_CHARTS_REQUIRED,
        STATE_TREND_CHARTS_REVISION_REQUIRED,
    }
)
TREND_CHARTS_READY_STATES = frozenset({STATE_TREND_CHARTS_VALID})
BODY_SECTIONS_INPUT_STATES = frozenset(
    {
        STATE_BODY_SECTIONS_REQUIRED,
        STATE_BODY_SECTIONS_REVISION_REQUIRED,
    }
)
BODY_SECTIONS_READY_STATES = frozenset({STATE_BODY_SECTIONS_VALID})
BODY_SKELETON_READY_STATES = frozenset(
    {
        STATE_BODY_SKELETON_COMPLETED,
        STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY,
        STATE_TABLE_RENDER_READY,
        STATE_PIPELINE_RUNNING,
    }
)
FINALIZE_READY_STATES = frozenset({STATE_PIPELINE_COMPLETED, STATE_FINAL_VALIDATING})


def rule_for(state_name: str | None) -> StateRule:
    if state_name is None:
        return StateRule("running")
    return STATE_RULES.get(state_name, StateRule("running"))


def body_skeleton_missing(blocking_reasons: list[str] | tuple[str, ...] | None) -> bool:
    return any("body/skeleton" in str(reason) for reason in (blocking_reasons or []))


def final_validation_failed(blocking_reasons: list[str] | tuple[str, ...] | None) -> bool:
    return any("final validation did not pass" in str(reason) for reason in (blocking_reasons or []))


def completed_intermediate_recoverable(blocking_reasons: list[str] | tuple[str, ...] | None) -> bool:
    return body_skeleton_missing(blocking_reasons) or final_validation_failed(blocking_reasons)


def allowed_artifacts_for_state(
    state_name: str | None,
    blocking_reasons: list[str] | tuple[str, ...] | None = None,
    *,
    recoverable_fact_packet_failure: bool = False,
) -> frozenset[str]:
    if recoverable_fact_packet_failure:
        return frozenset({ARTIFACT_FACT_PACKET})
    if state_name == STATE_COMPLETED_INTERMEDIATE and completed_intermediate_recoverable(blocking_reasons):
        return frozenset({ARTIFACT_BODY_SKELETON_DOCX})
    return rule_for(state_name).allowed_artifacts


def accepted_input_artifacts_for_state(
    state_name: str | None,
    blocking_reasons: list[str] | tuple[str, ...] | None = None,
    *,
    recoverable_fact_packet_failure: bool = False,
) -> frozenset[str]:
    allowed = set(
        allowed_artifacts_for_state(
            state_name,
            blocking_reasons,
            recoverable_fact_packet_failure=recoverable_fact_packet_failure,
        )
    )
    if state_name is None or state_name in FACT_PACKET_EARLY_INPUT_STATES:
        allowed.add(ARTIFACT_FACT_PACKET)
    return frozenset(allowed)


def recovery_mode_for_state(
    state_name: str | None,
    blocking_reasons: list[str] | tuple[str, ...] | None = None,
    *,
    recoverable_fact_packet_failure: bool = False,
) -> str:
    if recoverable_fact_packet_failure:
        return RECOVERY_SUBMIT_FACT_PACKET_AFTER_FAILED_VALIDATION
    if state_name == STATE_COMPLETED_INTERMEDIATE and completed_intermediate_recoverable(blocking_reasons):
        return RECOVERY_SUBMIT_BODY_SKELETON_DOCX
    return rule_for(state_name).recovery_mode


def state_is_paused_or_terminal(state_name: str | None) -> bool:
    return rule_for(state_name).kind in {"paused", "blocked", "failed", "final", "intermediate"}


def state_is_final(state_name: str | None, final: bool) -> bool:
    return bool(final and state_name == STATE_COMPLETED_FINAL)


def terminal_kind_for_state(
    state_name: str | None,
    final: bool,
    has_required_artifacts: bool,
) -> str:
    if state_is_final(state_name, final):
        return TERMINAL_KIND_FINAL
    if has_required_artifacts:
        return TERMINAL_KIND_PAUSED_RECOVERABLE
    kind = rule_for(state_name).kind
    if kind == "intermediate":
        return TERMINAL_KIND_INTERMEDIATE_NOT_DELIVERABLE
    if kind == "blocked":
        return TERMINAL_KIND_BLOCKED_NOT_DELIVERABLE
    if kind == "failed":
        return TERMINAL_KIND_FAILED
    if kind == "paused":
        return TERMINAL_KIND_PAUSED
    return TERMINAL_KIND_RUNNING


def delivery_status_for_state(state_name: str | None, final: bool) -> str:
    if state_is_final(state_name, final):
        return DELIVERY_FINAL_CANDIDATE
    if state_name == STATE_PAUSED or rule_for(state_name).kind in {"blocked", "failed", "final", "intermediate"}:
        return DELIVERY_NOT_DELIVERABLE
    return DELIVERY_NOT_READY


def step_done_for_state(
    state_name: str | None,
    final: bool,
    has_required_artifacts: bool,
) -> bool:
    if state_is_final(state_name, final):
        return True
    if has_required_artifacts:
        return False
    return rule_for(state_name).kind in {"blocked", "failed"}

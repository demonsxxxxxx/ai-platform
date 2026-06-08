from __future__ import annotations

from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "ai-platform.quality-golden-set-readiness.v1"
GATE_NAME = "G9 Quality Golden-Set Evaluation"
EVIDENCE_CONTRACT_SCHEMA_VERSION = "ai-platform.golden-set-eval-evidence-contract.v1"
QUALITY_SCORE_SCHEMA_VERSION = "ai-platform.quality-score.v1"

_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "office_document_revision",
        "workflow": "revise a proposal or policy document using bounded project context",
        "required_public_inputs": [
            "source document label",
            "requested revision goal",
            "approved style or terminology hints",
        ],
        "expected_public_outputs": [
            "revised artifact reference",
            "change summary",
            "context provenance list",
        ],
        "evaluator_signals": [
            "task_success",
            "instruction_following",
            "context_grounding",
            "artifact_quality",
            "safety_and_redaction",
        ],
    },
    {
        "id": "meeting_summary_followup",
        "workflow": "summarize meeting notes and answer a follow-up without re-uploading all context",
        "required_public_inputs": [
            "meeting note label",
            "follow-up request",
            "previous artifact label when available",
        ],
        "expected_public_outputs": [
            "summary or answer text",
            "referenced materials list",
            "follow-up continuity marker",
        ],
        "evaluator_signals": [
            "task_success",
            "instruction_following",
            "context_grounding",
            "safety_and_redaction",
        ],
    },
    {
        "id": "translation_terminology_consistency",
        "workflow": "translate a business document while preserving approved terminology",
        "required_public_inputs": [
            "source document label",
            "target language",
            "approved glossary label",
        ],
        "expected_public_outputs": [
            "translated artifact reference",
            "terminology consistency summary",
            "glossary provenance list",
        ],
        "evaluator_signals": [
            "task_success",
            "instruction_following",
            "context_grounding",
            "artifact_quality",
            "safety_and_redaction",
        ],
    },
    {
        "id": "sop_rag_answer_grounding",
        "workflow": "answer an SOP question with cited internal knowledge snippets",
        "required_public_inputs": [
            "question text",
            "knowledge source labels",
            "tenant-safe retrieval summary",
        ],
        "expected_public_outputs": [
            "grounded answer",
            "citation labels",
            "missing-context notice when needed",
        ],
        "evaluator_signals": [
            "task_success",
            "context_grounding",
            "safety_and_redaction",
        ],
    },
    {
        "id": "file_task_artifact_review",
        "workflow": "produce and review an Office/PDF artifact through public artifact projections",
        "required_public_inputs": [
            "file task request",
            "allowlisted artifact type",
            "public artifact metadata",
        ],
        "expected_public_outputs": [
            "artifact card projection",
            "preview or download reference",
            "review finding summary",
        ],
        "evaluator_signals": [
            "task_success",
            "instruction_following",
            "artifact_quality",
            "safety_and_redaction",
        ],
    },
]

_SCORE_DIMENSIONS: list[dict[str, Any]] = [
    {
        "id": "task_success",
        "description": "The response completes the requested workflow outcome.",
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
    },
    {
        "id": "instruction_following",
        "description": "The response follows explicit user constraints and output format requirements.",
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
    },
    {
        "id": "context_grounding",
        "description": "The response is grounded in the supplied public context and cites safe provenance.",
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
    },
    {
        "id": "artifact_quality",
        "description": "Generated or reviewed artifacts are usable, coherent, and match the requested file task.",
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
    },
    {
        "id": "safety_and_redaction",
        "description": "The projection avoids internal identifiers, executor-only data, and secret-like values.",
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
    },
]

_REQUIRED_EVIDENCE_FIELDS = [
    "commit_sha",
    "dataset_version",
    "scenario_id",
    "eval_run_id",
    "evaluator_version",
    "sample_count",
    "passed_count",
    "failed_count",
    "score_summary",
    "dimension_scores",
    "context_provenance_public",
    "artifact_refs_public",
    "redaction_scan_status",
    "review_status",
    "reviewed_at",
]


def _validate_catalog() -> None:
    scenario_ids = [str(scenario.get("id") or "") for scenario in _SCENARIOS]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise RuntimeError("quality_golden_set_scenario_duplicate")
    dimension_ids = [str(dimension.get("id") or "") for dimension in _SCORE_DIMENSIONS]
    if len(dimension_ids) != len(set(dimension_ids)):
        raise RuntimeError("quality_score_dimension_duplicate")
    allowed_dimensions = set(dimension_ids)
    for scenario in _SCENARIOS:
        signals = scenario.get("evaluator_signals")
        if not isinstance(signals, list) or not set(str(signal) for signal in signals).issubset(allowed_dimensions):
            raise RuntimeError("quality_golden_set_signal_drift")


def build_quality_golden_set_readiness() -> dict[str, Any]:
    """Build a source-level G9 quality/golden-set contract without enabling runtime evals."""
    _validate_catalog()
    scenario_catalog = deepcopy(_SCENARIOS)
    score_dimensions = deepcopy(_SCORE_DIMENSIONS)
    dimension_ids = [str(dimension["id"]) for dimension in score_dimensions]
    open_gaps = [
        "golden_set_eval_runtime_and_211_acceptance",
        "office_workflow_acceptance_dataset",
        "quality_threshold_calibration",
        "quality_dashboard_acceptance",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "active_eval_policy": "contract_only_not_enabled",
        "scenario_catalog": scenario_catalog,
        "score_schema": {
            "schema_version": QUALITY_SCORE_SCHEMA_VERSION,
            "scale": {"min": 0.0, "max": 1.0, "higher_is_better": True},
            "dimensions": score_dimensions,
            "blocking_rule": "privacy_or_context_provenance_violation_blocks_release",
        },
        "evidence_contract": {
            "schema_version": EVIDENCE_CONTRACT_SCHEMA_VERSION,
            "write_path": "quality_evaluation.golden_set_runs.<eval_run_id>",
            "required_fields": list(_REQUIRED_EVIDENCE_FIELDS),
            "accepted_redaction_scan_statuses": ["passed"],
            "accepted_review_statuses": ["approved_for_operator_review"],
            "forbidden_projection_policy": [
                "executor-only data",
                "object-storage internal identifiers",
                "sandbox working directory values",
                "gateway credentials",
                "real environment values",
            ],
            "does_not_enable_eval_runtime": True,
            "does_not_close_g9": True,
        },
        "summary": {
            "scenario_count": len(scenario_catalog),
            "required_score_dimensions": dimension_ids,
        },
        "open_gaps": open_gaps,
        "evidence_policy": (
            "This contract narrows the G9 golden-set definition gap only; runtime eval execution, "
            "dataset approval, threshold calibration, dashboard acceptance, review, and 211 smoke "
            "remain required before gate closure."
        ),
    }

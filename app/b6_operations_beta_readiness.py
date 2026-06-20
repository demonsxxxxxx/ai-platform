from __future__ import annotations

from typing import Any

from app.observability_readiness import build_observability_readiness
from app.quality_golden_set_readiness import build_quality_golden_set_readiness


SCHEMA_VERSION = "ai-platform.b6-operations-beta-readiness.v1"
BACKEND_STAGE = "B6 operations beta and workflow readiness"

_REQUIRED_WORKFLOW_FIELDS = [
    "workflow_owner",
    "business_owner",
    "support_owner",
    "tenant_workspace_scope",
    "slo_latency_threshold",
    "expected_sdk_subagent_fanout",
    "cost_budget",
    "quality_threshold",
    "alert_route",
    "rollback_drill",
    "linked_b1_b5_evidence",
]


class _ReadinessSettings:
    sandbox_container_provider = "fake"
    llm_gateway_provider = "openai_compatible"
    model_gateway_request_concurrency_limit = 0
    multi_agent_dispatch_worker_enabled = False


def _domain(
    *,
    gate_slice: str,
    implemented_controls: list[str],
    open_gaps: list[str],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domain: dict[str, Any] = {
        "gate_slice": gate_slice,
        "status": "local_contract_recorded",
        "implemented_controls": implemented_controls,
        "open_gaps": open_gaps,
    }
    if evidence is not None:
        domain["evidence"] = evidence
    return domain


def _admin_runtime_domain(observability: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        gate_slice="B6-G1 Admin Runtime coverage",
        implemented_controls=[
            "admin_runtime_observability_projection_contract",
            "queue_worker_token_cost_latency_error_summary",
            "release_evidence_operator_projection_contract",
        ],
        open_gaps=[
            "named_workflow_admin_runtime_211_acceptance",
            "operator_dashboard_without_chat_transcript_acceptance",
        ],
        evidence={
            "schema_version": observability["schema_version"],
            "status": observability["status"],
            "admin_runtime_projection": observability["admin_runtime_projection"],
        },
    )


def _trace_export_domain(observability: dict[str, Any]) -> dict[str, Any]:
    alerts = observability["domains"]["alerts_and_exports"]
    return _domain(
        gate_slice="B6-G2 Trace/export",
        implemented_controls=[
            "trace_audit_export_contract",
            "release_evidence_export_location_contract",
            "release_evidence_export_acceptance_preflight",
        ],
        open_gaps=[
            "named_workflow_trace_export_runtime_acceptance",
            "reviewer_friendly_reproducible_export_package",
            "trace_export_redaction_review_for_workflow_package",
        ],
        evidence={
            "implemented": alerts["implemented"],
            "open_gaps": alerts["gaps"],
        },
    )


def _alert_support_domain(observability: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        gate_slice="B6-G3 Alerting and support",
        implemented_controls=[
            "alert_slo_rule_template_evidence",
            "alert_delivery_channel_policy_contract",
            "error_taxonomy_dashboard_contract",
        ],
        open_gaps=[
            "workflow_support_owner_assignment",
            "workflow_alert_route_runtime_acceptance",
            "support_escalation_runbook_acceptance",
        ],
        evidence={
            "runtime_metrics_status": observability["domains"]["runtime_metrics"]["status"],
            "error_taxonomy_status": observability["domains"]["error_taxonomy"]["status"],
        },
    )


def _quality_gate_domain(quality: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        gate_slice="B6-G4 Quality gate",
        implemented_controls=[
            "quality_golden_set_readiness_contract",
            "quality_score_schema_contract",
            "golden_set_eval_evidence_contract",
        ],
        open_gaps=[
            "workflow_quality_dataset_approval",
            "quality_threshold_calibration",
            "golden_set_eval_runtime_and_211_acceptance",
        ],
        evidence={
            "schema_version": quality["schema_version"],
            "status": quality["status"],
            "scenario_count": quality["summary"]["scenario_count"],
            "score_schema": quality["score_schema"]["schema_version"],
        },
    )


def _owner_signoff_domain() -> dict[str, Any]:
    return _domain(
        gate_slice="B6-G5 Owner signoff and rollback drill",
        implemented_controls=[
            "workflow_package_required_field_contract",
            "non_beta_claim_boundary",
            "linked_b1_b5_evidence_required",
        ],
        open_gaps=[
            "workflow_owner_signoff_and_support_handoff",
            "rollback_drill_runtime_evidence",
            "linked_b1_b5_evidence_package_review",
        ],
    )


def build_b6_operations_beta_readiness() -> dict[str, Any]:
    """Build a B6 local readiness snapshot without claiming Operations Beta."""
    observability = build_observability_readiness(_ReadinessSettings())
    quality = build_quality_golden_set_readiness()
    domains = {
        "admin_runtime_coverage": _admin_runtime_domain(observability),
        "trace_export": _trace_export_domain(observability),
        "alert_support": _alert_support_domain(observability),
        "quality_gate": _quality_gate_domain(quality),
        "owner_signoff_rollback": _owner_signoff_domain(),
    }
    open_gaps = [
        "named_workflow_package_missing",
        "workflow_owner_signoff_and_support_handoff",
        "linked_b1_b5_evidence_package_review",
        "operations_beta_211_workflow_acceptance",
        "rollback_drill_runtime_evidence",
        "product_beta_issue_review_and_closure_evidence",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_stage": BACKEND_STAGE,
        "status": "partial_blocked",
        "status_label": "local partial",
        "issue": "#152",
        "required_workflow_package": {
            "minimum_workflow_count": 1,
            "maximum_initial_workflow_count": 2,
            "required_fields": list(_REQUIRED_WORKFLOW_FIELDS),
            "evidence_policy": (
                "A B6 workflow package must bind owner metadata, SLO/cost/quality, "
                "support, rollback, and linked B1-B5 evidence before any product beta claim."
            ),
        },
        "domains": domains,
        "open_gaps": open_gaps,
        "claim_boundary": {
            "does_not_create_product_beta": True,
            "does_not_create_department_rollout": True,
            "does_not_create_211_verified": True,
            "does_not_close_b6_g9_g10": True,
            "does_not_claim_owner_signoff": True,
            "does_not_claim_support_handoff": True,
        },
        "non_expansion_invariants": {
            "product_beta_allowed": False,
            "department_rollout_allowed": False,
            "owner_signoff_claimed": False,
            "support_handoff_claimed": False,
            "runtime_workflow_acceptance_claimed": False,
            "b1_b5_evidence_package_reviewed": False,
        },
        "evidence_policy": (
            "B6 local readiness records the Operations Beta package contract only. "
            "B6 cannot be 211 verified, gate closable, or product beta until a named "
            "workflow package proves Admin Runtime, trace/export, alert/support, "
            "quality, rollback, linked B1-B5 evidence, review, owner signoff, and "
            "residual caveats on the selected runtime subject."
        ),
    }


def render_b6_operations_beta_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B6 readiness as operator-readable Markdown."""
    if not readiness["claim_boundary"].get("does_not_create_product_beta"):
        raise RuntimeError("b6_product_beta_boundary_regression")
    package_fields = "\n".join(
        f"- {field}" for field in readiness["required_workflow_package"]["required_fields"]
    )
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    sections = []
    for domain in readiness["domains"].values():
        gate, name = str(domain["gate_slice"]).split(" ", 1)
        title = f"{gate} {name.title()}"
        implemented = "\n".join(f"- {item}" for item in domain["implemented_controls"])
        gaps = "\n".join(f"- {item}" for item in domain["open_gaps"])
        sections.append(
            f"## {title}\n\n"
            f"Status: `{domain['status']}`\n\n"
            "Implemented controls:\n\n"
            f"{implemented}\n\n"
            "Open gaps:\n\n"
            f"{gaps}\n"
        )
    boundary_lines = [
        "- does not create product beta",
        "- does not create department rollout",
        "- does not create `211 verified`",
        "- does not close B6/G9/G10",
        "- does not claim owner signoff",
        "- does not claim support handoff",
    ]
    return (
        "# ai-platform B6 Operations Beta Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Stage: `{readiness['backend_stage']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Status label: `{readiness['status_label']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Required Workflow Package\n\n"
        f"{package_fields}\n\n"
        + "\n\n".join(sections)
        + "\n\n"
        "## Claim Boundary\n\n"
        + "\n".join(boundary_lines)
        + "\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

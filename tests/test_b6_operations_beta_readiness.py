import json
import subprocess
import sys

import pytest

from app.b6_operations_beta_readiness import (
    build_b6_operations_beta_readiness,
    render_b6_operations_beta_readiness_markdown,
)


def test_b6_operations_beta_readiness_exposes_workflow_package_boundaries():
    readiness = build_b6_operations_beta_readiness()

    assert readiness["schema_version"] == "ai-platform.b6-operations-beta-readiness.v1"
    assert readiness["backend_stage"] == "B6 operations beta and workflow readiness"
    assert readiness["status"] == "partial_blocked"
    assert readiness["status_label"] == "local partial"
    assert readiness["issue"] == "#152"

    required_package = readiness["required_workflow_package"]
    assert required_package["required_fields"] == [
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
    assert required_package["minimum_workflow_count"] == 1
    assert required_package["maximum_initial_workflow_count"] == 2

    assert set(readiness["domains"]) == {
        "admin_runtime_coverage",
        "trace_export",
        "alert_support",
        "quality_gate",
        "owner_signoff_rollback",
    }
    assert readiness["domains"]["admin_runtime_coverage"]["gate_slice"] == "B6-G1 Admin Runtime coverage"
    assert readiness["domains"]["trace_export"]["gate_slice"] == "B6-G2 Trace/export"
    assert readiness["domains"]["alert_support"]["gate_slice"] == "B6-G3 Alerting and support"
    assert readiness["domains"]["quality_gate"]["gate_slice"] == "B6-G4 Quality gate"
    assert readiness["domains"]["owner_signoff_rollback"]["gate_slice"] == "B6-G5 Owner signoff and rollback drill"

    assert "workflow_owner_signoff_and_support_handoff" in readiness["open_gaps"]
    assert "linked_b1_b5_evidence_package_review" in readiness["open_gaps"]
    assert "operations_beta_211_workflow_acceptance" in readiness["open_gaps"]

    assert readiness["claim_boundary"]["does_not_create_product_beta"] is True
    assert readiness["claim_boundary"]["does_not_create_211_verified"] is True
    assert readiness["claim_boundary"]["does_not_close_b6_g9_g10"] is True
    assert readiness["claim_boundary"]["does_not_claim_owner_signoff"] is True


def test_b6_operations_beta_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_b6_operations_beta_readiness_markdown(
        build_b6_operations_beta_readiness()
    )

    assert "# ai-platform B6 Operations Beta Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    assert "workflow_owner_signoff_and_support_handoff" in markdown
    assert "## Required Workflow Package" in markdown
    assert "workflow_owner" in markdown
    assert "## B6-G5 Owner Signoff And Rollback Drill" in markdown
    assert "does not create product beta" in markdown
    assert "does not create department rollout" in markdown
    assert "does not close B6/G9/G10" in markdown
    assert "does not claim support handoff" in markdown


def test_b6_operations_beta_readiness_markdown_fails_closed_on_beta_boundary_regression():
    readiness = build_b6_operations_beta_readiness()
    readiness["claim_boundary"]["does_not_create_product_beta"] = False

    with pytest.raises(RuntimeError, match="b6_product_beta_boundary_regression"):
        render_b6_operations_beta_readiness_markdown(readiness)


def test_b6_operations_beta_readiness_cli_outputs_json_without_private_markers():
    result = subprocess.run(
        [sys.executable, "tools/b6_operations_beta_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.b6-operations-beta-readiness.v1"
    assert payload["status_label"] == "local partial"
    assert "C:\\Users" not in result.stdout
    assert "/home/xinlin.jiang" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "executor_private_payload" not in result.stdout
    assert "api_key" not in result.stdout

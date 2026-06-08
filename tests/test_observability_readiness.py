import json
import os
import subprocess
import sys

import pytest

import app.quality_golden_set_readiness as quality_golden_set_readiness
from app.error_taxonomy_dashboard_readiness import build_error_taxonomy_dashboard_readiness
from app.observability_readiness import (
    build_observability_readiness,
    render_observability_readiness_markdown,
)
from app.quality_golden_set_readiness import build_quality_golden_set_readiness
from app.release_evidence_readiness import build_release_evidence_readiness
from app.trace_audit_export_readiness import build_trace_audit_export_readiness


class SecretBearingSettings:
    sandbox_container_provider = "docker://token@internal/path"
    sandbox_callback_token = "callback-secret"
    sandbox_workspace_root = "/tmp/tenant-secret/workspaces"
    anthropic_auth_token = "anthropic-secret"
    llm_gateway_provider = "openai_compatible"
    model_gateway_request_concurrency_limit = 0
    multi_agent_dispatch_worker_enabled = False


def test_observability_readiness_records_g9_domains_and_open_gaps_without_secrets():
    readiness = build_observability_readiness(SecretBearingSettings())

    assert readiness["schema_version"] == "ai-platform.observability-readiness.v1"
    assert readiness["gate"] == "G9 Observability / Quality / Ops"
    assert readiness["status"] == "partial_blocked"
    assert readiness["admin_runtime_projection"] == "/api/ai/admin/runtime/overview"
    assert readiness["ordinary_user_policy"] == "admin_only_operational_projection"

    domains = readiness["domains"]
    assert set(domains) == {
        "runtime_metrics",
        "error_taxonomy",
        "quality_evaluation",
        "alerts_and_exports",
    }
    assert "admin_runtime_observability_summary" in domains["runtime_metrics"]["implemented"]
    assert "token_cost_latency_error_counts" in domains["runtime_metrics"]["implemented"]
    assert "latency_percentiles_p50_p95_p99_admin_projection" in domains["runtime_metrics"]["implemented"]
    assert "model_gateway_backpressure_policy_contract" in domains["runtime_metrics"]["implemented"]
    assert domains["runtime_metrics"]["evidence"]["model_gateway_backpressure_policy"] == {
        "schema_version": "ai-platform.model-gateway-backpressure-policy.v1",
        "status": "contract_only_not_enforced",
        "config_signal": "MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT",
        "default_limit_policy": "0_disables_platform_request_limit",
        "required_admin_runtime_fields": [
            "capacity.limits.model_gateway",
            "backpressure.model_gateway",
            "observability.error_categories",
        ],
        "required_load_test_gate": "model_gateway_timeout_and_backpressure",
        "enforcement_status": "not_implemented",
        "capacity_evidence": "unproven_without_load_test",
        "production_default_policy": "do_not_raise_without_recorded_load_test_evidence",
        "does_not_raise_defaults": True,
        "does_not_close_g9": True,
    }
    assert "latency_percentiles_p50_p95_p99" not in domains["runtime_metrics"]["gaps"]
    assert "latency_percentile_runtime_211_acceptance" not in domains["runtime_metrics"]["gaps"]
    assert "latency_percentile_per_surface_split_and_dashboard_acceptance" in domains["runtime_metrics"]["gaps"]
    assert "formal_error_taxonomy_contract" in domains["error_taxonomy"]["implemented"]
    assert "error_category_mapping_for_executor_tool_sandbox_model_gateway" in domains["error_taxonomy"]["implemented"]
    assert "error_taxonomy_dashboard_contract" in domains["error_taxonomy"]["implemented"]
    assert "error_taxonomy_dashboard_acceptance" not in domains["error_taxonomy"]["gaps"]
    assert "error_taxonomy_dashboard_runtime_acceptance" in domains["error_taxonomy"]["gaps"]
    assert "error_taxonomy_dashboard_visual_acceptance" in domains["error_taxonomy"]["gaps"]
    assert "error_taxonomy_dashboard_211_acceptance" in domains["error_taxonomy"]["gaps"]
    assert "quality_golden_set_readiness_contract" in domains["quality_evaluation"]["implemented"]
    assert "golden_set_eval_runtime_and_211_acceptance" in domains["quality_evaluation"]["gaps"]
    assert "alert_rules_runtime_dashboard_and_211_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "alert_delivery_channel_policy_contract" in domains["alerts_and_exports"]["implemented"]
    assert "alert_delivery_channel_policy" not in domains["alerts_and_exports"]["gaps"]
    assert "alert_delivery_channel_runtime_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "slo_threshold_runtime_calibration" in domains["alerts_and_exports"]["gaps"]
    assert "alert_rules_and_slo_thresholds" not in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_contract" in domains["alerts_and_exports"]["implemented"]
    assert "trace_audit_export_contract" not in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_runtime_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_dashboard_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_211_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "release_evidence_export_location" not in domains["alerts_and_exports"]["gaps"]
    assert "release_evidence_export_location_contract" in domains["alerts_and_exports"]["implemented"]
    assert "release_evidence_runtime_export_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "model_gateway_request_concurrency_limit" in readiness["open_gaps"]
    assert "latency_percentile_runtime_211_acceptance" not in readiness["open_gaps"]
    assert "latency_percentile_per_surface_split_and_dashboard_acceptance" in readiness["open_gaps"]
    assert readiness["config_signals"]["model_gateway_request_concurrency_limit"] is None
    assert "alert_delivery_channel_policy" not in readiness["open_gaps"]
    assert "alert_delivery_channel_runtime_acceptance" in readiness["open_gaps"]
    assert "slo_threshold_runtime_calibration" in readiness["open_gaps"]
    assert "release_evidence_export_location" not in readiness["open_gaps"]
    assert "release_evidence_runtime_export_acceptance" in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "anthropic-secret" not in serialized
    assert "docker://token" not in serialized
    assert "sandbox_workspace_root" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_error_taxonomy_dashboard_readiness_contract_defines_safe_admin_dashboard_without_closing_g9():
    readiness = build_error_taxonomy_dashboard_readiness()

    assert readiness["schema_version"] == "ai-platform.error-taxonomy-dashboard-readiness.v1"
    assert readiness["gate"] == "G9 Error Taxonomy Dashboard"
    assert readiness["status"] == "partial_blocked"
    assert readiness["active_dashboard_policy"] == "contract_only_not_runtime_dashboard_acceptance"
    assert readiness["dashboard_contract"] == {
        "schema_version": "ai-platform.error-taxonomy-dashboard-contract.v1",
        "required_admin_runtime_fields": [
            "observability.error_categories",
            "observability.error_types",
            "observability.recent_failures",
            "observability_readiness.error_taxonomy",
        ],
        "required_category_ids": [
            "executor",
            "tool",
            "tool_permission",
            "sandbox",
            "model_gateway",
            "queue",
            "database",
            "memory_context",
            "artifact",
            "auth_policy",
            "unknown",
        ],
        "allowed_display_fields": [
            "category",
            "count",
            "definition",
            "trend_window",
            "recent_failure_refs_public",
            "last_seen_at",
        ],
        "unknown_category_policy": "unknown_category_visible_but_raw_payload_hidden",
        "same_tenant_admin_only": True,
        "forbidden_payload_classes": [
            "executor private payload",
            "raw storage key",
            "sandbox workdir",
            "secret material",
            "API key",
            "bearer token",
            "database URL",
            "Redis URL",
        ],
        "does_not_close_g9": True,
    }
    assert readiness["open_gaps"] == [
        "error_taxonomy_dashboard_runtime_acceptance",
        "error_taxonomy_dashboard_visual_acceptance",
        "error_taxonomy_dashboard_211_acceptance",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "database_url" not in serialized
    assert "sk-secret" not in serialized


def test_observability_readiness_reports_configured_model_gateway_limit_without_closing_g9():
    class ConfiguredGatewaySettings(SecretBearingSettings):
        model_gateway_request_concurrency_limit = 8

    readiness = build_observability_readiness(ConfiguredGatewaySettings())

    assert readiness["status"] == "partial_blocked"
    assert readiness["config_signals"]["model_gateway_request_concurrency_limit"] == 8
    assert "model_gateway_request_concurrency_limit" in readiness["open_gaps"]
    assert "model_gateway_request_concurrency_limit_enforcement" in readiness["open_gaps"]
    assert "model_gateway_capacity_load_test_evidence" in readiness["open_gaps"]
    assert "recorded_capacity_load_test_evidence" in readiness["open_gaps"]
    assert readiness["domains"]["runtime_metrics"]["status"] == "partial_blocked"


def test_observability_readiness_includes_alert_slo_rule_template_evidence_without_closing_g9():
    readiness = build_observability_readiness(SecretBearingSettings())

    alerts = readiness["domains"]["alerts_and_exports"]
    assert "alert_slo_rule_template_evidence" in alerts["implemented"]

    evidence = alerts["evidence"]["alert_slo_rules"]
    assert evidence["schema_version"] == "ai-platform.alert-slo-readiness.v1"
    assert evidence["status"] == "partial_blocked"
    assert evidence["active_alerting_policy"] == "template_only_not_enabled"
    assert evidence["delivery_channel_policy"] == {
        "schema_version": "ai-platform.alert-delivery-channel-policy.v1",
        "status": "contract_only_not_enabled",
        "allowed_channels": [
            "admin_runtime_dashboard",
            "release_evidence_entry",
            "operator_manual_review",
        ],
        "ordinary_user_delivery_policy": "disabled_until_g9_acceptance",
        "payload_policy": "category_threshold_and_public_projection_refs_only",
        "forbidden_payload_classes": [
            "executor private payload",
            "raw storage key",
            "sandbox workdir",
            "secret material",
            "API key",
            "bearer token",
            "database URL",
            "Redis URL",
        ],
        "requires_runtime_dashboard_acceptance": True,
        "requires_211_smoke": True,
        "does_not_enable_alert_delivery": True,
        "does_not_close_g9": True,
    }
    assert evidence["summary"] == {
        "rule_count": 7,
        "categories": [
            "queue",
            "database",
            "worker",
            "model_gateway",
            "sandbox",
            "error_taxonomy",
            "capacity_gate",
        ],
    }
    assert [rule["id"] for rule in evidence["rules"]] == [
        "queue_depth_no_lease_progress",
        "database_pool_waiting_pressure",
        "worker_active_run_saturation",
        "model_gateway_timeout_spike",
        "sandbox_orphan_cleanup_regression",
        "error_taxonomy_spike",
        "capacity_load_evidence_missing",
    ]
    assert evidence["open_gaps"] == [
        "alert_rules_runtime_dashboard_and_211_acceptance",
        "alert_delivery_channel_runtime_acceptance",
        "slo_threshold_runtime_calibration",
    ]

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    assert "private_payload" not in serialized
    assert "storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "token=secret" not in serialized


def test_release_evidence_readiness_contract_defines_safe_export_location_without_closing_g9():
    readiness = build_release_evidence_readiness()

    assert readiness["schema_version"] == "ai-platform.release-evidence-readiness.v1"
    assert readiness["gate"] == "G9 Release Evidence Export"
    assert readiness["status"] == "partial_blocked"
    assert readiness["active_export_policy"] == "location_contract_only_not_runtime_export"
    assert readiness["export_location"] == {
        "type": "repository_path",
        "path": "docs/release-evidence/",
        "index": "docs/release-evidence/README.md",
        "write_policy": "append_reviewed_redacted_evidence_entries_only",
    }
    assert readiness["evidence_contract"]["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert readiness["evidence_contract"]["write_path"] == "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json"
    assert readiness["evidence_contract"]["required_fields"] == [
        "evidence_id",
        "commit_sha",
        "gate",
        "issue_refs",
        "artifact_kind",
        "captured_at",
        "source_ref",
        "evidence_ref",
        "redaction_scan_status",
        "review_status",
    ]
    assert readiness["evidence_contract"]["forbidden_marker_classes"] == [
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "API key",
        "bearer token",
        "database URL",
        "Redis URL",
    ]
    assert readiness["evidence_contract"]["does_not_close_g9"] is True
    assert readiness["retention_policy"]["schema_version"] == "ai-platform.release-evidence-retention-policy.v1"
    assert readiness["retention_policy"]["status"] == "contract_only_not_runtime_enforced"
    assert readiness["retention_policy"]["default_retention_days"] == 180
    assert readiness["retention_policy"]["minimum_retention_days"] == 30
    assert readiness["retention_policy"]["requires_review_before_delete"] is True
    assert readiness["retention_policy"]["delete_only_reviewed_redacted_entries"] is True
    assert readiness["retention_policy"]["forbidden_delete_targets"] == [
        "raw runtime payload",
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "unreviewed evidence draft",
    ]
    assert readiness["open_gaps"] == [
        "release_evidence_runtime_export_acceptance",
        "release_evidence_retention_runtime_acceptance",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "database_url" not in serialized
    assert "sk-secret" not in serialized


def test_trace_audit_export_readiness_contract_defines_safe_public_export_without_closing_g9():
    readiness = build_trace_audit_export_readiness()

    assert readiness["schema_version"] == "ai-platform.trace-audit-export-readiness.v1"
    assert readiness["gate"] == "G9 Trace / Audit Export"
    assert readiness["status"] == "partial_blocked"
    assert readiness["active_export_policy"] == "contract_only_not_runtime_export"
    assert readiness["export_contract"] == {
        "schema_version": "ai-platform.trace-audit-export-contract.v1",
        "write_path": "audit.trace_exports.<export_id>",
        "required_fields": [
            "export_id",
            "commit_sha",
            "tenant_id",
            "requested_by",
            "requested_at",
            "time_range",
            "filters",
            "artifact_refs_public",
            "redaction_scan_status",
            "review_status",
        ],
        "allowed_event_sources": [
            "run_event_public_projection",
            "audit_event_public_projection",
            "admin_runtime_observability_summary",
            "release_evidence_entry",
        ],
        "accepted_redaction_scan_statuses": ["passed"],
        "accepted_review_statuses": ["reviewed", "accepted"],
        "forbidden_marker_classes": [
            "executor private payload",
            "raw storage key",
            "sandbox workdir",
            "secret material",
            "API key",
            "bearer token",
            "database URL",
            "Redis URL",
        ],
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }
    assert readiness["open_gaps"] == [
        "trace_audit_export_runtime_acceptance",
        "trace_audit_export_dashboard_acceptance",
        "trace_audit_export_211_acceptance",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "database_url" not in serialized
    assert "sk-secret" not in serialized


def test_observability_readiness_includes_release_evidence_contract_without_closing_g9():
    readiness = build_observability_readiness(SecretBearingSettings())

    alerts = readiness["domains"]["alerts_and_exports"]
    assert "release_evidence_export_location_contract" in alerts["implemented"]
    assert "release_evidence_retention_policy_contract" in alerts["implemented"]
    assert "release_evidence_export_location" not in alerts["gaps"]
    assert "release_evidence_runtime_export_acceptance" in alerts["gaps"]
    assert "release_evidence_retention_policy" not in alerts["gaps"]
    assert "release_evidence_retention_runtime_acceptance" in alerts["gaps"]

    evidence = alerts["evidence"]["release_evidence"]
    assert evidence["schema_version"] == "ai-platform.release-evidence-readiness.v1"
    assert evidence["status"] == "partial_blocked"
    assert evidence["export_location"]["path"] == "docs/release-evidence/"
    assert evidence["evidence_contract"]["does_not_close_g9"] is True
    assert evidence["retention_policy"]["schema_version"] == "ai-platform.release-evidence-retention-policy.v1"
    assert evidence["retention_policy"]["forbidden_delete_targets"] == [
        "raw runtime payload",
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "unreviewed evidence draft",
    ]
    assert "release_evidence_runtime_export_acceptance" in readiness["open_gaps"]
    assert "release_evidence_retention_runtime_acceptance" in readiness["open_gaps"]


def test_observability_readiness_includes_trace_audit_export_contract_without_closing_g9():
    readiness = build_observability_readiness(SecretBearingSettings())

    alerts = readiness["domains"]["alerts_and_exports"]
    assert "trace_audit_export_contract" in alerts["implemented"]
    assert "trace_audit_export_contract" not in alerts["gaps"]
    assert "trace_audit_export_runtime_acceptance" in alerts["gaps"]
    assert "trace_audit_export_dashboard_acceptance" in alerts["gaps"]
    assert "trace_audit_export_211_acceptance" in alerts["gaps"]

    evidence = alerts["evidence"]["trace_audit_export"]
    assert evidence["schema_version"] == "ai-platform.trace-audit-export-readiness.v1"
    assert evidence["status"] == "partial_blocked"
    assert evidence["export_contract"]["schema_version"] == "ai-platform.trace-audit-export-contract.v1"
    assert evidence["export_contract"]["write_path"] == "audit.trace_exports.<export_id>"
    assert evidence["export_contract"]["does_not_close_g9"] is True
    assert "trace_audit_export_runtime_acceptance" in readiness["open_gaps"]
    assert "trace_audit_export_dashboard_acceptance" in readiness["open_gaps"]
    assert "trace_audit_export_211_acceptance" in readiness["open_gaps"]


def test_quality_golden_set_readiness_contract_is_source_level_and_fail_closed():
    readiness = build_quality_golden_set_readiness()

    assert readiness["schema_version"] == "ai-platform.quality-golden-set-readiness.v1"
    assert readiness["gate"] == "G9 Quality Golden-Set Evaluation"
    assert readiness["status"] == "partial_blocked"
    assert readiness["active_eval_policy"] == "contract_only_not_enabled"
    assert readiness["summary"] == {
        "scenario_count": 5,
        "required_score_dimensions": [
            "task_success",
            "instruction_following",
            "context_grounding",
            "artifact_quality",
            "safety_and_redaction",
        ],
    }
    assert [scenario["id"] for scenario in readiness["scenario_catalog"]] == [
        "office_document_revision",
        "meeting_summary_followup",
        "translation_terminology_consistency",
        "sop_rag_answer_grounding",
        "file_task_artifact_review",
    ]

    score_schema = readiness["score_schema"]
    assert score_schema["schema_version"] == "ai-platform.quality-score.v1"
    assert score_schema["scale"] == {"min": 0.0, "max": 1.0, "higher_is_better": True}
    assert [dimension["id"] for dimension in score_schema["dimensions"]] == readiness["summary"][
        "required_score_dimensions"
    ]
    assert score_schema["blocking_rule"] == "privacy_or_context_provenance_violation_blocks_release"

    evidence_contract = readiness["evidence_contract"]
    assert evidence_contract["schema_version"] == "ai-platform.golden-set-eval-evidence-contract.v1"
    assert evidence_contract["write_path"] == "quality_evaluation.golden_set_runs.<eval_run_id>"
    assert evidence_contract["does_not_enable_eval_runtime"] is True
    assert evidence_contract["does_not_close_g9"] is True
    assert evidence_contract["accepted_redaction_scan_statuses"] == ["passed"]
    assert evidence_contract["required_fields"] == [
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
    assert "golden_set_eval_runtime_and_211_acceptance" in readiness["open_gaps"]
    assert "office_workflow_acceptance_dataset" in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    for marker in (
        "private_payload",
        "executor_private_payload",
        "raw_storage_key",
        "sandbox_workdir",
        "api_key",
        "bearer",
        "callback-secret",
    ):
        assert marker not in serialized


def test_quality_golden_set_readiness_returns_isolated_nested_lists():
    first = build_quality_golden_set_readiness()
    first["scenario_catalog"][0]["required_public_inputs"].append("mutated-by-caller")
    first["score_schema"]["dimensions"][0]["id"] = "mutated_dimension"
    first["evidence_contract"]["required_fields"].append("mutated_field")

    second = build_quality_golden_set_readiness()

    assert "mutated-by-caller" not in second["scenario_catalog"][0]["required_public_inputs"]
    assert second["score_schema"]["dimensions"][0]["id"] == "task_success"
    assert "mutated_field" not in second["evidence_contract"]["required_fields"]


def test_quality_golden_set_readiness_fails_closed_on_duplicate_scenario_ids(monkeypatch):
    scenarios = [dict(scenario) for scenario in quality_golden_set_readiness._SCENARIOS]
    scenarios[1]["id"] = scenarios[0]["id"]
    monkeypatch.setattr(quality_golden_set_readiness, "_SCENARIOS", scenarios)

    with pytest.raises(RuntimeError, match="quality_golden_set_scenario_duplicate"):
        build_quality_golden_set_readiness()


def test_quality_golden_set_readiness_fails_closed_on_unknown_evaluator_signal(monkeypatch):
    scenarios = [dict(scenario) for scenario in quality_golden_set_readiness._SCENARIOS]
    scenarios[0]["evaluator_signals"] = [*scenarios[0]["evaluator_signals"], "unknown_dimension"]
    monkeypatch.setattr(quality_golden_set_readiness, "_SCENARIOS", scenarios)

    with pytest.raises(RuntimeError, match="quality_golden_set_signal_drift"):
        build_quality_golden_set_readiness()


def test_observability_readiness_includes_quality_golden_set_contract_without_closing_g9():
    readiness = build_observability_readiness(SecretBearingSettings())

    quality = readiness["domains"]["quality_evaluation"]
    assert "quality_golden_set_readiness_contract" in quality["implemented"]
    assert "quality_score_schema_contract" in quality["implemented"]
    assert "golden_set_eval_run_contract" not in quality["gaps"]
    assert "quality_score_schema" not in quality["gaps"]
    assert "golden_set_eval_runtime_and_211_acceptance" in quality["gaps"]
    assert "quality_threshold_calibration" in quality["gaps"]

    evidence = quality["evidence"]["quality_golden_set"]
    assert evidence["schema_version"] == "ai-platform.quality-golden-set-readiness.v1"
    assert evidence["evidence_contract"]["schema_version"] == "ai-platform.golden-set-eval-evidence-contract.v1"
    assert evidence["status"] == "partial_blocked"
    assert evidence["active_eval_policy"] == "contract_only_not_enabled"
    assert evidence["evidence_contract"]["does_not_close_g9"] is True
    assert "golden_set_eval_runtime_and_211_acceptance" in readiness["open_gaps"]


def test_render_observability_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_observability_readiness_markdown(
        build_observability_readiness(SecretBearingSettings())
    )

    assert "# ai-platform G9 Observability Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "formal_error_taxonomy_contract" in markdown
    assert "error_taxonomy_dashboard_contract" in markdown
    assert "ai-platform.error-taxonomy-dashboard-readiness.v1" in markdown
    assert "ai-platform.error-taxonomy-dashboard-contract.v1" in markdown
    assert "error_taxonomy_dashboard_runtime_acceptance" in markdown
    assert "error_taxonomy_dashboard_visual_acceptance" in markdown
    assert "error_taxonomy_dashboard_211_acceptance" in markdown
    assert "error_taxonomy_dashboard_acceptance" not in markdown
    assert "latency_percentiles_p50_p95_p99_admin_projection" in markdown
    assert "model_gateway_backpressure_policy_contract" in markdown
    assert "ai-platform.model-gateway-backpressure-policy.v1" in markdown
    assert "latency_percentile_runtime_211_acceptance" not in markdown
    assert "latency_percentile_per_surface_split_and_dashboard_acceptance" in markdown
    assert "quality_golden_set_readiness_contract" in markdown
    assert "ai-platform.quality-golden-set-readiness.v1" in markdown
    assert "ai-platform.golden-set-eval-evidence-contract.v1" in markdown
    assert "golden_set_eval_runtime_and_211_acceptance" in markdown
    assert "alert_slo_rule_template_evidence" in markdown
    assert "alert_rules_runtime_dashboard_and_211_acceptance" in markdown
    assert "alert_delivery_channel_policy_contract" in markdown
    assert "ai-platform.alert-delivery-channel-policy.v1" in markdown
    assert "alert_delivery_channel_runtime_acceptance" in markdown
    assert "slo_threshold_runtime_calibration" in markdown
    assert "trace_audit_export_contract" in markdown
    assert "ai-platform.trace-audit-export-readiness.v1" in markdown
    assert "ai-platform.trace-audit-export-contract.v1" in markdown
    assert "audit.trace_exports.<export_id>" in markdown
    assert "trace_audit_export_runtime_acceptance" in markdown
    assert "trace_audit_export_dashboard_acceptance" in markdown
    assert "trace_audit_export_211_acceptance" in markdown
    assert "release_evidence_export_location_contract" in markdown
    assert "release_evidence_retention_policy_contract" in markdown
    assert "ai-platform.release-evidence-readiness.v1" in markdown
    assert "ai-platform.release-evidence-retention-policy.v1" in markdown
    assert "docs/release-evidence/" in markdown
    assert "release_evidence_runtime_export_acceptance" in markdown
    assert "release_evidence_retention_runtime_acceptance" in markdown
    assert "template_only_not_enabled" in markdown
    assert "queue_depth_no_lease_progress" in markdown
    assert "## Domains" in markdown
    assert "callback-secret" not in markdown
    assert "anthropic-secret" not in markdown


def test_observability_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["ANTHROPIC_AUTH_TOKEN"] = "anthropic-secret"
    env["SANDBOX_CALLBACK_TOKEN"] = "callback-secret"
    result = subprocess.run(
        [sys.executable, "tools/observability_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.observability-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert "runtime_metrics" in payload["domains"]
    assert "anthropic-secret" not in result.stdout
    assert "callback-secret" not in result.stdout


def test_error_taxonomy_dashboard_readiness_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [sys.executable, "tools/error_taxonomy_dashboard_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.error-taxonomy-dashboard-readiness.v1"
    assert payload["dashboard_contract"]["schema_version"] == "ai-platform.error-taxonomy-dashboard-contract.v1"
    assert payload["status"] == "partial_blocked"
    assert "executor_private_payload" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "api_key" not in result.stdout


def test_release_evidence_readiness_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [sys.executable, "tools/release_evidence_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.release-evidence-readiness.v1"
    assert payload["export_location"]["path"] == "docs/release-evidence/"
    assert payload["status"] == "partial_blocked"
    assert "executor_private_payload" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "api_key" not in result.stdout


def test_trace_audit_export_readiness_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [sys.executable, "tools/trace_audit_export_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.trace-audit-export-readiness.v1"
    assert payload["export_contract"]["write_path"] == "audit.trace_exports.<export_id>"
    assert payload["status"] == "partial_blocked"
    assert "executor_private_payload" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "api_key" not in result.stdout

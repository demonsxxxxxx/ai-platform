import json
import os
import subprocess
import sys

from app.observability_readiness import (
    build_observability_readiness,
    render_observability_readiness_markdown,
)


class SecretBearingSettings:
    sandbox_container_provider = "docker://token@internal/path"
    sandbox_callback_token = "callback-secret"
    sandbox_workspace_root = "/tmp/tenant-secret/workspaces"
    anthropic_auth_token = "anthropic-secret"
    llm_gateway_provider = "openai_compatible"
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
    assert "latency_percentiles_p50_p95_p99" in domains["runtime_metrics"]["gaps"]
    assert "formal_error_taxonomy_contract" in domains["error_taxonomy"]["implemented"]
    assert "error_category_mapping_for_executor_tool_sandbox_model_gateway" in domains["error_taxonomy"]["implemented"]
    assert "golden_set_eval_run_contract" in domains["quality_evaluation"]["gaps"]
    assert "alert_rules_runtime_dashboard_and_211_acceptance" in domains["alerts_and_exports"]["gaps"]
    assert "alert_delivery_channel_policy" in domains["alerts_and_exports"]["gaps"]
    assert "slo_threshold_runtime_calibration" in domains["alerts_and_exports"]["gaps"]
    assert "alert_rules_and_slo_thresholds" not in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_contract" in domains["alerts_and_exports"]["gaps"]
    assert "model_gateway_request_concurrency_limit" in readiness["open_gaps"]
    assert "alert_delivery_channel_policy" in readiness["open_gaps"]
    assert "slo_threshold_runtime_calibration" in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "anthropic-secret" not in serialized
    assert "docker://token" not in serialized
    assert "sandbox_workspace_root" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_observability_readiness_includes_alert_slo_rule_template_evidence_without_closing_g9():
    readiness = build_observability_readiness(SecretBearingSettings())

    alerts = readiness["domains"]["alerts_and_exports"]
    assert "alert_slo_rule_template_evidence" in alerts["implemented"]

    evidence = alerts["evidence"]["alert_slo_rules"]
    assert evidence["schema_version"] == "ai-platform.alert-slo-readiness.v1"
    assert evidence["status"] == "partial_blocked"
    assert evidence["active_alerting_policy"] == "template_only_not_enabled"
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
        "alert_delivery_channel_policy",
        "slo_threshold_runtime_calibration",
    ]

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    assert "private_payload" not in serialized
    assert "storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "token=secret" not in serialized


def test_render_observability_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_observability_readiness_markdown(
        build_observability_readiness(SecretBearingSettings())
    )

    assert "# ai-platform G9 Observability Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "formal_error_taxonomy_contract" in markdown
    assert "golden_set_eval_run_contract" in markdown
    assert "alert_slo_rule_template_evidence" in markdown
    assert "alert_rules_runtime_dashboard_and_211_acceptance" in markdown
    assert "alert_delivery_channel_policy" in markdown
    assert "slo_threshold_runtime_calibration" in markdown
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

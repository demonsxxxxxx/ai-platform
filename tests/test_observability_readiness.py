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
    assert "formal_error_taxonomy_contract" in domains["error_taxonomy"]["gaps"]
    assert "golden_set_eval_run_contract" in domains["quality_evaluation"]["gaps"]
    assert "alert_rules_and_slo_thresholds" in domains["alerts_and_exports"]["gaps"]
    assert "trace_audit_export_contract" in domains["alerts_and_exports"]["gaps"]
    assert "model_gateway_request_concurrency_limit" in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "anthropic-secret" not in serialized
    assert "docker://token" not in serialized
    assert "sandbox_workspace_root" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_render_observability_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_observability_readiness_markdown(
        build_observability_readiness(SecretBearingSettings())
    )

    assert "# ai-platform G9 Observability Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "formal_error_taxonomy_contract" in markdown
    assert "golden_set_eval_run_contract" in markdown
    assert "alert_rules_and_slo_thresholds" in markdown
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

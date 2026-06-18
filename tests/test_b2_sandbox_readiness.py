import json
import subprocess
import sys
import importlib.util
from pathlib import Path

from app.b2_sandbox_readiness import (
    build_b2_sandbox_readiness,
    render_b2_sandbox_readiness_markdown,
)


def load_verifier():
    path = Path("scripts/verify_sandbox_runtime_211.py")
    spec = importlib.util.spec_from_file_location("verify_sandbox_runtime_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_generator():
    path = Path("scripts/generate_sandbox_runtime_evidence_211.py")
    spec = importlib.util.spec_from_file_location("generate_sandbox_runtime_evidence_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_b2_sandbox_readiness_records_source_contract_without_gate_closure():
    readiness = build_b2_sandbox_readiness()

    assert readiness["schema_version"] == "ai-platform.b2-sandbox-readiness.v1"
    assert readiness["backend_stage"] == "B2 real sandbox usable"
    assert readiness["issue"] == "#89"
    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["status_label"] == "local partial"
    assert readiness["provider_profile"]["provider"] == "docker"
    assert readiness["provider_profile"]["selected_by"] == "platform_policy"
    assert readiness["provider_profile"]["user_payload_provider_selection_allowed"] is False
    assert readiness["provider_profile"]["default_stack_provider"] == "fake"
    assert readiness["provider_profile"]["fake_provider_counts_as_production_evidence"] is False
    assert readiness["provider_profile"]["docker_socket_default_mount_allowed"] is False
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance"]["status_label_after_smoke_before_review"] == "local partial"
    assert readiness["runtime_acceptance"]["status_label_after_reviewed_evidence"] == "211 verified"
    assert readiness["runtime_acceptance"]["smoke_without_reviewed_evidence_status"] == (
        "runtime_smoke_recorded_review_required"
    )
    assert readiness["runtime_acceptance"]["reviewed_evidence_required_for_211_verified"] is True
    assert readiness["runtime_acceptance"]["does_not_close_b2_gate_by_itself"] is True
    assert readiness["runtime_acceptance"]["required_operator_target"] == "211_docker_capable_host"
    assert readiness["runtime_acceptance"]["generator_script"] == (
        "scripts/generate_sandbox_runtime_evidence_211.py"
    )
    assert readiness["runtime_acceptance"]["verifier_script"] == (
        "scripts/verify_sandbox_runtime_211.py"
    )
    assert readiness["runtime_acceptance"]["docker_cmd"] == "sudo -n docker"
    assert readiness["runtime_acceptance"]["cancel_probe_image"] == "ai-platform:local"
    assert readiness["open_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
        "b2_issue_review_and_closure_evidence",
    ]
    assert readiness["closed_source_controls"] == [
        "sandbox_provider_fail_closed_for_unknown_provider",
        "platform_policy_selects_provider_not_user_payload",
        "docker_provider_labels_tenant_workspace_user_session_run",
        "docker_provider_resource_limits_mapped",
        "docker_provider_health_timeout_removes_container",
        "docker_provider_cached_lease_scope_revalidation",
        "runtime_dispatch_failure_cleanup",
        "runtime_completion_cleanup_failure_keeps_db_lease_active",
        "verifier_requires_callback_stream_cancel_cleanup_hardening_and_redaction",
    ]
    assert readiness["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "admin_or_allowlist_only": True,
        "ordinary_user_multi_agent_allowed": False,
        "production_concurrency_defaults_raised": False,
        "docker_sandbox_production_hardening_claimed": False,
        "fake_provider_used_as_production_evidence": False,
    }
    assert "hardening.evidence_class" in readiness["runtime_acceptance"]["verifier_required_evidence_sections"]
    assert readiness["runtime_acceptance"]["prd_b2_g7_requirements_not_yet_verified"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
        "rollback_assumptions_evidence",
    ]
    for future_requirement in (
        "resource_limits",
        "egress_policy",
        "security_options",
        "rollback_assumptions",
    ):
        assert future_requirement not in readiness["runtime_acceptance"]["verifier_required_evidence_sections"]
    serialized_runtime_evidence = json.dumps(
        readiness["runtime_acceptance"]["verifier_required_runtime_evidence"],
        ensure_ascii=False,
    )
    assert "resource limits and timeout policy" not in serialized_runtime_evidence
    assert "egress policy" not in serialized_runtime_evidence
    assert "security options" not in serialized_runtime_evidence
    assert "rollback assumptions" not in serialized_runtime_evidence
    assert readiness["evidence_policy"] == (
        "B2 can become `211 verified` only after reviewed, redacted 211 Docker/equivalent "
        "sandbox smoke evidence proves launch, command execution, callback, cancel, cleanup, "
        "orphan prevention, artifact/event return, and projection redaction for merged source. "
        "Existing fake-provider and source-regression evidence stay `local partial`."
    )
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "gate closable" not in serialized
    assert "c:\\users" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "callback-secret" not in serialized


def test_b2_sandbox_readiness_tracks_current_verifier_and_generator_contract():
    readiness = build_b2_sandbox_readiness()
    runtime = readiness["runtime_acceptance"]
    verifier = load_verifier()
    generator = load_generator()

    expected_checks = [
        "check_docker_socket",
        "check_workspace_write",
        "check_executor_health",
        "check_callback_stream",
        "check_cancel_stops_container",
        "check_platform_runtime_evidence",
        "check_platform_hardening_evidence",
        "check_no_secret_leakage",
    ]
    assert runtime["verifier_required_checks"] == expected_checks
    assert runtime["verifier_check_entrypoints"] == {
        "check_docker_socket": "check_docker_socket",
        "check_workspace_write": "check_workspace_write",
        "check_executor_health": "check_executor_health_or_platform_evidence",
        "check_callback_stream": "check_callback_stream",
        "check_cancel_stops_container": "check_cancel_stops_container",
        "check_platform_runtime_evidence": "check_platform_runtime_evidence",
        "check_platform_hardening_evidence": "check_platform_hardening_evidence",
        "check_no_secret_leakage": "check_no_secret_leakage",
    }
    for entrypoint in runtime["verifier_check_entrypoints"].values():
        assert hasattr(verifier, entrypoint)

    assert runtime["verifier_evidence_shape"] == [
        "schema_version",
        "run_id",
        "executor_url",
        "runtime_mode",
        "sandbox_provider",
        "executed_task",
        "callback_auth",
        "generated_at",
        "callbacks",
        "cancel_stops_container",
        "cancelled_container_id",
        "timings",
        "hardening",
        "non_expansion_invariants",
    ]
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    assert list(recorder.to_dict().keys()) == runtime["verifier_evidence_shape"]
    assert runtime["verifier_timing_fields"] == verifier.REQUIRED_TIMING_FIELDS
    assert runtime["verifier_hardening_sections"] == list(verifier.REQUIRED_HARDENING_FLAGS)
    assert runtime["hardening_evidence_class"] == verifier.HARDENING_EVIDENCE_CLASS
    assert runtime["required_non_expansion_invariants"] == verifier.REQUIRED_NON_EXPANSION_INVARIANTS
    assert runtime["required_non_expansion_invariants"] == generator.NON_EXPANSION_INVARIANTS


def test_b2_sandbox_readiness_markdown_is_gap_first_and_operator_readable():
    markdown = render_b2_sandbox_readiness_markdown(build_b2_sandbox_readiness())

    assert "# B2 Real Sandbox Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    assert "- b2_211_real_sandbox_smoke" in markdown
    assert "- b2_reviewed_release_evidence" in markdown
    assert "- b2_issue_review_and_closure_evidence" in markdown
    assert "## Runtime Acceptance" in markdown
    assert "scripts/generate_sandbox_runtime_evidence_211.py" in markdown
    assert "scripts/verify_sandbox_runtime_211.py" in markdown
    assert "`sudo -n docker`" in markdown
    assert "`ai-platform:local`" in markdown
    assert "smoke status before reviewed evidence: `local partial`" in markdown
    assert "target status after reviewed evidence: `211 verified`" in markdown
    assert "hardening.evidence_class" in markdown
    assert "admin_or_allowlist_only" in markdown
    assert "PRD B2/G7 requirements not yet verifier-checked" in markdown
    assert "resource_limits_policy_evidence" in markdown
    assert "## Closed Source Controls" in markdown
    assert "docker_provider_cached_lease_scope_revalidation" in markdown
    assert "fake-provider and source-regression evidence stay `local partial`" in markdown
    assert "gate closable" not in markdown.lower()


def test_b2_sandbox_readiness_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [sys.executable, "tools/b2_sandbox_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.b2-sandbox-readiness.v1"
    assert payload["status_label"] == "local partial"
    assert payload["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert payload["runtime_acceptance"]["status_label_after_smoke_before_review"] == "local partial"
    assert payload["runtime_acceptance"]["reviewed_evidence_required_for_211_verified"] is True
    assert "callback-secret" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "gate closable" not in result.stdout.lower()

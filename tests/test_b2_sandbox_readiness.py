import json
import subprocess
import sys
import importlib.util
from pathlib import Path

import app.b2_sandbox_readiness as b2_sandbox_readiness
from app.b2_sandbox_readiness import (
    build_b2_sandbox_readiness,
    render_b2_sandbox_readiness_markdown,
)

CURRENT_B2_EVIDENCE_PATH = Path(
    "docs/release-evidence/b2-sandbox/"
    "f8a0f3c1168c34663850345d8f30358d435a0134/"
    "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json"
)
FUTURE_RUNTIME_SUBJECT = "1234567890abcdef1234567890abcdef12345678"
FUTURE_RUNTIME_TAG = "1234567-b2-runtime-evidence"
FUTURE_RUN_ID = "b2-1234567-20260620000102"


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


def write_future_reviewed_b2_smoke(
    repo_root: Path,
    *,
    commit_sha: str = FUTURE_RUNTIME_SUBJECT,
    directory_commit: str = FUTURE_RUNTIME_SUBJECT,
    source_tree_commit_sha: str = FUTURE_RUNTIME_SUBJECT,
    image_source_tree_commit: str = FUTURE_RUNTIME_SUBJECT,
    ordinary_user_high_risk_sandbox_allowed: bool = False,
    sandbox_provider: str = "docker",
) -> None:
    payload = json.loads(CURRENT_B2_EVIDENCE_PATH.read_text(encoding="utf-8"))
    payload["captured_at"] = "2026-06-20T00:01:02+08:00"
    payload["commit_sha"] = commit_sha
    payload["evidence_id"] = "2026-06-20-211-b2-sandbox-runtime-smoke-1234567"
    payload["runtime_subject_commit_sha"] = FUTURE_RUNTIME_SUBJECT

    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["run_id"] = FUTURE_RUN_ID
    smoke["sandbox_provider"] = sandbox_provider
    smoke["checks"]["check_opensandbox_provider_lifecycle_evidence"] = True
    smoke["executor"] = {
        "sdk_used": True,
        "executor_mode": "claude_agent_sdk",
    }
    timings = smoke["timings"]
    timings["sandbox_queue_wait_latency_ms"] = 0
    timings["sandbox_container_start_latency_ms"] = timings["sandbox_container_cold_start_latency_ms"]
    timings["executor_first_token_latency_ms"] = timings["executor_model_latency_ms"]
    timings["executor_tool_call_latency_ms"] = 0
    timings["artifact_upload_latency_ms"] = 0

    source_ref = payload["source_ref"]
    source_ref["image"] = f"ai-platform:{FUTURE_RUNTIME_TAG}"
    source_ref["runtime_source_marker"] = FUTURE_RUNTIME_SUBJECT
    source_ref["image_labels"]["ai-platform.source_revision"] = FUTURE_RUNTIME_SUBJECT
    source_ref["image_labels"]["ai-platform.source_tree_commit"] = image_source_tree_commit
    source_ref["image_labels"]["org.opencontainers.image.revision"] = FUTURE_RUNTIME_SUBJECT
    source_ref["source_snapshot"]["runtime_subject_commit_sha"] = FUTURE_RUNTIME_SUBJECT
    source_ref["source_snapshot"]["source_tree_commit_sha"] = source_tree_commit_sha

    smoke["non_expansion_invariants"]["ordinary_user_high_risk_sandbox_allowed"] = (
        ordinary_user_high_risk_sandbox_allowed
    )
    if sandbox_provider == "opensandbox":
        smoke["provider_lifecycle"] = {
            "schema_version": "ai-platform.opensandbox-provider-lifecycle.v1",
            "provider": "opensandbox",
            "run_id": FUTURE_RUN_ID,
            "lifecycle": {
                "create_observed": True,
                "delete_observed": True,
                "delete_stop_status": "stopped",
                "container_id_present": True,
                "executor_endpoint_present": True,
            },
            "db_lease": {
                "recorded": True,
                "released": True,
                "release_reason": "dispatch_completed",
                "recorded_scope_matches_request": True,
            },
            "startup_io": {
                "file_write_read_verified": True,
                "command_execution_verified": True,
                "source": "OpenSandboxContainerProvider.startup_io_probe",
            },
            "resource_policy": {
                "resource_limits_requested": True,
                "memory_mb": 512,
                "cpu_count": 0.5,
                "pids_limit": 128,
                "policy_projection_source": "provider_request",
            },
            "egress_policy": {
                "policy_requested": True,
                "callback_host_allowlisted": True,
                "policy_projection_source": "provider_request",
            },
            "dispatch": {
                "executor_response_present": True,
                "callback_stream_observed": True,
                "sdk_executor_observed": True,
            },
            "redaction": {
                "host_paths_redacted": True,
                "secrets_absent": True,
            },
        }

    evidence_path = (
        repo_root
        / "docs/release-evidence/b2-sandbox"
        / directory_commit
        / "2026-06-20-211-b2-sandbox-runtime-smoke-1234567.json"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_b2_issue_closure_evidence(
    repo_root: Path,
    *,
    issue: str = "#130",
    evidence_refs: list[str] | None = None,
    residual_caveats: list[str] | None = None,
    non_expansion_invariants: dict[str, bool] | None = None,
) -> None:
    issue_number = issue.removeprefix("#")
    payload = {
        "schema_version": "ai-platform.backend-stage-closure-evidence.v1",
        "backend_stage": "B2 real sandbox usable",
        "issue": issue,
        "issue_url": f"https://github.com/demonsxxxxxx/ai-platform/issues/{issue_number}",
        "issue_state": "closed",
        "closed_at": "2026-06-18T20:16:00Z",
        "closed_gap": "b2_issue_review_and_closure_evidence",
        "review_status": "reviewed",
        "redaction_scan_status": "passed",
        "linked_prs": [
            {
                "number": 90,
                "url": "https://github.com/demonsxxxxxx/ai-platform/pull/90",
                "merge_commit": "f8a0f3c1168c34663850345d8f30358d435a0134",
            }
        ],
        "closure_comments": [
            {
                "url": (
                    f"https://github.com/demonsxxxxxx/ai-platform/issues/{issue_number}"
                    "#issuecomment-4745786980"
                ),
                "summary": (
                    f"Final {issue} closure evidence records only the issue-scope sandbox smoke loop."
                ),
            }
        ],
        "evidence_refs": (
            evidence_refs
            if evidence_refs is not None
            else [
                "docs/release-evidence/b2-sandbox/"
                "f8a0f3c1168c34663850345d8f30358d435a0134/"
                "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json"
            ]
        ),
        "residual_caveats": (
            residual_caveats
            if residual_caveats is not None
            else [
                "does_not_close_broader_b2_g7_production_hardening_gate",
            ]
        ),
        "non_expansion_invariants": (
            non_expansion_invariants
            if non_expansion_invariants is not None
            else {
                "ordinary_user_high_risk_sandbox_allowed": False,
                "ordinary_user_multi_agent_allowed": False,
                "production_concurrency_defaults_raised": False,
                "docker_sandbox_production_hardening_claimed": False,
            }
        ),
        "does_not_close_broader_gate": True,
    }
    evidence_path = (
        repo_root
        / "docs/release-evidence/backend-stage-closures/b2-sandbox"
        / f"2026-06-18-issue{issue_number}-b2-closure.json"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_b2_sandbox_readiness_records_source_contract_without_gate_closure(tmp_path):
    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["schema_version"] == "ai-platform.b2-sandbox-readiness.v1"
    assert readiness["backend_stage"] == "B2 real sandbox usable"
    assert readiness["issue"] == "#130"
    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["status_label"] == "local partial"
    assert readiness["provider_profile"]["provider"] == "docker"
    assert readiness["provider_profile"]["selected_by"] == "platform_policy"
    assert readiness["provider_profile"]["user_payload_provider_selection_allowed"] is False
    assert readiness["provider_profile"]["default_stack_provider"] == "fake"
    assert readiness["provider_profile"]["first_stage_provider_adapters"]["opensandbox"] == {
        "status": "local_partial_211_smoke_required",
        "role": "B2 first-stage provider adapter",
        "does_not_close_b2": True,
    }
    assert readiness["provider_profile"]["fake_provider_counts_as_production_evidence"] is False
    assert readiness["provider_profile"]["docker_socket_default_mount_allowed"] is False
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance"]["status_label_after_smoke_before_review"] == "local partial"
    assert readiness["runtime_acceptance"]["status_label_after_reviewed_evidence"] == "local partial"
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
        "docker_provider_security_options_mapped",
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
    for runtime_section in (
        "hardening.resource_limits",
        "hardening.egress_policy",
        "hardening.security_options",
    ):
        assert runtime_section in readiness["runtime_acceptance"]["verifier_required_evidence_sections"]
    assert readiness["broader_b2_g7_open_requirements"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    policy_contracts = readiness["hardening_policy_contracts"]
    assert list(policy_contracts) == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert policy_contracts["resource_limits_policy_evidence"] == {
        "status": "recorded_source_policy_contract",
        "evidence_level": "source_contract",
        "does_not_close_broader_b2_g7_gate": True,
        "does_not_claim_docker_sandbox_production_hardening": True,
        "required_controls": [
            "container_memory_limit_defined",
            "container_cpu_limit_defined",
            "process_timeout_defined",
            "workspace_size_or_artifact_limit_defined",
            "over_limit_cleanup_and_error_projection_defined",
        ],
        "runtime_evidence_required": [
            "211 Docker/equivalent smoke records configured memory and CPU limits for the sandbox container",
            "over-limit or timeout probe proves the container is stopped and the lease is released",
            "Admin Runtime projection reports bounded error metadata without host paths or raw Docker payloads",
        ],
        "remaining_runtime_gap": "resource_limits_runtime_hardening_evidence",
    }
    assert policy_contracts["egress_policy_evidence"] == {
        "status": "recorded_source_policy_contract",
        "evidence_level": "source_contract",
        "does_not_close_broader_b2_g7_gate": True,
        "does_not_claim_docker_sandbox_production_hardening": True,
        "required_controls": [
            "default_deny_outbound_network_policy_defined",
            "allowlist_owned_by_platform_policy_not_user_payload",
            "callback_endpoint_exception_scoped_to_run_token",
            "egress_denial_logged_without_secret_or_url_leakage",
        ],
        "runtime_evidence_required": [
            "211 Docker/equivalent smoke proves an unapproved outbound request is denied",
            "callback path still works through the scoped run token",
            "release evidence redaction scan excludes callback tokens, host paths, and denied target secrets",
        ],
        "remaining_runtime_gap": "egress_runtime_hardening_evidence",
    }
    assert policy_contracts["security_options_evidence"] == {
        "status": "recorded_source_policy_contract",
        "evidence_level": "source_contract",
        "does_not_close_broader_b2_g7_gate": True,
        "does_not_claim_docker_sandbox_production_hardening": True,
        "required_controls": [
            "privileged_container_disabled",
            "capability_drop_or_minimal_capabilities_defined",
            "no_new_privileges_enabled",
            "readonly_root_or_workspace_mount_boundary_defined",
            "docker_socket_mount_forbidden_by_default",
        ],
        "runtime_evidence_required": [
            "211 Docker/equivalent smoke captures security options from the launched sandbox container",
            "privileged and Docker-socket access probes fail closed",
            "cleanup proves no elevated container or mount remains after cancel or failure",
        ],
        "remaining_runtime_gap": "security_options_runtime_hardening_evidence",
    }
    for contract in policy_contracts.values():
        assert contract["status"] == "recorded_source_policy_contract"
        assert contract["evidence_level"] == "source_contract"
        assert contract["does_not_close_broader_b2_g7_gate"] is True
        assert contract["does_not_claim_docker_sandbox_production_hardening"] is True
        assert contract["remaining_runtime_gap"].endswith("_runtime_hardening_evidence")
    for future_requirement in (
        "rollback_assumptions",
    ):
        assert future_requirement not in readiness["runtime_acceptance"]["verifier_required_evidence_sections"]
    serialized_runtime_evidence = json.dumps(
        readiness["runtime_acceptance"]["verifier_required_runtime_evidence"],
        ensure_ascii=False,
    )
    assert "resource limits and timeout policy" in serialized_runtime_evidence
    assert "egress policy" in serialized_runtime_evidence
    assert "security options" in serialized_runtime_evidence
    assert "rollback assumptions" not in serialized_runtime_evidence
    assert readiness["evidence_policy"] == (
        "B2 remains `local partial` until the current issue boundary, reviewed release evidence, "
        "and required 211 smoke/readiness evidence are all complete. Reviewed fake-provider, "
        "source-regression, or runtime-hardening evidence by itself does not complete gate closure."
    )
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "gate closable" not in serialized
    assert "c:\\users" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "callback-secret" not in serialized


def test_b2_sandbox_readiness_accepts_future_reviewed_smoke_run_ids(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["open_gaps"] == [
        "b2_issue_review_and_closure_evidence",
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    runtime_review = readiness["gate_boundary_evidence"][
        "b2_runtime_evidence_review_against_merged_source"
    ]
    assert runtime_review["status"] == "open_unable_to_classify_runtime_delta"
    assert runtime_review["closed_gap"] is None
    assert runtime_review["runtime_subject_commit_sha"] == FUTURE_RUNTIME_SUBJECT
    assert runtime_review["current_source_commit_sha"]
    assert runtime_review["runtime_affecting_changes_since_runtime_subject"] is None
    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence["run_id"] == FUTURE_RUN_ID
    assert smoke_evidence["runtime_subject_commit_sha"] == FUTURE_RUNTIME_SUBJECT
    assert smoke_evidence["runtime_subject"] == FUTURE_RUNTIME_TAG
    assert smoke_evidence["status"] == "verified_211_runtime_acceptance"
    assert smoke_evidence["does_not_close_b2_gate"] is True
    assert "runtime_hardening" not in readiness["closed_runtime_gaps"]


def test_b2_sandbox_readiness_accepts_reviewed_runtime_hardening_without_gate_closure(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)
    evidence_path = (
        tmp_path
        / "docs/release-evidence/b2-sandbox"
        / FUTURE_RUNTIME_SUBJECT
        / "2026-06-20-211-b2-sandbox-runtime-smoke-1234567.json"
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["hardening"] = {
        **smoke["hardening"],
        "resource_limits": {
            "evidence_class": "live_platform_probe",
            "memory_limit_mb": 512,
            "cpu_limit_count": 0.5,
            "pids_limit": 128,
            "process_timeout_seconds": 60,
            "limit_source": "platform_request",
            "docker_inspection_verified": True,
            "over_limit_cleanup_verified": True,
            "over_limit_probe_kind": "platform_resource_timeout",
            "over_limit_timeout_probe_seconds": 0,
            "bounded_error_projection_verified": True,
            "bounded_error_projection": {
                "source": "admin_runtime_projection",
                "run_id": FUTURE_RUN_ID,
                "status": "failed",
                "error_code": "executor_health_timeout",
                "host_paths_redacted": True,
                "raw_docker_payload_absent": True,
                "callback_token_absent": True,
            },
        },
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_hardening_acceptance_recorded"
    assert readiness["status_label"] == "local partial"
    assert readiness["open_gaps"] == [
        "b2_issue_review_and_closure_evidence",
        "b2_runtime_evidence_review_against_merged_source",
    ]
    assert readiness["closed_runtime_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence["hardening_runtime_evidence"] == {
        "resource_limits_policy_evidence": "verified_211_runtime_acceptance",
        "egress_policy_evidence": "verified_211_runtime_acceptance",
        "security_options_evidence": "verified_211_runtime_acceptance",
    }
    assert "gate closable" not in json.dumps(readiness, ensure_ascii=False).lower()


def test_b2_runtime_delta_filter_treats_frontend_only_changes_as_b2_runtime_neutral(monkeypatch):
    monkeypatch.setattr(
        b2_sandbox_readiness,
        "_resolve_source_runtime_affecting_changes_between",
        lambda _base, _source: [
            "frontend/web/src/App.tsx",
            "frontend/web/src/components/panels/MCPPanel.tsx",
            "app/routes/runs.py",
        ],
    )

    changes = b2_sandbox_readiness._resolve_b2_runtime_affecting_changes_between(
        "runtime-subject",
        "current-source",
    )

    assert changes == ["app/routes/runs.py"]


def test_b2_runtime_delta_filter_treats_non_b2_readiness_and_evidence_as_neutral(monkeypatch):
    monkeypatch.setattr(
        b2_sandbox_readiness,
        "_resolve_source_runtime_affecting_changes_between",
        lambda _base, _source: [
            "app/foundation_alpha_readiness.py",
            "docs/release-evidence/README.md",
            (
                "docs/release-evidence/b1-memory-context/"
                "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c/"
                "2026-07-01-211-b1-memory-context-workflow-smoke-96f27bb.json"
            ),
            (
                "docs/release-evidence/foundation-alpha-poc/"
                "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c/"
                "2026-06-30-211-foundation-alpha-poc-96f27bb-runtime-poc-smoke.json"
            ),
            (
                "docs/release-evidence/foundation-runtime-concurrency/"
                "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c-frc-b0-20260630/"
                "2026-06-30-211-foundation-alpha-poc-96f27bb-foundation-runtime-concurrency.json"
            ),
            "tests/test_b1_memory_context_readiness.py",
            "tests/test_foundation_alpha_readiness.py",
            "tests/test_source_authority_docs.py",
            "app/runtime/sandbox/runtime.py",
        ],
    )

    changes = b2_sandbox_readiness._resolve_b2_runtime_affecting_changes_between(
        "runtime-subject",
        "current-source",
    )

    assert changes == ["app/runtime/sandbox/runtime.py"]


def test_b2_sandbox_readiness_rejects_egress_hardening_without_probe_details(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)
    evidence_path = (
        tmp_path
        / "docs/release-evidence/b2-sandbox"
        / FUTURE_RUNTIME_SUBJECT
        / "2026-06-20-211-b2-sandbox-runtime-smoke-1234567.json"
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["hardening"] = {
        **smoke["hardening"],
        "resource_limits": {
            "evidence_class": "live_platform_probe",
            "memory_limit_mb": 512,
            "cpu_limit_count": 0.5,
            "pids_limit": 128,
            "process_timeout_seconds": 60,
            "limit_source": "platform_request",
            "docker_inspection_verified": True,
            "over_limit_cleanup_verified": True,
            "over_limit_probe_kind": "platform_resource_timeout",
            "over_limit_timeout_probe_seconds": 0,
            "bounded_error_projection_verified": True,
            "bounded_error_projection": {
                "source": "admin_runtime_projection",
                "run_id": FUTURE_RUN_ID,
                "status": "failed",
                "error_code": "executor_health_timeout",
                "host_paths_redacted": True,
                "raw_docker_payload_absent": True,
                "callback_token_absent": True,
            },
        },
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "policy_source": "platform_policy",
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert "egress_policy_evidence" in readiness["open_gaps"]
    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence.get("hardening_runtime_evidence") is None


def test_b2_sandbox_readiness_rejects_partial_runtime_hardening_closure(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)
    evidence_path = (
        tmp_path
        / "docs/release-evidence/b2-sandbox"
        / FUTURE_RUNTIME_SUBJECT
        / "2026-06-20-211-b2-sandbox-runtime-smoke-1234567.json"
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["hardening"] = {
        **smoke["hardening"],
        "resource_limits": {
            "evidence_class": "live_platform_probe",
            "memory_limit_mb": 512,
            "cpu_limit_count": 0.5,
            "pids_limit": 128,
            "process_timeout_seconds": 60,
            "limit_source": "platform_request",
            "docker_inspection_verified": True,
            "over_limit_cleanup_verified": True,
            "over_limit_probe_kind": "platform_resource_timeout",
            "over_limit_timeout_probe_seconds": 0,
            "bounded_error_projection_verified": True,
            "bounded_error_projection": {
                "source": "admin_runtime_projection",
                "run_id": FUTURE_RUN_ID,
                "status": "failed",
                "error_code": "executor_health_timeout",
                "host_paths_redacted": True,
                "raw_docker_payload_absent": True,
                "callback_token_absent": True,
            },
        },
    }
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["open_gaps"] == [
        "b2_issue_review_and_closure_evidence",
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert readiness["closed_runtime_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
    ]
    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence.get("hardening_runtime_evidence") is None


def test_b2_sandbox_readiness_rejects_self_asserted_bounded_projection(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)
    evidence_path = (
        tmp_path
        / "docs/release-evidence/b2-sandbox"
        / FUTURE_RUNTIME_SUBJECT
        / "2026-06-20-211-b2-sandbox-runtime-smoke-1234567.json"
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["hardening"] = {
        **smoke["hardening"],
        "resource_limits": {
            "evidence_class": "live_platform_probe",
            "memory_limit_mb": 512,
            "cpu_limit_count": 0.5,
            "pids_limit": 128,
            "process_timeout_seconds": 60,
            "limit_source": "platform_request",
            "docker_inspection_verified": True,
            "over_limit_cleanup_verified": True,
            "over_limit_probe_kind": "platform_resource_timeout",
            "over_limit_timeout_probe_seconds": 0,
            "bounded_error_projection_verified": True,
        },
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["open_gaps"] == [
        "b2_issue_review_and_closure_evidence",
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence.get("hardening_runtime_evidence") is None


def test_b2_sandbox_readiness_rejects_smoke_with_mismatched_commit_sha(tmp_path):
    write_future_reviewed_b2_smoke(
        tmp_path,
        commit_sha="0000000000000000000000000000000000000000",
    )

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["open_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
        "b2_issue_review_and_closure_evidence",
    ]
    assert readiness["runtime_acceptance_evidence"] == {}


def test_b2_sandbox_readiness_rejects_smoke_stored_under_wrong_runtime_subject(tmp_path):
    write_future_reviewed_b2_smoke(
        tmp_path,
        directory_commit="0000000000000000000000000000000000000000",
    )

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance_evidence"] == {}


def test_b2_sandbox_readiness_rejects_smoke_with_mismatched_source_tree_commit(tmp_path):
    write_future_reviewed_b2_smoke(
        tmp_path,
        source_tree_commit_sha="0000000000000000000000000000000000000000",
    )

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance_evidence"] == {}


def test_b2_sandbox_readiness_rejects_smoke_with_mismatched_image_source_tree_commit(tmp_path):
    write_future_reviewed_b2_smoke(
        tmp_path,
        image_source_tree_commit="0000000000000000000000000000000000000000",
    )

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance_evidence"] == {}


def test_b2_sandbox_readiness_rejects_smoke_with_expanded_user_sandbox_invariant(tmp_path):
    write_future_reviewed_b2_smoke(
        tmp_path,
        ordinary_user_high_risk_sandbox_allowed=True,
    )

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance_evidence"] == {}


def test_b2_sandbox_readiness_keeps_historical_211_evidence_open_after_timing_contract_expands():
    readiness = build_b2_sandbox_readiness()

    assert readiness["status"] == "local_contract_ready_runtime_smoke_required"
    assert readiness["status_label"] == "local partial"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert readiness["runtime_acceptance"]["status_label_after_reviewed_evidence"] == "local partial"
    assert readiness["runtime_acceptance"]["does_not_close_b2_gate_by_itself"] is True
    assert readiness["open_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
        "b2_issue_review_and_closure_evidence",
    ]
    assert readiness["closed_runtime_gaps"] == []
    assert readiness["closed_gate_boundary_gaps"] == [
        "b2_issue_review_and_closure_evidence",
    ]
    runtime_review = readiness["gate_boundary_evidence"][
        "b2_runtime_evidence_review_against_merged_source"
    ]
    assert runtime_review["status"] == "open_missing_runtime_subject_evidence"
    assert runtime_review["closed_gap"] is None
    assert runtime_review["runtime_subject_commit_sha"] == ""
    assert runtime_review["current_source_commit_sha"]
    assert runtime_review["runtime_affecting_changes_since_runtime_subject"] is None
    assert runtime_review["required_next_step"] == (
        "record reviewed 211 B2 sandbox smoke evidence before reviewing merged-source drift"
    )
    closure_evidence = readiness["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "recorded_issue_closure_evidence"
    assert closure_evidence["closed_gap"] == "b2_issue_review_and_closure_evidence"
    assert closure_evidence["issue"] == "#130"
    assert closure_evidence["issue_state"] == "closed"
    assert closure_evidence["does_not_close_broader_b2_g7_gate"] is True
    assert closure_evidence["path"].endswith("2026-06-24-issue130-b2-closure.json")
    assert closure_evidence["evidence_refs"] == [
        "docs/release-evidence/b2-sandbox/0822dad411fb72c89d9888ffde08a6c13a468cd9/2026-06-24-211-b2-sandbox-runtime-smoke-0822dad.json"
    ]

    assert readiness["runtime_acceptance_evidence"] == {}

    assert readiness["runtime_acceptance"]["prd_b2_g7_requirements_not_yet_verified"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert readiness["broader_b2_g7_open_requirements"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    rollback = readiness["rollback_assumptions"]
    assert rollback["status"] == "recorded_source_operator_contract"
    assert rollback["closed_gap"] == "rollback_assumptions_evidence"
    assert rollback["does_not_close_broader_b2_g7_gate"] is True
    assert rollback["does_not_claim_docker_sandbox_production_hardening"] is True
    assert rollback["required_after_rollback_evidence"] == [
        "Admin Runtime sandbox overview shows zero verifier-owned active containers or active leases",
        "selected workflow is disabled or restored to fake/test-only provider posture",
        "orphan cleanup scan completed for same tenant/workspace/user/session/run scope",
        "B2 readiness still reports any remaining issue, source, or hardening boundary as open",
        "operator issue comment records source/runtime subject, command result, and residual caveats",
    ]
    assert rollback["remaining_hardening_gaps"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert readiness["hardening_policy_contracts"]["resource_limits_policy_evidence"]["status"] == (
        "recorded_source_policy_contract"
    )
    assert (
        readiness["hardening_policy_contracts"]["egress_policy_evidence"]["remaining_runtime_gap"]
        == "egress_runtime_hardening_evidence"
    )
    assert readiness["hardening_policy_contracts"]["egress_policy_evidence"]["runtime_evidence_required"]
    assert readiness["hardening_policy_contracts"]["security_options_evidence"][
        "does_not_claim_docker_sandbox_production_hardening"
    ] is True

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "211 verified" not in serialized
    assert "gate closable" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "callback-secret" not in serialized
    assert "c:\\users" not in serialized


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
        "check_opensandbox_provider_lifecycle_evidence",
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
        "check_opensandbox_provider_lifecycle_evidence": "check_opensandbox_provider_lifecycle_evidence",
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
        "executor",
        "generated_at",
        "callbacks",
        "cancel_stops_container",
        "cancelled_container_id",
        "timings",
        "hardening",
        "provider_lifecycle",
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
    assert runtime["runtime_probe_results_schema_version"] == generator.RUNTIME_PROBE_RESULTS_SCHEMA_VERSION
    assert runtime["runtime_probe_results_generate_cli_flag"] == "--generate-runtime-probe-results-file"
    assert runtime["runtime_probe_results_cli_flag"] == "--runtime-probe-results-file"
    assert runtime["runtime_probe_results_environment_variable"] == (
        "AI_PLATFORM_SANDBOX_RUNTIME_PROBE_RESULTS"
    )
    assert runtime["runtime_probe_results_required_fields"] == [
        "schema_version",
        "run_id",
        "source=platform_runtime_probe",
        "resource_limits",
        "egress_policy",
        "security_options",
    ]
    assert runtime["runtime_probe_results_required_section_fields"] == {
        "resource_limits": [
            "over_limit_cleanup_verified=true",
            "probe_kind=platform_resource_timeout",
            "timeout_probe_seconds=0",
            "bounded_error_projection.safe_admin_runtime_projection",
        ],
        "egress_policy": [
            "default_deny_outbound=true",
            "platform_allowlist_enforced=true",
            "callback_exception_scoped_to_run_token=true",
            "denied_egress_redacted=true",
            "denied_target",
            "denied_probe_error_code=egress_denied",
            "allowed_callback_host",
            "callback_probe_status=delivered",
            "policy_source=platform_policy",
            "probe_source=runtime_probe_results",
        ],
        "security_options": [
            "privileged=false",
            "docker_socket_mounted=false",
            "no_new_privileges=true",
            "capabilities_dropped=true",
            "root_filesystem_read_only_or_minimal=true",
            "workspace_mount_mode=rw|ro",
        ],
    }
    for section_name in ("resource_limits", "egress_policy", "security_options"):
        assert section_name in verifier.REQUIRED_HARDENING_FLAGS
        assert section_name in runtime["verifier_hardening_sections"]


def test_b2_sandbox_readiness_markdown_is_gap_first_and_operator_readable():
    markdown = render_b2_sandbox_readiness_markdown(build_b2_sandbox_readiness())

    assert "# B2 Real Sandbox Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    open_gap_section = markdown.split("## Closed Gate Boundary Gaps", 1)[0]
    assert "- b2_211_real_sandbox_smoke" in open_gap_section
    assert "- b2_reviewed_release_evidence" in open_gap_section
    assert "- b2_issue_review_and_closure_evidence" in open_gap_section
    assert "- resource_limits_policy_evidence" not in open_gap_section
    assert "- egress_policy_evidence" not in open_gap_section
    assert "- security_options_evidence" not in open_gap_section
    assert "- b2_runtime_evidence_review_against_merged_source" not in open_gap_section
    assert "- rollback_assumptions_evidence" not in open_gap_section
    assert "## Closed Gate Boundary Gaps" in markdown
    closed_gap_section = markdown.split("## Closed Gate Boundary Gaps", 1)[1].split(
        "## Gate Boundary Evidence",
        1,
    )[0]
    assert "- b2_issue_review_and_closure_evidence" in closed_gap_section
    assert "- b2_runtime_evidence_review_against_merged_source" not in closed_gap_section
    assert "## Gate Boundary Evidence" in markdown
    assert "### B2 Issue Closure Evidence" in markdown
    assert "### B2 Runtime Evidence Review Against Merged Source" in markdown
    assert "recorded_issue_closure_evidence" in markdown
    assert "open_missing_runtime_subject_evidence" in markdown
    assert "2026-06-24-issue130-b2-closure.json" in markdown
    assert "docs/release-evidence/backend-stage-closures/b2-sandbox" in markdown
    assert "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json" not in markdown
    assert "- b2_211_real_sandbox_smoke" in markdown
    assert "- b2_reviewed_release_evidence" in markdown
    assert "## Runtime Acceptance" in markdown
    assert "scripts/generate_sandbox_runtime_evidence_211.py" in markdown
    assert "scripts/verify_sandbox_runtime_211.py" in markdown
    assert "`sudo -n docker`" in markdown
    assert "`ai-platform:local`" in markdown
    assert "smoke status before reviewed evidence: `local partial`" in markdown
    assert "target status after reviewed evidence: `local partial`" in markdown
    assert "211 verified" not in markdown
    assert "hardening.evidence_class" in markdown
    assert "admin_or_allowlist_only" in markdown
    assert "PRD B2/G7 runtime hardening requirements still open" in markdown
    remaining_requirement_section = markdown.split(
        "PRD B2/G7 runtime hardening requirements still open:",
        1,
    )[1].split("## Hardening Policy Contracts", 1)[0]
    assert "- `resource_limits_policy_evidence`" in remaining_requirement_section
    assert "- `egress_policy_evidence`" in remaining_requirement_section
    assert "- `security_options_evidence`" in remaining_requirement_section
    assert "hardening.resource_limits" in markdown
    assert "hardening.egress_policy" in markdown
    assert "hardening.security_options" in markdown
    assert "runtime probe results schema: `ai-platform.sandbox-runtime-probe-results.v1`" in markdown
    assert "`--generate-runtime-probe-results-file`" in markdown
    assert "`--runtime-probe-results-file`" in markdown
    assert "resource_limits_policy_evidence" in markdown
    assert "## Hardening Policy Contracts" in markdown
    assert "resource_limits_runtime_hardening_evidence" in markdown
    assert "egress_runtime_hardening_evidence" in markdown
    assert "security_options_runtime_hardening_evidence" in markdown
    assert "remaining runtime gap: `None`" not in markdown
    assert "Runtime evidence still required:" in markdown
    assert "recorded_source_policy_contract" in markdown
    assert "container_memory_limit_defined" in markdown
    assert "default_deny_outbound_network_policy_defined" in markdown
    assert "privileged_container_disabled" in markdown
    assert "does not claim Docker sandbox production hardening: `true`" in markdown
    assert "## Rollback Assumptions" in markdown
    assert "recorded_source_operator_contract" in markdown
    assert "Admin Runtime sandbox overview shows zero verifier-owned active containers" in markdown
    assert "## Closed Source Controls" in markdown
    assert "docker_provider_cached_lease_scope_revalidation" in markdown
    assert "runtime-hardening evidence by itself does not complete gate closure" in markdown
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
    assert payload["status"] == "local_contract_ready_runtime_smoke_required"
    assert payload["runtime_acceptance"]["status"] == "missing_211_real_sandbox_smoke"
    assert payload["runtime_acceptance"]["status_label_after_smoke_before_review"] == "local partial"
    assert payload["runtime_acceptance"]["reviewed_evidence_required_for_211_verified"] is True
    assert payload["open_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
        "b2_issue_review_and_closure_evidence",
    ]
    assert payload["closed_runtime_gaps"] == []
    assert payload["closed_gate_boundary_gaps"] == [
        "b2_issue_review_and_closure_evidence",
    ]
    assert payload["gate_boundary_evidence"]["b2_runtime_evidence_review_against_merged_source"]["status"] == (
        "open_missing_runtime_subject_evidence"
    )
    assert (
        payload["gate_boundary_evidence"]["b2_runtime_evidence_review_against_merged_source"]["closed_gap"]
        is None
    )
    assert payload["runtime_acceptance"]["prd_b2_g7_requirements_not_yet_verified"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert payload["broader_b2_g7_open_requirements"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert payload["hardening_policy_contracts"]["resource_limits_policy_evidence"]["evidence_level"] == (
        "source_contract"
    )
    assert (
        payload["hardening_policy_contracts"]["egress_policy_evidence"]["remaining_runtime_gap"]
        == "egress_runtime_hardening_evidence"
    )
    assert payload["hardening_policy_contracts"]["egress_policy_evidence"]["runtime_evidence_required"]
    assert payload["hardening_policy_contracts"]["security_options_evidence"][
        "does_not_close_broader_b2_g7_gate"
    ] is True
    assert payload["rollback_assumptions"]["closed_gap"] == "rollback_assumptions_evidence"
    assert payload["rollback_assumptions"]["remaining_hardening_gaps"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert payload["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]["status"] == (
        "recorded_issue_closure_evidence"
    )
    assert payload["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]["closed_gap"] == (
        "b2_issue_review_and_closure_evidence"
    )
    assert "callback-secret" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "gate closable" not in result.stdout.lower()
    assert "211 verified" not in result.stdout.lower()


def test_b2_issue_closure_gap_stays_open_without_valid_local_closure_evidence(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert readiness["open_gaps"] == [
        "b2_issue_review_and_closure_evidence",
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    closure_evidence = readiness["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "open_missing_issue_closure_evidence"
    assert closure_evidence["closed_gap"] is None
    assert closure_evidence["required_next_step"] == (
        "record reviewed local issue-closure evidence for #130 before closing this boundary"
    )


def test_b2_issue_closure_gap_stays_open_when_only_historical_issue89_is_closed(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)
    write_b2_issue_closure_evidence(tmp_path, issue="#89")

    readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

    assert "b2_issue_review_and_closure_evidence" in readiness["open_gaps"]
    closure_evidence = readiness["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "open_missing_issue_closure_evidence"
    assert closure_evidence["closed_gap"] is None
    assert closure_evidence["issue"] == "#130"


def test_b2_issue_closure_gap_stays_open_for_under_specified_closure_evidence(tmp_path):
    write_future_reviewed_b2_smoke(tmp_path)

    for kwargs in (
        {"evidence_refs": []},
        {"residual_caveats": []},
        {"non_expansion_invariants": {}},
    ):
        write_b2_issue_closure_evidence(tmp_path, **kwargs)

        readiness = build_b2_sandbox_readiness(repo_root=tmp_path)

        assert "b2_issue_review_and_closure_evidence" in readiness["open_gaps"]
        closure_evidence = readiness["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]
        assert closure_evidence["status"] == "open_missing_issue_closure_evidence"

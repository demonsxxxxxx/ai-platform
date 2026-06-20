import json
import subprocess
import sys
import importlib.util
from pathlib import Path

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
) -> None:
    payload = json.loads(CURRENT_B2_EVIDENCE_PATH.read_text(encoding="utf-8"))
    payload["captured_at"] = "2026-06-20T00:01:02+08:00"
    payload["commit_sha"] = commit_sha
    payload["evidence_id"] = "2026-06-20-211-b2-sandbox-runtime-smoke-1234567"
    payload["runtime_subject_commit_sha"] = FUTURE_RUNTIME_SUBJECT

    smoke = payload["evidence_ref"]["runtime_checks"]["b2_211_real_sandbox_smoke"]
    smoke["run_id"] = FUTURE_RUN_ID

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
    evidence_refs: list[str] | None = None,
    residual_caveats: list[str] | None = None,
    non_expansion_invariants: dict[str, bool] | None = None,
) -> None:
    payload = {
        "schema_version": "ai-platform.backend-stage-closure-evidence.v1",
        "backend_stage": "B2 real sandbox usable",
        "issue": "#89",
        "issue_url": "https://github.com/demonsxxxxxx/ai-platform/issues/89",
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
                "url": "https://github.com/demonsxxxxxx/ai-platform/issues/89#issuecomment-4745786980",
                "summary": "Final #89 closure evidence records only the issue-scope sandbox smoke loop.",
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
        / "2026-06-18-issue89-b2-closure.json"
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
            "bounded_error_projection_verified": True,
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


def test_b2_sandbox_readiness_records_reviewed_211_smoke_without_closing_b2_gate():
    readiness = build_b2_sandbox_readiness()

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["status_label"] == "local partial"
    assert readiness["runtime_acceptance"]["status"] == "verified_211_runtime_acceptance"
    assert readiness["runtime_acceptance"]["status_label_after_reviewed_evidence"] == "211 verified"
    assert readiness["runtime_acceptance"]["does_not_close_b2_gate_by_itself"] is True
    assert readiness["open_gaps"] == [
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert readiness["closed_runtime_gaps"] == [
        "b2_211_real_sandbox_smoke",
        "b2_reviewed_release_evidence",
    ]
    assert readiness["closed_gate_boundary_gaps"] == ["b2_issue_review_and_closure_evidence"]
    runtime_review = readiness["gate_boundary_evidence"][
        "b2_runtime_evidence_review_against_merged_source"
    ]
    assert runtime_review["status"] == "runtime_affecting_delta_requires_fresh_211_smoke"
    assert runtime_review["closed_gap"] is None
    assert runtime_review["runtime_subject_commit_sha"] == (
        "f8a0f3c1168c34663850345d8f30358d435a0134"
    )
    assert runtime_review["current_source_commit_sha"]
    runtime_delta = runtime_review["runtime_affecting_changes_since_runtime_subject"]
    assert runtime_delta
    for path in (
        "app/b2_sandbox_readiness.py",
        "app/runtime/sandbox/container_provider.py",
        "app/sandbox_hardening_contract.py",
        "scripts/generate_sandbox_runtime_evidence_211.py",
        "scripts/verify_sandbox_runtime_211.py",
    ):
        assert path in runtime_delta
    assert runtime_review["required_next_step"] == (
        "deploy current main to 211 and rerun scripts/verify_sandbox_runtime_211.py before closing this gap"
    )
    closure_evidence = readiness["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "recorded_issue_closure_evidence"
    assert closure_evidence["closed_gap"] == "b2_issue_review_and_closure_evidence"
    assert closure_evidence["issue"] == "#89"
    assert closure_evidence["issue_state"] == "closed"
    assert closure_evidence["does_not_close_broader_b2_g7_gate"] is True
    assert "docs/release-evidence/backend-stage-closures" in closure_evidence["path"]
    assert closure_evidence["evidence_refs"] == [
        "docs/release-evidence/b2-sandbox/"
        "f8a0f3c1168c34663850345d8f30358d435a0134/"
        "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json"
    ]
    assert "does_not_close_broader_b2_g7_production_hardening_gate" in closure_evidence[
        "residual_caveats"
    ]

    smoke_evidence = readiness["runtime_acceptance_evidence"]["b2_211_real_sandbox_smoke"]
    assert smoke_evidence["status"] == "verified_211_runtime_acceptance"
    assert smoke_evidence["evidence_id"] == "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c"
    assert smoke_evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert smoke_evidence["verifier"] == "scripts/verify_sandbox_runtime_211.py"
    assert smoke_evidence["run_id"] == "b2-f8a0f3c-20260618184106"
    assert smoke_evidence["runtime_subject_commit_sha"] == (
        "f8a0f3c1168c34663850345d8f30358d435a0134"
    )
    assert smoke_evidence["runtime_subject"] == "f8a0f3c-b2-readiness-runtime-only"
    assert smoke_evidence["callbacks"] == ["running", "completed"]
    assert smoke_evidence["timings"]["sandbox_total_latency_ms"] == 3774
    assert smoke_evidence["checks"] == {
        "check_docker_socket": True,
        "check_workspace_write": True,
        "check_executor_health": True,
        "check_callback_stream": True,
        "check_cancel_stops_container": True,
        "check_platform_runtime_evidence": True,
        "check_platform_hardening_evidence": True,
        "check_no_secret_leakage": True,
    }
    assert smoke_evidence["does_not_close_b2_gate"] is True

    assert readiness["runtime_acceptance"]["prd_b2_g7_requirements_not_yet_verified"] == [
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
        "B2 readiness still reports resource limits, egress, and security options as open",
        "operator issue comment records source/runtime subject, command result, and residual caveats",
    ]
    assert readiness["hardening_policy_contracts"]["resource_limits_policy_evidence"]["status"] == (
        "recorded_source_policy_contract"
    )
    assert readiness["hardening_policy_contracts"]["egress_policy_evidence"]["remaining_runtime_gap"] == (
        "egress_runtime_hardening_evidence"
    )
    assert readiness["hardening_policy_contracts"]["security_options_evidence"][
        "does_not_claim_docker_sandbox_production_hardening"
    ] is True

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "211 verified" in serialized
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
    assert runtime["runtime_probe_results_schema_version"] == generator.RUNTIME_PROBE_RESULTS_SCHEMA_VERSION
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
            "bounded_error_projection.safe_admin_runtime_projection",
        ],
        "egress_policy": [
            "default_deny_outbound=true",
            "platform_allowlist_enforced=true",
            "callback_exception_scoped_to_run_token=true",
            "denied_egress_redacted=true",
            "policy_source=platform_policy",
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
    assert "- resource_limits_policy_evidence" in open_gap_section
    assert "- egress_policy_evidence" in open_gap_section
    assert "- security_options_evidence" in open_gap_section
    assert "- b2_runtime_evidence_review_against_merged_source" in open_gap_section
    assert "- rollback_assumptions_evidence" not in open_gap_section
    assert "- b2_issue_review_and_closure_evidence" not in open_gap_section
    assert "## Closed Gate Boundary Gaps" in markdown
    assert "- b2_issue_review_and_closure_evidence" in markdown.split("## Closed Gate Boundary Gaps", 1)[1]
    assert "## Gate Boundary Evidence" in markdown
    assert "### B2 Issue Closure Evidence" in markdown
    assert "### B2 Runtime Evidence Review Against Merged Source" in markdown
    assert "runtime_affecting_delta_requires_fresh_211_smoke" in markdown
    assert "deploy current main to 211 and rerun scripts/verify_sandbox_runtime_211.py before closing this gap" in markdown
    assert "docs/release-evidence/backend-stage-closures/b2-sandbox" in markdown
    assert "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json" in markdown
    assert "does_not_close_broader_b2_g7_production_hardening_gate" in markdown
    assert "- b2_211_real_sandbox_smoke" not in markdown
    assert "- b2_reviewed_release_evidence" not in markdown
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
    assert "hardening.resource_limits" in markdown
    assert "hardening.egress_policy" in markdown
    assert "hardening.security_options" in markdown
    assert "runtime probe results schema: `ai-platform.sandbox-runtime-probe-results.v1`" in markdown
    assert "`--runtime-probe-results-file`" in markdown
    assert "resource_limits_policy_evidence" in markdown
    assert "## Hardening Policy Contracts" in markdown
    assert "resource_limits_runtime_hardening_evidence" in markdown
    assert "egress_runtime_hardening_evidence" in markdown
    assert "security_options_runtime_hardening_evidence" in markdown
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
    assert payload["status"] == "runtime_acceptance_recorded"
    assert payload["runtime_acceptance"]["status"] == "verified_211_runtime_acceptance"
    assert payload["runtime_acceptance"]["status_label_after_smoke_before_review"] == "local partial"
    assert payload["runtime_acceptance"]["reviewed_evidence_required_for_211_verified"] is True
    assert payload["open_gaps"] == [
        "b2_runtime_evidence_review_against_merged_source",
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert payload["closed_gate_boundary_gaps"] == ["b2_issue_review_and_closure_evidence"]
    assert payload["gate_boundary_evidence"]["b2_runtime_evidence_review_against_merged_source"]["status"] == (
        "runtime_affecting_delta_requires_fresh_211_smoke"
    )
    assert payload["gate_boundary_evidence"]["b2_runtime_evidence_review_against_merged_source"]["closed_gap"] is None
    assert payload["broader_b2_g7_open_requirements"] == [
        "resource_limits_policy_evidence",
        "egress_policy_evidence",
        "security_options_evidence",
    ]
    assert payload["hardening_policy_contracts"]["resource_limits_policy_evidence"]["evidence_level"] == (
        "source_contract"
    )
    assert payload["hardening_policy_contracts"]["egress_policy_evidence"]["remaining_runtime_gap"] == (
        "egress_runtime_hardening_evidence"
    )
    assert payload["hardening_policy_contracts"]["security_options_evidence"][
        "does_not_close_broader_b2_g7_gate"
    ] is True
    assert payload["rollback_assumptions"]["closed_gap"] == "rollback_assumptions_evidence"
    assert payload["gate_boundary_evidence"]["b2_issue_review_and_closure_evidence"]["status"] == (
        "recorded_issue_closure_evidence"
    )
    assert "callback-secret" not in result.stdout
    assert "sandbox_workdir" not in result.stdout
    assert "gate closable" not in result.stdout.lower()


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
        "record reviewed local issue-closure evidence for #89 before closing this boundary"
    )


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

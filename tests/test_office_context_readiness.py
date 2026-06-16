import json
import os
import subprocess
import sys

from app.office_context_readiness import (
    build_office_context_readiness,
    render_office_context_readiness_markdown,
)


def _valid_executor_context_pack_evidence() -> dict:
    return {
        "schema_version": "ai-platform.executor-context-pack-211.v1",
        "source_schema_version": "ai-platform.executor-context-pack.v1",
        "run_id": "run_f417cf0ac1104d5884ed58e0f111fd00",
        "runtime_mode": "worker",
        "evidence_strength": "live_worker_run_payload",
        "does_not_close_211_acceptance": False,
        "runtime_acceptance_requires_real_run_payload": False,
        "runtime_run_payload_verified": True,
        "source_functions": [
            "app.repositories.get_context_snapshot_for_worker",
            "app.context_builder.executor_context_pack_from_snapshot",
            "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
            "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
            "app.worker._context_snapshot_ref_from_row",
        ],
        "prompt_checks": {
            "bounded_summary_present": True,
            "context_pack_version_present": True,
            "context_pack_generated_at_present": True,
            "raw_storage_identifiers_absent": True,
            "sandbox_runtime_paths_absent": True,
            "executor_private_content_absent": True,
            "long_term_memory_read_false": True,
        },
        "scope_checks": {
            "tenant_id_scoped": True,
            "workspace_id_scoped": True,
            "user_id_scoped": True,
            "session_id_scoped": True,
            "source_run_artifact_count_positive": True,
            "source_run_artifact_scope_verified": True,
        },
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "ordinary_user_high_risk_sandbox_allowed": False,
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "long_term_cross_session_memory_enabled": False,
            "public_projection_only_for_ordinary_users": True,
        },
        "live_run_checks": {
            "run_row_loaded": True,
            "context_snapshot_id_present": True,
            "scoped_context_snapshot_loaded": True,
            "worker_context_ref_rebuilt_from_db_snapshot": True,
            "context_pack_schema_present": True,
        },
        "runtime_evidence": {
            "live_worker_run_payload": True,
            "run_row_loaded": True,
            "context_snapshot_id_present": True,
            "scoped_context_snapshot_loaded": True,
            "worker_context_ref_rebuilt_from_db_snapshot": True,
            "prompt_includes_bounded_summary": True,
            "prompt_includes_context_pack_version": True,
            "prompt_includes_context_pack_generated_at": True,
            "raw_storage_identifiers_absent": True,
            "sandbox_runtime_paths_absent": True,
            "executor_private_content_absent": True,
            "long_term_memory_read_false": True,
            "source_run_artifact_scope_tenant_workspace_user_session": True,
            "source_run_artifact_count_positive": True,
            "fresh_generated_at": True,
            "source_functions_bound_to_current_runtime": True,
        },
        "public_context_summary": {
            "execution_tier": "document_worker",
            "context_pack_version": "v1",
            "context_pack_generated_at_present": True,
            "referenced_material_counts": {
                "message_count": 1,
                "file_count": 1,
                "artifact_count": 1,
                "memory_record_count": 0,
            },
            "input_keys": ["attachments", "message"],
        },
    }


def _valid_sandbox_runtime_evidence() -> dict:
    run_id = "sandbox-pr44-mcp-final-20260616083439"
    return {
        "schema_version": "ai-platform.sandbox-runtime-211.v1",
        "run_id": run_id,
        "executor_url": "http://127.0.0.1:18000",
        "runtime_mode": "platform",
        "sandbox_provider": "docker",
        "executed_task": True,
        "callback_auth": "token",
        "callbacks": [
            {"run_id": run_id, "status": "running", "progress": 5},
            {"run_id": run_id, "status": "completed", "progress": 100},
        ],
        "cancel_stops_container": True,
        "cancelled_container_id": "78e0c17d4210be4f1429b616101bffd50d7a228b270f60d1dd8655ed2f3b6405",
        "timings": {
            "schema_version": "ai-platform.sandbox-latency-split.v1",
            "sandbox_lease_acquire_latency_ms": 2405,
            "sandbox_container_cold_start_latency_ms": 1573,
            "sandbox_healthcheck_latency_ms": 821,
            "sandbox_executor_dispatch_latency_ms": 54,
            "executor_model_latency_ms": 40,
            "document_processing_latency_ms": 0,
            "sandbox_cleanup_latency_ms": 1114,
            "sandbox_total_latency_ms": 3575,
        },
        "hardening": {
            "lease_isolation": {
                "evidence_class": "live_platform_probe",
                "recorded_lease_id": f"lease-{run_id}",
                "released_lease_id": f"lease-{run_id}",
                "release_reason": "dispatch_completed",
                "host_paths_redacted": True,
            },
            "workspace_isolation": {
                "evidence_class": "live_platform_probe",
                "workspace_container_path": "/workspace",
                "inputs_container_path": "/workspace/inputs",
                "host_paths_redacted": True,
                "marker_path_is_container_path": True,
            },
            "cleanup": {
                "evidence_class": "live_platform_probe",
                "ephemeral_container_removed": True,
                "cancel_probe_container_removed": True,
                "active_lease_released": True,
            },
            "resource_timeout": {
                "evidence_class": "source_regression_guard",
                "max_seconds_enforced": True,
                "timeout_error_code": "executor_health_timeout",
                "failed_container_removed": True,
                "source_regression_tests": [
                    "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
                    "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout",
                ],
            },
            "failure_fallback": {
                "evidence_class": "source_regression_guard",
                "dispatch_failure_stops_container": True,
                "lease_record_failure_stops_container": True,
                "db_lease_not_released_when_stop_fails": True,
                "source_regression_tests": [
                    "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                    "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                    "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
                ],
            },
            "cached_lease_revalidation": {
                "evidence_class": "source_regression_guard",
                "cached_lease_revalidates_scope_labels": True,
                "scope_mismatch_fails_closed": True,
                "tenant_workspace_user_session_checked": True,
                "source_regression_tests": [
                    "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels",
                ],
            },
        },
        "non_expansion_invariants": {
            "ordinary_user_high_risk_sandbox_allowed": False,
            "admin_or_allowlist_only": True,
            "production_concurrency_defaults_raised": False,
            "docker_sandbox_production_hardening_claimed": False,
            "ordinary_user_multi_agent_allowed": False,
        },
    }


def _write_office_runtime_entry(
    repo_root,
    *,
    evidence_id: str,
    artifact_kind: str,
    verifier: str,
    runtime_key: str,
    runtime_payload: dict,
    verifier_checks: list[str],
    evidence_dir_name: str = "pr44",
    commit_sha: str = "e421210c9dc510e7afbdc9ebdf8d4f7601f11cb1",
    runtime_subject_commit_sha: str = "e421210c9dc510e7afbdc9ebdf8d4f7601f11cb1",
    pr_refs: list[str] | None = None,
    source_branch: str = "codex/issue22-sandbox-latency-split",
    runtime_source_marker: str = "pr44-s2-verifier-20260616083334",
    image: str = "ai-platform:pr44-s2-verifier-20260616083334",
    source_tree_dirty: bool = True,
) -> None:
    evidence_dir = repo_root / "docs" / "release-evidence" / "office-context-runtime" / evidence_dir_name
    evidence_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": evidence_id,
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": runtime_subject_commit_sha,
        "gate": "G6/G9/#22 Office Context Pack Architecture",
        "issue_refs": ["#22"],
        "pr_refs": pr_refs if pr_refs is not None else ["#44"],
        "artifact_kind": artifact_kind,
        "captured_at": "2026-06-16T08:36:40+08:00",
        "source_ref": {
            "branch": source_branch,
            "runtime_source_marker": runtime_source_marker,
            "image": image,
            "containers": ["ai-platform-api", "ai-platform-worker"],
            "source_tree_dirty": source_tree_dirty,
            "compose_env_source": "external 211 env file supplied through compose --env-file; values not copied",
        },
        "evidence_ref": {
            "verifier": verifier,
            "result": "ok:true",
            "runtime_checks": {
                runtime_key: runtime_payload,
                "verifier_checks": [
                    {"name": check_name, "passed": True} for check_name in verifier_checks
                ],
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
        "review_notes": [
            "Reviewed 211 MCP runtime smoke evidence for PR #44 office context runtime acceptance.",
            "This closes only the named #22 runtime evidence gap and does not close G6, G9, production Docker sandbox hardening, ordinary-user multi-agent, or packaged frontend acceptance.",
        ],
        "open_followups": [
            "G6/G9 gate closure still requires broader governance, observability, frontend, and production-hardening gates.",
        ],
    }
    (evidence_dir / f"{evidence_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_synthetic_valid_office_runtime_acceptance_entries(repo_root) -> None:
    _write_office_runtime_entry(
        repo_root,
        evidence_id="2026-06-16-211-office-context-pr44-executor-context-pack-runtime-acceptance",
        artifact_kind="executor_context_pack_211_acceptance",
        verifier="scripts/verify_executor_context_pack_211.py",
        runtime_key="executor_context_pack_211_acceptance",
        runtime_payload=_valid_executor_context_pack_evidence(),
        verifier_checks=[
            "check_executor_context_pack_evidence",
            "check_no_secret_leakage",
        ],
    )
    _write_office_runtime_entry(
        repo_root,
        evidence_id="2026-06-16-211-office-context-pr44-sandbox-latency-split-runtime-acceptance",
        artifact_kind="sandbox_cold_start_latency_split_211_acceptance",
        verifier="scripts/verify_sandbox_runtime_211.py",
        runtime_key="sandbox_cold_start_latency_split_211_acceptance",
        runtime_payload=_valid_sandbox_runtime_evidence(),
        verifier_checks=[
            "check_docker_socket",
            "check_workspace_write",
            "check_executor_health",
            "check_callback_stream",
            "check_cancel_stops_container",
            "check_platform_runtime_evidence",
            "check_platform_hardening_evidence",
            "check_no_secret_leakage",
        ],
    )


def test_office_context_readiness_defines_safe_context_pack_contract_without_enabling_runtime(tmp_path):
    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["schema_version"] == "ai-platform.office-context-pack-readiness.v1"
    assert readiness["gate"] == "G6/G9/#22 Office Context Pack Architecture"
    assert readiness["status"] == "partial_blocked"
    assert readiness["issue"] == "#22"
    assert readiness["policy"] == {
        "default_office_execution_tier": "sdk_only_writing",
        "lightweight_office_tasks_start_sandbox_by_default": False,
        "ordinary_user_policy": "public_projection_only",
        "long_term_memory_policy": "fail_closed_until_policy_and_acceptance",
        "does_not_expand_multi_agent_beta": True,
    }
    assert readiness["implemented_controls"] == [
        "source_level_context_pack_contract",
        "context_snapshot_public_provenance_projection_contract",
        "executor_context_pack_prompt_injection_source_tests",
        "source_level_context_pack_persistence_and_versioning",
        "user_visible_context_provenance_api_projection_source_tests",
        "frontend_context_provenance_playback_source_tests",
        "office_execution_tier_router_source_tests",
        "document_centric_followup_state_source_tests",
        "sandbox_cold_start_latency_split_source_contract",
        "sandbox_runtime_hardening_source_verifier_contract",
        "sandbox_cached_lease_scope_revalidation_source_tests",
    ]
    assert "persistence/versioning" in readiness["evidence_policy"]
    assert "versioned persistence" not in readiness["evidence_policy"]
    assert "211 executor context-pack" in readiness["evidence_policy"]
    assert "does not close `executor_context_pack_211_acceptance`" in readiness["evidence_policy"]
    assert "packaged frontend acceptance" in readiness["evidence_policy"]
    assert "document-centric follow-up state" in readiness["evidence_policy"]
    assert "sandbox latency split runtime evidence" in readiness["evidence_policy"]
    assert "resource/timeout/cleanup/fallback" in readiness["evidence_policy"]
    assert "cached lease scope revalidation" in readiness["evidence_policy"]

    context_pack = readiness["context_pack_contract"]
    assert context_pack["bounded_summary_required"] is True
    assert context_pack["allowed_sources"] == [
        "uploaded_source_documents",
        "previous_generated_artifacts",
        "user_instructions",
        "department_templates",
        "terminology_glossary",
        "meeting_notes",
        "accepted_style_preferences",
    ]
    assert context_pack["user_visible_projection"] == [
        "referenced_materials",
        "used_context_summary",
        "latest_artifact_version",
        "execution_tier",
        "context_pack_version",
        "context_pack_generated_at",
    ]
    assert context_pack["forbidden_projection_terms"] == [
        "executor_private_payload",
        "raw_storage_key",
        "sandbox_workdir",
        "secret_like_values",
        "absolute_runtime_paths",
    ]

    tier_ids = [tier["id"] for tier in readiness["execution_tiers"]]
    assert tier_ids == ["sdk_only_writing", "document_worker", "heavy_sandbox"]
    assert readiness["execution_tiers"][0]["uses_sandbox_by_default"] is False
    assert readiness["execution_tiers"][0]["task_examples"] == [
        "rewrite",
        "summarize",
        "translate",
        "proposal_followup",
    ]
    assert readiness["execution_tiers"][2]["uses_sandbox_by_default"] is True
    assert "script_execution" in readiness["execution_tiers"][2]["task_examples"]

    sandbox_latency = readiness["sandbox_latency_observability"]
    assert sandbox_latency == {
        "status": "source_contract_defined_runtime_acceptance_required",
        "applies_to_execution_tiers": ["heavy_sandbox"],
        "required_metric_fields": [
            "sandbox_lease_acquire_latency_ms",
            "sandbox_container_cold_start_latency_ms",
            "sandbox_healthcheck_latency_ms",
            "sandbox_executor_dispatch_latency_ms",
            "executor_model_latency_ms",
            "document_processing_latency_ms",
            "sandbox_cleanup_latency_ms",
            "sandbox_total_latency_ms",
        ],
        "must_not_hide_cold_start_in_executor_latency": True,
        "runtime_acceptance_required": "211_sandbox_latency_split_smoke",
    }
    smoke_contract = readiness["sandbox_runtime_smoke_contract"]
    assert smoke_contract == {
        "schema_version": "ai-platform.sandbox-runtime-smoke-contract.v1",
        "target": "211_docker_capable_host",
        "generator_script": "scripts/generate_sandbox_runtime_evidence_211.py",
        "verifier_script": "scripts/verify_sandbox_runtime_211.py",
        "runtime_mode": "platform",
        "sandbox_provider": "docker",
        "docker_cmd": "sudo -n docker",
        "cancel_probe_image": "ai-platform:local",
        "required_checks": [
            "check_docker_socket",
            "check_workspace_write",
            "check_executor_health",
            "check_callback_stream",
            "check_cancel_stops_container",
            "check_platform_runtime_evidence",
            "check_platform_hardening_evidence",
            "check_no_secret_leakage",
        ],
        "required_evidence_sections": [
            "timings",
            "hardening",
            "hardening.evidence_class",
            "non_expansion_invariants",
        ],
        "non_expansion_invariants": {
            "ordinary_user_high_risk_sandbox_allowed": False,
            "admin_or_allowlist_only": True,
            "production_concurrency_defaults_raised": False,
            "docker_sandbox_production_hardening_claimed": False,
            "ordinary_user_multi_agent_allowed": False,
        },
        "acceptance_gap": "sandbox_cold_start_latency_split_211_acceptance",
    }
    executor_contract = readiness["executor_context_pack_runtime_acceptance_contract"]
    assert executor_contract == {
        "schema_version": "ai-platform.executor-context-pack-runtime-acceptance.v1",
        "target": "211_api_worker_runtime",
        "generator_script": "scripts/generate_executor_context_pack_evidence_211.py",
        "verifier_script": "scripts/verify_executor_context_pack_211.py",
        "source_schema_version": "ai-platform.executor-context-pack.v1",
        "source_probe_evidence_strength": "source_probe_on_target_runtime",
        "required_live_evidence_strength": "live_worker_run_payload",
        "does_not_close_211_acceptance": True,
        "runtime_acceptance_requires_real_run_payload": True,
        "required_live_evidence_sections": [
            "live_run_checks",
            "runtime_evidence",
            "prompt_checks",
            "scope_checks",
            "non_expansion_invariants",
        ],
        "source_functions": [
            "app.repositories.get_context_snapshot_for_worker",
            "app.context_builder.executor_context_pack_from_snapshot",
            "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
            "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
            "app.worker._context_snapshot_ref_from_row",
        ],
        "required_runtime_evidence": [
            "live_worker_run_payload",
            "run_row_loaded",
            "context_snapshot_id_present",
            "scoped_context_snapshot_loaded",
            "worker_context_ref_rebuilt_from_db_snapshot",
            "prompt_includes_bounded_summary",
            "prompt_includes_context_pack_version",
            "prompt_includes_context_pack_generated_at",
            "raw_storage_identifiers_absent",
            "sandbox_runtime_paths_absent",
            "executor_private_content_absent",
            "long_term_memory_read_false",
            "source_run_artifact_scope_tenant_workspace_user_session",
            "source_run_artifact_count_positive",
            "fresh_generated_at",
            "source_functions_bound_to_current_runtime",
        ],
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "ordinary_user_high_risk_sandbox_allowed": False,
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "long_term_cross_session_memory_enabled": False,
            "public_projection_only_for_ordinary_users": True,
        },
        "acceptance_gap": "executor_context_pack_211_acceptance",
        "does_not_close_g6_g9": True,
    }

    assert readiness["open_gaps"] == [
        "executor_context_pack_211_acceptance",
        "sandbox_cold_start_latency_split_211_acceptance",
    ]
    assert readiness["non_goals"] == [
        "do_not_start_docker_sandbox_for_lightweight_writing_by_default",
        "do_not_expose_raw_storage_keys_or_executor_private_payloads",
        "do_not_enable_long_term_cross_session_memory_by_default",
        "do_not_expand_g8_g10_multi_agent_to_ordinary_users",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "storage_key" not in serialized.replace("raw_storage_key", "")
    assert "s3://private" not in serialized
    assert "/tmp/private" not in serialized
    assert "c:\\users" not in serialized
    assert "sk-secret" not in serialized
    assert "callback-token" not in serialized


def test_office_context_readiness_closes_runtime_gaps_with_synthetic_valid_reviewed_211_evidence(tmp_path):
    _write_synthetic_valid_office_runtime_acceptance_entries(tmp_path)

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["open_gaps"] == []
    assert readiness["closed_runtime_gaps"] == [
        "executor_context_pack_211_acceptance",
        "sandbox_cold_start_latency_split_211_acceptance",
    ]
    assert readiness["does_not_close_g6_g9"] is True
    executor_evidence = readiness["runtime_acceptance_evidence"]["executor_context_pack_211_acceptance"]
    assert executor_evidence == {
        "status": "verified_211_runtime_acceptance",
        "artifact_kind": "executor_context_pack_211_acceptance",
        "evidence_id": "2026-06-16-211-office-context-pr44-executor-context-pack-runtime-acceptance",
        "path": (
            "docs/release-evidence/office-context-runtime/pr44/"
            "2026-06-16-211-office-context-pr44-executor-context-pack-runtime-acceptance.json"
        ),
        "verifier": "scripts/verify_executor_context_pack_211.py",
        "runtime_subject": "pr44-s2-verifier-20260616083334",
        "run_id": "run_f417cf0ac1104d5884ed58e0f111fd00",
        "runtime_mode": "worker",
        "evidence_strength": "live_worker_run_payload",
        "runtime_run_payload_verified": True,
        "does_not_close_g6_g9": True,
    }
    sandbox_evidence = readiness["runtime_acceptance_evidence"][
        "sandbox_cold_start_latency_split_211_acceptance"
    ]
    assert sandbox_evidence["status"] == "verified_211_runtime_acceptance"
    assert sandbox_evidence["artifact_kind"] == "sandbox_cold_start_latency_split_211_acceptance"
    assert sandbox_evidence["runtime_subject"] == "pr44-s2-verifier-20260616083334"
    assert sandbox_evidence["run_id"] == "sandbox-pr44-mcp-final-20260616083439"
    assert sandbox_evidence["runtime_mode"] == "platform"
    assert sandbox_evidence["sandbox_provider"] == "docker"
    assert sandbox_evidence["timings"]["sandbox_container_cold_start_latency_ms"] == 1573
    assert sandbox_evidence["timings"]["sandbox_healthcheck_latency_ms"] == 821
    assert sandbox_evidence["timings"]["executor_model_latency_ms"] == 40
    assert sandbox_evidence["hardening_evidence"] == {
        "cached_lease_revalidation": "source_regression_guard",
        "cleanup": "live_platform_probe",
        "failure_fallback": "source_regression_guard",
        "lease_isolation": "live_platform_probe",
        "resource_timeout": "source_regression_guard",
        "workspace_isolation": "live_platform_probe",
    }
    assert sandbox_evidence["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "admin_or_allowlist_only": True,
        "production_concurrency_defaults_raised": False,
        "docker_sandbox_production_hardening_claimed": False,
        "ordinary_user_multi_agent_allowed": False,
    }
    assert readiness["policy"]["lightweight_office_tasks_start_sandbox_by_default"] is False
    assert readiness["policy"]["does_not_expand_multi_agent_beta"] is True


def test_office_context_readiness_accepts_reviewed_8e0389e_executor_context_pack_evidence(tmp_path):
    runtime_subject_sha = "8e0389ea621a57f3ded2044e410943cc0d298571"
    _write_office_runtime_entry(
        tmp_path,
        evidence_id="2026-06-17-211-office-context-8e0389e-executor-context-pack-runtime-acceptance",
        artifact_kind="executor_context_pack_211_acceptance",
        verifier="scripts/verify_executor_context_pack_211.py",
        runtime_key="executor_context_pack_211_acceptance",
        runtime_payload={
            **_valid_executor_context_pack_evidence(),
            "run_id": "run_a618c52ee5c148a185254b68e1c81b9e",
        },
        verifier_checks=[
            "check_executor_context_pack_evidence",
            "check_no_secret_leakage",
        ],
        evidence_dir_name="8e0389e-main-runtime-rebase",
        commit_sha="494243efa847a831db95539716390b7d66d60480",
        runtime_subject_commit_sha=runtime_subject_sha,
        pr_refs=[],
        source_branch="main",
        runtime_source_marker=runtime_subject_sha,
        image="ai-platform:8e0389e-main-runtime-rebase",
        source_tree_dirty=False,
    )

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == ["sandbox_cold_start_latency_split_211_acceptance"]
    assert readiness["closed_runtime_gaps"] == ["executor_context_pack_211_acceptance"]
    assert readiness["does_not_close_g6_g9"] is True
    assert readiness["policy"]["does_not_expand_multi_agent_beta"] is True
    executor_evidence = readiness["runtime_acceptance_evidence"]["executor_context_pack_211_acceptance"]
    assert executor_evidence == {
        "status": "verified_211_runtime_acceptance",
        "artifact_kind": "executor_context_pack_211_acceptance",
        "evidence_id": "2026-06-17-211-office-context-8e0389e-executor-context-pack-runtime-acceptance",
        "path": (
            "docs/release-evidence/office-context-runtime/8e0389e-main-runtime-rebase/"
            "2026-06-17-211-office-context-8e0389e-executor-context-pack-runtime-acceptance.json"
        ),
        "verifier": "scripts/verify_executor_context_pack_211.py",
        "runtime_subject": "8e0389e-main-runtime-rebase",
        "run_id": "run_a618c52ee5c148a185254b68e1c81b9e",
        "runtime_mode": "worker",
        "evidence_strength": "live_worker_run_payload",
        "runtime_run_payload_verified": True,
        "does_not_close_g6_g9": True,
    }


def test_office_context_readiness_rejects_unreviewed_runtime_evidence(tmp_path):
    _write_synthetic_valid_office_runtime_acceptance_entries(tmp_path)
    evidence_path = next(
        (
            tmp_path
            / "docs/release-evidence/office-context-runtime/pr44"
        ).glob("*executor-context-pack*.json")
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["review_status"] = "draft"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["open_gaps"] == ["executor_context_pack_211_acceptance"]
    assert readiness["closed_runtime_gaps"] == ["sandbox_cold_start_latency_split_211_acceptance"]


def test_office_context_readiness_rejects_runtime_evidence_with_source_run_input_keys(tmp_path):
    _write_synthetic_valid_office_runtime_acceptance_entries(tmp_path)
    evidence_path = next(
        (
            tmp_path
            / "docs/release-evidence/office-context-runtime/pr44"
        ).glob("*executor-context-pack*.json")
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    runtime_checks = payload["evidence_ref"]["runtime_checks"]
    executor_payload = runtime_checks["executor_context_pack_211_acceptance"]
    executor_payload["public_context_summary"]["input_keys"] = [
        "attachments",
        "copied_from_run_id",
        "message",
    ]
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == ["executor_context_pack_211_acceptance"]
    assert readiness["closed_runtime_gaps"] == ["sandbox_cold_start_latency_split_211_acceptance"]


def test_office_context_readiness_requires_pr44_runtime_evidence_binding(tmp_path):
    _write_synthetic_valid_office_runtime_acceptance_entries(tmp_path)
    evidence_root = tmp_path / "docs/release-evidence/office-context-runtime/pr44"
    for evidence_path in evidence_root.glob("*.json"):
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        payload["pr_refs"] = ["#43"]
        evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == [
        "executor_context_pack_211_acceptance",
        "sandbox_cold_start_latency_split_211_acceptance",
    ]
    assert readiness["closed_runtime_gaps"] == []
    assert readiness["runtime_acceptance_evidence"] == {}


def test_office_context_readiness_rejects_incomplete_sandbox_hardening_evidence(tmp_path):
    _write_synthetic_valid_office_runtime_acceptance_entries(tmp_path)
    evidence_path = next(
        (
            tmp_path
            / "docs/release-evidence/office-context-runtime/pr44"
        ).glob("*sandbox-latency-split*.json")
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    runtime_checks = payload["evidence_ref"]["runtime_checks"]
    sandbox_payload = runtime_checks["sandbox_cold_start_latency_split_211_acceptance"]
    sandbox_payload["hardening"].pop("cached_lease_revalidation")
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    readiness = build_office_context_readiness(repo_root=tmp_path)

    assert readiness["open_gaps"] == ["sandbox_cold_start_latency_split_211_acceptance"]
    assert readiness["closed_runtime_gaps"] == ["executor_context_pack_211_acceptance"]


def test_office_context_readiness_markdown_is_gap_first_and_operator_readable(tmp_path):
    markdown = render_office_context_readiness_markdown(build_office_context_readiness(repo_root=tmp_path))
    open_gaps_section = markdown.split("## Implemented Controls", 1)[0]

    assert "# ai-platform Office Context Pack Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "source_level_context_pack_persistence_and_versioning" in markdown
    assert "user_visible_context_provenance_api_projection_source_tests" in markdown
    assert "frontend_context_provenance_playback_source_tests" in markdown
    assert "- office_context_pack_persistence_and_versioning" not in markdown
    assert "executor_context_pack_prompt_injection_source_tests" in markdown
    assert "office_execution_tier_router_source_tests" in markdown
    assert "document_centric_followup_state_source_tests" in markdown
    assert "- document_centric_followup_state\n" not in open_gaps_section
    assert "sandbox_runtime_hardening_source_verifier_contract" in markdown
    assert "sandbox_cached_lease_scope_revalidation_source_tests" in markdown
    assert "sandbox_runtime_smoke_contract" in markdown
    assert "scripts/generate_sandbox_runtime_evidence_211.py" in markdown
    assert "scripts/verify_sandbox_runtime_211.py" in markdown
    assert "ordinary_user_high_risk_sandbox_allowed" in markdown
    assert "executor_context_pack_runtime_acceptance_contract" in markdown
    assert "ai-platform.executor-context-pack-runtime-acceptance.v1" in markdown
    assert "app.repositories.get_context_snapshot_for_worker" in markdown
    assert "app.context_builder.executor_context_pack_from_snapshot" in markdown
    assert "prompt_includes_bounded_summary" in markdown
    assert "source_run_artifact_scope_tenant_workspace_user_session" in markdown
    assert "fresh_generated_at" in markdown
    assert "source_functions_bound_to_current_runtime" in markdown
    assert "- office_execution_tier_router\n" not in open_gaps_section
    assert "sdk_only_writing" in markdown
    assert "heavy_sandbox" in markdown
    assert "raw_storage_key" in markdown
    assert "sk-secret" not in markdown
    assert "callback-token" not in markdown


def test_office_context_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["SANDBOX_CALLBACK_TOKEN"] = "callback-token"
    env["OPENAI_API_KEY"] = "sk-secret"
    result = subprocess.run(
        [sys.executable, "tools/office_context_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.office-context-pack-readiness.v1"
    assert payload["status"] == "runtime_acceptance_recorded"
    assert payload["policy"]["lightweight_office_tasks_start_sandbox_by_default"] is False
    assert "executor_context_pack_prompt_injection_source_tests" in payload["implemented_controls"]
    assert "source_level_context_pack_persistence_and_versioning" in payload["implemented_controls"]
    assert "user_visible_context_provenance_api_projection_source_tests" in payload["implemented_controls"]
    assert "frontend_context_provenance_playback_source_tests" in payload["implemented_controls"]
    assert "office_execution_tier_router_source_tests" in payload["implemented_controls"]
    assert "document_centric_followup_state_source_tests" in payload["implemented_controls"]
    assert "office_context_pack_persistence_and_versioning" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split_source_contract" in payload["implemented_controls"]
    assert "sandbox_runtime_hardening_source_verifier_contract" in payload["implemented_controls"]
    assert "sandbox_cached_lease_scope_revalidation_source_tests" in payload["implemented_controls"]
    assert payload["sandbox_latency_observability"]["must_not_hide_cold_start_in_executor_latency"] is True
    assert payload["sandbox_runtime_smoke_contract"]["runtime_mode"] == "platform"
    assert payload["sandbox_runtime_smoke_contract"]["sandbox_provider"] == "docker"
    assert payload["sandbox_runtime_smoke_contract"]["docker_cmd"] == "sudo -n docker"
    assert payload["sandbox_runtime_smoke_contract"]["non_expansion_invariants"][
        "ordinary_user_high_risk_sandbox_allowed"
    ] is False
    assert payload["executor_context_pack_runtime_acceptance_contract"]["target"] == "211_api_worker_runtime"
    assert payload["executor_context_pack_runtime_acceptance_contract"]["source_schema_version"] == (
        "ai-platform.executor-context-pack.v1"
    )
    assert (
        payload["executor_context_pack_runtime_acceptance_contract"]["source_probe_evidence_strength"]
        == "source_probe_on_target_runtime"
    )
    assert (
        payload["executor_context_pack_runtime_acceptance_contract"]["required_live_evidence_strength"]
        == "live_worker_run_payload"
    )
    assert "accepted_evidence_strength" not in payload["executor_context_pack_runtime_acceptance_contract"]
    assert payload["executor_context_pack_runtime_acceptance_contract"]["does_not_close_211_acceptance"] is True
    assert (
        payload["executor_context_pack_runtime_acceptance_contract"]["runtime_acceptance_requires_real_run_payload"]
        is True
    )
    assert "runtime_evidence" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_live_evidence_sections"]
    assert "app.repositories.get_context_snapshot_for_worker" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["source_functions"]
    assert payload["executor_context_pack_runtime_acceptance_contract"]["generator_script"] == (
        "scripts/generate_executor_context_pack_evidence_211.py"
    )
    assert payload["executor_context_pack_runtime_acceptance_contract"]["verifier_script"] == (
        "scripts/verify_executor_context_pack_211.py"
    )
    assert "prompt_includes_bounded_summary" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "live_worker_run_payload" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "scoped_context_snapshot_loaded" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "fresh_generated_at" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "source_functions_bound_to_current_runtime" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "source_run_artifact_count_positive" in payload[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert payload["executor_context_pack_runtime_acceptance_contract"]["non_expansion_invariants"][
        "long_term_cross_session_memory_enabled"
    ] is False
    assert "executor_context_pack_injection" not in payload["open_gaps"]
    assert "user_visible_context_provenance_projection" not in payload["open_gaps"]
    assert "frontend_context_provenance_acceptance" not in payload["open_gaps"]
    assert "document_centric_followup_state" not in payload["open_gaps"]
    assert "executor_context_pack_211_acceptance" not in payload["open_gaps"]
    assert "executor_context_pack_211_acceptance" in payload["closed_runtime_gaps"]
    assert "office_execution_tier_router" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split_211_acceptance" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split_211_acceptance" in payload["closed_runtime_gaps"]
    assert payload["runtime_acceptance_evidence"]["executor_context_pack_211_acceptance"]["run_id"] == (
        "run_a618c52ee5c148a185254b68e1c81b9e"
    )
    assert payload["runtime_acceptance_evidence"]["sandbox_cold_start_latency_split_211_acceptance"][
        "timings"
    ]["sandbox_container_cold_start_latency_ms"] > 0
    assert "sk-secret" not in result.stdout
    assert "callback-token" not in result.stdout

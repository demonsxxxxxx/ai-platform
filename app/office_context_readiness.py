from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.public_context_keys import public_context_input_key_findings


SCHEMA_VERSION = "ai-platform.office-context-pack-readiness.v1"
GATE_NAME = "G6/G9/#22 Office Context Pack Architecture"
_PR44_BRANCH = "codex/issue22-sandbox-latency-split"
_PR44_RUNTIME_MARKER = "pr44-s2-verifier-20260616083334"

_ALLOWED_CONTEXT_SOURCES = [
    "uploaded_source_documents",
    "previous_generated_artifacts",
    "user_instructions",
    "department_templates",
    "terminology_glossary",
    "meeting_notes",
    "accepted_style_preferences",
]

_USER_VISIBLE_PROJECTION = [
    "referenced_materials",
    "used_context_summary",
    "latest_artifact_version",
    "execution_tier",
    "context_pack_version",
    "context_pack_generated_at",
]

_FORBIDDEN_PROJECTION_TERMS = [
    "executor_private_payload",
    "raw_storage_key",
    "sandbox_workdir",
    "secret_like_values",
    "absolute_runtime_paths",
]

_EXECUTION_TIERS = [
    {
        "id": "sdk_only_writing",
        "uses_sandbox_by_default": False,
        "task_examples": [
            "rewrite",
            "summarize",
            "translate",
            "proposal_followup",
        ],
        "required_runtime_evidence": [
            "context_pack_prompt_injection",
            "model_latency_metric",
            "user_visible_context_projection",
        ],
    },
    {
        "id": "document_worker",
        "uses_sandbox_by_default": False,
        "task_examples": [
            "docx_generation",
            "pptx_generation",
            "format_conversion",
            "document_comments",
        ],
        "required_runtime_evidence": [
            "artifact_version_linkage",
            "document_processing_latency_metric",
            "cleanup_proof",
        ],
    },
    {
        "id": "heavy_sandbox",
        "uses_sandbox_by_default": True,
        "task_examples": [
            "script_execution",
            "browser_automation",
            "risky_tool_use",
            "complex_multi_tool_workflow",
        ],
        "required_runtime_evidence": [
            "sandbox_lease_policy",
            "cold_start_latency_metric",
            "sandbox_cleanup_orphan_check",
        ],
    },
]

_OPEN_GAPS = [
    "executor_context_pack_211_acceptance",
    "sandbox_cold_start_latency_split_211_acceptance",
]

_IMPLEMENTED_CONTROLS = [
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

_NON_GOALS = [
    "do_not_start_docker_sandbox_for_lightweight_writing_by_default",
    "do_not_expose_raw_storage_keys_or_executor_private_payloads",
    "do_not_enable_long_term_cross_session_memory_by_default",
    "do_not_expand_g8_g10_multi_agent_to_ordinary_users",
]

_SANDBOX_LATENCY_OBSERVABILITY = {
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

_SANDBOX_RUNTIME_SMOKE_CONTRACT = {
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

_SANDBOX_HARDENING_EVIDENCE_CLASSES = {
    "lease_isolation": "live_platform_probe",
    "workspace_isolation": "live_platform_probe",
    "cleanup": "live_platform_probe",
    "resource_timeout": "source_regression_guard",
    "failure_fallback": "source_regression_guard",
    "cached_lease_revalidation": "source_regression_guard",
}

_SANDBOX_REQUIRED_SOURCE_REGRESSION_TESTS = {
    "resource_timeout": {
        "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
        "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout",
    },
    "failure_fallback": {
        "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
        "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
        "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
    },
    "cached_lease_revalidation": {
        "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels",
    },
}

_EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT = {
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


_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_EVIDENCE_ROOT = "docs/release-evidence/office-context-runtime"


def _path_for_output(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _all_true(section: dict[str, Any], fields: list[str]) -> bool:
    return all(section.get(field) is True for field in fields)


def _verifier_checks_passed(payload: dict[str, Any], required_checks: list[str]) -> bool:
    evidence_ref = payload.get("evidence_ref")
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    checks = runtime_checks.get("verifier_checks") if isinstance(runtime_checks, dict) else None
    if not isinstance(checks, list):
        return False
    passed = {
        item.get("name")
        for item in checks
        if isinstance(item, dict) and item.get("passed") is True
    }
    return set(required_checks).issubset(passed)


def _runtime_subject(payload: dict[str, Any]) -> str:
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return ""
    image = str(source_ref.get("image") or "")
    marker = str(source_ref.get("runtime_source_marker") or "")
    if image.startswith("ai-platform:"):
        return image.removeprefix("ai-platform:")
    return marker


def _entry_is_pr44_runtime_evidence(payload: dict[str, Any]) -> bool:
    source_ref = payload.get("source_ref")
    pr_refs = payload.get("pr_refs")
    return (
        isinstance(source_ref, dict)
        and source_ref.get("branch") == _PR44_BRANCH
        and source_ref.get("runtime_source_marker") == _PR44_RUNTIME_MARKER
        and isinstance(pr_refs, list)
        and "#44" in pr_refs
    )


def _entry_has_runtime_subject_binding(payload: dict[str, Any]) -> bool:
    runtime_subject = payload.get("runtime_subject_commit_sha")
    source_ref = payload.get("source_ref")
    if not isinstance(runtime_subject, str) or not runtime_subject:
        return False
    if not isinstance(source_ref, dict):
        return False
    marker = source_ref.get("runtime_source_marker")
    if marker != runtime_subject:
        return False
    source_snapshot = source_ref.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        return False
    snapshot_runtime_subject = source_snapshot.get("runtime_subject_commit_sha")
    runtime_affecting_changes = source_snapshot.get("runtime_affecting_changes_since_runtime_subject")
    runtime_affecting_dirty_paths = source_snapshot.get("runtime_affecting_dirty_paths")
    if snapshot_runtime_subject != runtime_subject:
        return False
    if runtime_affecting_changes != []:
        return False
    if runtime_affecting_dirty_paths != []:
        return False
    return True


def _entry_is_reviewed(payload: dict[str, Any], artifact_kind: str, verifier: str) -> bool:
    evidence_ref = payload.get("evidence_ref")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == GATE_NAME
        and payload.get("artifact_kind") == artifact_kind
        and (
            _entry_is_pr44_runtime_evidence(payload)
            or (
                artifact_kind == "executor_context_pack_211_acceptance"
                and _entry_has_runtime_subject_binding(payload)
            )
        )
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") == "reviewed"
        and isinstance(evidence_ref, dict)
        and evidence_ref.get("verifier") == verifier
        and evidence_ref.get("result") == "ok:true"
        and _runtime_subject(payload) != ""
    )


def _runtime_payload(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    evidence_ref = payload.get("evidence_ref")
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    runtime_payload = runtime_checks.get(key) if isinstance(runtime_checks, dict) else None
    return runtime_payload if isinstance(runtime_payload, dict) else None


def _sandbox_hardening_sections_are_complete(hardening: dict[str, Any]) -> bool:
    for section_name, evidence_class in _SANDBOX_HARDENING_EVIDENCE_CLASSES.items():
        section = hardening.get(section_name)
        if not isinstance(section, dict) or section.get("evidence_class") != evidence_class:
            return False
        required_tests = _SANDBOX_REQUIRED_SOURCE_REGRESSION_TESTS.get(section_name)
        if required_tests is not None:
            source_tests = section.get("source_regression_tests")
            if not isinstance(source_tests, list):
                return False
            if not required_tests.issubset({item for item in source_tests if isinstance(item, str)}):
                return False
    return True


def _executor_context_evidence_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    artifact_kind = "executor_context_pack_211_acceptance"
    verifier = _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["verifier_script"]
    if not _entry_is_reviewed(payload, artifact_kind, verifier):
        return None
    if not _verifier_checks_passed(
        payload,
        [
            "check_executor_context_pack_evidence",
            "check_no_secret_leakage",
        ],
    ):
        return None
    evidence = _runtime_payload(payload, artifact_kind)
    if evidence is None:
        return None
    if (
        evidence.get("schema_version") != "ai-platform.executor-context-pack-211.v1"
        or evidence.get("source_schema_version") != _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT[
            "source_schema_version"
        ]
        or evidence.get("runtime_mode") != "worker"
        or evidence.get("evidence_strength")
        != _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["required_live_evidence_strength"]
        or evidence.get("runtime_run_payload_verified") is not True
        or evidence.get("does_not_close_211_acceptance") is not False
        or evidence.get("runtime_acceptance_requires_real_run_payload") is not False
    ):
        return None
    prompt_checks = evidence.get("prompt_checks")
    if not isinstance(prompt_checks, dict) or not _all_true(
        prompt_checks,
        [
            "bounded_summary_present",
            "context_pack_version_present",
            "context_pack_generated_at_present",
            "raw_storage_identifiers_absent",
            "sandbox_runtime_paths_absent",
            "executor_private_content_absent",
            "long_term_memory_read_false",
        ],
    ):
        return None
    scope_checks = evidence.get("scope_checks")
    if not isinstance(scope_checks, dict) or not _all_true(
        scope_checks,
        [
            "tenant_id_scoped",
            "workspace_id_scoped",
            "user_id_scoped",
            "session_id_scoped",
            "source_run_artifact_count_positive",
            "source_run_artifact_scope_verified",
        ],
    ):
        return None
    public_context_summary = evidence.get("public_context_summary")
    if not isinstance(public_context_summary, dict):
        return None
    referenced_materials = public_context_summary.get("referenced_material_counts")
    if not isinstance(referenced_materials, dict):
        return None
    artifact_count = referenced_materials.get("artifact_count")
    if not isinstance(artifact_count, int) or isinstance(artifact_count, bool) or artifact_count <= 0:
        return None
    input_keys = public_context_summary.get("input_keys")
    if not isinstance(input_keys, list) or any(not isinstance(item, str) for item in input_keys):
        return None
    _safe_input_keys, unsafe_input_keys = public_context_input_key_findings(input_keys)
    if unsafe_input_keys:
        return None
    live_run_checks = evidence.get("live_run_checks")
    if not isinstance(live_run_checks, dict) or not _all_true(
        live_run_checks,
        [
            "run_row_loaded",
            "context_snapshot_id_present",
            "scoped_context_snapshot_loaded",
            "worker_context_ref_rebuilt_from_db_snapshot",
            "context_pack_schema_present",
        ],
    ):
        return None
    runtime_evidence = evidence.get("runtime_evidence")
    if not isinstance(runtime_evidence, dict) or not _all_true(
        runtime_evidence,
        list(_EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["required_runtime_evidence"]),
    ):
        return None
    invariants = evidence.get("non_expansion_invariants")
    if invariants != _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["non_expansion_invariants"]:
        return None
    return {
        "status": "verified_211_runtime_acceptance",
        "artifact_kind": artifact_kind,
        "evidence_id": payload.get("evidence_id"),
        "path": _path_for_output(path, repo_root),
        "verifier": verifier,
        "runtime_subject": _runtime_subject(payload),
        "run_id": evidence.get("run_id"),
        "runtime_mode": evidence.get("runtime_mode"),
        "evidence_strength": evidence.get("evidence_strength"),
        "runtime_run_payload_verified": evidence.get("runtime_run_payload_verified"),
        "does_not_close_g6_g9": True,
    }


def _sandbox_runtime_evidence_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    artifact_kind = "sandbox_cold_start_latency_split_211_acceptance"
    verifier = _SANDBOX_RUNTIME_SMOKE_CONTRACT["verifier_script"]
    if not _entry_is_reviewed(payload, artifact_kind, verifier):
        return None
    if not _verifier_checks_passed(payload, list(_SANDBOX_RUNTIME_SMOKE_CONTRACT["required_checks"])):
        return None
    evidence = _runtime_payload(payload, artifact_kind)
    if evidence is None:
        return None
    if (
        evidence.get("schema_version") != "ai-platform.sandbox-runtime-211.v1"
        or evidence.get("runtime_mode") != _SANDBOX_RUNTIME_SMOKE_CONTRACT["runtime_mode"]
        or evidence.get("sandbox_provider") != _SANDBOX_RUNTIME_SMOKE_CONTRACT["sandbox_provider"]
        or evidence.get("executed_task") is not True
    ):
        return None
    timings = evidence.get("timings")
    if not isinstance(timings, dict) or timings.get("schema_version") != "ai-platform.sandbox-latency-split.v1":
        return None
    for field in [
        "sandbox_lease_acquire_latency_ms",
        "sandbox_container_cold_start_latency_ms",
        "sandbox_healthcheck_latency_ms",
        "sandbox_executor_dispatch_latency_ms",
        "executor_model_latency_ms",
        "document_processing_latency_ms",
        "sandbox_cleanup_latency_ms",
        "sandbox_total_latency_ms",
    ]:
        value = timings.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
    hardening = evidence.get("hardening")
    if not isinstance(hardening, dict):
        return None
    if not _sandbox_hardening_sections_are_complete(hardening):
        return None
    invariants = evidence.get("non_expansion_invariants")
    if invariants != _SANDBOX_RUNTIME_SMOKE_CONTRACT["non_expansion_invariants"]:
        return None
    return {
        "status": "verified_211_runtime_acceptance",
        "artifact_kind": artifact_kind,
        "evidence_id": payload.get("evidence_id"),
        "path": _path_for_output(path, repo_root),
        "verifier": verifier,
        "runtime_subject": _runtime_subject(payload),
        "run_id": evidence.get("run_id"),
        "runtime_mode": evidence.get("runtime_mode"),
        "sandbox_provider": evidence.get("sandbox_provider"),
        "timings": {field: timings[field] for field in sorted(timings) if field.endswith("_ms")},
        "hardening_evidence": {
            section_name: hardening[section_name]["evidence_class"]
            for section_name in sorted(_SANDBOX_HARDENING_EVIDENCE_CLASSES)
        },
        "non_expansion_invariants": dict(invariants),
        "does_not_close_g6_g9": True,
    }


def _runtime_acceptance_evidence(repo_root: Path) -> dict[str, dict[str, Any]]:
    evidence_root = repo_root / _RUNTIME_EVIDENCE_ROOT
    if not evidence_root.exists():
        return {}
    summaries: dict[str, dict[str, Any]] = {}
    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        executor_summary = _executor_context_evidence_summary(
            payload,
            path=path,
            repo_root=repo_root,
        )
        if executor_summary is not None:
            summaries["executor_context_pack_211_acceptance"] = executor_summary
        sandbox_summary = _sandbox_runtime_evidence_summary(
            payload,
            path=path,
            repo_root=repo_root,
        )
        if sandbox_summary is not None:
            summaries["sandbox_cold_start_latency_split_211_acceptance"] = sandbox_summary
    return summaries


def build_office_context_readiness(repo_root: Path | None = None) -> dict[str, Any]:
    """Build the #22 context-pack baseline and reviewed 211 runtime evidence status."""
    root = (repo_root or _ROOT).resolve()
    runtime_acceptance_evidence = _runtime_acceptance_evidence(root)
    open_gaps = [gap for gap in _OPEN_GAPS if gap not in runtime_acceptance_evidence]
    closed_runtime_gaps = [gap for gap in _OPEN_GAPS if gap in runtime_acceptance_evidence]
    if "executor_context_pack_211_acceptance" in closed_runtime_gaps:
        executor_evidence_policy = (
            "reviewed PR #44 211 executor context-pack runtime evidence closes only "
            "`executor_context_pack_211_acceptance`"
        )
    else:
        executor_evidence_policy = (
            "superseded PR #44 211 executor context-pack evidence does not close "
            "`executor_context_pack_211_acceptance`; fresh 211 live worker-run payload must prove "
            "positive source-run artifact scope and public input-key redaction"
        )
    if "sandbox_cold_start_latency_split_211_acceptance" in closed_runtime_gaps:
        sandbox_evidence_policy = (
            "reviewed PR #44 211 sandbox latency split runtime evidence closes only "
            "`sandbox_cold_start_latency_split_211_acceptance`"
        )
    else:
        sandbox_evidence_policy = (
            "211 sandbox latency split runtime evidence is still required for "
            "`sandbox_cold_start_latency_split_211_acceptance`"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "issue": "#22",
        "status": "partial_blocked" if open_gaps else "runtime_acceptance_recorded",
        "policy": {
            "default_office_execution_tier": "sdk_only_writing",
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "ordinary_user_policy": "public_projection_only",
            "long_term_memory_policy": "fail_closed_until_policy_and_acceptance",
            "does_not_expand_multi_agent_beta": True,
        },
        "implemented_controls": list(_IMPLEMENTED_CONTROLS),
        "context_pack_contract": {
            "bounded_summary_required": True,
            "allowed_sources": list(_ALLOWED_CONTEXT_SOURCES),
            "user_visible_projection": list(_USER_VISIBLE_PROJECTION),
            "forbidden_projection_terms": list(_FORBIDDEN_PROJECTION_TERMS),
        },
        "execution_tiers": [dict(tier) for tier in _EXECUTION_TIERS],
        "sandbox_latency_observability": {
            **_SANDBOX_LATENCY_OBSERVABILITY,
            "applies_to_execution_tiers": list(_SANDBOX_LATENCY_OBSERVABILITY["applies_to_execution_tiers"]),
            "required_metric_fields": list(_SANDBOX_LATENCY_OBSERVABILITY["required_metric_fields"]),
        },
        "sandbox_runtime_smoke_contract": {
            **_SANDBOX_RUNTIME_SMOKE_CONTRACT,
            "required_checks": list(_SANDBOX_RUNTIME_SMOKE_CONTRACT["required_checks"]),
            "required_evidence_sections": list(_SANDBOX_RUNTIME_SMOKE_CONTRACT["required_evidence_sections"]),
            "non_expansion_invariants": dict(_SANDBOX_RUNTIME_SMOKE_CONTRACT["non_expansion_invariants"]),
        },
        "executor_context_pack_runtime_acceptance_contract": {
            **_EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT,
            "source_functions": list(
                _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["source_functions"]
            ),
            "required_live_evidence_sections": list(
                _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT[
                    "required_live_evidence_sections"
                ]
            ),
            "required_runtime_evidence": list(
                _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["required_runtime_evidence"]
            ),
            "non_expansion_invariants": dict(
                _EXECUTOR_CONTEXT_PACK_RUNTIME_ACCEPTANCE_CONTRACT["non_expansion_invariants"]
            ),
        },
        "open_gaps": open_gaps,
        "closed_runtime_gaps": closed_runtime_gaps,
        "runtime_acceptance_evidence": runtime_acceptance_evidence,
        "does_not_close_g6_g9": True,
        "non_goals": list(_NON_GOALS),
        "evidence_policy": (
            "This records source-level context-pack contract, persistence/versioning, public API "
            "provenance projection, execution-tier routing source tests, and executor prompt-injection "
            "tests, plus document-centric follow-up state source tests, the sandbox latency split observability "
            "contract, resource/timeout/cleanup/fallback source verifier contract, and cached lease scope "
            f"revalidation source regression; {executor_evidence_policy}; {sandbox_evidence_policy}; "
            "these runtime evidence items do not close G6/G9, production Docker sandbox hardening, "
            "ordinary-user multi-agent, or packaged frontend acceptance."
        ),
    }


def render_office_context_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the office context-pack readiness snapshot for operator review."""
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    source_lines = "\n".join(
        f"- {source}" for source in readiness["context_pack_contract"]["allowed_sources"]
    )
    projection_lines = "\n".join(
        f"- {field}" for field in readiness["context_pack_contract"]["user_visible_projection"]
    )
    forbidden_lines = "\n".join(
        f"- {field}" for field in readiness["context_pack_contract"]["forbidden_projection_terms"]
    )
    implemented_lines = "\n".join(f"- {item}" for item in readiness["implemented_controls"])
    smoke_contract = readiness["sandbox_runtime_smoke_contract"]
    smoke_check_lines = "\n".join(f"- `{item}`" for item in smoke_contract["required_checks"])
    smoke_section_lines = "\n".join(f"- `{item}`" for item in smoke_contract["required_evidence_sections"])
    invariant_lines = "\n".join(
        f"- `{key}`: `{str(value).lower()}`"
        for key, value in smoke_contract["non_expansion_invariants"].items()
    )
    executor_contract = readiness["executor_context_pack_runtime_acceptance_contract"]
    executor_source_lines = "\n".join(f"- `{item}`" for item in executor_contract["source_functions"])
    executor_section_lines = "\n".join(
        f"- `{item}`" for item in executor_contract["required_live_evidence_sections"]
    )
    executor_evidence_lines = "\n".join(
        f"- `{item}`" for item in executor_contract["required_runtime_evidence"]
    )
    executor_invariant_lines = "\n".join(
        f"- `{key}`: `{str(value).lower()}`"
        for key, value in executor_contract["non_expansion_invariants"].items()
    )
    tier_lines = []
    for tier in readiness["execution_tiers"]:
        examples = ", ".join(tier["task_examples"])
        tier_lines.append(
            f"- `{tier['id']}`: sandbox by default `{str(tier['uses_sandbox_by_default']).lower()}`, "
            f"examples `{examples}`"
        )
    non_goal_lines = "\n".join(f"- {item}" for item in readiness["non_goals"])
    return (
        "# ai-platform Office Context Pack Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Issue: `{readiness['issue']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Implemented Controls\n\n"
        f"{implemented_lines}\n\n"
        "## Context Pack Contract\n\n"
        f"Bounded summary required: `{str(readiness['context_pack_contract']['bounded_summary_required']).lower()}`\n\n"
        "Allowed sources:\n\n"
        f"{source_lines}\n\n"
        "User-visible projection:\n\n"
        f"{projection_lines}\n\n"
        "Forbidden projection terms:\n\n"
        f"{forbidden_lines}\n\n"
        "## Execution Tiers\n\n"
        + "\n".join(tier_lines)
        + "\n\n"
        "## Non-goals\n\n"
        f"{non_goal_lines}\n\n"
        "## sandbox_runtime_smoke_contract\n\n"
        f"Target: `{smoke_contract['target']}`\n\n"
        f"Generator: `{smoke_contract['generator_script']}`\n\n"
        f"Verifier: `{smoke_contract['verifier_script']}`\n\n"
        f"Runtime mode: `{smoke_contract['runtime_mode']}`\n\n"
        f"Sandbox provider: `{smoke_contract['sandbox_provider']}`\n\n"
        f"Docker command: `{smoke_contract['docker_cmd']}`\n\n"
        f"Cancel probe image: `{smoke_contract['cancel_probe_image']}`\n\n"
        "Required checks:\n\n"
        f"{smoke_check_lines}\n\n"
        "Required evidence sections:\n\n"
        f"{smoke_section_lines}\n\n"
        "Non-expansion invariants:\n\n"
        f"{invariant_lines}\n\n"
        f"Acceptance gap: `{smoke_contract['acceptance_gap']}`\n\n"
        "## executor_context_pack_runtime_acceptance_contract\n\n"
        f"Schema: `{executor_contract['schema_version']}`\n\n"
        f"Target: `{executor_contract['target']}`\n\n"
        f"Generator: `{executor_contract['generator_script']}`\n\n"
        f"Verifier: `{executor_contract['verifier_script']}`\n\n"
        f"Source schema: `{executor_contract['source_schema_version']}`\n\n"
        f"Source probe evidence strength: `{executor_contract['source_probe_evidence_strength']}`\n\n"
        f"Required live evidence strength: `{executor_contract['required_live_evidence_strength']}`\n\n"
        "Required live evidence sections:\n\n"
        f"{executor_section_lines}\n\n"
        "Source functions:\n\n"
        f"{executor_source_lines}\n\n"
        "Required runtime evidence:\n\n"
        f"{executor_evidence_lines}\n\n"
        "Non-expansion invariants:\n\n"
        f"{executor_invariant_lines}\n\n"
        f"Acceptance gap: `{executor_contract['acceptance_gap']}`\n\n"
        f"Does not close G6/G9: `{str(executor_contract['does_not_close_g6_g9']).lower()}`\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.backend_stage_closure_evidence import find_stage_issue_closure_evidence
from app.foundation_alpha_readiness import (
    _resolve_runtime_affecting_changes_between as _resolve_source_runtime_affecting_changes_between,
)
from app.sandbox_hardening_contract import bounded_error_projection_is_safe


SCHEMA_VERSION = "ai-platform.b2-sandbox-readiness.v1"
BACKEND_STAGE = "B2 real sandbox usable"
ISSUE = "#130"
RUNTIME_ACCEPTANCE_GAP = "b2_211_real_sandbox_smoke"
REVIEWED_EVIDENCE_GAP = "b2_reviewed_release_evidence"
ISSUE_CLOSURE_GAP = "b2_issue_review_and_closure_evidence"
RUNTIME_SOURCE_REVIEW_GAP = "b2_runtime_evidence_review_against_merged_source"
GENERATOR_SCRIPT = "scripts/generate_sandbox_runtime_evidence_211.py"
VERIFIER_SCRIPT = "scripts/verify_sandbox_runtime_211.py"
RUNTIME_ACCEPTANCE_ARTIFACT_KIND = "211_sandbox_runtime_smoke"
RUNTIME_SOURCE_DELTA_REVIEW_ARTIFACT_KIND = "b2_runtime_source_delta_review"
RUNTIME_SOURCE_DELTA_REVIEW_SCHEMA = "ai-platform.b2-runtime-source-delta-review.v1"
RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA = "ai-platform.sandbox-runtime-211.v1"
RUNTIME_PROBE_RESULTS_SCHEMA_VERSION = "ai-platform.sandbox-runtime-probe-results.v1"
_RUNTIME_EVIDENCE_ROOT = "docs/release-evidence/b2-sandbox"
_RUNTIME_SOURCE_REVIEW_ROOT = "docs/release-evidence/b2-sandbox-source-review"
_B2_RUNTIME_NEUTRAL_EXACT_PATHS = {
    ".github/workflows/ai-platform-backend.yml",
    "app/b2_sandbox_readiness.py",
    "app/foundation_alpha_readiness.py",
    "docs/operations/ai-platform-gate-status.md",
    "docs/operations/opensandbox-provider-phase-status.md",
    "docs/release-evidence/README.md",
    "docs/release-evidence/foundation-alpha-poc/source-runtime-relation-manifest.json",
    "scripts/generate_sandbox_runtime_evidence_211.py",
    "scripts/verify_sandbox_runtime_211.py",
    "tools/b2_sandbox_readiness.py",
    "tests/test_b1_memory_context_readiness.py",
    "tests/test_b2_sandbox_readiness.py",
    "tests/test_foundation_alpha_readiness.py",
    "tests/test_source_authority_docs.py",
}
_B2_RUNTIME_NEUTRAL_PREFIXES = (
    "docs/release-evidence/b2-sandbox/",
    "docs/release-evidence/b2-sandbox-source-review/",
    "docs/release-evidence/b1-memory-context/",
    "docs/release-evidence/foundation-alpha-poc/",
    "docs/release-evidence/foundation-runtime-concurrency/",
    "frontend/",
)

_CLOSED_SOURCE_CONTROLS = [
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

_SOURCE_TESTS = [
    "tests/test_sandbox_container_provider.py",
    "tests/test_sandbox_runtime.py",
    "tests/test_sandbox_runtime_211_script.py",
    "tests/test_sandbox_lease_routes.py",
    "tests/test_runtime_callbacks.py",
]

_VERIFIER_REQUIRED_CHECKS = [
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

_VERIFIER_CHECK_ENTRYPOINTS = {
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

_VERIFIER_EVIDENCE_SHAPE = [
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

_VERIFIER_TIMING_FIELDS = [
    "sandbox_queue_wait_latency_ms",
    "sandbox_lease_acquire_latency_ms",
    "sandbox_container_start_latency_ms",
    "sandbox_container_cold_start_latency_ms",
    "sandbox_healthcheck_latency_ms",
    "sandbox_executor_dispatch_latency_ms",
    "executor_first_token_latency_ms",
    "executor_tool_call_latency_ms",
    "executor_model_latency_ms",
    "document_processing_latency_ms",
    "artifact_upload_latency_ms",
    "sandbox_cleanup_latency_ms",
    "sandbox_total_latency_ms",
]

_VERIFIER_HARDENING_SECTIONS = [
    "lease_isolation",
    "workspace_isolation",
    "cleanup",
    "resource_timeout",
    "failure_fallback",
    "cached_lease_revalidation",
    "resource_limits",
    "egress_policy",
    "security_options",
]

_VERIFIER_BASE_SMOKE_HARDENING_SECTIONS = [
    "lease_isolation",
    "workspace_isolation",
    "cleanup",
    "resource_timeout",
    "failure_fallback",
    "cached_lease_revalidation",
]

_HARDENING_EVIDENCE_CLASS = {
    "lease_isolation": "live_platform_probe",
    "workspace_isolation": "live_platform_probe",
    "cleanup": "live_platform_probe",
    "resource_timeout": "source_regression_guard",
    "failure_fallback": "source_regression_guard",
    "cached_lease_revalidation": "source_regression_guard",
    "resource_limits": "live_platform_probe",
    "egress_policy": "live_platform_probe",
    "security_options": "live_platform_probe",
}

_VERIFIER_REQUIRED_EVIDENCE_SECTIONS = [
    "runtime_mode=platform",
    "sandbox_provider=docker_or_opensandbox",
    "executed_task=true",
    "callback_auth=token",
    "executor.sdk_used=true",
    "executor.executor_mode=claude_agent_sdk",
    "callbacks.running_and_terminal",
    "cancel_stops_container=true",
    "timings",
    "hardening.lease_isolation",
    "hardening.workspace_isolation",
    "hardening.cleanup",
    "hardening.resource_timeout",
    "hardening.failure_fallback",
    "hardening.cached_lease_revalidation",
    "hardening.resource_limits",
    "hardening.egress_policy",
    "hardening.security_options",
    "hardening.evidence_class",
    "non_expansion_invariants",
]
_RUNTIME_PROBE_RESULTS_REQUIRED_FIELDS = [
    "schema_version",
    "run_id",
    "source=platform_runtime_probe",
    "resource_limits",
    "egress_policy",
    "security_options",
]
_RUNTIME_PROBE_RESULTS_REQUIRED_SECTION_FIELDS = {
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

_PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED = [
    "resource_limits_policy_evidence",
    "egress_policy_evidence",
    "security_options_evidence",
]

_HARDENING_POLICY_CONTRACTS = {
    "resource_limits_policy_evidence": {
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
    },
    "egress_policy_evidence": {
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
    },
    "security_options_evidence": {
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
    },
}

_ROLLBACK_ASSUMPTIONS_OPERATOR_STEPS = [
    "record current source/runtime subject, sandbox provider, image, and active lease/container counts",
    "disable governed Docker sandbox exposure for the selected workflow or restore the previous fake/test-only provider posture",
    "cancel verifier-owned active runs before stopping verifier-owned ephemeral containers",
    "run sandbox runtime cleanup for expired or same-scope active leases",
    "verify Admin Runtime sandbox overview and B2 readiness after rollback",
    "record issue comment with command result, source/runtime subject, and remaining hardening caveats",
]

_ROLLBACK_ASSUMPTIONS_PRECONDITIONS = [
    "rollback is scoped to verifier-owned or selected-workflow sandbox resources",
    "operator has current release evidence for the image and runtime subject being rolled back",
    "no broad Docker socket mount or ordinary-user high-risk sandbox exposure is enabled by this contract",
    "resource limits, egress policy, and security options remain separately visible when still open",
]

_ROLLBACK_ASSUMPTIONS_FAILURE_CONDITIONS = [
    "active same-scope sandbox lease cannot be released",
    "verifier-owned container cannot be stopped or identified safely",
    "Admin Runtime sandbox overview cannot be read by same-tenant admin",
    "B2 readiness stops reporting remaining issue, source, or hardening boundaries accurately",
]

_ROLLBACK_ASSUMPTIONS_AFTER_EVIDENCE = [
    "Admin Runtime sandbox overview shows zero verifier-owned active containers or active leases",
    "selected workflow is disabled or restored to fake/test-only provider posture",
    "orphan cleanup scan completed for same tenant/workspace/user/session/run scope",
    "B2 readiness still reports any remaining issue, source, or hardening boundary as open",
    "operator issue comment records source/runtime subject, command result, and residual caveats",
]

_NON_EXPANSION_INVARIANTS = {
    "ordinary_user_high_risk_sandbox_allowed": False,
    "admin_or_allowlist_only": True,
    "production_concurrency_defaults_raised": False,
    "docker_sandbox_production_hardening_claimed": False,
    "ordinary_user_multi_agent_allowed": False,
}

_B2_NON_EXPANSION_INVARIANTS = {
    **_NON_EXPANSION_INVARIANTS,
    "fake_provider_used_as_production_evidence": False,
}


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


def _resolve_source_tree_revision(repo_root: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        for marker_name in (".ai-platform-source-tree-commit", ".ai-platform-source-revision"):
            try:
                marker = (repo_root / marker_name).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if marker:
                return marker
        return "unknown"
    return result.stdout.strip() or "unknown"


def _runtime_subject(payload: dict[str, Any]) -> str:
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return ""
    image = str(source_ref.get("image") or "")
    marker = str(source_ref.get("runtime_source_marker") or "")
    if image.startswith("ai-platform:"):
        return image.removeprefix("ai-platform:")
    return marker


def _entry_has_runtime_subject_binding(payload: dict[str, Any], *, path: Path) -> bool:
    runtime_subject = payload.get("runtime_subject_commit_sha")
    if not isinstance(runtime_subject, str) or not runtime_subject:
        return False
    if payload.get("commit_sha") != runtime_subject:
        return False
    if path.parent.name != runtime_subject:
        return False
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return False
    if source_ref.get("branch") != "main":
        return False
    if source_ref.get("runtime_source_marker") != runtime_subject:
        return False
    if source_ref.get("source_tree_dirty") is not False:
        return False
    image = source_ref.get("image")
    if not isinstance(image, str) or not image.startswith("ai-platform:"):
        return False
    image_labels = source_ref.get("image_labels")
    if not isinstance(image_labels, dict):
        return False
    if image_labels.get("ai-platform.source_revision") != runtime_subject:
        return False
    if image_labels.get("org.opencontainers.image.revision") != runtime_subject:
        return False
    if image_labels.get("ai-platform.source_tree_commit") != runtime_subject:
        return False
    source_snapshot = source_ref.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        return False
    if source_snapshot.get("runtime_subject_commit_sha") != runtime_subject:
        return False
    if source_snapshot.get("source_tree_commit_sha") != runtime_subject:
        return False
    if source_snapshot.get("source_tree_dirty") is not False:
        return False
    if source_snapshot.get("runtime_affecting_changes_since_runtime_subject") != []:
        return False
    if source_snapshot.get("runtime_affecting_dirty_paths") != []:
        return False
    return True


def _entry_is_reviewed_b2_smoke(payload: dict[str, Any], *, path: Path) -> bool:
    evidence_ref = payload.get("evidence_ref")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == BACKEND_STAGE
        and payload.get("artifact_kind") == RUNTIME_ACCEPTANCE_ARTIFACT_KIND
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") == "reviewed"
        and _entry_has_runtime_subject_binding(payload, path=path)
        and isinstance(evidence_ref, dict)
        and evidence_ref.get("verifier") == VERIFIER_SCRIPT
        and evidence_ref.get("result") == "ok:true"
        and _runtime_subject(payload) != ""
    )


def _runtime_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    evidence_ref = payload.get("evidence_ref")
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    runtime_payload = (
        runtime_checks.get(RUNTIME_ACCEPTANCE_GAP)
        if isinstance(runtime_checks, dict)
        else None
    )
    return runtime_payload if isinstance(runtime_payload, dict) else None


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, int | float):
        return False
    return value > 0


def _resource_limits_runtime_verified(hardening: dict[str, Any], *, run_id: str) -> bool:
    section = hardening.get("resource_limits")
    if not isinstance(section, dict):
        return False
    if section.get("evidence_class") != "live_platform_probe":
        return False
    if section.get("limit_source") != "platform_request":
        return False
    for field in (
        "memory_limit_mb",
        "cpu_limit_count",
        "pids_limit",
        "process_timeout_seconds",
    ):
        if not _positive_number(section.get(field)):
            return False
    return (
        section.get("docker_inspection_verified") is True
        and section.get("over_limit_cleanup_verified") is True
        and section.get("over_limit_probe_kind") == "platform_resource_timeout"
        and section.get("over_limit_timeout_probe_seconds") == 0
        and section.get("bounded_error_projection_verified") is True
        and bounded_error_projection_is_safe(section.get("bounded_error_projection"), run_id=run_id)
    )


def _egress_policy_runtime_verified(hardening: dict[str, Any]) -> bool:
    section = hardening.get("egress_policy")
    if not isinstance(section, dict):
        return False
    if section.get("evidence_class") != "live_platform_probe":
        return False
    if section.get("policy_source") != "platform_policy":
        return False
    for field in (
        "denied_target",
        "denied_probe_error_code",
        "allowed_callback_host",
        "callback_probe_status",
    ):
        value = section.get(field)
        if not isinstance(value, str) or not value:
            return False
    if section.get("denied_probe_error_code") != "egress_denied":
        return False
    if section.get("callback_probe_status") != "delivered":
        return False
    return all(
        section.get(field) is True
        for field in (
            "default_deny_outbound",
            "platform_allowlist_enforced",
            "callback_exception_scoped_to_run_token",
            "denied_egress_redacted",
        )
    )


def _security_options_runtime_verified(hardening: dict[str, Any]) -> bool:
    section = hardening.get("security_options")
    if not isinstance(section, dict):
        return False
    if section.get("evidence_class") != "live_platform_probe":
        return False
    if section.get("privileged") is not False:
        return False
    if section.get("docker_socket_mounted") is not False:
        return False
    if section.get("workspace_mount_mode") not in {"rw", "ro"}:
        return False
    return all(
        section.get(field) is True
        for field in (
            "no_new_privileges",
            "capabilities_dropped",
            "root_filesystem_read_only_or_minimal",
        )
    )


def _hardening_runtime_evidence_status(hardening: dict[str, Any], *, run_id: str) -> dict[str, str]:
    if not (
        _resource_limits_runtime_verified(hardening, run_id=run_id)
        and _egress_policy_runtime_verified(hardening)
        and _security_options_runtime_verified(hardening)
    ):
        return {}
    return {
        "resource_limits_policy_evidence": "verified_211_runtime_acceptance",
        "egress_policy_evidence": "verified_211_runtime_acceptance",
        "security_options_evidence": "verified_211_runtime_acceptance",
    }


def _runtime_lease_projection_is_real(evidence: dict[str, Any]) -> bool:
    lease_projection = evidence.get("lease_projection")
    if lease_projection is None:
        return True
    if not isinstance(lease_projection, dict):
        return False
    provider = str(lease_projection.get("provider") or "")
    if provider == "fake":
        return False
    if provider and provider not in {"docker", "opensandbox"}:
        return False
    if provider and provider != evidence.get("sandbox_provider"):
        return False
    lease_payload = lease_projection.get("lease_payload")
    if not isinstance(lease_payload, dict):
        return True
    source = str(lease_payload.get("source") or "")
    evidence_class = str(lease_payload.get("evidence_class") or "")
    return source != "sdk_only_lifecycle_placeholder" and evidence_class != "sdk_only_lifecycle_placeholder"


def _b2_smoke_evidence_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    if not _entry_is_reviewed_b2_smoke(payload, path=path):
        return None
    evidence = _runtime_payload(payload)
    if evidence is None:
        return None
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        return None
    hardening_check_passed = checks.get("check_platform_hardening_evidence") is True
    base_smoke_checks = [
        check for check in _VERIFIER_REQUIRED_CHECKS if check != "check_platform_hardening_evidence"
    ]
    if not all(checks.get(check) is True for check in base_smoke_checks):
        return None
    callbacks = evidence.get("callbacks")
    if callbacks != ["running", "completed"]:
        return None
    timings = evidence.get("timings")
    hardening = evidence.get("hardening")
    run_id = evidence.get("run_id")
    if not isinstance(timings, dict) or not isinstance(hardening, dict):
        return None
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    executor = evidence.get("executor")
    if not isinstance(executor, dict):
        return None
    if not all(field in timings for field in _VERIFIER_TIMING_FIELDS):
        return None
    if not all(section in hardening for section in _VERIFIER_BASE_SMOKE_HARDENING_SECTIONS):
        return None
    if (
        evidence.get("schema_version") != RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA
        or evidence.get("runtime_mode") != "platform"
        or evidence.get("sandbox_provider") not in {"docker", "opensandbox"}
        or evidence.get("executed_task") is not True
        or evidence.get("callback_auth") != "token"
        or executor.get("sdk_used") is not True
        or executor.get("executor_mode") != "claude_agent_sdk"
        or evidence.get("cancel_stops_container") is not True
        or evidence.get("does_not_close_b2_gate") is not True
        or evidence.get("redaction_scan_status") != "passed"
    ):
        return None
    if not _runtime_lease_projection_is_real(evidence):
        return None
    non_expansion_invariants = evidence.get("non_expansion_invariants")
    if not isinstance(non_expansion_invariants, dict):
        return None
    for key, expected in _NON_EXPANSION_INVARIANTS.items():
        if non_expansion_invariants.get(key) is not expected:
            return None
    summary = {
        "status": (
            "verified_211_runtime_acceptance"
            if hardening_check_passed
            else "recorded_211_runtime_smoke_hardening_open"
        ),
        "artifact_kind": RUNTIME_ACCEPTANCE_ARTIFACT_KIND,
        "captured_at": payload.get("captured_at"),
        "evidence_id": payload.get("evidence_id"),
        "path": _path_for_output(path, repo_root),
        "verifier": VERIFIER_SCRIPT,
        "runtime_subject": _runtime_subject(payload),
        "runtime_subject_commit_sha": payload.get("runtime_subject_commit_sha"),
        "run_id": run_id,
        "runtime_mode": evidence.get("runtime_mode"),
        "sandbox_provider": evidence.get("sandbox_provider"),
        "executor": dict(executor),
        "callbacks": list(callbacks),
        "timings": dict(timings),
        "checks": {check: checks.get(check) is True for check in _VERIFIER_REQUIRED_CHECKS},
        "hardening_verifier_status": "passed" if hardening_check_passed else "failed",
        "redaction_scan_status": evidence.get("redaction_scan_status"),
        "does_not_close_b2_gate": True,
    }
    hardening_runtime_evidence = _hardening_runtime_evidence_status(hardening, run_id=run_id)
    if hardening_runtime_evidence:
        summary["hardening_runtime_evidence"] = hardening_runtime_evidence
    return summary


def _runtime_acceptance_evidence(repo_root: Path) -> dict[str, dict[str, Any]]:
    evidence_root = repo_root / _RUNTIME_EVIDENCE_ROOT
    if not evidence_root.exists():
        return {}
    candidates: list[dict[str, Any]] = []
    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        summary = _b2_smoke_evidence_summary(payload, path=path, repo_root=repo_root)
        if summary is not None:
            candidates.append(summary)
    if not candidates:
        return {}
    candidates.sort(
        key=lambda summary: (
            str(summary.get("captured_at") or ""),
            str(summary.get("path") or ""),
        ),
        reverse=True,
    )
    return {RUNTIME_ACCEPTANCE_GAP: candidates[0]}


def _issue_closure_boundary_evidence(repo_root: Path) -> dict[str, Any]:
    evidence = find_stage_issue_closure_evidence(
        repo_root,
        issue=ISSUE,
        backend_stage=BACKEND_STAGE,
        closed_gap=ISSUE_CLOSURE_GAP,
    )
    if evidence is None:
        return {
            "status": "open_missing_issue_closure_evidence",
            "closed_gap": None,
            "issue": ISSUE,
            "required_next_step": f"record reviewed local issue-closure evidence for {ISSUE} before closing this boundary",
            "does_not_close_broader_b2_g7_gate": True,
        }
    return {
        "status": "recorded_issue_closure_evidence",
        "closed_gap": ISSUE_CLOSURE_GAP,
        "issue": evidence["issue"],
        "issue_state": evidence["issue_state"],
        "closed_at": evidence.get("closed_at"),
        "path": evidence["path"],
        "linked_prs": evidence["linked_prs"],
        "closure_comments": evidence["closure_comments"],
        "evidence_refs": evidence["evidence_refs"],
        "residual_caveats": evidence["residual_caveats"],
        "non_expansion_invariants": evidence["non_expansion_invariants"],
        "does_not_close_broader_b2_g7_gate": True,
    }


def _runtime_subject_commit_from_evidence(
    runtime_acceptance_evidence: dict[str, dict[str, Any]],
) -> str:
    evidence = runtime_acceptance_evidence.get(RUNTIME_ACCEPTANCE_GAP)
    if not isinstance(evidence, dict):
        return ""
    value = evidence.get("runtime_subject_commit_sha")
    return value if isinstance(value, str) else ""


def _source_delta_review_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
    runtime_subject: str,
    current_source: str,
) -> dict[str, Any] | None:
    if (
        payload.get("schema_version") != RUNTIME_SOURCE_DELTA_REVIEW_SCHEMA
        or payload.get("artifact_kind") != RUNTIME_SOURCE_DELTA_REVIEW_ARTIFACT_KIND
        or payload.get("gate") != BACKEND_STAGE
        or payload.get("review_status") != "reviewed"
        or payload.get("runtime_subject_commit_sha") != runtime_subject
        or payload.get("current_source_commit_sha") != current_source
        or payload.get("source_tree_dirty") is not False
        or payload.get("runtime_affecting_changes_since_runtime_subject") != []
        or payload.get("does_not_close_b2_gate") is not True
        or path.parent.name != current_source
    ):
        return None
    runtime_neutral_paths = payload.get("runtime_neutral_paths")
    if not isinstance(runtime_neutral_paths, list) or not all(
        isinstance(item, str) and item for item in runtime_neutral_paths
    ):
        return None
    normalized_runtime_neutral_paths = [
        item.replace("\\", "/").strip() for item in runtime_neutral_paths
    ]
    if not all(_is_b2_runtime_neutral_path(item) for item in normalized_runtime_neutral_paths):
        return None
    review_basis = payload.get("review_basis")
    if not isinstance(review_basis, dict):
        return None
    command = str(review_basis.get("command") or "")
    if runtime_subject not in command or current_source not in command:
        return None
    result = review_basis.get("result")
    if not isinstance(result, list) or not all(isinstance(item, str) and item for item in result):
        return None
    result_paths = {_source_delta_result_path(item) for item in result}
    if None in result_paths:
        return None
    if result_paths != set(normalized_runtime_neutral_paths):
        return None
    return {
        "evidence_id": payload.get("evidence_id"),
        "artifact_kind": RUNTIME_SOURCE_DELTA_REVIEW_ARTIFACT_KIND,
        "path": _path_for_output(path, repo_root),
        "runtime_subject_commit_sha": runtime_subject,
        "current_source_commit_sha": current_source,
        "runtime_neutral_paths": list(normalized_runtime_neutral_paths),
        "reviewed_at": payload.get("reviewed_at"),
        "reviewer": payload.get("reviewer"),
        "review_basis": dict(review_basis),
        "does_not_close_b2_gate": True,
    }


def _source_delta_result_path(line: str) -> str | None:
    normalized = line.strip().replace("\\", "/")
    if not normalized:
        return None
    parts = normalized.split()
    if len(parts) >= 2 and len(parts[0]) <= 3:
        return parts[-1]
    return normalized


def _reviewed_source_delta_evidence(
    repo_root: Path,
    *,
    runtime_subject: str,
    current_source: str,
) -> dict[str, Any] | None:
    if not runtime_subject or not current_source or current_source == "unknown":
        return None
    evidence_root = repo_root / _RUNTIME_SOURCE_REVIEW_ROOT / current_source
    for path in sorted(evidence_root.glob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        summary = _source_delta_review_summary(
            payload,
            path=path,
            repo_root=repo_root,
            runtime_subject=runtime_subject,
            current_source=current_source,
        )
        if summary is not None:
            return summary
    return None


def _resolve_b2_runtime_affecting_changes_between(
    base_commit: str,
    source_tree_commit: str,
) -> list[str] | None:
    changes = _resolve_source_runtime_affecting_changes_between(
        base_commit,
        source_tree_commit,
    )
    if changes is None:
        return None
    return [
        path
        for path in changes
        if not _is_b2_runtime_neutral_path(path)
    ]


def _is_b2_runtime_neutral_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if normalized in _B2_RUNTIME_NEUTRAL_EXACT_PATHS:
        return True
    return normalized.startswith(_B2_RUNTIME_NEUTRAL_PREFIXES)


def _merged_source_runtime_review(
    repo_root: Path,
    runtime_acceptance_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current_source = _resolve_source_tree_revision(repo_root)
    runtime_subject = _runtime_subject_commit_from_evidence(runtime_acceptance_evidence)
    if not runtime_subject:
        return {
            "status": "open_missing_runtime_subject_evidence",
            "closed_gap": None,
            "runtime_subject_commit_sha": "",
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": None,
            "required_next_step": "record reviewed 211 B2 sandbox smoke evidence before reviewing merged-source drift",
            "does_not_close_broader_b2_g7_gate": True,
        }
    runtime_affecting_changes = _resolve_b2_runtime_affecting_changes_between(
        runtime_subject,
        current_source,
    )
    if runtime_affecting_changes is None:
        source_delta_review = _reviewed_source_delta_evidence(
            repo_root,
            runtime_subject=runtime_subject,
            current_source=current_source,
        )
        if source_delta_review is not None:
            return {
                "status": "recorded_reviewed_source_delta_evidence",
                "closed_gap": RUNTIME_SOURCE_REVIEW_GAP,
                "runtime_subject_commit_sha": runtime_subject,
                "current_source_commit_sha": current_source,
                "runtime_affecting_changes_since_runtime_subject": [],
                "source_delta_review_evidence": source_delta_review,
                "required_next_step": "keep hardening runtime gaps open until live verifier evidence passes",
                "does_not_close_broader_b2_g7_gate": True,
            }
        return {
            "status": "open_unable_to_classify_runtime_delta",
            "closed_gap": None,
            "runtime_subject_commit_sha": runtime_subject,
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": None,
            "required_next_step": "classify runtime-affecting source delta before accepting or rerunning B2 211 sandbox smoke evidence",
            "does_not_close_broader_b2_g7_gate": True,
        }
    if runtime_affecting_changes:
        return {
            "status": "runtime_affecting_delta_requires_fresh_211_smoke",
            "closed_gap": None,
            "runtime_subject_commit_sha": runtime_subject,
            "current_source_commit_sha": current_source,
            "runtime_affecting_changes_since_runtime_subject": runtime_affecting_changes,
            "required_next_step": "deploy current main to 211 and rerun scripts/verify_sandbox_runtime_211.py before closing this gap",
            "does_not_close_broader_b2_g7_gate": True,
        }
    return {
        "status": "recorded_local_contract",
        "closed_gap": RUNTIME_SOURCE_REVIEW_GAP,
        "runtime_subject_commit_sha": runtime_subject,
        "current_source_commit_sha": current_source,
        "runtime_affecting_changes_since_runtime_subject": [],
        "required_next_step": "record issue closure evidence after final issue review",
        "does_not_close_broader_b2_g7_gate": True,
    }


def _rollback_assumptions_contract(open_hardening_runtime_gaps: list[str]) -> dict[str, Any]:
    return {
        "status": "recorded_source_operator_contract",
        "closed_gap": "rollback_assumptions_evidence",
        "does_not_close_broader_b2_g7_gate": True,
        "does_not_claim_docker_sandbox_production_hardening": True,
        "evidence_level": "source_contract",
        "operator_steps": list(_ROLLBACK_ASSUMPTIONS_OPERATOR_STEPS),
        "preconditions": list(_ROLLBACK_ASSUMPTIONS_PRECONDITIONS),
        "failure_conditions": list(_ROLLBACK_ASSUMPTIONS_FAILURE_CONDITIONS),
        "required_after_rollback_evidence": list(_ROLLBACK_ASSUMPTIONS_AFTER_EVIDENCE),
        "remaining_hardening_gaps": list(open_hardening_runtime_gaps),
        "non_expansion_invariants": {
            **dict(_B2_NON_EXPANSION_INVARIANTS),
            "department_rollout_allowed": False,
        },
    }


def _hardening_policy_contracts(
    open_hardening_runtime_gaps: list[str],
) -> dict[str, dict[str, Any]]:
    return {
        gap: {
            "status": "recorded_source_policy_contract",
            "evidence_level": "source_contract",
            "does_not_close_broader_b2_g7_gate": True,
            "does_not_claim_docker_sandbox_production_hardening": True,
            "required_controls": list(contract["required_controls"]),
            "runtime_evidence_required": (
                list(contract["runtime_evidence_required"])
                if gap in open_hardening_runtime_gaps
                else []
            ),
            "remaining_runtime_gap": (
                contract["remaining_runtime_gap"]
                if gap in open_hardening_runtime_gaps
                else None
            ),
        }
        for gap, contract in _HARDENING_POLICY_CONTRACTS.items()
    }


def build_b2_sandbox_readiness(repo_root: Path | None = None) -> dict[str, Any]:
    """Build the B2 real-sandbox readiness contract without claiming runtime closure."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    runtime_acceptance_evidence = _runtime_acceptance_evidence(root)
    b2_smoke_recorded = RUNTIME_ACCEPTANCE_GAP in runtime_acceptance_evidence
    hardening_runtime_evidence = {}
    if b2_smoke_recorded:
        hardening_runtime_evidence = runtime_acceptance_evidence[RUNTIME_ACCEPTANCE_GAP].get(
            "hardening_runtime_evidence",
            {},
        )
        if not isinstance(hardening_runtime_evidence, dict):
            hardening_runtime_evidence = {}
    closed_hardening_runtime_gaps = [
        gap
        for gap in _PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED
        if hardening_runtime_evidence.get(gap) == "verified_211_runtime_acceptance"
    ]
    open_hardening_runtime_gaps = [
        gap
        for gap in _PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED
        if gap not in closed_hardening_runtime_gaps
    ]
    runtime_acceptance_provider = (
        runtime_acceptance_evidence[RUNTIME_ACCEPTANCE_GAP].get("sandbox_provider")
        if b2_smoke_recorded
        else None
    )
    opensandbox_provider_status = "local_partial_211_smoke_required"
    if runtime_acceptance_provider == "opensandbox":
        opensandbox_provider_status = (
            "runtime_hardening_acceptance_recorded"
            if not open_hardening_runtime_gaps
            else "first_stage_runtime_smoke_recorded_hardening_open"
        )
    gate_boundary_evidence = {
        ISSUE_CLOSURE_GAP: _issue_closure_boundary_evidence(root),
        RUNTIME_SOURCE_REVIEW_GAP: _merged_source_runtime_review(
            root,
            runtime_acceptance_evidence,
        ),
    }
    closed_gate_boundary_gaps = [
        gap
        for gap, evidence in gate_boundary_evidence.items()
        if evidence.get("closed_gap") == gap
    ]
    open_gaps = [
        RUNTIME_ACCEPTANCE_GAP,
        REVIEWED_EVIDENCE_GAP,
        ISSUE_CLOSURE_GAP,
    ]
    if b2_smoke_recorded:
        open_gaps = [
            gap
            for gap in [ISSUE_CLOSURE_GAP, RUNTIME_SOURCE_REVIEW_GAP]
            if gap not in closed_gate_boundary_gaps
        ]
        open_gaps.extend(open_hardening_runtime_gaps)
    if b2_smoke_recorded and len(closed_hardening_runtime_gaps) == len(
        _PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED
    ):
        status = "runtime_hardening_acceptance_recorded"
    elif b2_smoke_recorded:
        status = "runtime_acceptance_recorded"
    else:
        status = "local_contract_ready_runtime_smoke_required"
    runtime_status = (
        "verified_211_runtime_acceptance"
        if b2_smoke_recorded
        else "missing_211_real_sandbox_smoke"
    )
    closed_runtime_gaps = (
        [RUNTIME_ACCEPTANCE_GAP, REVIEWED_EVIDENCE_GAP]
        if b2_smoke_recorded
        else []
    )
    closed_runtime_gaps.extend(closed_hardening_runtime_gaps)
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_stage": BACKEND_STAGE,
        "issue": ISSUE,
        "status": status,
        "status_label": "local partial",
        "provider_profile": {
            "provider": "docker",
            "selected_by": "platform_policy",
            "default_stack_provider": "fake",
            "first_stage_provider_adapters": {
                "opensandbox": {
                    "status": opensandbox_provider_status,
                    "role": "B2 first-stage provider adapter",
                    "does_not_close_b2": True,
                },
            },
            "user_payload_provider_selection_allowed": False,
            "fake_provider_counts_as_production_evidence": False,
            "docker_socket_default_mount_allowed": False,
            "runtime_policy": "docker_capable_host_only",
        },
        "runtime_acceptance": {
            "required": True,
            "status": runtime_status,
            "acceptance_gap": RUNTIME_ACCEPTANCE_GAP,
            "required_operator_target": "211_docker_capable_host",
            "generator_script": GENERATOR_SCRIPT,
            "verifier_script": VERIFIER_SCRIPT,
            "verifier_schema_version": "ai-platform.sandbox-runtime-211.v1",
            "docker_cmd": "sudo -n docker",
            "cancel_probe_image": "ai-platform:local",
            "runtime_probe_results_schema_version": RUNTIME_PROBE_RESULTS_SCHEMA_VERSION,
            "runtime_probe_results_generate_cli_flag": "--generate-runtime-probe-results-file",
            "runtime_probe_results_cli_flag": "--runtime-probe-results-file",
            "runtime_probe_results_environment_variable": "AI_PLATFORM_SANDBOX_RUNTIME_PROBE_RESULTS",
            "runtime_probe_results_required_fields": list(_RUNTIME_PROBE_RESULTS_REQUIRED_FIELDS),
            "runtime_probe_results_required_section_fields": {
                section: list(fields)
                for section, fields in _RUNTIME_PROBE_RESULTS_REQUIRED_SECTION_FIELDS.items()
            },
            "status_label_before_smoke": "local partial",
            "status_label_after_smoke_before_review": "local partial",
            "smoke_without_reviewed_evidence_status": "runtime_smoke_recorded_review_required",
            "status_label_after_reviewed_evidence": "local partial",
            "reviewed_evidence_required_for_211_verified": True,
            "does_not_close_b2_gate_by_itself": True,
            "verifier_required_checks": list(_VERIFIER_REQUIRED_CHECKS),
            "verifier_check_entrypoints": dict(_VERIFIER_CHECK_ENTRYPOINTS),
            "verifier_evidence_shape": list(_VERIFIER_EVIDENCE_SHAPE),
            "verifier_timing_fields": list(_VERIFIER_TIMING_FIELDS),
            "verifier_hardening_sections": list(_VERIFIER_HARDENING_SECTIONS),
            "hardening_evidence_class": dict(_HARDENING_EVIDENCE_CLASS),
            "required_non_expansion_invariants": dict(_NON_EXPANSION_INVARIANTS),
            "verifier_required_evidence_sections": list(_VERIFIER_REQUIRED_EVIDENCE_SECTIONS),
            "prd_b2_g7_requirements_not_yet_verified": list(open_hardening_runtime_gaps),
            "verifier_required_runtime_evidence": [
                "platform lease record for tenant/workspace/user/session/run",
                "Docker/equivalent launch selected by platform policy",
                "executor command/task dispatch through callback token path",
                "Claude Agent SDK execution is recorded as sdk_used=true with executor_mode=claude_agent_sdk",
                "running and terminal callback events",
                "cancel stops only verifier-owned container",
                "cleanup releases active lease and removes ephemeral container",
                "orphan scan or cleanup proof for stopped same-scope containers",
                "artifact/event return is public/admin projection safe",
                "resource limits and timeout policy are captured from platform-issued runtime evidence",
                "egress policy proves default deny, platform allowlist, scoped callback exception, and redacted denial",
                "security options prove non-privileged container posture, no Docker socket mount, no-new-privileges or equivalent, dropped/minimal capabilities, and root/workspace boundary",
                "redaction scan excludes socket, host paths, callback tokens, and secret markers",
            ],
        },
        "closed_source_controls": list(_CLOSED_SOURCE_CONTROLS),
        "source_tests": list(_SOURCE_TESTS),
        "open_gaps": open_gaps,
        "closed_runtime_gaps": closed_runtime_gaps,
        "closed_gate_boundary_gaps": closed_gate_boundary_gaps,
        "gate_boundary_evidence": gate_boundary_evidence,
        "broader_b2_g7_open_requirements": list(open_hardening_runtime_gaps),
        "hardening_policy_contracts": _hardening_policy_contracts(open_hardening_runtime_gaps),
        "rollback_assumptions": _rollback_assumptions_contract(open_hardening_runtime_gaps),
        "runtime_acceptance_evidence": runtime_acceptance_evidence,
        "non_expansion_invariants": dict(_B2_NON_EXPANSION_INVARIANTS),
        "evidence_policy": (
            "B2 remains `local partial` until runtime acceptance, source/issue review boundaries, "
            "and required hardening evidence are all complete. Reviewed fake-provider, "
            "source-regression, or partial runtime-hardening evidence by itself does not complete gate closure."
        ),
    }


def render_b2_sandbox_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B2 sandbox readiness as gap-first operator Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    closed_gate_boundary_gaps = (
        "\n".join(f"- {gap}" for gap in readiness.get("closed_gate_boundary_gaps", []))
        or "- none"
    )
    issue_closure = readiness.get("gate_boundary_evidence", {}).get(ISSUE_CLOSURE_GAP)
    runtime_review = readiness.get("gate_boundary_evidence", {}).get(RUNTIME_SOURCE_REVIEW_GAP)
    issue_closure_lines = "- none"
    if isinstance(issue_closure, dict):
        evidence_refs = issue_closure.get("evidence_refs")
        residual_caveats = issue_closure.get("residual_caveats")
        linked_prs = issue_closure.get("linked_prs")
        evidence_ref_lines = (
            "\n".join(f"- `{item}`" for item in evidence_refs)
            if isinstance(evidence_refs, list)
            else "- none"
        )
        residual_caveat_lines = (
            "\n".join(f"- `{item}`" for item in residual_caveats)
            if isinstance(residual_caveats, list)
            else "- none"
        )
        linked_pr_lines = (
            "\n".join(f"- `{item.get('url')}`" for item in linked_prs if isinstance(item, dict))
            if isinstance(linked_prs, list)
            else "- none"
        )
        issue_closure_lines = (
            f"- status: `{issue_closure.get('status')}`\n"
            f"- issue: `{issue_closure.get('issue')}`\n"
            f"- path: `{issue_closure.get('path')}`\n"
            f"- closed at: `{issue_closure.get('closed_at')}`\n"
            f"- required next step: `{issue_closure.get('required_next_step')}`\n"
            "- linked PRs:\n"
            f"{linked_pr_lines}\n"
            "- evidence refs:\n"
            f"{evidence_ref_lines}\n"
            "- residual caveats:\n"
            f"{residual_caveat_lines}\n"
            f"- does not close broader B2/G7 gate: `{str(issue_closure.get('does_not_close_broader_b2_g7_gate')).lower()}`"
        )
    runtime_review_lines = "- none"
    if isinstance(runtime_review, dict):
        runtime_delta = runtime_review.get("runtime_affecting_changes_since_runtime_subject")
        if isinstance(runtime_delta, list):
            runtime_delta_lines = "\n".join(f"- {item}" for item in runtime_delta) or "- none"
        else:
            runtime_delta_lines = "- unknown"
        runtime_review_lines = (
            f"- status: `{runtime_review.get('status')}`\n"
            f"- runtime subject commit: `{runtime_review.get('runtime_subject_commit_sha')}`\n"
            f"- current source commit: `{runtime_review.get('current_source_commit_sha')}`\n"
            "- runtime-affecting changes since runtime subject:\n"
            f"{runtime_delta_lines}\n"
            f"- required next step: `{runtime_review.get('required_next_step')}`\n"
            f"- does not close broader B2/G7 gate: `{str(runtime_review.get('does_not_close_broader_b2_g7_gate')).lower()}`"
        )
    closed_source_controls = "\n".join(
        f"- {control}" for control in readiness["closed_source_controls"]
    )
    tests = "\n".join(f"- `{test}`" for test in readiness["source_tests"])
    runtime = readiness["runtime_acceptance"]
    runtime_evidence = "\n".join(
        f"- {item}" for item in runtime["verifier_required_runtime_evidence"]
    )
    required_checks = "\n".join(f"- `{check}`" for check in runtime["verifier_required_checks"])
    required_sections = "\n".join(
        f"- `{section}`" for section in runtime["verifier_required_evidence_sections"]
    )
    probe_result_fields = "\n".join(
        f"- `{field}`" for field in runtime.get("runtime_probe_results_required_fields", [])
    ) or "- none"
    pending_prd_requirements = "\n".join(
        f"- `{item}`" for item in readiness["broader_b2_g7_open_requirements"]
    ) or "- none"
    hardening_policy_contracts = []
    for gap, contract in readiness.get("hardening_policy_contracts", {}).items():
        required_controls = "\n".join(
            f"  - `{item}`" for item in contract.get("required_controls", [])
        ) or "  - none"
        remaining_runtime_gap = contract.get("remaining_runtime_gap")
        remaining_runtime_lines = ""
        if remaining_runtime_gap:
            runtime_evidence_items = "\n".join(
                f"  - {item}" for item in contract.get("runtime_evidence_required", [])
            ) or "  - none"
            remaining_runtime_lines = (
                f"- remaining runtime gap: `{remaining_runtime_gap}`\n"
                "\nRuntime evidence still required:\n\n"
                f"{runtime_evidence_items}\n\n"
            )
        hardening_policy_contracts.append(
            f"### {gap}\n\n"
            f"- status: `{contract.get('status')}`\n"
            f"- evidence level: `{contract.get('evidence_level')}`\n"
            f"{remaining_runtime_lines}"
            f"- does not close broader B2/G7 gate: `{str(contract.get('does_not_close_broader_b2_g7_gate')).lower()}`\n"
            f"- does not claim Docker sandbox production hardening: `{str(contract.get('does_not_claim_docker_sandbox_production_hardening')).lower()}`\n\n"
            "Required controls:\n\n"
            f"{required_controls}"
        )
    hardening_policy_contract_lines = "\n\n".join(hardening_policy_contracts) or "- none"
    rollback = readiness.get("rollback_assumptions", {})
    rollback_operator_steps = "\n".join(
        f"- {item}" for item in rollback.get("operator_steps", [])
    ) or "- none"
    rollback_preconditions = "\n".join(
        f"- {item}" for item in rollback.get("preconditions", [])
    ) or "- none"
    rollback_failure_conditions = "\n".join(
        f"- {item}" for item in rollback.get("failure_conditions", [])
    ) or "- none"
    rollback_after_evidence = "\n".join(
        f"- {item}" for item in rollback.get("required_after_rollback_evidence", [])
    ) or "- none"
    invariants = "\n".join(
        f"- `{key}={str(value).lower()}`"
        for key, value in readiness["non_expansion_invariants"].items()
    )
    return (
        "# B2 Real Sandbox Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Backend stage: `{readiness['backend_stage']}`\n\n"
        f"Issue: `{readiness['issue']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Status label: `{readiness['status_label']}`\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "## Closed Gate Boundary Gaps\n\n"
        f"{closed_gate_boundary_gaps}\n\n"
        "## Gate Boundary Evidence\n\n"
        "### B2 Issue Closure Evidence\n\n"
        f"{issue_closure_lines}\n\n"
        "### B2 Runtime Evidence Review Against Merged Source\n\n"
        f"{runtime_review_lines}\n\n"
        "## Runtime Acceptance\n\n"
        f"- generator: `{runtime['generator_script']}`\n"
        f"- verifier: `{runtime['verifier_script']}`\n"
        f"- docker command: `{runtime['docker_cmd']}`\n"
        f"- cancel probe image: `{runtime['cancel_probe_image']}`\n"
        f"- smoke status before reviewed evidence: `{runtime['status_label_after_smoke_before_review']}`\n"
        f"- smoke-without-review status: `{runtime['smoke_without_reviewed_evidence_status']}`\n"
        f"- target status after reviewed evidence: `{runtime['status_label_after_reviewed_evidence']}`\n"
        f"- reviewed evidence required before elevated status: `{str(runtime['reviewed_evidence_required_for_211_verified']).lower()}`\n"
        f"- does not close B2 gate by itself: `{str(runtime['does_not_close_b2_gate_by_itself']).lower()}`\n\n"
        f"- runtime probe results schema: `{runtime.get('runtime_probe_results_schema_version')}`\n"
        f"- runtime probe results generate flag: `{runtime.get('runtime_probe_results_generate_cli_flag')}`\n"
        f"- runtime probe results flag: `{runtime.get('runtime_probe_results_cli_flag')}`\n"
        f"- runtime probe results env: `{runtime.get('runtime_probe_results_environment_variable')}`\n\n"
        "Runtime probe results required fields:\n\n"
        f"{probe_result_fields}\n\n"
        "Verifier-required checks:\n\n"
        f"{required_checks}\n\n"
        "Verifier-required evidence sections:\n\n"
        f"{required_sections}\n\n"
        "Verifier-required runtime evidence:\n\n"
        f"{runtime_evidence}\n\n"
        "PRD B2/G7 runtime hardening requirements still open:\n\n"
        f"{pending_prd_requirements}\n\n"
        "## Hardening Policy Contracts\n\n"
        f"{hardening_policy_contract_lines}\n\n"
        "## Rollback Assumptions\n\n"
        f"- status: `{rollback.get('status')}`\n"
        f"- closed gap: `{rollback.get('closed_gap')}`\n"
        f"- evidence level: `{rollback.get('evidence_level')}`\n"
        f"- does not close broader B2/G7 gate: `{str(rollback.get('does_not_close_broader_b2_g7_gate')).lower()}`\n"
        f"- does not claim Docker sandbox production hardening: `{str(rollback.get('does_not_claim_docker_sandbox_production_hardening')).lower()}`\n\n"
        "Operator steps:\n\n"
        f"{rollback_operator_steps}\n\n"
        "Preconditions:\n\n"
        f"{rollback_preconditions}\n\n"
        "Failure conditions:\n\n"
        f"{rollback_failure_conditions}\n\n"
        "Required after-rollback evidence:\n\n"
        f"{rollback_after_evidence}\n\n"
        "Non-expansion invariants:\n\n"
        f"{invariants}\n\n"
        "## Closed Source Controls\n\n"
        f"{closed_source_controls}\n\n"
        "## Source Tests\n\n"
        f"{tests}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

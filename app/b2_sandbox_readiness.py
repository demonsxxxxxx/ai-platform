from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.b2-sandbox-readiness.v1"
BACKEND_STAGE = "B2 real sandbox usable"
ISSUE = "#89"
RUNTIME_ACCEPTANCE_GAP = "b2_211_real_sandbox_smoke"
REVIEWED_EVIDENCE_GAP = "b2_reviewed_release_evidence"
ISSUE_CLOSURE_GAP = "b2_issue_review_and_closure_evidence"
GENERATOR_SCRIPT = "scripts/generate_sandbox_runtime_evidence_211.py"
VERIFIER_SCRIPT = "scripts/verify_sandbox_runtime_211.py"
RUNTIME_ACCEPTANCE_ARTIFACT_KIND = "211_sandbox_runtime_smoke"
RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA = "ai-platform.sandbox-runtime-211.v1"
_RUNTIME_EVIDENCE_ROOT = "docs/release-evidence/b2-sandbox"

_CLOSED_SOURCE_CONTROLS = [
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
    "generated_at",
    "callbacks",
    "cancel_stops_container",
    "cancelled_container_id",
    "timings",
    "hardening",
    "non_expansion_invariants",
]

_VERIFIER_TIMING_FIELDS = [
    "sandbox_lease_acquire_latency_ms",
    "sandbox_container_cold_start_latency_ms",
    "sandbox_healthcheck_latency_ms",
    "sandbox_executor_dispatch_latency_ms",
    "executor_model_latency_ms",
    "document_processing_latency_ms",
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
]

_HARDENING_EVIDENCE_CLASS = {
    "lease_isolation": "live_platform_probe",
    "workspace_isolation": "live_platform_probe",
    "cleanup": "live_platform_probe",
    "resource_timeout": "source_regression_guard",
    "failure_fallback": "source_regression_guard",
    "cached_lease_revalidation": "source_regression_guard",
}

_VERIFIER_REQUIRED_EVIDENCE_SECTIONS = [
    "runtime_mode=platform",
    "sandbox_provider=docker",
    "executed_task=true",
    "callback_auth=token",
    "callbacks.running_and_terminal",
    "cancel_stops_container=true",
    "timings",
    "hardening.lease_isolation",
    "hardening.workspace_isolation",
    "hardening.cleanup",
    "hardening.resource_timeout",
    "hardening.failure_fallback",
    "hardening.cached_lease_revalidation",
    "hardening.evidence_class",
    "non_expansion_invariants",
]

_PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED = [
    "resource_limits_policy_evidence",
    "egress_policy_evidence",
    "security_options_evidence",
    "rollback_assumptions_evidence",
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


def _runtime_subject(payload: dict[str, Any]) -> str:
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, dict):
        return ""
    image = str(source_ref.get("image") or "")
    marker = str(source_ref.get("runtime_source_marker") or "")
    if image.startswith("ai-platform:"):
        return image.removeprefix("ai-platform:")
    return marker


def _entry_has_runtime_subject_binding(payload: dict[str, Any]) -> bool:
    runtime_subject = payload.get("runtime_subject_commit_sha")
    if not isinstance(runtime_subject, str) or not runtime_subject:
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
    source_snapshot = source_ref.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        return False
    if source_snapshot.get("runtime_subject_commit_sha") != runtime_subject:
        return False
    if source_snapshot.get("source_tree_dirty") is not False:
        return False
    if source_snapshot.get("runtime_affecting_changes_since_runtime_subject") != []:
        return False
    if source_snapshot.get("runtime_affecting_dirty_paths") != []:
        return False
    return True


def _entry_is_reviewed_b2_smoke(payload: dict[str, Any]) -> bool:
    evidence_ref = payload.get("evidence_ref")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == BACKEND_STAGE
        and payload.get("artifact_kind") == RUNTIME_ACCEPTANCE_ARTIFACT_KIND
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") == "reviewed"
        and _entry_has_runtime_subject_binding(payload)
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


def _b2_smoke_evidence_summary(
    payload: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    if not _entry_is_reviewed_b2_smoke(payload):
        return None
    evidence = _runtime_payload(payload)
    if evidence is None:
        return None
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        return None
    if not all(checks.get(check) is True for check in _VERIFIER_REQUIRED_CHECKS):
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
    if not all(field in timings for field in _VERIFIER_TIMING_FIELDS):
        return None
    if not all(section in hardening for section in _VERIFIER_HARDENING_SECTIONS):
        return None
    if (
        evidence.get("schema_version") != RUNTIME_ACCEPTANCE_VERIFIER_SCHEMA
        or evidence.get("runtime_mode") != "platform"
        or evidence.get("sandbox_provider") != "docker"
        or evidence.get("executed_task") is not True
        or evidence.get("callback_auth") != "token"
        or evidence.get("cancel_stops_container") is not True
        or evidence.get("does_not_close_b2_gate") is not True
        or evidence.get("redaction_scan_status") != "passed"
    ):
        return None
    return {
        "status": "verified_211_runtime_acceptance",
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
        "callbacks": list(callbacks),
        "timings": dict(timings),
        "checks": {check: True for check in _VERIFIER_REQUIRED_CHECKS},
        "redaction_scan_status": evidence.get("redaction_scan_status"),
        "does_not_close_b2_gate": True,
    }


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


def build_b2_sandbox_readiness(repo_root: Path | None = None) -> dict[str, Any]:
    """Build the B2 real-sandbox readiness contract without claiming runtime closure."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    runtime_acceptance_evidence = _runtime_acceptance_evidence(root)
    b2_smoke_recorded = RUNTIME_ACCEPTANCE_GAP in runtime_acceptance_evidence
    open_gaps = [
        RUNTIME_ACCEPTANCE_GAP,
        REVIEWED_EVIDENCE_GAP,
        ISSUE_CLOSURE_GAP,
    ]
    if b2_smoke_recorded:
        open_gaps = [ISSUE_CLOSURE_GAP]
    status = (
        "runtime_acceptance_recorded"
        if b2_smoke_recorded
        else "local_contract_ready_runtime_smoke_required"
    )
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
            "status_label_before_smoke": "local partial",
            "status_label_after_smoke_before_review": "local partial",
            "smoke_without_reviewed_evidence_status": "runtime_smoke_recorded_review_required",
            "status_label_after_reviewed_evidence": "211 verified",
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
            "prd_b2_g7_requirements_not_yet_verified": list(
                _PRD_B2_G7_REQUIREMENTS_NOT_YET_VERIFIED
            ),
            "verifier_required_runtime_evidence": [
                "platform lease record for tenant/workspace/user/session/run",
                "Docker/equivalent launch selected by platform policy",
                "executor command/task dispatch through callback token path",
                "running and terminal callback events",
                "cancel stops only verifier-owned container",
                "cleanup releases active lease and removes ephemeral container",
                "orphan scan or cleanup proof for stopped same-scope containers",
                "artifact/event return is public/admin projection safe",
                "redaction scan excludes socket, host paths, callback tokens, and secret markers",
            ],
        },
        "closed_source_controls": list(_CLOSED_SOURCE_CONTROLS),
        "source_tests": list(_SOURCE_TESTS),
        "open_gaps": open_gaps,
        "closed_runtime_gaps": closed_runtime_gaps,
        "runtime_acceptance_evidence": runtime_acceptance_evidence,
        "non_expansion_invariants": dict(_B2_NON_EXPANSION_INVARIANTS),
        "evidence_policy": (
            "B2 can become `211 verified` only after reviewed, redacted 211 Docker/equivalent "
            "sandbox smoke evidence proves launch, command execution, callback, cancel, cleanup, "
            "orphan prevention, artifact/event return, and projection redaction for merged source. "
            "Existing fake-provider and source-regression evidence stay `local partial`."
        ),
    }


def render_b2_sandbox_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B2 sandbox readiness as gap-first operator Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {control}" for control in readiness["closed_source_controls"])
    tests = "\n".join(f"- `{test}`" for test in readiness["source_tests"])
    runtime = readiness["runtime_acceptance"]
    runtime_evidence = "\n".join(
        f"- {item}" for item in runtime["verifier_required_runtime_evidence"]
    )
    required_checks = "\n".join(f"- `{check}`" for check in runtime["verifier_required_checks"])
    required_sections = "\n".join(
        f"- `{section}`" for section in runtime["verifier_required_evidence_sections"]
    )
    pending_prd_requirements = "\n".join(
        f"- `{item}`" for item in runtime["prd_b2_g7_requirements_not_yet_verified"]
    )
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
        "## Runtime Acceptance\n\n"
        f"- generator: `{runtime['generator_script']}`\n"
        f"- verifier: `{runtime['verifier_script']}`\n"
        f"- docker command: `{runtime['docker_cmd']}`\n"
        f"- cancel probe image: `{runtime['cancel_probe_image']}`\n"
        f"- smoke status before reviewed evidence: `{runtime['status_label_after_smoke_before_review']}`\n"
        f"- smoke-without-review status: `{runtime['smoke_without_reviewed_evidence_status']}`\n"
        f"- target status after reviewed evidence: `{runtime['status_label_after_reviewed_evidence']}`\n"
        f"- reviewed evidence required for 211 verified: `{str(runtime['reviewed_evidence_required_for_211_verified']).lower()}`\n"
        f"- does not close B2 gate by itself: `{str(runtime['does_not_close_b2_gate_by_itself']).lower()}`\n\n"
        "Verifier-required checks:\n\n"
        f"{required_checks}\n\n"
        "Verifier-required evidence sections:\n\n"
        f"{required_sections}\n\n"
        "Verifier-required runtime evidence:\n\n"
        f"{runtime_evidence}\n\n"
        "PRD B2/G7 requirements not yet verifier-checked:\n\n"
        f"{pending_prd_requirements}\n\n"
        "Non-expansion invariants:\n\n"
        f"{invariants}\n\n"
        "## Closed Source Controls\n\n"
        f"{controls}\n\n"
        "## Source Tests\n\n"
        f"{tests}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

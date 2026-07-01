from __future__ import annotations

from typing import Any

from app.capacity_baseline import LOAD_TEST_GATES


SCHEMA_VERSION = "ai-platform.g7-b3-completion-audit.v1"
B3_TARGET_PROFILE_ID = "b3_10x4_sdk_subagents"
B3_REQUIRED_PROFILE_EVIDENCE = [
    "target_profile_id",
    "evidence_source",
    "observed_concurrent_sessions",
    "observed_peak_sdk_subagents_per_session",
    "sdk_subagent_fanout_measurement_ref",
    "production_concurrency_defaults_raised",
    "safe_concurrency_claimed",
    "ordinary_user_multi_agent_enabled",
]
_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
)
_SAFE_RUNTIME_ENV_KEYS = {
    "SANDBOX_CONTAINER_PROVIDER",
    "SANDBOX_EXECUTOR_IMAGE",
    "SANDBOX_EGRESS_POLICY_ENABLED",
    "MAX_ACTIVE_WORKER_RUNS",
    "MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT",
}
_BLOCKED_CAPACITY_PROFILE_STATUSES = {
    "blocked_missing_profile_evidence",
    "blocked_missing_load_test_evidence",
    "blocked_incomplete_load_test_evidence",
    "blocked_missing_admin_runtime_sections",
}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _safe_runtime_env(env: object) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    safe: dict[str, str] = {}
    for key, value in env.items():
        key_text = str(key)
        if key_text not in _SAFE_RUNTIME_ENV_KEYS:
            continue
        if any(marker in key_text.lower() for marker in _SECRET_KEY_MARKERS):
            continue
        safe[key_text] = _safe_text(value)
    return safe


def _canonical_runtime_label_commit(labels: dict[str, Any]) -> str:
    for key in (
        "ai-platform.source-revision",
        "ai-platform.runtime-subject",
        "org.opencontainers.image.revision",
        "ai-platform.source_revision",
        "ai-platform.runtime_subject",
    ):
        value = _safe_text(labels.get(key))
        if value:
            return value
    return ""


def _legacy_runtime_label_commit(labels: dict[str, Any]) -> str:
    for key in (
        "ai_platform_source_revision",
        "ai_platform_runtime_subject",
    ):
        value = _safe_text(labels.get(key))
        if value:
            return value
    return ""


def _source_runtime_mismatch(
    *,
    current_source_commit: str,
    source_marker_commit: str,
    canonical_runtime_label_commit: str,
    legacy_runtime_label_commit: str,
) -> bool:
    if not current_source_commit:
        return True
    if source_marker_commit and source_marker_commit != current_source_commit:
        return True
    if canonical_runtime_label_commit != current_source_commit:
        return True
    return bool(legacy_runtime_label_commit and legacy_runtime_label_commit != current_source_commit)


def _build_g7_audit(
    runtime_observation: dict[str, Any],
    *,
    current_source_commit: str,
) -> dict[str, Any]:
    labels = _dict(runtime_observation.get("runtime_image_labels"))
    safe_env = _safe_runtime_env(runtime_observation.get("api_env"))
    source_marker_commit = _safe_text(runtime_observation.get("source_marker_commit"))
    runtime_image = _safe_text(runtime_observation.get("runtime_image"))
    canonical_label_commit = _canonical_runtime_label_commit(labels)
    legacy_label_commit = _legacy_runtime_label_commit(labels)
    live_api_provider = safe_env.get("SANDBOX_CONTAINER_PROVIDER", "")
    blocking_reasons: list[str] = []

    if _source_runtime_mismatch(
        current_source_commit=current_source_commit,
        source_marker_commit=source_marker_commit,
        canonical_runtime_label_commit=canonical_label_commit,
        legacy_runtime_label_commit=legacy_label_commit,
    ):
        blocking_reasons.append("current_main_source_runtime_label_mismatch")
    if live_api_provider == "fake":
        blocking_reasons.append("live_api_uses_fake_sandbox_provider")
    blocking_reasons.append("reviewed_local_release_evidence_entry_missing")

    return {
        "status": "blocked" if blocking_reasons else "candidate_evidence_requires_review",
        "source_marker_commit": source_marker_commit,
        "runtime_image": runtime_image,
        "canonical_runtime_label_commit": canonical_label_commit,
        "legacy_runtime_label_commit": legacy_label_commit,
        "live_api_sandbox_provider": live_api_provider,
        "safe_runtime_env": safe_env,
        "blocking_reasons": blocking_reasons,
        "required_next_steps": [
            "reconcile current-main source marker, runtime image labels, and reviewed release-evidence binding",
            "rerun reviewed G7 Docker sandbox hardening verifier on the Docker-capable target",
            "rerun Foundation Runtime concurrency evidence for the same current runtime subject",
        ],
        "does_not_claim_production_docker_sandbox_hardening": True,
    }


def _profile_readiness_b3_profile(capacity_profile_readiness: dict[str, Any]) -> dict[str, Any]:
    profiles = capacity_profile_readiness.get("profiles")
    if not isinstance(profiles, list):
        return {}
    for profile in profiles:
        item = _dict(profile)
        if item.get("id") == B3_TARGET_PROFILE_ID:
            return item
    return {}


def _readiness_status_is_blocked(status: object) -> bool:
    return str(status or "").strip() in _BLOCKED_CAPACITY_PROFILE_STATUSES


def _missing_load_test_gates(capacity_profile_readiness: dict[str, Any]) -> tuple[list[str], bool]:
    source = _dict(capacity_profile_readiness.get("source_gate_readiness"))
    missing = source.get("missing_load_test_gates")
    if isinstance(missing, list):
        normalized = [str(gate) for gate in missing if str(gate).strip()]
        if normalized:
            return normalized, False
        if _readiness_status_is_blocked(source.get("status")):
            return list(LOAD_TEST_GATES), True
        return [], False
    return list(LOAD_TEST_GATES), False


def _missing_profile_evidence(capacity_profile_readiness: dict[str, Any]) -> tuple[list[str], bool]:
    profile = _profile_readiness_b3_profile(capacity_profile_readiness)
    missing = profile.get("missing_profile_evidence")
    if isinstance(missing, list):
        normalized = [str(field) for field in missing if str(field).strip()]
        if normalized:
            return normalized, False
        if _readiness_status_is_blocked(profile.get("status")):
            return list(B3_REQUIRED_PROFILE_EVIDENCE), True
        return [], False
    return list(B3_REQUIRED_PROFILE_EVIDENCE), False


def _build_b3_audit(capacity_profile_readiness: dict[str, Any] | None) -> dict[str, Any]:
    readiness = _dict(capacity_profile_readiness)
    missing_gates, inconsistent_gates = _missing_load_test_gates(readiness)
    missing_profile, inconsistent_profile = _missing_profile_evidence(readiness)
    blocking_reasons: list[str] = []
    if inconsistent_gates or inconsistent_profile:
        blocking_reasons.append("b3_capacity_readiness_inconsistent")
    if missing_gates:
        blocking_reasons.append("b3_recorded_load_test_gates_missing")
    if missing_profile:
        blocking_reasons.append("b3_10x4_sdk_subagents_profile_evidence_missing")
    return {
        "status": "blocked" if blocking_reasons else "operator_review_required",
        "target_profile_id": B3_TARGET_PROFILE_ID,
        "missing_recorded_load_test_gates": missing_gates,
        "missing_profile_evidence": missing_profile,
        "blocking_reasons": blocking_reasons,
        "production_default_decision": "do_not_raise_without_recorded_load_test_evidence",
        "does_not_raise_production_defaults": True,
        "does_not_enable_ordinary_user_platform_multi_run_orchestration": True,
    }


def build_g7_b3_completion_audit(
    *,
    runtime_observation: dict[str, object] | None,
    capacity_profile_readiness: dict[str, object] | None,
    current_source_commit: str,
) -> dict[str, Any]:
    """Build a gap-first G7/B3 audit without treating probes as closure evidence."""
    runtime = _dict(runtime_observation)
    source_commit = _safe_text(current_source_commit)
    g7 = _build_g7_audit(runtime, current_source_commit=source_commit)
    b3 = _build_b3_audit(capacity_profile_readiness)
    blocked = g7["status"] == "blocked" or b3["status"] == "blocked"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "blocked_missing_g7_b3_completion_evidence"
            if blocked
            else "operator_review_required_before_status_upgrade"
        ),
        "status_label": "local partial",
        "current_source_commit": source_commit,
        "g7": g7,
        "b3": b3,
        "does_not_claim_211_verified": True,
        "does_not_claim_gate_closable": True,
        "does_not_close_g7": True,
        "does_not_close_b3": True,
        "does_not_complete_foundation_alpha": True,
    }


def _bullet_lines(items: list[str]) -> str:
    return "\n".join(f"- `{item}`" for item in items) or "- none"


def render_g7_b3_completion_audit_markdown(audit: dict[str, Any]) -> str:
    """Render the G7/B3 completion audit as operator-readable Markdown."""
    g7 = _dict(audit.get("g7"))
    b3 = _dict(audit.get("b3"))
    g7_reasons = _bullet_lines([str(item) for item in g7.get("blocking_reasons", [])])
    g7_steps = _bullet_lines([str(item) for item in g7.get("required_next_steps", [])])
    b3_reasons = _bullet_lines([str(item) for item in b3.get("blocking_reasons", [])])
    missing_gates = _bullet_lines([str(item) for item in b3.get("missing_recorded_load_test_gates", [])])
    missing_profile = _bullet_lines([str(item) for item in b3.get("missing_profile_evidence", [])])
    return (
        "# G7/B3 Completion Audit\n\n"
        f"Schema: `{audit.get('schema_version')}`\n\n"
        f"Status: `{audit.get('status')}`\n\n"
        f"Status label: `{audit.get('status_label')}`\n\n"
        "## G7\n\n"
        f"Status: `{g7.get('status')}`\n\n"
        f"Source marker commit: `{g7.get('source_marker_commit')}`\n\n"
        f"Runtime image: `{g7.get('runtime_image')}`\n\n"
        f"Canonical runtime label commit: `{g7.get('canonical_runtime_label_commit')}`\n\n"
        f"Legacy runtime label commit: `{g7.get('legacy_runtime_label_commit')}`\n\n"
        f"Live API sandbox provider: `{g7.get('live_api_sandbox_provider')}`\n\n"
        "Blocking reasons:\n\n"
        f"{g7_reasons}\n\n"
        "Required next steps:\n\n"
        f"{g7_steps}\n\n"
        "## B3\n\n"
        f"Status: `{b3.get('status')}`\n\n"
        f"Target profile: `{b3.get('target_profile_id')}`\n\n"
        "Blocking reasons:\n\n"
        f"{b3_reasons}\n\n"
        "Missing recorded load-test gates:\n\n"
        f"{missing_gates}\n\n"
        "Missing profile evidence:\n\n"
        f"{missing_profile}\n\n"
        "## Boundary\n\n"
        f"- does not close G7: `{str(audit.get('does_not_close_g7')).lower()}`\n"
        f"- does not close B3: `{str(audit.get('does_not_close_b3')).lower()}`\n"
        "- does not raise production defaults: "
        f"`{str(b3.get('does_not_raise_production_defaults')).lower()}`\n"
    )

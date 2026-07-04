from __future__ import annotations

import re
from typing import Any

from app.capacity_baseline import LOAD_TEST_GATES
from app.foundation_runtime_concurrency import build_foundation_runtime_concurrency_readiness


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
    "ordinary_user_platform_multi_run_orchestration_enabled",
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
_G7_STATUS_UPGRADE_REVIEW_SCHEMA = "ai-platform.g7-operator-status-review.v1"
_G7_STATUS_UPGRADE_APPROVED_DECISION = "approved_for_g7_status_upgrade"
_G7_REQUIRED_NON_EXPANSION_INVARIANTS = {
    "ordinary_user_high_risk_sandbox_allowed": False,
    "ordinary_user_platform_multi_run_orchestration_exposure": False,
    "production_concurrency_defaults_raised": False,
    "g7_closed": True,
    "b3_closed": False,
    "foundation_alpha_complete": False,
}
_B3_PROFILE_EVIDENCE_ALLOWED_SOURCES = {
    "platform_runtime_profile",
    "live_worker_run_payload",
    "operator_reviewed_recorded_snapshot",
}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _is_placeholder_text(value: object) -> bool:
    text = _safe_text(value)
    if not text:
        return True
    lowered = text.lower()
    if re.search(r"<[^<>]+>", text) or re.search(r"\$\{[^{}]+\}", text):
        return True
    if lowered.startswith("todo_operator_reviewed_"):
        return True
    placeholder_tokens = {
        "todo",
        "tbd",
        "placeholder",
        "fill-me",
        "fill_me",
        "fill me",
        "replace-me",
        "replace_me",
        "replace me",
        "example",
        "sample",
    }
    if lowered in placeholder_tokens:
        return True
    return bool(re.fullmatch(r"(?:todo|tbd)[:\s_-].*", lowered)) or bool(
        re.search(r"\b(?:placeholder|fill[-_ ]?me|replace[-_ ]?me)\b", lowered)
    )


def _safe_evidence_ref(value: object) -> str:
    text = _safe_text(value)
    if (
        _is_placeholder_text(text)
        or "\\" in text
        or "://" in text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", text)
    ):
        return ""
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return text


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


def _reviewed_release_evidence_entries(runtime_observation: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    singular = _dict(runtime_observation.get("reviewed_release_evidence"))
    if singular:
        entries.append(singular)
    plural = runtime_observation.get("reviewed_release_evidence_entries")
    if isinstance(plural, list):
        entries.extend(_dict(entry) for entry in plural if _dict(entry))
    return entries


def _canonical_runtime_label_commit(labels: dict[str, Any]) -> str:
    for key in (
        "ai-platform.source-revision",
        "ai-platform.runtime-subject",
        "org.opencontainers.image.revision",
    ):
        value = _safe_text(labels.get(key))
        if value:
            return value
    return ""


def _legacy_runtime_label_commit(labels: dict[str, Any]) -> str:
    for key in (
        "ai-platform.source_revision",
        "ai-platform.source_commit",
        "ai-platform.runtime_subject",
        "ai-platform.source_tree_commit",
        "ai_platform_source_revision",
        "ai_platform_source_commit",
        "ai_platform_runtime_subject",
        "ai_platform_source_tree_commit",
    ):
        value = _safe_text(labels.get(key))
        if value:
            return value
    return ""


def _legacy_runtime_label_commits(labels: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "ai-platform.source_revision",
        "ai-platform.source_commit",
        "ai-platform.runtime_subject",
        "ai-platform.source_tree_commit",
        "ai_platform_source_revision",
        "ai_platform_source_commit",
        "ai_platform_runtime_subject",
        "ai_platform_source_tree_commit",
    ):
        value = _safe_text(labels.get(key))
        if value and value not in values:
            values.append(value)
    return values


def _canonical_source_runtime_mismatch(
    *,
    current_source_commit: str,
    source_marker_commit: str,
    canonical_runtime_label_commit: str,
) -> bool:
    if not current_source_commit:
        return True
    if source_marker_commit and source_marker_commit != current_source_commit:
        return True
    return canonical_runtime_label_commit != current_source_commit


def _legacy_source_runtime_mismatch(
    *,
    current_source_commit: str,
    legacy_runtime_label_commit: str = "",
    legacy_runtime_label_commits: list[str] | None = None,
) -> bool:
    commits = legacy_runtime_label_commits
    if commits is None:
        commits = [legacy_runtime_label_commit] if legacy_runtime_label_commit else []
    return any(commit != current_source_commit for commit in commits)


def _reviewed_g7_release_evidence_id(
    release_evidence: object,
    *,
    current_source_commit: str,
) -> str:
    evidence = _dict(release_evidence)
    if evidence.get("schema_version") != "ai-platform.release-evidence-entry.v1":
        return ""
    if evidence.get("artifact_kind") != "211_sandbox_runtime_smoke":
        return ""
    if _safe_text(evidence.get("review_status")) != "reviewed":
        return ""
    if _safe_text(evidence.get("redaction_scan_status")) != "passed":
        return ""
    if _safe_text(evidence.get("runtime_subject_commit_sha")) != current_source_commit:
        return ""
    if _safe_text(evidence.get("commit_sha")) != current_source_commit:
        return ""
    runtime_checks = _dict(_dict(evidence.get("evidence_ref")).get("runtime_checks"))
    for key in ("g7_211_sandbox_runtime_hardening", "b2_211_real_sandbox_smoke"):
        check = _dict(runtime_checks.get(key))
        if (
            check.get("schema_version") == "ai-platform.sandbox-runtime-211.v1"
            and check.get("runtime_mode") == "platform"
            and check.get("sandbox_provider") == "docker"
        ):
            return _safe_text(check.get("run_id")) or _safe_text(evidence.get("evidence_id"))
    return ""


def _reviewed_g7_release_evidence_selected_id(
    release_evidence_entries: list[dict[str, Any]],
    *,
    current_source_commit: str,
) -> str:
    selected = ""
    for evidence in release_evidence_entries:
        evidence_id = _reviewed_g7_release_evidence_id(
            evidence,
            current_source_commit=current_source_commit,
        )
        if evidence_id:
            selected = evidence_id
    return selected


def _reviewed_g7_source_override_allowed(
    evidence: dict[str, Any],
    *,
    current_source_commit: str,
) -> bool:
    if evidence.get("schema_version") != "ai-platform.release-evidence-entry.v1":
        return False
    if _safe_text(evidence.get("review_status")) != "reviewed":
        return False
    if _safe_text(evidence.get("redaction_scan_status")) != "passed":
        return False
    if _safe_text(evidence.get("runtime_subject_commit_sha")) != current_source_commit:
        return False
    if _safe_text(evidence.get("commit_sha")) != current_source_commit:
        return False
    if evidence.get("gate") != "G7 Sandbox / Resource Hardening":
        return False
    artifact_kind = evidence.get("artifact_kind")
    if artifact_kind == "211_runtime_identity_label_repair":
        return True
    if artifact_kind != "211_sandbox_runtime_smoke":
        return False
    source_ref = _dict(evidence.get("source_ref"))
    image_labels = _dict(source_ref.get("image_labels"))
    return _legacy_runtime_label_commit(image_labels) == current_source_commit


def _safe_runtime_env_from_release_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    source_ref = _dict(evidence.get("source_ref"))
    safe_env = _safe_runtime_env(source_ref.get("safe_live_runtime_env"))
    if safe_env:
        return safe_env
    evidence_ref = _dict(evidence.get("evidence_ref"))
    readback = _dict(evidence_ref.get("readback"))
    return _safe_runtime_env(readback.get("safe_runtime_env"))


def _runtime_observation_identity_matches_current_subject(
    runtime_observation: dict[str, Any],
    *,
    current_source_commit: str,
) -> bool:
    labels = _dict(runtime_observation.get("runtime_image_labels"))
    return bool(
        current_source_commit
        and _safe_text(runtime_observation.get("source_marker_commit")) == current_source_commit
        and _canonical_runtime_label_commit(labels) == current_source_commit
    )


def _runtime_observation_with_reviewed_overrides(
    runtime_observation: dict[str, Any],
    *,
    current_source_commit: str,
) -> dict[str, Any]:
    merged = dict(runtime_observation)
    live_env = _safe_runtime_env(runtime_observation.get("api_env"))
    preserve_live_env = bool(
        live_env.get("SANDBOX_CONTAINER_PROVIDER") == "docker"
        and _runtime_observation_identity_matches_current_subject(
            runtime_observation,
            current_source_commit=current_source_commit,
        )
    )
    for evidence in _reviewed_release_evidence_entries(runtime_observation):
        if not _reviewed_g7_source_override_allowed(
            evidence,
            current_source_commit=current_source_commit,
        ):
            continue
        source_ref = _dict(evidence.get("source_ref"))
        source_marker_commit = _safe_text(source_ref.get("runtime_source_marker"))
        if source_marker_commit:
            merged["source_marker_commit"] = source_marker_commit
        runtime_image = _safe_text(source_ref.get("image"))
        if runtime_image:
            merged["runtime_image"] = runtime_image
        image_labels = _dict(source_ref.get("image_labels"))
        if image_labels:
            merged["runtime_image_labels"] = image_labels
        safe_env = _safe_runtime_env_from_release_evidence(evidence)
        if safe_env and not preserve_live_env:
            merged["api_env"] = safe_env
    return merged


def _executor_image_is_current_main_bound(
    executor_image: str,
    *,
    runtime_image: str,
    current_source_commit: str,
) -> bool:
    short_commit = current_source_commit[:7]
    return bool(
        executor_image
        and executor_image != "ai-platform:local"
        and (
            executor_image == runtime_image
            or (short_commit and short_commit in executor_image)
        )
    )


def _foundation_runtime_concurrency_summary(
    evidence: object,
    *,
    current_source_commit: str,
) -> dict[str, Any]:
    payload = _dict(evidence)
    readiness = build_foundation_runtime_concurrency_readiness(payload if payload else None)
    status = _safe_text(readiness.get("status"))
    verified = readiness.get("verified") is True
    current_subject = (
        verified
        and _safe_text(payload.get("commit_sha")) == current_source_commit
        and _safe_text(payload.get("source_tree_commit_sha")) == current_source_commit
        and _safe_text(payload.get("runtime_subject_commit_sha")) == current_source_commit
    )
    return {
        "status": status,
        "verified": verified,
        "current_subject": current_subject,
    }


def _build_g7_audit(
    runtime_observation: dict[str, Any],
    *,
    current_source_commit: str,
) -> dict[str, Any]:
    release_evidence_entries = _reviewed_release_evidence_entries(runtime_observation)
    runtime_observation = _runtime_observation_with_reviewed_overrides(
        runtime_observation,
        current_source_commit=current_source_commit,
    )
    labels = _dict(runtime_observation.get("runtime_image_labels"))
    safe_env = _safe_runtime_env(runtime_observation.get("api_env"))
    source_marker_commit = _safe_text(runtime_observation.get("source_marker_commit"))
    runtime_image = _safe_text(runtime_observation.get("runtime_image"))
    canonical_label_commit = _canonical_runtime_label_commit(labels)
    legacy_label_commit = _legacy_runtime_label_commit(labels)
    legacy_label_commits = _legacy_runtime_label_commits(labels)
    live_api_provider = safe_env.get("SANDBOX_CONTAINER_PROVIDER", "")
    live_executor_image = safe_env.get("SANDBOX_EXECUTOR_IMAGE", "")
    live_egress_enabled = safe_env.get("SANDBOX_EGRESS_POLICY_ENABLED", "")
    reviewed_evidence_id = _reviewed_g7_release_evidence_selected_id(
        release_evidence_entries,
        current_source_commit=current_source_commit,
    )
    foundation_runtime_concurrency = _foundation_runtime_concurrency_summary(
        runtime_observation.get("foundation_runtime_concurrency_evidence"),
        current_source_commit=current_source_commit,
    )
    blocking_reasons: list[str] = []

    canonical_mismatch = _canonical_source_runtime_mismatch(
        current_source_commit=current_source_commit,
        source_marker_commit=source_marker_commit,
        canonical_runtime_label_commit=canonical_label_commit,
    )
    legacy_mismatch = _legacy_source_runtime_mismatch(
        current_source_commit=current_source_commit,
        legacy_runtime_label_commit=legacy_label_commit,
        legacy_runtime_label_commits=legacy_label_commits,
    )

    if canonical_mismatch:
        blocking_reasons.append("current_main_source_runtime_label_mismatch")
    if legacy_mismatch:
        blocking_reasons.append("stale_runtime_alias_label_mismatch")
    if live_api_provider == "fake":
        blocking_reasons.append("live_api_uses_fake_sandbox_provider")
    if live_api_provider == "docker" and not _executor_image_is_current_main_bound(
        live_executor_image,
        runtime_image=runtime_image,
        current_source_commit=current_source_commit,
    ):
        blocking_reasons.append("live_api_sandbox_executor_image_not_current_main_bound")
    if live_api_provider == "docker" and live_egress_enabled.lower() != "true":
        blocking_reasons.append("live_api_sandbox_egress_policy_disabled")
    if not reviewed_evidence_id:
        blocking_reasons.append("reviewed_local_release_evidence_entry_missing")
    if not foundation_runtime_concurrency["current_subject"]:
        blocking_reasons.append("foundation_runtime_concurrency_evidence_missing_or_not_current_subject")

    next_steps = []
    if canonical_mismatch:
        next_steps.append("reconcile current-main source marker, runtime image labels, and reviewed release-evidence binding")
    elif legacy_mismatch:
        next_steps.append("clean stale runtime alias labels that still point at an older runtime subject")
    if "live_api_uses_fake_sandbox_provider" in blocking_reasons:
        next_steps.append("move live API and worker default sandbox posture from fake provider to the reviewed Docker-provider path")
    if "live_api_sandbox_executor_image_not_current_main_bound" in blocking_reasons:
        next_steps.append("bind live API and worker sandbox executor image to a reviewed current-main executor image")
    if "live_api_sandbox_egress_policy_disabled" in blocking_reasons:
        next_steps.append("enable and verify live API and worker sandbox egress policy defaults")
    if "reviewed_local_release_evidence_entry_missing" in blocking_reasons:
        next_steps.append("wrap current-main G7 Docker sandbox hardening verifier output as reviewed release evidence")
    if not foundation_runtime_concurrency["current_subject"]:
        next_steps.append("rerun Foundation Runtime concurrency evidence for the same current runtime subject")
    if not blocking_reasons:
        next_steps.append(
            "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
        )

    return {
        "status": "blocked" if blocking_reasons else "candidate_evidence_requires_review",
        "source_marker_commit": source_marker_commit,
        "runtime_image": runtime_image,
        "canonical_runtime_label_commit": canonical_label_commit,
        "legacy_runtime_label_commit": legacy_label_commit,
        "live_api_sandbox_provider": live_api_provider,
        "live_api_sandbox_executor_image": live_executor_image,
        "live_api_sandbox_egress_policy_enabled": live_egress_enabled,
        "reviewed_release_evidence_id": reviewed_evidence_id,
        "foundation_runtime_concurrency_status": foundation_runtime_concurrency["status"],
        "foundation_runtime_concurrency_current_subject": foundation_runtime_concurrency["current_subject"],
        "safe_runtime_env": safe_env,
        "blocking_reasons": blocking_reasons,
        "required_next_steps": next_steps,
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


def _source_b3_profile_evidence(capacity_profile_readiness: dict[str, Any]) -> dict[str, Any]:
    source = _dict(capacity_profile_readiness.get("source_gate_readiness"))
    profile_evidence = _dict(source.get("profile_evidence"))
    return _dict(profile_evidence.get(B3_TARGET_PROFILE_ID))


def _invalid_b3_profile_evidence_fields(profile_evidence: dict[str, Any]) -> list[str]:
    if not profile_evidence:
        return list(B3_REQUIRED_PROFILE_EVIDENCE)

    invalid: list[str] = []
    if profile_evidence.get("target_profile_id") != B3_TARGET_PROFILE_ID:
        invalid.append("target_profile_id")
    if profile_evidence.get("evidence_source") not in _B3_PROFILE_EVIDENCE_ALLOWED_SOURCES:
        invalid.append("evidence_source")

    observed_sessions = profile_evidence.get("observed_concurrent_sessions")
    if not isinstance(observed_sessions, int) or isinstance(observed_sessions, bool) or observed_sessions < 10:
        invalid.append("observed_concurrent_sessions")

    observed_subagents = profile_evidence.get("observed_peak_sdk_subagents_per_session")
    if (
        not isinstance(observed_subagents, int)
        or isinstance(observed_subagents, bool)
        or observed_subagents < 4
    ):
        invalid.append("observed_peak_sdk_subagents_per_session")

    if not _safe_evidence_ref(profile_evidence.get("sdk_subagent_fanout_measurement_ref")):
        invalid.append("sdk_subagent_fanout_measurement_ref")
    if profile_evidence.get("production_concurrency_defaults_raised") is not False:
        invalid.append("production_concurrency_defaults_raised")
    if profile_evidence.get("safe_concurrency_claimed") is not False:
        invalid.append("safe_concurrency_claimed")
    if profile_evidence.get("ordinary_user_platform_multi_run_orchestration_enabled") is not False:
        invalid.append("ordinary_user_platform_multi_run_orchestration_enabled")
    return invalid


def _readiness_status_is_blocked(status: object) -> bool:
    return str(status or "").strip() in _BLOCKED_CAPACITY_PROFILE_STATUSES


def _source_gate_statuses(
    capacity_profile_readiness: dict[str, Any],
) -> tuple[dict[str, str], bool]:
    source = _dict(capacity_profile_readiness.get("source_gate_readiness"))
    rows = source.get("load_test_gates")
    statuses: dict[str, str] = {}
    if not isinstance(rows, list):
        return statuses, False
    for row in rows:
        item = _dict(row)
        gate = str(item.get("gate") or "").strip()
        status = str(item.get("status") or "").strip()
        if gate in LOAD_TEST_GATES and status:
            statuses[gate] = status
    return statuses, set(statuses) == set(LOAD_TEST_GATES)


def _normalized_non_empty_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _missing_load_test_gates(capacity_profile_readiness: dict[str, Any]) -> tuple[list[str], bool]:
    source = _dict(capacity_profile_readiness.get("source_gate_readiness"))
    if not source:
        return list(LOAD_TEST_GATES), False
    missing = source.get("missing_load_test_gates")
    invalid_gates = _dedupe_preserving_order(
        _normalized_non_empty_strings(source.get("invalid_load_test_gates"))
        + _normalized_non_empty_strings(
            _profile_readiness_b3_profile(capacity_profile_readiness).get(
                "invalid_load_test_gates"
            )
        )
    )
    if isinstance(missing, list):
        normalized = _normalized_non_empty_strings(missing)
        if normalized or invalid_gates:
            return _dedupe_preserving_order(normalized + invalid_gates), bool(
                invalid_gates
            )
        if _readiness_status_is_blocked(source.get("status")):
            return list(LOAD_TEST_GATES), True
        gate_statuses, statuses_complete = _source_gate_statuses(capacity_profile_readiness)
        missing_by_status = [
            gate
            for gate in LOAD_TEST_GATES
            if gate_statuses.get(gate) != "recorded"
        ]
        if missing_by_status:
            return missing_by_status, True
        if not statuses_complete or str(source.get("status") or "").strip() != "ready_for_operator_review":
            return list(LOAD_TEST_GATES), True
        if _invalid_b3_profile_evidence_fields(
            _source_b3_profile_evidence(capacity_profile_readiness)
        ):
            return list(LOAD_TEST_GATES), True
        return [], False
    return list(LOAD_TEST_GATES), True


def _missing_profile_evidence(capacity_profile_readiness: dict[str, Any]) -> tuple[list[str], bool]:
    profile = _profile_readiness_b3_profile(capacity_profile_readiness)
    if not profile:
        return list(B3_REQUIRED_PROFILE_EVIDENCE), False
    missing = profile.get("missing_profile_evidence")
    if isinstance(missing, list):
        normalized = [str(field) for field in missing if str(field).strip()]
        if normalized:
            return normalized, False
        if _readiness_status_is_blocked(profile.get("status")):
            return list(B3_REQUIRED_PROFILE_EVIDENCE), True
        observed = _dict(profile.get("observed_profile_evidence"))
        invalid_observed = _invalid_b3_profile_evidence_fields(observed)
        invalid_source = _invalid_b3_profile_evidence_fields(
            _source_b3_profile_evidence(capacity_profile_readiness)
        )
        if (
            str(profile.get("status") or "").strip() != "operator_review_required"
            or str(profile.get("profile_evidence_status") or "").strip() != "accepted"
            or invalid_observed
            or invalid_source
        ):
            return list(B3_REQUIRED_PROFILE_EVIDENCE), True
        return [], False
    return list(B3_REQUIRED_PROFILE_EVIDENCE), True


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
    production_default_decision = (
        "do_not_raise_without_recorded_load_test_evidence"
        if blocking_reasons
        else "operator_review_required_before_default_change"
    )
    return {
        "status": "blocked" if blocking_reasons else "operator_review_required",
        "target_profile_id": B3_TARGET_PROFILE_ID,
        "missing_recorded_load_test_gates": missing_gates,
        "missing_profile_evidence": missing_profile,
        "blocking_reasons": blocking_reasons,
        "production_default_decision": production_default_decision,
        "does_not_raise_production_defaults": True,
        "does_not_enable_ordinary_user_platform_multi_run_orchestration": True,
    }


def _g7_status_upgrade_review_status(
    value: object,
    *,
    current_source_commit: str,
    b3_blocking_reasons: list[str],
) -> dict[str, Any]:
    source = _dict(value)
    if not source:
        return {"status": "not_provided", "input_errors": []}

    errors: list[str] = []
    if source.get("schema_version") != "ai-platform.release-evidence-entry.v1":
        errors.append("g7_status_upgrade_review_entry_schema_unsupported")
    if source.get("artifact_kind") != "211_g7_operator_status_review":
        errors.append("g7_status_upgrade_review_artifact_kind_invalid")
    if source.get("gate") != "G7 Sandbox / Resource Hardening":
        errors.append("g7_status_upgrade_review_gate_invalid")
    if _safe_text(source.get("review_status")) != "reviewed":
        errors.append("g7_status_upgrade_review_not_reviewed")
    if _safe_text(source.get("redaction_scan_status")) != "passed":
        errors.append("g7_status_upgrade_review_redaction_not_passed")
    if _safe_text(source.get("commit_sha")) != current_source_commit:
        errors.append("g7_status_upgrade_review_commit_mismatch")
    if _safe_text(source.get("runtime_subject_commit_sha")) != current_source_commit:
        errors.append("g7_status_upgrade_review_runtime_subject_mismatch")

    review = _dict(_dict(source.get("evidence_ref")).get("operator_status_review"))
    if review.get("schema_version") != _G7_STATUS_UPGRADE_REVIEW_SCHEMA:
        errors.append("g7_status_upgrade_review_schema_unsupported")
    if _safe_text(review.get("runtime_subject_commit_sha")) != current_source_commit:
        errors.append("g7_status_upgrade_review_runtime_subject_mismatch")
    if _safe_text(review.get("status_upgrade_decision")) != _G7_STATUS_UPGRADE_APPROVED_DECISION:
        errors.append("g7_status_upgrade_review_not_approved")
    if _safe_text(review.get("status")) != "status_upgrade_approved":
        errors.append("g7_status_upgrade_review_status_not_approved")
    if review.get("g7_runtime_blocking_reasons") != []:
        errors.append("g7_status_upgrade_review_g7_blockers_not_empty")

    reviewed_b3_blockers = review.get("b3_blocking_reasons")
    if not isinstance(reviewed_b3_blockers, list):
        errors.append("g7_status_upgrade_review_b3_blockers_missing")
    else:
        missing_acknowledgements = [
            reason
            for reason in b3_blocking_reasons
            if reason not in reviewed_b3_blockers
        ]
        if missing_acknowledgements:
            errors.append("g7_status_upgrade_review_b3_blockers_not_acknowledged")

    invariants = _dict(review.get("non_expansion_invariants"))
    for key, expected in _G7_REQUIRED_NON_EXPANSION_INVARIANTS.items():
        if invariants.get(key) is not expected:
            errors.append(f"g7_status_upgrade_review_{key}_invalid")

    return {
        "status": "accepted" if not errors else "not_accepted",
        "input_errors": errors,
        "status_upgrade_decision": _safe_text(review.get("status_upgrade_decision")),
    }


def _apply_g7_status_upgrade_review(
    g7: dict[str, Any],
    *,
    g7_status_upgrade_review: object,
    current_source_commit: str,
    b3: dict[str, Any],
) -> dict[str, Any]:
    result = dict(g7)
    review_status = _g7_status_upgrade_review_status(
        g7_status_upgrade_review,
        current_source_commit=current_source_commit,
        b3_blocking_reasons=[str(reason) for reason in b3.get("blocking_reasons", [])],
    )
    result["status_upgrade_review"] = review_status
    if result.get("blocking_reasons") == [] and review_status["status"] == "accepted":
        result["status"] = "status_upgrade_approved"
        result["required_next_steps"] = []
    return result


def _overall_audit_status(g7: dict[str, Any], b3: dict[str, Any]) -> str:
    g7_blocked = g7["status"] != "status_upgrade_approved"
    b3_blocked = b3["status"] == "blocked"
    if g7_blocked and b3_blocked:
        return "blocked_missing_g7_b3_completion_evidence"
    if g7_blocked:
        return "blocked_missing_g7_completion_evidence"
    if b3_blocked:
        return "blocked_missing_b3_completion_evidence"
    if g7["status"] != "status_upgrade_approved":
        return "operator_review_required_before_status_upgrade"
    return "operator_review_required_before_gate_closure"


def build_g7_b3_completion_audit(
    *,
    runtime_observation: dict[str, object] | None,
    capacity_profile_readiness: dict[str, object] | None,
    g7_status_upgrade_review: dict[str, object] | None = None,
    current_source_commit: str,
) -> dict[str, Any]:
    """Build a gap-first G7/B3 audit without treating probes as closure evidence."""
    runtime = _dict(runtime_observation)
    source_commit = _safe_text(current_source_commit)
    g7 = _build_g7_audit(runtime, current_source_commit=source_commit)
    b3 = _build_b3_audit(capacity_profile_readiness)
    g7 = _apply_g7_status_upgrade_review(
        g7,
        g7_status_upgrade_review=g7_status_upgrade_review,
        current_source_commit=source_commit,
        b3=b3,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": _overall_audit_status(g7, b3),
        "status_label": "local partial",
        "current_source_commit": source_commit,
        "g7": g7,
        "b3": b3,
        "does_not_claim_211_verified": True,
        "does_not_claim_gate_closable": True,
        "does_not_close_g7": g7["status"] != "status_upgrade_approved",
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
        f"Live API sandbox executor image: `{g7.get('live_api_sandbox_executor_image')}`\n\n"
        f"Live API sandbox egress policy enabled: `{g7.get('live_api_sandbox_egress_policy_enabled')}`\n\n"
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

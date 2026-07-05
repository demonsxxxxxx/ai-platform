import json
import subprocess
import sys
from pathlib import Path

from tests.test_foundation_runtime_concurrency import complete_evidence

from app.g7_b3_completion_audit import (
    B3_REQUIRED_PROFILE_EVIDENCE,
    build_g7_b3_completion_audit,
    render_g7_b3_completion_audit_markdown,
)
from app.capacity_baseline import LOAD_TEST_GATES
from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance
from app.sandbox_hardening_contract import bounded_error_projection_is_safe


def assert_no_sensitive_callback_token_leak(serialized: str) -> None:
    assert '"callback_token"' not in serialized
    assert "secret-token" not in serialized
    assert "callback_secret" not in serialized


CURRENT_SOURCE = "3071a02945c84370f62a9b36884a0a2df8ea9c45"
RUNTIME_SUBJECT = "d318f9f6a68b4c17e221eb32705b3f31d349227a"
LEGACY_LABEL_SUBJECT = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"
CURRENT_MAIN_G7_SOURCE = "ae6b7e52c656fd8296cf039834ce8d8559b01228"
PR297_G7_B3_SOURCE = "4805031fc3333ccbf38224172e4e85e21c0630bb"
PR300_G7_B3_SOURCE = "93155b4a5bdb4e6b7ac29bfc802a7a70c891c34e"
PR304_G7_B3_SOURCE = "decf33a017e0b97e2a2992f80e3ccdc19152c1f4"
PR306_G7_B3_SOURCE = "9c669761bbb4bd719af64a341d361b7c3b3e380e"
PR308_G7_B3_SOURCE = "15903fdfe96ffcfba9daa1252741111017dcf832"
HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE = "755e50ea2ad08c2d4218ae5d8cc612970b19e2a4"
PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE = "61073b16a5b2c135e7ee467434ab39502ca3d194"
POST_PR319_G7_B3_RUNTIME_SOURCE = "a294727046024958c41b15f646512e68f3c04b47"
CURRENT_G7_B3_RUNTIME_SOURCE = "945db2bb5926ad7b01ead98c3283d55b77d2677d"
CLEAN_B3_RECORDED_RUNTIME_SOURCE = "53887e20f5141e66a8f635affc87f4af930348ba"
CURRENT_MAIN_G7_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / CURRENT_MAIN_G7_SOURCE
    / "2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5.json"
)
CURRENT_MAIN_G7_HARDENING_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / CURRENT_MAIN_G7_SOURCE
    / "2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5.json"
)
CURRENT_MAIN_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / CURRENT_MAIN_G7_SOURCE
    / "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json"
)
CURRENT_MAIN_G7_LABEL_REPAIR_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / CURRENT_MAIN_G7_SOURCE
    / "2026-07-01-211-g7-runtime-identity-label-repair-ae6b7e5.json"
)
PR297_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR297_G7_B3_SOURCE
    / "2026-07-02-211-g7-sandbox-live-env-hardening-4805031.json"
)
PR300_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR300_G7_B3_SOURCE
    / "2026-07-02-211-g7-sandbox-live-env-hardening-93155b4.json"
)
PR304_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR304_G7_B3_SOURCE
    / "2026-07-02-211-g7-sandbox-live-env-hardening-decf33a.json"
)
PR306_G7_HARDENING_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR306_G7_B3_SOURCE
    / "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json"
)
PR306_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR306_G7_B3_SOURCE
    / "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json"
)
PR306_G7_OPERATOR_STATUS_REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-status-review"
    / PR306_G7_B3_SOURCE
    / "2026-07-03-211-g7-operator-status-review-9c669761.json"
)
PR308_G7_LIVE_ENV_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PR308_G7_B3_SOURCE
    / "2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json"
)
PR308_G7_OPERATOR_STATUS_REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-status-review"
    / PR308_G7_B3_SOURCE
    / "2026-07-03-211-g7-operator-status-review-15903fd-label-clean.json"
)
CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    / "2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json"
)
CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    / "2026-07-03-211-g7-sandbox-live-env-hardening-61073b1-clean-main.json"
)
CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_A294727 = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / POST_PR319_G7_B3_RUNTIME_SOURCE
    / "2026-07-04-211-g7-sandbox-live-env-hardening-a294727-source-marker-fix.json"
)
CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_945DB2B = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-sandbox"
    / CURRENT_G7_B3_RUNTIME_SOURCE
    / "2026-07-05-211-g7-sandbox-live-env-hardening-945db2b-live-default.json"
)
CURRENT_G7_B3_FRC_EVIDENCE_PATH_945DB2B = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{CURRENT_G7_B3_RUNTIME_SOURCE}-frc-g7-b3-20260705"
    / "2026-07-05-211-foundation-alpha-poc-945db2b-foundation-runtime-concurrency.json"
)
CURRENT_G7_B3_FRC_READINESS_PATH_945DB2B = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{CURRENT_G7_B3_RUNTIME_SOURCE}-frc-g7-b3-20260705"
    / "2026-07-05-211-foundation-alpha-poc-945db2b-foundation-runtime-concurrency-readiness.json"
)
CLEAN_B3_RECORDED_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{CLEAN_B3_RECORDED_RUNTIME_SOURCE}-frc-g7-b3-20260705"
    / "2026-07-05-211-foundation-alpha-poc-53887e2-foundation-runtime-concurrency.json"
)
CLEAN_B3_RECORDED_FRC_READINESS_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{CLEAN_B3_RECORDED_RUNTIME_SOURCE}-frc-g7-b3-20260705"
    / "2026-07-05-211-foundation-alpha-poc-53887e2-foundation-runtime-concurrency-readiness.json"
)
PR297_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{PR297_G7_B3_SOURCE}-frc-g7-b3-20260702"
    / "2026-07-02-211-foundation-alpha-poc-4805031-foundation-runtime-concurrency.json"
)
PR304_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{PR304_G7_B3_SOURCE}-frc-g7-b3-20260702"
    / "2026-07-02-211-foundation-alpha-poc-decf33a-foundation-runtime-concurrency.json"
)
PR306_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{PR306_G7_B3_SOURCE}-frc-g7-b3-20260703"
    / "2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json"
)
PR308_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{PR308_G7_B3_SOURCE}-frc-g7-b3-20260703"
    / "2026-07-03-211-foundation-alpha-poc-15903fd-foundation-runtime-concurrency.json"
)
CURRENT_G7_B3_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE}-frc-g7-b3-20260703"
    / "2026-07-03-211-foundation-alpha-poc-755e50e-foundation-runtime-concurrency.json"
)
CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE}-frc-g7-b3-20260703"
    / "2026-07-03-211-foundation-alpha-poc-61073b1-foundation-runtime-concurrency.json"
)
CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/capacity-gate-readiness"
    / HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    / "2026-07-03-211-capacity-runtime-readiness-755e50e.json"
)
CURRENT_CLEAN_MAIN_G7_B3_CAPACITY_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/capacity-gate-readiness"
    / PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    / "2026-07-03-211-capacity-runtime-readiness-61073b1.json"
)
CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH_A294727 = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/capacity-gate-readiness"
    / POST_PR319_G7_B3_RUNTIME_SOURCE
    / "2026-07-04-211-capacity-runtime-readiness-a294727.json"
)
CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH_945DB2B = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/capacity-gate-readiness"
    / CURRENT_G7_B3_RUNTIME_SOURCE
    / "2026-07-05-211-capacity-runtime-readiness-945db2b.json"
)
CLEAN_B3_RECORDED_CAPACITY_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/capacity-gate-readiness"
    / CLEAN_B3_RECORDED_RUNTIME_SOURCE
    / "2026-07-05-211-capacity-recorded-gate-readiness-53887e2.json"
)
CURRENT_G7_B3_STATUS_UPGRADE_REVIEW_PATH_945DB2B = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-status-review"
    / CURRENT_G7_B3_RUNTIME_SOURCE
    / "2026-07-05-211-g7-operator-status-upgrade-945db2b.json"
)
CURRENT_CLEAN_MAIN_G7_B3_OPERATOR_STATUS_REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/g7-status-review"
    / PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    / "2026-07-03-211-g7-operator-status-review-61073b1-clean-main.json"
)
CURRENT_CLEAN_MAIN_B3_DIAGNOSTIC_OBSERVATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/diagnostics"
    / "2026-07-04-211-b3-sandbox-observation-61073b1.json"
)
CURRENT_MAIN_FRC_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/release-evidence/foundation-runtime-concurrency"
    / f"{CURRENT_MAIN_G7_SOURCE}-frc-g7-b3-20260702"
    / "2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency.json"
)

def _runtime_observation() -> dict[str, object]:
    return {
        "source_marker_commit": CURRENT_SOURCE,
        "runtime_image": "ai-platform:d318f9f-g7-b3-runtime-only-v1",
        "runtime_image_labels": {
            "ai-platform.source-revision": RUNTIME_SUBJECT,
            "ai-platform.runtime-subject": RUNTIME_SUBJECT,
            "org.opencontainers.image.revision": RUNTIME_SUBJECT,
            "ai_platform_source_revision": LEGACY_LABEL_SUBJECT,
            "ai_platform_runtime_subject": LEGACY_LABEL_SUBJECT,
        },
        "api_env": {
            "SANDBOX_CONTAINER_PROVIDER": "fake",
            "SANDBOX_EXECUTOR_IMAGE": "ai-platform:local",
            "MAX_ACTIVE_WORKER_RUNS": "3",
            "MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT": "0",
            "OPENAI_BASE_URL": "https://user:secretpass@example.invalid/v1",
            "AWS_ACCESS_KEY_ID": "AKIA_SHOULD_NOT_PRINT",
            "CALLBACK_TOKEN": "must-not-leak",
        },
        "health": {"status": "ok"},
    }


def _stale_current_main_runtime_observation() -> dict[str, object]:
    return {
        "source_marker_commit": "4805031fc3333ccbf38224172e4e85e21c0630bb",
        "runtime_image": "ai-platform:4805031-g7-b3-post-297-label-repair-v2",
        "runtime_image_labels": {
            "ai-platform.source-revision": "4805031fc3333ccbf38224172e4e85e21c0630bb",
            "ai-platform.runtime-subject": "4805031fc3333ccbf38224172e4e85e21c0630bb",
            "org.opencontainers.image.revision": "4805031fc3333ccbf38224172e4e85e21c0630bb",
            "ai-platform.source_revision": "4805031fc3333ccbf38224172e4e85e21c0630bb",
            "ai-platform.runtime_subject": "4805031fc3333ccbf38224172e4e85e21c0630bb",
            "ai-platform.source_tree_commit": "4805031fc3333ccbf38224172e4e85e21c0630bb",
        },
        "api_env": {
            "SANDBOX_CONTAINER_PROVIDER": "docker",
            "SANDBOX_EXECUTOR_IMAGE": "ai-platform:4805031-g7-b3-post-297-label-repair-v2",
            "SANDBOX_EGRESS_POLICY_ENABLED": "true",
        },
        "health": {"status": "ok"},
    }


def _current_main_runtime_observation_with_reviewed_g7() -> dict[str, object]:
    return {
        "source_marker_commit": CURRENT_SOURCE,
        "runtime_image": "ai-platform:3071a02-g7-current-main-runtime-only-v1",
        "runtime_image_labels": {
            "ai-platform.source-revision": CURRENT_SOURCE,
            "ai-platform.runtime-subject": CURRENT_SOURCE,
            "org.opencontainers.image.revision": CURRENT_SOURCE,
            "ai-platform.source_revision": LEGACY_LABEL_SUBJECT,
            "ai-platform.runtime_subject": LEGACY_LABEL_SUBJECT,
        },
        "api_env": {
            "SANDBOX_CONTAINER_PROVIDER": "fake",
            "SANDBOX_EXECUTOR_IMAGE": "ai-platform:local",
            "SANDBOX_EGRESS_POLICY_ENABLED": "false",
        },
        "reviewed_release_evidence": {
            "schema_version": "ai-platform.release-evidence-entry.v1",
            "artifact_kind": "211_sandbox_runtime_smoke",
            "gate": "G7 Sandbox / Resource Hardening",
            "commit_sha": CURRENT_SOURCE,
            "runtime_subject_commit_sha": CURRENT_SOURCE,
            "review_status": "reviewed",
            "redaction_scan_status": "passed",
            "evidence_ref": {
                "runtime_checks": {
                    "g7_211_sandbox_runtime_hardening": {
                        "schema_version": "ai-platform.sandbox-runtime-211.v1",
                        "run_id": "g7-current-main-3071a02",
                        "runtime_mode": "platform",
                        "sandbox_provider": "docker",
                    }
                }
            },
        },
    }


def _current_main_foundation_runtime_concurrency_evidence() -> dict[str, object]:
    return complete_evidence(
        commit_sha=CURRENT_SOURCE,
        source_tree_commit_sha=CURRENT_SOURCE,
        runtime_subject_commit_sha=CURRENT_SOURCE,
    )


def _actual_current_main_foundation_runtime_concurrency_evidence() -> dict[str, object]:
    return complete_evidence(
        commit_sha=CURRENT_MAIN_G7_SOURCE,
        source_tree_commit_sha=CURRENT_MAIN_G7_SOURCE,
        runtime_subject_commit_sha=CURRENT_MAIN_G7_SOURCE,
    )


def _complete_b3_capacity_profile_readiness(
    observed_profile_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    profile_evidence = observed_profile_evidence or {
        "target_profile_id": "b3_10x4_sdk_subagents",
        "evidence_source": "operator_reviewed_recorded_snapshot",
        "observed_concurrent_sessions": 10,
        "observed_peak_sdk_subagents_per_session": 4,
        "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
        "production_concurrency_defaults_raised": False,
        "safe_concurrency_claimed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
    }
    return {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": "operator_review_required",
        "source_gate_readiness": {
            "schema_version": "ai-platform.capacity-gate-readiness.v1",
            "status": "ready_for_operator_review",
            "load_test_gates": [
                {"gate": gate, "status": "recorded"}
                for gate in LOAD_TEST_GATES
            ],
            "missing_load_test_gates": [],
            "invalid_load_test_gates": [],
            "profile_evidence": {
                "b3_10x4_sdk_subagents": profile_evidence,
            },
        },
        "profiles": [
            {
                "id": "b3_10x4_sdk_subagents",
                "status": "operator_review_required",
                "required_load_test_gates": list(LOAD_TEST_GATES),
                "missing_load_test_gates": [],
                "invalid_load_test_gates": [],
                "profile_evidence_status": "accepted",
                "missing_profile_evidence": [],
                "observed_profile_evidence": profile_evidence,
            }
        ],
    }


def _approved_g7_status_upgrade_review(
    *,
    commit_sha: str = CURRENT_G7_B3_RUNTIME_SOURCE,
) -> dict[str, object]:
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "artifact_kind": "211_g7_operator_status_review",
        "gate": "G7 Sandbox / Resource Hardening",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "review_status": "reviewed",
        "redaction_scan_status": "passed",
        "evidence_ref": {
            "operator_status_review": {
                "schema_version": "ai-platform.g7-operator-status-review.v1",
                "runtime_subject_commit_sha": commit_sha,
                "status": "status_upgrade_approved",
                "status_label_recommendation": "g7_status_upgrade_approved",
                "status_upgrade_decision": "approved_for_g7_status_upgrade",
                "g7_runtime_blocking_reasons": [],
                "b3_blocking_reasons": [
                    "b3_recorded_load_test_gates_missing",
                    "b3_10x4_sdk_subagents_profile_evidence_missing",
                ],
                "non_expansion_invariants": {
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "ordinary_user_platform_multi_run_orchestration_exposure": False,
                    "production_concurrency_defaults_raised": False,
                    "g7_closed": True,
                    "b3_closed": False,
                    "foundation_alpha_complete": False,
                },
            }
        },
    }


def test_current_main_g7_release_evidence_is_reviewed_redacted_and_does_not_overclose():
    evidence = json.loads(CURRENT_MAIN_G7_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["gate"] == "G7 Sandbox / Resource Hardening"
    assert evidence["commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["runtime_subject_commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["runtime_source_marker"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["source_ref"]["source_snapshot"]["source_tree_dirty"] is False
    assert evidence["source_ref"]["image"] == "ai-platform:ae6b7e5-g7-current-main-runtime-only-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:a91d17dbd5e4d3b3f02800781a1039c92fc67902d582c88bd1d09105c2cf4a41"
    )
    assert evidence["source_ref"]["image_labels"]["ai-platform.source-revision"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["source_ref"]["image_labels"]["ai-platform.runtime-subject"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["source_ref"]["image_labels"]["org.opencontainers.image.revision"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["source_ref"]["stale_label_followups"] == [
        "legacy underscore alias labels ai-platform.source_revision, ai-platform.runtime_subject, and ai-platform.source_tree_commit still point at bd690f72723080beeb820d07679da59d84c7913e",
        "compose project environment_file label still points outside repo-local deploy composition",
    ]

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["schema_version"] == "ai-platform.sandbox-runtime-211.v1"
    assert runtime_check["run_id"] == "g7-current-main-ae6b7e5-20260701172910"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["executed_task"] is True
    assert runtime_check["callback_auth"] == "token"
    assert runtime_check["cancel_stops_container"] is True
    assert {item["name"] for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"]} == {
        "check_docker_socket",
        "check_workspace_write",
        "check_executor_health",
        "check_callback_stream",
        "check_cancel_stops_container",
        "check_platform_runtime_evidence",
        "check_platform_hardening_evidence",
        "check_no_secret_leakage",
    }
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert runtime_check["non_expansion_invariants"]["production_concurrency_defaults_raised"] is False
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": CURRENT_MAIN_G7_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": {
                "ai-platform.source-revision": CURRENT_MAIN_G7_SOURCE,
                "ai-platform.runtime-subject": CURRENT_MAIN_G7_SOURCE,
                "org.opencontainers.image.revision": CURRENT_MAIN_G7_SOURCE,
                "ai-platform.source_revision": "bd690f72723080beeb820d07679da59d84c7913e",
                "ai-platform.runtime_subject": "bd690f72723080beeb820d07679da59d84c7913e",
            },
            "api_env": {
                "SANDBOX_CONTAINER_PROVIDER": "fake",
                "SANDBOX_EXECUTOR_IMAGE": "ai-platform:local",
                "SANDBOX_EGRESS_POLICY_ENABLED": "false",
            },
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_MAIN_G7_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == "g7-current-main-ae6b7e5-20260701172910"
    assert "reviewed_local_release_evidence_entry_missing" not in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["status"] == "blocked"
    assert audit["b3"]["status"] == "blocked"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-sandbox/"
            f"{CURRENT_MAIN_G7_SOURCE}/"
            "2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "93c28",
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_current_main_g7_formal_hardening_evidence_is_reviewed_and_does_not_overclose():
    evidence = json.loads(CURRENT_MAIN_G7_HARDENING_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["runtime_subject_commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:ae6b7e5-g7-b3-label-repair-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:59d9c73fe449fd3285aa88bc38dcc1aa6b96a4569ed4b9d447773c9fea0f5140"
    )
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-current-main-label-repair-probe-20260701201919"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "failed"]
    assert runtime_check["cancel_stops_container"] is True
    resource_limits = runtime_check["hardening"]["resource_limits"]
    assert resource_limits["over_limit_cleanup_verified"] is True
    assert resource_limits["over_limit_probe_kind"] == "platform_resource_timeout"
    assert resource_limits["over_limit_timeout_probe_seconds"] == 0
    egress_policy = runtime_check["hardening"]["egress_policy"]
    assert egress_policy["default_deny_outbound"] is True
    assert egress_policy["callback_probe_status"] == "delivered"
    assert egress_policy["probe_source"] == "runtime_probe_results"
    security_options = runtime_check["hardening"]["security_options"]
    assert security_options["no_new_privileges"] is True
    assert security_options["capabilities_dropped"] is True
    assert security_options["docker_socket_mounted"] is False
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": CURRENT_MAIN_G7_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": {
                "SANDBOX_CONTAINER_PROVIDER": "docker",
                "SANDBOX_EXECUTOR_IMAGE": "ai-platform:local",
                "SANDBOX_EGRESS_POLICY_ENABLED": "false",
            },
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_MAIN_G7_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert "reviewed_local_release_evidence_entry_missing" not in audit["g7"]["blocking_reasons"]
    assert "live_api_uses_fake_sandbox_provider" not in audit["g7"]["blocking_reasons"]
    assert "live_api_sandbox_executor_image_not_current_main_bound" in audit["g7"]["blocking_reasons"]
    assert "live_api_sandbox_egress_policy_disabled" in audit["g7"]["blocking_reasons"]
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_current_main_g7_live_env_hardening_evidence_clears_old_live_posture_blockers_without_overclosing():
    evidence = json.loads(CURRENT_MAIN_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["runtime_subject_commit_sha"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:ae6b7e5-g7-b3-label-repair-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-ae6b7e5-20260702045743"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["live_runtime_env"] == evidence["source_ref"]["safe_live_runtime_env"]
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["platform_allowlist_enforced"] is True
    assert runtime_check["hardening"]["egress_policy"]["denied_probe_error_code"] == "egress_denied"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": CURRENT_MAIN_G7_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": (
                _actual_current_main_foundation_runtime_concurrency_evidence()
            ),
        },
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_MAIN_G7_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-sandbox/"
            f"{CURRENT_MAIN_G7_SOURCE}/"
            "2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "0caa794f6d66",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr297_g7_live_env_hardening_and_frc_evidence_require_operator_review():
    evidence = json.loads(PR297_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(PR297_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-02-211-g7-sandbox-live-env-hardening-4805031"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR297_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR297_G7_B3_SOURCE
    assert evidence["issue_refs"] == []
    assert evidence["pr_refs"] == ["#297"]
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:4805031-g7-b3-post-297-label-repair-v2"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:a8c2448f2083eb0e7537a2f07b4245cec9b4f467c1c81207c3cd6396316b08a5"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR297_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == CURRENT_MAIN_G7_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:4805031-g7-b3-post-297-label-repair-v2",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-4805031-20260702023507"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "failed"]
    assert runtime_check["cancel_stops_container"] is True
    assert runtime_check["live_runtime_env"] == evidence["source_ref"]["safe_live_runtime_env"]
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True
    assert frc_evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert frc_evidence["commit_sha"] == PR297_G7_B3_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == PR297_G7_B3_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == PR297_G7_B3_SOURCE

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR297_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR297_G7_B3_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_status"] == (
        "verified_foundation_runtime_concurrency"
    )
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "6ea6c27b1c17",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr300_g7_live_env_hardening_evidence_remains_frc_and_b3_blocked():
    evidence = json.loads(PR300_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-02-211-g7-sandbox-live-env-hardening-93155b4"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR300_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR300_G7_B3_SOURCE
    assert evidence["issue_refs"] == []
    assert evidence["pr_refs"] == ["#300"]
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:93155b4-g7-b3-post-300-runtime-only-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:12ed308e6bad69fa413604146f0ed5eb214d4b988c6f00f5b43a13dfec24d232"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR300_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PR300_G7_B3_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:93155b4-g7-b3-post-300-runtime-only-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-93155b4-20260702075354"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["cancel_stops_container"] is True
    assert runtime_check["live_runtime_env"] == evidence["source_ref"]["safe_live_runtime_env"]
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR300_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR300_G7_B3_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["blocking_reasons"] == [
        "foundation_runtime_concurrency_evidence_missing_or_not_current_subject"
    ]
    assert audit["g7"]["required_next_steps"] == [
        "rerun Foundation Runtime concurrency evidence for the same current runtime subject"
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr304_branch_g7_and_frc_evidence_reach_candidate_review_without_overclosing():
    evidence = json.loads(PR304_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(PR304_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-02-211-g7-sandbox-live-env-hardening-decf33a"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR304_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR304_G7_B3_SOURCE
    assert evidence["issue_refs"] == []
    assert evidence["pr_refs"] == ["#304"]
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["branch"] == "codex/g7-b3-post-300-followup"
    assert evidence["source_ref"]["image"] == "ai-platform:decf33a-g7-b3-post-300-followup-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:36745d97ddb62d86dc0dd3f1af3e2ae67aa8fc7648a766fe77245801d3d1268e"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR304_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PR304_G7_B3_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:decf33a-g7-b3-post-300-followup-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-decf33a-post-300-followup-20260702095227"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["cancel_stops_container"] is True
    assert runtime_check["live_runtime_env"] == evidence["source_ref"]["safe_live_runtime_env"]
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["callback_probe_status"] == "delivered"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    assert frc_evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert frc_evidence["commit_sha"] == PR304_G7_B3_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == PR304_G7_B3_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == PR304_G7_B3_SOURCE
    assert frc_evidence["summary"]["concurrent_request_count"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert frc_evidence["summary"]["concurrency_probe_source"] == "client_case_timestamps"

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR304_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR304_G7_B3_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_status"] == (
        "verified_foundation_runtime_concurrency"
    )
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "39ddbf68a250",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr306_g7_hardening_evidence_is_reviewed_but_keeps_live_executor_and_frc_blockers():
    evidence = json.loads(PR306_G7_HARDENING_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["issue_refs"] == []
    assert evidence["pr_refs"] == ["#306"]
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["branch"] == "origin/main"
    assert evidence["source_ref"]["image"] == "ai-platform:9c66976-g7-b3-workspace-owner-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:2a28464d850abc3abae6e4fdbd5ed17f381f89ac06e47322f6fa4626e2b8f31d"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR306_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PR306_G7_B3_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:4805031-g7-b3-post-297-label-repair-v2",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    assert evidence["source_ref"]["verifier_effective_sandbox_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:9c66976-g7-b3-workspace-owner-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-current-main-9c66976-sudo-20260702155816"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["cancel_stops_container"] is True
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["callback_probe_status"] == "delivered"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert {item["name"] for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"]} == {
        "check_docker_socket",
        "check_workspace_write",
        "check_executor_health",
        "check_callback_stream",
        "check_cancel_stops_container",
        "check_platform_runtime_evidence",
        "check_platform_hardening_evidence",
        "check_no_secret_leakage",
    }
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR306_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR306_G7_B3_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert "reviewed_local_release_evidence_entry_missing" not in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["blocking_reasons"] == [
        "live_api_sandbox_executor_image_not_current_main_bound",
        "foundation_runtime_concurrency_evidence_missing_or_not_current_subject",
    ]
    assert audit["g7"]["required_next_steps"] == [
        "bind live API and worker sandbox executor image to a reviewed current-main executor image",
        "rerun Foundation Runtime concurrency evidence for the same current runtime subject",
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["b3"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-sandbox/"
            f"{PR306_G7_B3_SOURCE}/"
            "2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "50b78c4a3ebc",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr306_live_env_g7_and_frc_evidence_reaches_g7_operator_review_without_overclosing():
    evidence = json.loads(PR306_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(PR306_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:9c66976-g7-b3-workspace-owner-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:2a28464d850abc3abae6e4fdbd5ed17f381f89ac06e47322f6fa4626e2b8f31d"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR306_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PR306_G7_B3_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:9c66976-g7-b3-workspace-owner-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-9c669761-sudo-20260703091724"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["hardening"]["workspace_isolation"]["marker_path_is_container_path"] is True
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["callback_probe_status"] == "delivered"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert runtime_check["remaining_gate_boundaries"] == [
        "same-subject Foundation Runtime concurrency evidence for 9c669761 is recorded separately in the 2026-07-03 FRC evidence entry",
        "B3 seven-gate recorded load evidence remains missing",
        "b3_10x4_sdk_subagents operator-reviewed profile evidence remains missing",
        "operator status-upgrade review is still required before any G7 closure claim",
    ]

    assert frc_evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert frc_evidence["commit_sha"] == PR306_G7_B3_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == PR306_G7_B3_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == PR306_G7_B3_SOURCE
    assert frc_evidence["summary"]["max_observed_concurrency"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert all(check["status"] == "passed" for check in frc_evidence["checks"].values())
    assert frc_evidence["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert frc_evidence["non_expansion_invariants"]["production_concurrency_increase_allowed"] is False

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR306_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR306_G7_B3_SOURCE,
    )

    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-sandbox/"
            f"{PR306_G7_B3_SOURCE}/"
            "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "28487c71eea8",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr306_g7_operator_status_review_artifact_records_candidate_without_overclosing():
    evidence = json.loads(PR306_G7_OPERATOR_STATUS_REVIEW_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-operator-status-review-9c669761"
    assert evidence["artifact_kind"] == "211_g7_operator_status_review"
    assert evidence["gate"] == "G7 Sandbox / Resource Hardening"
    assert evidence["commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR306_G7_B3_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["g7_live_env_evidence_path"] == (
        "g7-sandbox/"
        f"{PR306_G7_B3_SOURCE}/"
        "2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json"
    )
    assert evidence["source_ref"]["foundation_runtime_concurrency_evidence_path"] == (
        "foundation-runtime-concurrency/"
        f"{PR306_G7_B3_SOURCE}-frc-g7-b3-20260703/"
        "2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json"
    )
    status_review = evidence["evidence_ref"]["operator_status_review"]
    assert status_review["schema_version"] == "ai-platform.g7-operator-status-review.v1"
    assert status_review["runtime_subject_commit_sha"] == PR306_G7_B3_SOURCE
    assert status_review["status"] == "candidate_evidence_requires_review"
    assert status_review["status_label_recommendation"] == "local partial"
    assert status_review["status_upgrade_decision"] == "not_approved_for_closure"
    assert status_review["g7_runtime_blocking_reasons"] == []
    assert status_review["required_next_steps"] == [
        "operator must explicitly approve any future G7 status upgrade after source-runtime and non-expansion boundary review",
        "record B3 seven-gate load evidence and b3_10x4_sdk_subagents profile evidence before any B3 closure or default increase",
    ]
    assert status_review["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
        "production_concurrency_defaults_raised": False,
        "g7_closed": False,
        "b3_closed": False,
        "foundation_alpha_complete": False,
    }
    assert evidence["open_followups"] == [
        "G7 remains pending explicit operator status-upgrade approval; this artifact records candidate status only.",
        "B3 recorded seven-gate load evidence and b3_10x4_sdk_subagents profile evidence remain missing.",
        "Do not use issue #164 closure history as current G7/B3 closure evidence.",
    ]

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-status-review/"
            f"{PR306_G7_B3_SOURCE}/"
            "2026-07-03-211-g7-operator-status-review-9c669761.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "211 verified",
        "gate closable",
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr308_label_clean_g7_and_frc_evidence_reaches_operator_review_without_overclosing():
    evidence = json.loads(PR308_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(PR308_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PR308_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR308_G7_B3_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:15903fd-g7-b3-label-clean-v2"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:805dfc27cb3cb70867e2f04a604b0716d1c098cdc6b6519a29b96a7cd20bb538"
    )
    assert evidence["source_ref"]["runtime_source_marker"] == PR308_G7_B3_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PR308_G7_B3_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:15903fd-g7-b3-label-clean-v2",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    assert evidence["source_ref"]["image_labels"]["ai-platform.runtime_subject"] == PR308_G7_B3_SOURCE
    assert evidence["source_ref"]["image_labels"]["ai-platform.source_commit"] == PR308_G7_B3_SOURCE
    assert "legacy source alias labels" in evidence["source_ref"]["source_authority_caveat"]

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-15903fd-label-clean-sudo-20260703055828"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["hardening"]["workspace_isolation"]["marker_path_is_container_path"] is True
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert bounded_error_projection_is_safe(
        runtime_check["hardening"]["resource_limits"]["bounded_error_projection"],
        run_id=runtime_check["run_id"],
    )
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["callback_probe_status"] == "delivered"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert runtime_check["remaining_gate_boundaries"] == [
        "same-subject Foundation Runtime concurrency evidence for 15903fd is recorded separately in the 2026-07-03 FRC evidence entry",
        "B3 seven-gate recorded load evidence remains missing",
        "b3_10x4_sdk_subagents operator-reviewed profile evidence remains missing",
        "operator status-upgrade review is still required before any G7 closure claim",
    ]

    assert frc_evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert frc_evidence["commit_sha"] == PR308_G7_B3_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == PR308_G7_B3_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == PR308_G7_B3_SOURCE
    assert frc_evidence["summary"]["max_observed_concurrency"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert all(check["status"] == "passed" for check in frc_evidence["checks"].values())
    assert frc_evidence["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert frc_evidence["non_expansion_invariants"]["production_concurrency_increase_allowed"] is False

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR308_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR308_G7_B3_SOURCE,
    )

    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-sandbox/"
            f"{PR308_G7_B3_SOURCE}/"
            "2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "4ace009f0db3",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_current_dirty_runtime_g7_and_frc_evidence_stays_local_partial():
    evidence = json.loads(CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_G7_B3_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-sandbox-live-env-hardening-755e50e"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["runtime_subject_commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:755e50e-g7-b3-principal-userid-fix-v2"
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:755e50e-g7-b3-principal-userid-fix-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
        "MAX_ACTIVE_WORKER_RUNS": "3",
    }
    assert evidence["source_ref"]["image_labels"]["ai-platform.build-dirty"] == "true"
    assert evidence["source_ref"]["runtime_source_marker"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["legacy_container_source_markers"]["api"] == {
        ".ai-platform-source-revision": PR306_G7_B3_SOURCE,
        ".source-commit": "28676df4abcbb7063211fceb4cc1701648c43d49",
    }
    assert "dirty runtime-only local patch" in evidence["source_ref"]["source_authority_caveat"]

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-755e50e-principal-userid-fix-v2-container-20260703115120"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["callbacks"] == ["running", "completed"]
    assert runtime_check["hardening"]["workspace_isolation"]["marker_path_is_container_path"] is True
    assert runtime_check["hardening"]["resource_limits"]["over_limit_cleanup_verified"] is True
    assert runtime_check["hardening"]["egress_policy"]["default_deny_outbound"] is True
    assert runtime_check["hardening"]["egress_policy"]["callback_probe_status"] == "delivered"
    assert runtime_check["hardening"]["security_options"]["docker_socket_mounted"] is False
    assert runtime_check["remaining_gate_boundaries"] == [
        "same-subject Foundation Runtime concurrency for 755e50e is recorded separately with status verified_foundation_runtime_concurrency",
        "B3 seven-gate recorded load evidence remains missing",
        "b3_10x4_sdk_subagents operator-reviewed profile evidence remains missing",
        "operator status-upgrade review is still required before any G7 closure claim",
        "the current 211 API/worker image is dirty runtime-only local patch evidence, not clean current-main 211 verified evidence",
    ]

    assert frc_evidence["schema_version"] == "ai-platform.foundation-runtime-concurrency.v1"
    assert frc_evidence["commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["summary"]["max_observed_concurrency"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert all(check["status"] == "passed" for check in frc_evidence["checks"].values())
    assert frc_evidence["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert frc_evidence["non_expansion_invariants"]["production_concurrency_increase_allowed"] is False

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE,
    )

    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "gate closable",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_current_dirty_runtime_capacity_visibility_stays_fail_closed():
    evidence = json.loads(CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-capacity-runtime-readiness-755e50e"
    assert evidence["artifact_kind"] == "capacity_gate_readiness"
    assert evidence["gate"] == "B3 Capacity Baseline"
    assert evidence["commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["runtime_subject_commit_sha"] == HISTORICAL_DIRTY_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["api_worker_image"] == "ai-platform:755e50e-g7-b3-principal-userid-fix-v2"
    assert evidence["source_ref"]["frontend_image"] == "ai-platform-frontend:4518a05"
    assert "dirty-runtime visibility only" in evidence["source_ref"]["source_authority_caveat"]
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["review_status"] == "reviewed"

    ref = evidence["evidence_ref"]
    assert ref["schema_version"] == "ai-platform.capacity-runtime-evidence.v1"
    assert ref["source_http_status"] == 200
    assert ref["admin_runtime_missing_sections"] == ["sandbox"]
    assert ref["readiness_status"] == "blocked_missing_admin_runtime_sections"
    assert ref["capacity_answer"] == "safe_max_concurrency_unproven_without_recorded_load_test_evidence"
    assert ref["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert ref["missing_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert ref["profile_evidence"] == {}
    assert ref["target_profile_id"] == "b3_10x4_sdk_subagents"
    assert ref["does_not_raise_defaults"] is True
    assert ref["does_not_claim_safe_concurrency"] is True
    assert ref["does_not_mark_recorded_load_test_gate"] is True
    assert ref["does_not_close_b3"] is True
    assert ref["does_not_make_clean_current_main_211_verified"] is True
    assert ref["does_not_make_issue_164_gate_closable"] is True

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized


def test_historical_a294727_g7_evidence_and_capacity_visibility_stays_local_partial_without_frc_or_status_upgrade():
    evidence = json.loads(CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_A294727.read_text(encoding="utf-8"))
    capacity_evidence = json.loads(CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH_A294727.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-04-211-g7-sandbox-live-env-hardening-a294727-source-marker-fix"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert evidence["runtime_subject_commit_sha"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:a294727-g7-b3-source-marker-fix-v1"
    assert evidence["source_ref"]["image_labels"]["ai-platform.build-dirty"] == "false"
    assert evidence["source_ref"]["runtime_source_marker"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["container_source_marker"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["container_source_marker_kind"] == "/app/.ai-platform-source-revision"
    assert evidence["source_ref"]["legacy_container_source_markers"] == {
        "/app/.codex-source-revision": "28676df4abcbb7063211fceb4cc1701648c43d49",
        "/app/.source-commit": "28676df4abcbb7063211fceb4cc1701648c43d49",
    }
    assert evidence["source_ref"]["legacy_container_marker_status"] == (
        "stale_legacy_marker_files_retained_not_source_authority"
    )
    assert (
        "legacy /app/.codex-source-revision and /app/.source-commit still show 28676df"
        in evidence["source_ref"]["source_authority_caveat"]
    )
    assert evidence["source_ref"]["repo_backend_source_marker"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["source_snapshot"]["source_tree_dirty"] is False
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:a294727-g7-b3-source-marker-fix-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }
    assert evidence["evidence_ref"]["result"] == "all_eight_checks_passed"
    assert evidence["evidence_ref"]["run_id"] == (
        "g7-live-env-hardening-a294727-source-marker-fix-20260704170251"
    )
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["verifier_summary"]["checks"])
    assert evidence["remaining_blockers"] == [
        "approved G7 status-upgrade review is still missing for a294727",
        "B3 seven recorded load-test gates are still missing",
        "b3_10x4_sdk_subagents profile evidence is still missing",
    ]

    assert capacity_evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert capacity_evidence["commit_sha"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert capacity_evidence["runtime_subject_commit_sha"] == POST_PR319_G7_B3_RUNTIME_SOURCE
    assert capacity_evidence["review_status"] == "reviewed"
    assert capacity_evidence["redaction_scan_status"] == "passed"
    assert capacity_evidence["source_ref"]["source_marker_status"] == (
        "canonical_source_revision_snapshot_and_runtime_labels_match_a294727_legacy_container_markers_stale_28676df"
    )
    assert capacity_evidence["source_ref"]["legacy_container_source_markers"] == {
        "/app/.codex-source-revision": "28676df4abcbb7063211fceb4cc1701648c43d49",
        "/app/.source-commit": "28676df4abcbb7063211fceb4cc1701648c43d49",
    }
    capacity_ref = capacity_evidence["evidence_ref"]
    assert capacity_ref["readiness_status"] == "blocked_missing_load_test_evidence"
    assert capacity_ref["admin_runtime_missing_sections"] == []
    assert capacity_ref["host_sandbox_observation_status"] == "accepted"
    assert capacity_ref["missing_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert capacity_ref["missing_profile_evidence"] == [
        "target_profile_id",
        "evidence_source",
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
        "sdk_subagent_fanout_measurement_ref",
        "production_concurrency_defaults_raised",
        "safe_concurrency_claimed",
        "ordinary_user_platform_multi_run_orchestration_enabled",
    ]

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=capacity_evidence["capacity_profile_readiness"],
        current_source_commit=POST_PR319_G7_B3_RUNTIME_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-a294727-source-marker-fix-20260704170251"
    )
    assert audit["g7"]["blocking_reasons"] == [
        "foundation_runtime_concurrency_evidence_missing_or_not_current_subject"
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["g7"]["status_upgrade_review"]["status"] == "not_provided"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True


def test_current_945db2b_g7_status_upgrade_approved_but_b3_stays_blocked():
    evidence = json.loads(CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_945DB2B.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_G7_B3_FRC_EVIDENCE_PATH_945DB2B.read_text(encoding="utf-8"))
    frc_readiness = json.loads(CURRENT_G7_B3_FRC_READINESS_PATH_945DB2B.read_text(encoding="utf-8"))
    capacity_evidence = json.loads(CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH_945DB2B.read_text(encoding="utf-8"))
    status_upgrade_evidence = json.loads(
        CURRENT_G7_B3_STATUS_UPGRADE_REVIEW_PATH_945DB2B.read_text(encoding="utf-8")
    )

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-05-211-g7-sandbox-live-env-hardening-945db2b-live-default"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert evidence["runtime_subject_commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:945db2b-g7-legacy-source-markers-v1"
    assert evidence["source_ref"]["image_id"] == (
        "sha256:7c191741cab4d6415dafdaea4d1ef5a38d9f80ff8574eba30109ccdbdf860dec"
    )
    assert evidence["source_ref"]["image_labels"]["ai-platform.build-dirty"] == "false"
    assert evidence["source_ref"]["runtime_source_marker"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["container_source_marker"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["container_source_markers"] == {
        "/app/.ai-platform-source-revision": CURRENT_G7_B3_RUNTIME_SOURCE,
        "/app/.codex-source-revision": CURRENT_G7_B3_RUNTIME_SOURCE,
        "/app/.source-commit": CURRENT_G7_B3_RUNTIME_SOURCE,
    }
    assert evidence["source_ref"]["legacy_container_marker_status"] == (
        "legacy_marker_files_reconciled_current_subject"
    )
    assert evidence["source_ref"]["repo_backend_source_marker"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["source_snapshot"]["source_tree_dirty"] is False
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:945db2b-g7-legacy-source-markers-v1",
    }
    assert evidence["evidence_ref"]["result"] == "all_eight_checks_passed"
    assert evidence["evidence_ref"]["run_id"] == (
        "g7-live-env-hardening-945db2b-live-default-20260704185430"
    )
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["verifier_summary"]["checks"])
    assert evidence["remaining_blockers"] == [
        "B3 seven recorded load-test gates are still missing",
        "b3_10x4_sdk_subagents profile evidence is still missing",
    ]
    assert "44daf19" in evidence["source_ref"]["source_authority_caveat"]
    assert (
        "Same-subject Foundation Runtime concurrency evidence and approved G7 "
        "status-upgrade evidence are recorded separately"
        in evidence["source_ref"]["source_authority_caveat"]
    )

    assert frc_evidence["commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert frc_readiness["status"] == "verified_foundation_runtime_concurrency"
    assert frc_readiness["verified"] is True
    assert frc_readiness["failures"] == []
    assert frc_evidence["summary"]["concurrent_request_count"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert all(check["status"] == "passed" for check in frc_evidence["checks"].values())

    assert capacity_evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert capacity_evidence["evidence_id"] == "2026-07-05-211-capacity-runtime-readiness-945db2b"
    assert capacity_evidence["commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert capacity_evidence["runtime_subject_commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert capacity_evidence["review_status"] == "reviewed"
    assert capacity_evidence["redaction_scan_status"] == "passed"
    assert capacity_evidence["source_ref"]["source_marker_status"] == (
        "source_revision_snapshot_runtime_labels_and_all_container_marker_files_match_945db2b"
    )
    capacity_ref = capacity_evidence["evidence_ref"]
    assert capacity_ref["readiness_status"] == "blocked_missing_load_test_evidence"
    assert capacity_ref["admin_runtime_missing_sections"] == []
    assert capacity_ref["host_sandbox_observation_status"] == "accepted"
    assert capacity_ref["missing_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert capacity_ref["missing_profile_evidence"] == [
        "target_profile_id",
        "evidence_source",
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
        "sdk_subagent_fanout_measurement_ref",
        "production_concurrency_defaults_raised",
        "safe_concurrency_claimed",
        "ordinary_user_platform_multi_run_orchestration_enabled",
    ]

    status_review = status_upgrade_evidence["evidence_ref"]["operator_status_review"]
    assert status_upgrade_evidence["artifact_kind"] == "211_g7_operator_status_review"
    assert status_upgrade_evidence["commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert status_upgrade_evidence["runtime_subject_commit_sha"] == CURRENT_G7_B3_RUNTIME_SOURCE
    assert status_upgrade_evidence["review_status"] == "reviewed"
    assert status_upgrade_evidence["redaction_scan_status"] == "passed"
    assert status_review["status"] == "status_upgrade_approved"
    assert status_review["status_label_recommendation"] == "g7_status_upgrade_approved"
    assert status_review["status_upgrade_decision"] == "approved_for_g7_status_upgrade"
    assert status_review["g7_runtime_blocking_reasons"] == []
    assert status_review["b3_blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=capacity_evidence["capacity_profile_readiness"],
        g7_status_upgrade_review=status_upgrade_evidence,
        current_source_commit=CURRENT_G7_B3_RUNTIME_SOURCE,
    )
    assert audit["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-945db2b-live-default-20260704185430"
    )
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["foundation_runtime_concurrency_status"] == (
        "verified_foundation_runtime_concurrency"
    )
    assert audit["g7"]["status"] == "status_upgrade_approved"
    assert audit["g7"]["status_upgrade_review"]["status"] == "accepted"
    assert audit["g7"]["status_upgrade_review"]["status_upgrade_decision"] == (
        "approved_for_g7_status_upgrade"
    )
    assert audit["g7"]["required_next_steps"] == []
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert "g8_ordinary_user_multi_agent_exposure" not in audit["b3"]["blocking_reasons"]
    assert audit["status"] == "blocked_missing_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is False
    assert audit["does_not_close_b3"] is True

    serialized = json.dumps(
        [evidence, frc_evidence, capacity_evidence, status_upgrade_evidence, audit],
        ensure_ascii=False,
    ).lower()
    assert "g8_ordinary_user_multi_agent_exposure" not in serialized
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_clean_53887e2_b3_recorded_evidence_reaches_operator_review_only():
    capacity_evidence = json.loads(CLEAN_B3_RECORDED_CAPACITY_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CLEAN_B3_RECORDED_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_readiness = json.loads(CLEAN_B3_RECORDED_FRC_READINESS_PATH.read_text(encoding="utf-8"))

    assert capacity_evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert capacity_evidence["evidence_id"] == (
        "2026-07-05-211-capacity-recorded-gate-readiness-53887e2"
    )
    assert capacity_evidence["artifact_kind"] == "capacity_gate_readiness"
    assert capacity_evidence["commit_sha"] == CLEAN_B3_RECORDED_RUNTIME_SOURCE
    assert capacity_evidence["runtime_subject_commit_sha"] == CLEAN_B3_RECORDED_RUNTIME_SOURCE
    assert capacity_evidence["review_status"] == "reviewed"
    assert capacity_evidence["redaction_scan_status"] == "passed"
    assert capacity_evidence["source_ref"]["api_worker_image"] == (
        "ai-platform:53887e2-b3-recorded-clean-v1"
    )
    assert capacity_evidence["source_ref"]["api_worker_image_build_dirty"] is False

    capacity_ref = capacity_evidence["evidence_ref"]
    assert capacity_ref["recorded_gate_batch_snapshot_status"] == (
        "recorded_gate_batch_input_accepted"
    )
    assert capacity_ref["recorded_gate_batch_readiness_status"] == "ready_for_operator_review"
    assert capacity_ref["capacity_profile_readiness_status"] == "operator_review_required"
    assert capacity_ref["input_status"] == {
        "profile_evidence": "accepted",
        "recorded_gate_evidence": "accepted",
        "runtime_evidence": "accepted",
    }
    assert capacity_ref["load_test_evidence_status"] == "recorded"
    assert capacity_ref["recorded_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert capacity_ref["missing_load_test_gates"] == []
    assert capacity_ref["invalid_load_test_evidence"] == []
    assert capacity_ref["profile_evidence"] == {
        "target_profile_id": "b3_10x4_sdk_subagents",
        "evidence_source": "live_worker_run_payload",
        "observed_concurrent_sessions": 10,
        "observed_peak_sdk_subagents_per_session": 4,
        "sdk_subagent_fanout_measurement_ref": (
            "capacity-evidence/b3/b3-sdk-subagent-fanout-measurement-summary.json"
        ),
        "production_concurrency_defaults_raised": False,
        "safe_concurrency_claimed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
    }
    fanout = capacity_ref["strict_sdk_fanout_measurement_summary"]
    assert fanout["runtime_image"] == "ai-platform:53887e2-b3-recorded-clean-v1"
    assert fanout["runtime_image_dirty"] is False
    assert fanout["sdk_transcript_run_count"] == 10
    assert fanout["agent_type_total"] == {"general-purpose": 40}
    assert fanout["runs_with_exactly_4_agent_tool_uses"] == 10
    assert fanout["runs_with_exactly_4_tool_results"] == 10
    assert fanout["runs_with_exactly_4_subagent_jsonl"] == 10
    assert fanout["runs_with_exactly_4_subagent_meta"] == 10
    assert fanout["workspace_git_head_present_count"] == 10
    assert fanout["redaction"]["raw_private_content_excluded"] is True
    assert capacity_ref["foundation_runtime_concurrency"]["readiness_status"] == (
        "verified_foundation_runtime_concurrency"
    )
    assert capacity_ref["foundation_runtime_concurrency"]["verified"] is True
    assert capacity_ref["foundation_runtime_concurrency"]["failures"] == []
    assert capacity_ref["production_default_decision"] == (
        "operator_review_required_before_default_change"
    )
    assert capacity_ref["non_expansion_invariants"] == {
        "production_concurrency_defaults_raised": False,
        "safe_concurrency_claimed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
        "ordinary_user_platform_multi_run_orchestration_exposure": False,
    }
    assert capacity_evidence["does_not_close_b3"] is True
    assert capacity_evidence["does_not_raise_production_defaults"] is True
    assert capacity_evidence["does_not_make_gate_closable"] is True

    assert frc_evidence["commit_sha"] == CLEAN_B3_RECORDED_RUNTIME_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == CLEAN_B3_RECORDED_RUNTIME_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == CLEAN_B3_RECORDED_RUNTIME_SOURCE
    assert frc_readiness["status"] == "verified_foundation_runtime_concurrency"
    assert frc_readiness["verified"] is True
    assert frc_readiness["failures"] == []

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": CLEAN_B3_RECORDED_RUNTIME_SOURCE,
            "runtime_image": "ai-platform:53887e2-b3-recorded-clean-v1",
            "runtime_image_labels": {
                "ai-platform.source-revision": CLEAN_B3_RECORDED_RUNTIME_SOURCE,
                "ai-platform.runtime-subject": CLEAN_B3_RECORDED_RUNTIME_SOURCE,
                "org.opencontainers.image.revision": CLEAN_B3_RECORDED_RUNTIME_SOURCE,
            },
            "api_env": {
                "SANDBOX_CONTAINER_PROVIDER": "docker",
                "SANDBOX_EXECUTOR_IMAGE": "ai-platform:53887e2-b3-recorded-clean-v1",
                "SANDBOX_EGRESS_POLICY_ENABLED": "true",
            },
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=capacity_evidence["capacity_profile_readiness"],
        current_source_commit=CLEAN_B3_RECORDED_RUNTIME_SOURCE,
    )
    assert audit["b3"]["status"] == "operator_review_required"
    assert audit["b3"]["blocking_reasons"] == []
    assert audit["b3"]["missing_recorded_load_test_gates"] == []
    assert audit["b3"]["missing_profile_evidence"] == []
    assert audit["b3"]["production_default_decision"] == (
        "operator_review_required_before_default_change"
    )
    assert audit["status"] == "blocked_missing_g7_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_close_b3"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_claim_211_verified"] is True

    serialized = json.dumps([capacity_evidence, frc_evidence, audit], ensure_ascii=False).lower()
    assert "g8_ordinary_user_multi_agent_exposure" not in serialized
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_cli_accepts_current_945db2b_g7_status_upgrade_without_b3_overclosure(tmp_path):
    evidence = json.loads(CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_945DB2B.read_text(encoding="utf-8"))
    capacity_evidence = json.loads(CURRENT_G7_B3_CAPACITY_EVIDENCE_PATH_945DB2B.read_text(encoding="utf-8"))
    runtime_observation_path = tmp_path / "runtime-observation-945db2b.json"
    capacity_profile_readiness_path = tmp_path / "capacity-profile-readiness-945db2b.json"
    runtime_observation_path.write_text(
        json.dumps(
            {
                "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
                "runtime_image": evidence["source_ref"]["image"],
                "runtime_image_labels": evidence["source_ref"]["image_labels"],
                "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            }
        ),
        encoding="utf-8",
    )
    capacity_profile_readiness_path.write_text(
        json.dumps(capacity_evidence["capacity_profile_readiness"]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--capacity-profile-readiness-json",
            str(capacity_profile_readiness_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_G7_B3_RUNTIME_EVIDENCE_PATH_945DB2B),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_G7_B3_FRC_EVIDENCE_PATH_945DB2B),
            "--g7-status-upgrade-review-json",
            str(CURRENT_G7_B3_STATUS_UPGRADE_REVIEW_PATH_945DB2B),
            "--current-source-commit",
            CURRENT_G7_B3_RUNTIME_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["blocking_reasons"] == []
    assert payload["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert payload["g7"]["status"] == "status_upgrade_approved"
    assert payload["g7"]["status_upgrade_review"]["status"] == "accepted"
    assert payload["g7"]["status_upgrade_review"]["status_upgrade_decision"] == (
        "approved_for_g7_status_upgrade"
    )
    assert payload["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert "g8_ordinary_user_multi_agent_exposure" not in payload["b3"]["blocking_reasons"]
    assert payload["status"] == "blocked_missing_b3_completion_evidence"
    assert payload["does_not_close_g7"] is False
    assert payload["does_not_close_b3"] is True
    assert payload["does_not_claim_gate_closable"] is True
    assert "g8_ordinary_user_multi_agent_exposure" not in json.dumps(payload)


def test_prior_clean_main_g7_b3_evidence_records_candidate_without_closure():
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))
    capacity_evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_CAPACITY_EVIDENCE_PATH.read_text(encoding="utf-8"))
    status_review_evidence = json.loads(
        CURRENT_CLEAN_MAIN_G7_B3_OPERATOR_STATUS_REVIEW_PATH.read_text(encoding="utf-8")
    )

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-sandbox-live-env-hardening-61073b1-clean-main"
    assert evidence["artifact_kind"] == "211_sandbox_runtime_smoke"
    assert evidence["commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["image"] == "ai-platform:61073b1-g7-b3-clean-main-v1"
    assert evidence["source_ref"]["image_labels"]["ai-platform.build-dirty"] == "false"
    assert evidence["source_ref"]["runtime_source_marker"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["container_source_marker"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["repo_backend_source_marker"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert evidence["source_ref"]["safe_live_runtime_env"] == {
        "SANDBOX_CONTAINER_PROVIDER": "docker",
        "SANDBOX_EXECUTOR_IMAGE": "ai-platform:61073b1-g7-b3-clean-main-v1",
        "SANDBOX_EGRESS_POLICY_ENABLED": "true",
    }

    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]
    assert runtime_check["run_id"] == "g7-live-env-hardening-61073b1-clean-main-20260703161911"
    assert runtime_check["runtime_mode"] == "platform"
    assert runtime_check["sandbox_provider"] == "docker"
    assert runtime_check["executed_task"] is True
    assert all(item["passed"] is True for item in evidence["evidence_ref"]["runtime_checks"]["verifier_checks"])
    assert runtime_check["does_not_close_g7_gate"] is True
    assert runtime_check["does_not_close_b3_gate"] is True

    assert frc_evidence["commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["source_tree_commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["runtime_subject_commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert frc_evidence["summary"]["max_observed_concurrency"] == 12
    assert frc_evidence["summary"]["tenant_count"] == 2
    assert frc_evidence["summary"]["user_count"] == 4
    assert all(check["status"] == "passed" for check in frc_evidence["checks"].values())
    assert frc_evidence["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert frc_evidence["non_expansion_invariants"]["production_concurrency_increase_allowed"] is False

    assert capacity_evidence["commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert capacity_evidence["runtime_subject_commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    capacity_ref = capacity_evidence["evidence_ref"]
    assert capacity_ref["readiness_status"] == "blocked_missing_admin_runtime_sections"
    assert capacity_ref["admin_runtime_missing_sections"] == ["sandbox"]
    assert capacity_ref["missing_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert capacity_ref["profile_evidence"] == {}
    assert capacity_ref["target_profile_id"] == "b3_10x4_sdk_subagents"
    assert capacity_ref["does_not_close_b3"] is True
    assert capacity_ref["does_not_close_g7"] is True

    status_review = status_review_evidence["evidence_ref"]["operator_status_review"]
    assert status_review_evidence["artifact_kind"] == "211_g7_operator_status_review"
    assert status_review_evidence["commit_sha"] == PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE
    assert status_review["status"] == "candidate_evidence_requires_review"
    assert status_review["status_label_recommendation"] == "local partial"
    assert status_review["status_upgrade_decision"] == "not_approved_for_closure"
    assert status_review["g7_runtime_blocking_reasons"] == []
    assert status_review["b3_blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert status_review["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
        "production_concurrency_defaults_raised": False,
        "g7_closed": False,
        "b3_closed": False,
        "foundation_alpha_complete": False,
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
    )
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-status-review/"
            f"{PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE}/"
            "2026-07-03-211-g7-operator-status-review-61073b1-clean-main.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(
        {
            "g7": evidence,
            "frc": frc_evidence,
            "capacity": capacity_evidence,
            "status_review": status_review_evidence,
        },
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/tmp/",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
        "gate closable",
        "211 verified",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_pr308_g7_operator_status_review_artifact_records_candidate_without_overclosing():
    evidence = json.loads(PR308_G7_OPERATOR_STATUS_REVIEW_PATH.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert evidence["evidence_id"] == "2026-07-03-211-g7-operator-status-review-15903fd-label-clean"
    assert evidence["artifact_kind"] == "211_g7_operator_status_review"
    assert evidence["gate"] == "G7 Sandbox / Resource Hardening"
    assert evidence["commit_sha"] == PR308_G7_B3_SOURCE
    assert evidence["runtime_subject_commit_sha"] == PR308_G7_B3_SOURCE
    assert evidence["review_status"] == "reviewed"
    assert evidence["redaction_scan_status"] == "passed"
    assert evidence["source_ref"]["g7_live_env_evidence_path"] == (
        "g7-sandbox/"
        f"{PR308_G7_B3_SOURCE}/"
        "2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json"
    )
    assert evidence["source_ref"]["foundation_runtime_concurrency_evidence_path"] == (
        "foundation-runtime-concurrency/"
        f"{PR308_G7_B3_SOURCE}-frc-g7-b3-20260703/"
        "2026-07-03-211-foundation-alpha-poc-15903fd-foundation-runtime-concurrency.json"
    )
    assert evidence["source_ref"]["source_runtime_boundary"]["legacy_alias_label_cleanup_required"] is False
    assert evidence["source_ref"]["source_runtime_boundary"]["source_runtime_boundary_reviewed_for_candidate_status"] is True
    status_review = evidence["evidence_ref"]["operator_status_review"]
    assert status_review["schema_version"] == "ai-platform.g7-operator-status-review.v1"
    assert status_review["runtime_subject_commit_sha"] == PR308_G7_B3_SOURCE
    assert status_review["status"] == "candidate_evidence_requires_review"
    assert status_review["status_label_recommendation"] == "local partial"
    assert status_review["status_upgrade_decision"] == "not_approved_for_closure"
    assert status_review["g7_runtime_blocking_reasons"] == []
    assert status_review["b3_blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert status_review["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
        "production_concurrency_defaults_raised": False,
        "g7_closed": False,
        "b3_closed": False,
        "foundation_alpha_complete": False,
    }
    assert evidence["open_followups"] == [
        "G7 remains pending explicit operator status-upgrade approval; this artifact records candidate evidence only.",
        "B3 recorded seven-gate load evidence and b3_10x4_sdk_subagents profile evidence remain missing.",
        "Do not use issue #164 closure history as current G7/B3 closure evidence.",
    ]

    acceptance = build_release_evidence_export_acceptance()
    assert acceptance["status"] == "ready_for_operator_review"
    assert any(
        entry["path"]
        == (
            "g7-status-review/"
            f"{PR308_G7_B3_SOURCE}/"
            "2026-07-03-211-g7-operator-status-review-15903fd-label-clean.json"
        )
        for entry in acceptance["entries"]
    )

    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in (
        "211 verified",
        "gate closable",
        "openai_api_key",
        "anthropic_auth_token",
        "database_url",
        "redis_url",
        "/home/xinlin",
        "/var/run/docker.sock",
        "c:\\users",
    ):
        assert forbidden not in serialized
    assert_no_sensitive_callback_token_leak(serialized)


def test_audit_preserves_current_live_env_over_reviewed_evidence_when_executor_image_regresses():
    evidence = json.loads(PR297_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(PR297_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": PR297_G7_B3_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": {
                "SANDBOX_CONTAINER_PROVIDER": "docker",
                "SANDBOX_EXECUTOR_IMAGE": "ai-platform:ae6b7e5-g7-b3-label-repair-v1",
                "SANDBOX_EGRESS_POLICY_ENABLED": "true",
            },
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=PR297_G7_B3_SOURCE,
    )

    assert audit["g7"]["live_api_sandbox_executor_image"] == (
        "ai-platform:ae6b7e5-g7-b3-label-repair-v1"
    )
    assert audit["g7"]["safe_runtime_env"]["SANDBOX_EXECUTOR_IMAGE"] == (
        "ai-platform:ae6b7e5-g7-b3-label-repair-v1"
    )
    assert "live_api_sandbox_executor_image_not_current_main_bound" in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-4805031-20260702023507"
    )
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"


def test_audit_merges_latest_reviewed_g7_evidence_over_stale_runtime_observation():
    runtime_observation = _stale_current_main_runtime_observation()
    label_repair = json.loads(CURRENT_MAIN_G7_LABEL_REPAIR_EVIDENCE_PATH.read_text(encoding="utf-8"))
    live_env_hardening = json.loads(CURRENT_MAIN_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_observation["reviewed_release_evidence_entries"] = [
        label_repair,
        live_env_hardening,
    ]
    runtime_observation["foundation_runtime_concurrency_evidence"] = json.loads(
        CURRENT_MAIN_FRC_EVIDENCE_PATH.read_text(encoding="utf-8")
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_MAIN_G7_SOURCE,
    )

    assert audit["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-ae6b7e5-20260702045743"
    )
    assert audit["g7"]["canonical_runtime_label_commit"] == CURRENT_MAIN_G7_SOURCE
    assert audit["g7"]["legacy_runtime_label_commit"] == CURRENT_MAIN_G7_SOURCE
    assert audit["g7"]["live_api_sandbox_provider"] == "docker"
    assert audit["g7"]["live_api_sandbox_executor_image"] == (
        "ai-platform:ae6b7e5-g7-b3-label-repair-v1"
    )
    assert audit["g7"]["live_api_sandbox_egress_policy_enabled"] == "true"
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["b3"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True


def test_audit_reports_current_g7_b3_blockers_without_status_overclaiming():
    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["schema_version"] == "ai-platform.g7-b3-completion-audit.v1"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True

    assert audit["g7"]["status"] == "blocked"
    assert audit["g7"]["source_marker_commit"] == CURRENT_SOURCE
    assert audit["g7"]["runtime_image"] == "ai-platform:d318f9f-g7-b3-runtime-only-v1"
    assert audit["g7"]["canonical_runtime_label_commit"] == RUNTIME_SUBJECT
    assert audit["g7"]["legacy_runtime_label_commit"] == LEGACY_LABEL_SUBJECT
    assert "current_main_source_runtime_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert "live_api_uses_fake_sandbox_provider" in audit["g7"]["blocking_reasons"]
    assert "reviewed_local_release_evidence_entry_missing" in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["live_api_sandbox_provider"] == "fake"
    assert audit["g7"]["required_next_steps"] == [
        "reconcile current-main source marker, runtime image labels, and reviewed release-evidence binding",
        "move live API and worker default sandbox posture from fake provider to the reviewed Docker-provider path",
        "wrap current-main G7 Docker sandbox hardening verifier output as reviewed release evidence",
        "rerun Foundation Runtime concurrency evidence for the same current runtime subject",
    ]

    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["target_profile_id"] == "b3_10x4_sdk_subagents"
    assert audit["b3"]["missing_recorded_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert audit["b3"]["missing_profile_evidence"] == [
        "target_profile_id",
        "evidence_source",
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
        "sdk_subagent_fanout_measurement_ref",
        "production_concurrency_defaults_raised",
        "safe_concurrency_claimed",
        "ordinary_user_platform_multi_run_orchestration_enabled",
    ]
    assert "b3_recorded_load_test_gates_missing" in audit["b3"]["blocking_reasons"]
    assert "b3_10x4_sdk_subagents_profile_evidence_missing" in audit["b3"]["blocking_reasons"]
    assert audit["b3"]["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"

    serialized = json.dumps(audit, ensure_ascii=False).lower()
    assert "211 verified" not in serialized
    assert "gate closable" not in serialized
    assert "must-not-leak" not in serialized
    assert_no_sensitive_callback_token_leak(serialized)
    assert "akia_should_not_print" not in serialized
    assert "secretpass" not in serialized
    assert "openai_base_url" not in serialized


def test_audit_accepts_reviewed_current_main_g7_evidence_without_overclosing():
    audit = build_g7_b3_completion_audit(
        runtime_observation=_current_main_runtime_observation_with_reviewed_g7(),
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["status_label"] == "local partial"
    assert audit["g7"]["status"] == "blocked"
    assert "reviewed_local_release_evidence_entry_missing" not in audit["g7"]["blocking_reasons"]
    assert "current_main_source_runtime_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert "live_api_uses_fake_sandbox_provider" in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["reviewed_release_evidence_id"] == "g7-current-main-3071a02"
    assert audit["g7"]["required_next_steps"] == [
        "clean stale runtime alias labels that still point at an older runtime subject",
        "move live API and worker default sandbox posture from fake provider to the reviewed Docker-provider path",
        "rerun Foundation Runtime concurrency evidence for the same current runtime subject",
    ]
    assert audit["b3"]["status"] == "blocked"
    assert audit["does_not_claim_211_verified"] is True
    assert audit["does_not_claim_gate_closable"] is True


def test_g7_operator_status_review_cannot_override_runtime_identity_or_env():
    runtime_observation = _runtime_observation()
    runtime_observation["reviewed_release_evidence"] = {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "artifact_kind": "211_g7_operator_status_review",
        "gate": "G7 Sandbox / Resource Hardening",
        "commit_sha": CURRENT_SOURCE,
        "runtime_subject_commit_sha": CURRENT_SOURCE,
        "review_status": "reviewed",
        "redaction_scan_status": "passed",
        "source_ref": {
            "runtime_source_marker": CURRENT_SOURCE,
            "image": "ai-platform:should-not-override",
            "image_labels": {
                "ai-platform.source-revision": CURRENT_SOURCE,
                "ai-platform.runtime-subject": CURRENT_SOURCE,
                "org.opencontainers.image.revision": CURRENT_SOURCE,
                "ai-platform.source_revision": CURRENT_SOURCE,
                "ai-platform.runtime_subject": CURRENT_SOURCE,
            },
            "safe_live_runtime_env": {
                "SANDBOX_CONTAINER_PROVIDER": "docker",
                "SANDBOX_EXECUTOR_IMAGE": "ai-platform:should-not-override",
                "SANDBOX_EGRESS_POLICY_ENABLED": "true",
            },
        },
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["g7"]["runtime_image"] == "ai-platform:d318f9f-g7-b3-runtime-only-v1"
    assert audit["g7"]["live_api_sandbox_provider"] == "fake"
    assert audit["g7"]["live_api_sandbox_executor_image"] == "ai-platform:local"
    assert "current_main_source_runtime_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert "reviewed_local_release_evidence_entry_missing" in audit["g7"]["blocking_reasons"]


def test_audit_accepts_current_main_foundation_runtime_concurrency_without_overclosing():
    runtime_observation = _current_main_runtime_observation_with_reviewed_g7()
    runtime_observation["foundation_runtime_concurrency_evidence"] = (
        _current_main_foundation_runtime_concurrency_evidence()
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["g7"]["foundation_runtime_concurrency_status"] == (
        "verified_foundation_runtime_concurrency"
    )
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert audit["g7"]["required_next_steps"] == [
        "clean stale runtime alias labels that still point at an older runtime subject",
        "move live API and worker default sandbox posture from fake provider to the reviewed Docker-provider path",
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["b3"]["status"] == "blocked"
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True


def test_audit_keeps_g7_blocked_after_label_repair_when_live_api_still_uses_fake_provider():
    runtime_observation = _current_main_runtime_observation_with_reviewed_g7()
    runtime_observation["runtime_image"] = "ai-platform:3071a02-g7-b3-label-repair-v1"
    runtime_observation["runtime_image_labels"] = {
        "ai-platform.source-revision": CURRENT_SOURCE,
        "ai-platform.runtime-subject": CURRENT_SOURCE,
        "org.opencontainers.image.revision": CURRENT_SOURCE,
        "ai-platform.source_revision": CURRENT_SOURCE,
        "ai-platform.runtime_subject": CURRENT_SOURCE,
        "ai-platform.source_tree_commit": CURRENT_SOURCE,
    }
    runtime_observation["foundation_runtime_concurrency_evidence"] = (
        _current_main_foundation_runtime_concurrency_evidence()
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert "current_main_source_runtime_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["blocking_reasons"] == ["live_api_uses_fake_sandbox_provider"]
    assert audit["g7"]["required_next_steps"] == [
        "move live API and worker default sandbox posture from fake provider to the reviewed Docker-provider path",
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["status_label"] == "local partial"
    assert audit["does_not_close_g7"] is True


def test_audit_keeps_g7_blocked_when_same_subject_foundation_runtime_concurrency_missing():
    evidence = json.loads(CURRENT_MAIN_G7_LIVE_ENV_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_check = evidence["evidence_ref"]["runtime_checks"]["g7_211_sandbox_runtime_hardening"]

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": CURRENT_MAIN_G7_SOURCE,
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
        },
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_MAIN_G7_SOURCE,
    )

    assert audit["g7"]["reviewed_release_evidence_id"] == runtime_check["run_id"]
    assert audit["g7"]["foundation_runtime_concurrency_current_subject"] is False
    assert audit["g7"]["blocking_reasons"] == [
        "foundation_runtime_concurrency_evidence_missing_or_not_current_subject"
    ]
    assert audit["g7"]["required_next_steps"] == [
        "rerun Foundation Runtime concurrency evidence for the same current runtime subject"
    ]
    assert audit["g7"]["status"] == "blocked"
    assert audit["status"] == "blocked_missing_g7_b3_completion_evidence"
    assert audit["does_not_claim_211_verified"] is True


def test_audit_does_not_treat_legacy_alias_labels_as_canonical_runtime_labels():
    runtime_observation = _current_main_runtime_observation_with_reviewed_g7()
    runtime_observation["runtime_image_labels"] = {
        "ai-platform.source_revision": CURRENT_SOURCE,
        "ai-platform.runtime_subject": CURRENT_SOURCE,
        "ai-platform.source_tree_commit": CURRENT_SOURCE,
    }
    runtime_observation["foundation_runtime_concurrency_evidence"] = (
        _current_main_foundation_runtime_concurrency_evidence()
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["g7"]["canonical_runtime_label_commit"] == ""
    assert audit["g7"]["legacy_runtime_label_commit"] == CURRENT_SOURCE
    assert "current_main_source_runtime_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["required_next_steps"][0] == (
        "reconcile current-main source marker, runtime image labels, and reviewed release-evidence binding"
    )


def test_audit_flags_mixed_stale_legacy_alias_labels_even_when_first_alias_is_current():
    stale_subject = "9c669761bbb4bd719af64a341d361b7c3b3e380e"
    runtime_observation = _current_main_runtime_observation_with_reviewed_g7()
    runtime_observation["source_marker_commit"] = CURRENT_SOURCE
    runtime_observation["runtime_image_labels"] = {
        "ai-platform.source-revision": CURRENT_SOURCE,
        "ai-platform.runtime-subject": CURRENT_SOURCE,
        "org.opencontainers.image.revision": CURRENT_SOURCE,
        "ai-platform.source_revision": CURRENT_SOURCE,
        "ai-platform.runtime_subject": stale_subject,
        "ai-platform.source_tree_commit": stale_subject,
    }
    runtime_observation["foundation_runtime_concurrency_evidence"] = (
        _current_main_foundation_runtime_concurrency_evidence()
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["g7"]["canonical_runtime_label_commit"] == CURRENT_SOURCE
    assert audit["g7"]["legacy_runtime_label_commit"] == CURRENT_SOURCE
    assert "current_main_source_runtime_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["required_next_steps"][0] == (
        "clean stale runtime alias labels that still point at an older runtime subject"
    )


def test_audit_flags_stale_source_commit_alias_label():
    stale_subject = "9c669761bbb4bd719af64a341d361b7c3b3e380e"
    runtime_observation = _current_main_runtime_observation_with_reviewed_g7()
    runtime_observation["source_marker_commit"] = CURRENT_SOURCE
    runtime_observation["runtime_image_labels"] = {
        "ai-platform.source-revision": CURRENT_SOURCE,
        "ai-platform.runtime-subject": CURRENT_SOURCE,
        "org.opencontainers.image.revision": CURRENT_SOURCE,
        "ai-platform.source_revision": CURRENT_SOURCE,
        "ai-platform.runtime_subject": CURRENT_SOURCE,
        "ai-platform.source_tree_commit": CURRENT_SOURCE,
        "ai-platform.source_commit": stale_subject,
    }
    runtime_observation["foundation_runtime_concurrency_evidence"] = (
        _current_main_foundation_runtime_concurrency_evidence()
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=None,
        current_source_commit=CURRENT_SOURCE,
    )

    assert "current_main_source_runtime_label_mismatch" not in audit["g7"]["blocking_reasons"]
    assert "stale_runtime_alias_label_mismatch" in audit["g7"]["blocking_reasons"]


def test_audit_can_consume_capacity_profile_readiness_and_render_markdown():
    capacity_profile_readiness = {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": "blocked_missing_profile_evidence",
        "source_gate_readiness": {
            "status": "blocked_missing_load_test_evidence",
            "missing_load_test_gates": [
                "api_read_write_burst",
                "model_gateway_timeout_and_backpressure",
            ],
        },
        "profiles": [
            {
                "id": "b3_10x4_sdk_subagents",
                "status": "blocked_missing_profile_evidence",
                "missing_profile_evidence": ["sdk_subagent_fanout_measurement_ref"],
            }
        ],
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=capacity_profile_readiness,
        current_source_commit=CURRENT_SOURCE,
    )
    markdown = render_g7_b3_completion_audit_markdown(audit)

    assert audit["b3"]["missing_recorded_load_test_gates"] == [
        "api_read_write_burst",
        "model_gateway_timeout_and_backpressure",
    ]
    assert audit["b3"]["missing_profile_evidence"] == ["sdk_subagent_fanout_measurement_ref"]
    assert "# G7/B3 Completion Audit" in markdown
    assert "Status: `blocked_missing_g7_b3_completion_evidence`" in markdown
    assert "`current_main_source_runtime_label_mismatch`" in markdown
    assert "`stale_runtime_alias_label_mismatch`" in markdown
    assert "`live_api_uses_fake_sandbox_provider`" in markdown
    assert "`b3_recorded_load_test_gates_missing`" in markdown
    assert "does not close G7: `true`" in markdown
    assert "does not close B3: `true`" in markdown
    assert "211 verified" not in markdown.lower()
    assert "gate closable" not in markdown.lower()


def test_b3_audit_rejects_truncated_readiness_even_with_empty_missing_lists():
    capacity_profile_readiness = {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": "operator_review_required",
        "source_gate_readiness": {
            "status": "operator_review_required",
            "missing_load_test_gates": [],
        },
        "profiles": [
            {
                "id": "b3_10x4_sdk_subagents",
                "status": "operator_review_required",
                "missing_profile_evidence": [],
            }
        ],
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=capacity_profile_readiness,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["b3"]["status"] == "blocked"
    assert "b3_capacity_readiness_inconsistent" in audit["b3"]["blocking_reasons"]
    assert "b3_recorded_load_test_gates_missing" in audit["b3"]["blocking_reasons"]
    assert "b3_10x4_sdk_subagents_profile_evidence_missing" in audit["b3"]["blocking_reasons"]
    assert audit["b3"]["missing_recorded_load_test_gates"] == LOAD_TEST_GATES
    assert audit["b3"]["missing_profile_evidence"] == B3_REQUIRED_PROFILE_EVIDENCE
    assert audit["b3"]["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )
    assert audit["b3"]["does_not_raise_production_defaults"] is True
    assert audit["b3"]["does_not_enable_ordinary_user_platform_multi_run_orchestration"] is True
    assert audit["does_not_close_b3"] is True


def test_b3_audit_requires_operator_review_before_default_change_when_evidence_complete():
    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=_complete_b3_capacity_profile_readiness(),
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["b3"]["status"] == "operator_review_required"
    assert audit["b3"]["blocking_reasons"] == []
    assert audit["b3"]["missing_recorded_load_test_gates"] == []
    assert audit["b3"]["missing_profile_evidence"] == []
    assert audit["b3"]["production_default_decision"] == (
        "operator_review_required_before_default_change"
    )
    assert audit["b3"]["does_not_raise_production_defaults"] is True
    assert audit["b3"]["does_not_enable_ordinary_user_platform_multi_run_orchestration"] is True
    assert audit["does_not_close_b3"] is True


def test_b3_audit_rejects_invalid_load_test_gates_even_when_all_rows_recorded():
    capacity_profile_readiness = _complete_b3_capacity_profile_readiness()
    capacity_profile_readiness["source_gate_readiness"]["invalid_load_test_gates"] = [
        "api_read_write_burst"
    ]

    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=capacity_profile_readiness,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["b3"]["status"] == "blocked"
    assert "b3_capacity_readiness_inconsistent" in audit["b3"]["blocking_reasons"]
    assert "b3_recorded_load_test_gates_missing" in audit["b3"]["blocking_reasons"]
    assert audit["b3"]["missing_recorded_load_test_gates"] == ["api_read_write_burst"]
    assert audit["b3"]["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )


def test_b3_audit_accepts_capacity_profile_readiness_allowlisted_profile_sources():
    for evidence_source in [
        "platform_runtime_profile",
        "live_worker_run_payload",
        "operator_reviewed_recorded_snapshot",
    ]:
        observed_profile_evidence = {
            "target_profile_id": "b3_10x4_sdk_subagents",
            "evidence_source": evidence_source,
            "observed_concurrent_sessions": 10,
            "observed_peak_sdk_subagents_per_session": 4,
            "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
            "production_concurrency_defaults_raised": False,
            "safe_concurrency_claimed": False,
            "ordinary_user_platform_multi_run_orchestration_enabled": False,
        }
        capacity_profile_readiness = {
            "schema_version": "ai-platform.capacity-profile-readiness.v1",
            "status": "operator_review_required",
            "source_gate_readiness": {
                "schema_version": "ai-platform.capacity-gate-readiness.v1",
                "status": "ready_for_operator_review",
                "load_test_gates": [
                    {"gate": gate, "status": "recorded"}
                    for gate in LOAD_TEST_GATES
                ],
                "missing_load_test_gates": [],
                "invalid_load_test_gates": [],
                "profile_evidence": {
                    "b3_10x4_sdk_subagents": observed_profile_evidence,
                },
            },
            "profiles": [
                {
                    "id": "b3_10x4_sdk_subagents",
                    "status": "operator_review_required",
                    "required_load_test_gates": list(LOAD_TEST_GATES),
                    "missing_load_test_gates": [],
                    "invalid_load_test_gates": [],
                    "profile_evidence_status": "accepted",
                    "missing_profile_evidence": [],
                    "observed_profile_evidence": observed_profile_evidence,
                }
            ],
        }

        audit = build_g7_b3_completion_audit(
            runtime_observation=_runtime_observation(),
            capacity_profile_readiness=capacity_profile_readiness,
            current_source_commit=CURRENT_SOURCE,
        )

        assert audit["b3"]["status"] == "operator_review_required"
        assert audit["b3"]["blocking_reasons"] == []
        assert audit["b3"]["missing_profile_evidence"] == []


def test_b3_audit_rejects_summary_only_todo_profile_readiness():
    capacity_profile_readiness = {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": "operator_review_required",
        "source_gate_readiness": {
            "schema_version": "ai-platform.capacity-gate-readiness.v1",
            "status": "ready_for_operator_review",
            "load_test_gates": [
                {"gate": gate, "status": "recorded"}
                for gate in LOAD_TEST_GATES
            ],
            "missing_load_test_gates": [],
            "invalid_load_test_gates": [],
        },
        "profiles": [
            {
                "id": "b3_10x4_sdk_subagents",
                "status": "operator_review_required",
                "required_load_test_gates": list(LOAD_TEST_GATES),
                "missing_load_test_gates": [],
                "invalid_load_test_gates": [],
                "profile_evidence_status": "accepted",
                "missing_profile_evidence": [],
                "observed_profile_evidence": {
                    field: f"TODO_OPERATOR_REVIEWED_{field.upper()}"
                    for field in B3_REQUIRED_PROFILE_EVIDENCE
                },
            }
        ],
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=capacity_profile_readiness,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["b3"]["status"] == "blocked"
    assert "b3_capacity_readiness_inconsistent" in audit["b3"]["blocking_reasons"]
    assert "b3_recorded_load_test_gates_missing" in audit["b3"]["blocking_reasons"]
    assert "b3_10x4_sdk_subagents_profile_evidence_missing" in audit["b3"]["blocking_reasons"]
    assert audit["b3"]["missing_recorded_load_test_gates"] == LOAD_TEST_GATES
    assert audit["b3"]["missing_profile_evidence"] == B3_REQUIRED_PROFILE_EVIDENCE


def test_b3_audit_fails_closed_for_inconsistent_capacity_readiness():
    capacity_profile_readiness = {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": "blocked_missing_profile_evidence",
        "source_gate_readiness": {
            "status": "blocked_missing_load_test_evidence",
            "missing_load_test_gates": [],
        },
        "profiles": [
            {
                "id": "b3_10x4_sdk_subagents",
                "status": "blocked_missing_profile_evidence",
                "missing_profile_evidence": [],
            }
        ],
    }

    audit = build_g7_b3_completion_audit(
        runtime_observation=_runtime_observation(),
        capacity_profile_readiness=capacity_profile_readiness,
        current_source_commit=CURRENT_SOURCE,
    )

    assert audit["b3"]["status"] == "blocked"
    assert audit["b3"]["missing_recorded_load_test_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    assert audit["b3"]["missing_profile_evidence"] == [
        "target_profile_id",
        "evidence_source",
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
        "sdk_subagent_fanout_measurement_ref",
        "production_concurrency_defaults_raised",
        "safe_concurrency_claimed",
        "ordinary_user_platform_multi_run_orchestration_enabled",
    ]
    assert "b3_capacity_readiness_inconsistent" in audit["b3"]["blocking_reasons"]


def test_cli_outputs_json_from_runtime_observation(tmp_path):
    runtime_path = tmp_path / "runtime-observation.json"
    runtime_path.write_text(json.dumps(_runtime_observation()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_path),
            "--current-source-commit",
            CURRENT_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema_version"] == "ai-platform.g7-b3-completion-audit.v1"
    assert payload["g7"]["live_api_sandbox_provider"] == "fake"
    assert payload["b3"]["missing_recorded_load_test_gates"][0] == "api_read_write_burst"


def test_cli_accepts_latest_reviewed_evidence_over_stale_runtime_observation(tmp_path):
    runtime_observation_path = tmp_path / "runtime-observation-ae6b7e5.json"
    runtime_observation_path.write_text(
        json.dumps(_stale_current_main_runtime_observation()),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_MAIN_G7_LABEL_REPAIR_EVIDENCE_PATH),
            "--reviewed-release-evidence-json",
            str(CURRENT_MAIN_G7_LIVE_ENV_EVIDENCE_PATH),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_MAIN_FRC_EVIDENCE_PATH),
            "--current-source-commit",
            CURRENT_MAIN_G7_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["blocking_reasons"] == []
    assert payload["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-ae6b7e5-20260702045743"
    )
    assert payload["g7"]["required_next_steps"] == [
        "complete operator status-upgrade review before claiming G7 closure or 211 verified status"
    ]
    assert payload["b3"]["status"] == "blocked"
    assert payload["status_label"] == "local partial"


def test_cli_accepts_prior_clean_main_g7_frc_and_status_review_without_overclosing(tmp_path):
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_observation_path = tmp_path / "runtime-observation-61073b1.json"
    runtime_observation_path.write_text(
        json.dumps(
            {
                "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
                "runtime_image": evidence["source_ref"]["image"],
                "runtime_image_labels": evidence["source_ref"]["image_labels"],
                "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH),
            "--reviewed-release-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_OPERATOR_STATUS_REVIEW_PATH),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH),
            "--current-source-commit",
            PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["blocking_reasons"] == []
    assert payload["g7"]["reviewed_release_evidence_id"] == (
        "g7-live-env-hardening-61073b1-clean-main-20260703161911"
    )
    assert payload["g7"]["foundation_runtime_concurrency_current_subject"] is True
    assert payload["g7"]["status"] == "candidate_evidence_requires_review"
    assert payload["b3"]["status"] == "blocked"
    assert payload["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert payload["status_label"] == "local partial"
    assert payload["does_not_claim_211_verified"] is True
    assert payload["does_not_claim_gate_closable"] is True
    assert payload["does_not_close_g7"] is True
    assert payload["does_not_close_b3"] is True


def test_cli_ignores_diagnostic_release_evidence_as_reviewed_override(tmp_path):
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_observation_path = tmp_path / "runtime-observation-61073b1.json"
    runtime_observation_path.write_text(
        json.dumps(
            {
                "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
                "runtime_image": evidence["source_ref"]["image"],
                "runtime_image_labels": evidence["source_ref"]["image_labels"],
                "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_CLEAN_MAIN_B3_DIAGNOSTIC_OBSERVATION_PATH),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH),
            "--current-source-commit",
            PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["reviewed_release_evidence_id"] == ""
    assert payload["g7"]["blocking_reasons"] == ["reviewed_local_release_evidence_entry_missing"]
    assert payload["b3"]["blocking_reasons"] == [
        "b3_recorded_load_test_gates_missing",
        "b3_10x4_sdk_subagents_profile_evidence_missing",
    ]
    assert payload["status_label"] == "local partial"
    assert payload["does_not_close_b3"] is True


def test_g7_status_upgrade_review_approval_closes_only_g7_not_b3_or_gate_closable():
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        g7_status_upgrade_review=_approved_g7_status_upgrade_review(
            commit_sha=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
        ),
        current_source_commit=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
    )

    assert audit["g7"]["blocking_reasons"] == []
    assert audit["g7"]["status"] == "status_upgrade_approved"
    assert audit["g7"]["status_upgrade_review"]["status"] == "accepted"
    assert audit["g7"]["required_next_steps"] == []
    assert audit["status"] == "blocked_missing_b3_completion_evidence"
    assert audit["does_not_close_g7"] is False
    assert audit["does_not_close_b3"] is True
    assert audit["does_not_claim_gate_closable"] is True
    assert audit["does_not_claim_211_verified"] is True


def test_g7_status_upgrade_review_rejects_commit_mismatch_without_overclosing():
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        g7_status_upgrade_review=_approved_g7_status_upgrade_review(
            commit_sha=PR308_G7_B3_SOURCE,
        ),
        current_source_commit=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
    )

    assert audit["g7"]["status"] == "candidate_evidence_requires_review"
    assert audit["g7"]["status_upgrade_review"]["status"] == "not_accepted"
    assert "g7_status_upgrade_review_runtime_subject_mismatch" in audit["g7"][
        "status_upgrade_review"
    ]["input_errors"]
    assert audit["does_not_close_g7"] is True
    assert audit["does_not_close_b3"] is True


def test_cli_accepts_g7_status_upgrade_review_without_b3_overclosure(tmp_path):
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_observation_path = tmp_path / "runtime-observation-61073b1.json"
    status_review_path = tmp_path / "g7-status-upgrade-review.json"
    runtime_observation_path.write_text(
        json.dumps(
            {
                "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
                "runtime_image": evidence["source_ref"]["image"],
                "runtime_image_labels": evidence["source_ref"]["image_labels"],
                "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            }
        ),
        encoding="utf-8",
    )
    status_review_path.write_text(
        json.dumps(
            _approved_g7_status_upgrade_review(
                commit_sha=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH),
            "--g7-status-upgrade-review-json",
            str(status_review_path),
            "--current-source-commit",
            PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["status"] == "status_upgrade_approved"
    assert payload["status"] == "blocked_missing_b3_completion_evidence"
    assert payload["does_not_close_g7"] is False
    assert payload["does_not_close_b3"] is True
    assert payload["does_not_claim_gate_closable"] is True


def test_cli_rejects_current_not_approved_g7_status_review(tmp_path):
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_observation_path = tmp_path / "runtime-observation-61073b1.json"
    runtime_observation_path.write_text(
        json.dumps(
            {
                "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
                "runtime_image": evidence["source_ref"]["image"],
                "runtime_image_labels": evidence["source_ref"]["image_labels"],
                "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_observation_path),
            "--reviewed-release-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH),
            "--foundation-runtime-concurrency-evidence-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH),
            "--g7-status-upgrade-review-json",
            str(CURRENT_CLEAN_MAIN_G7_B3_OPERATOR_STATUS_REVIEW_PATH),
            "--current-source-commit",
            PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["g7"]["status"] == "candidate_evidence_requires_review"
    assert payload["g7"]["status_upgrade_review"]["status"] == "not_accepted"
    assert payload["g7"]["status_upgrade_review"]["status_upgrade_decision"] == (
        "not_approved_for_closure"
    )
    assert "g7_status_upgrade_review_not_approved" in payload["g7"][
        "status_upgrade_review"
    ]["input_errors"]
    assert payload["does_not_close_g7"] is True
    assert payload["does_not_close_b3"] is True
    assert payload["does_not_claim_gate_closable"] is True


def test_g7_status_upgrade_review_requires_platform_multi_run_exposure_invariant():
    evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_RUNTIME_EVIDENCE_PATH.read_text(encoding="utf-8"))
    frc_evidence = json.loads(CURRENT_CLEAN_MAIN_G7_B3_FRC_EVIDENCE_PATH.read_text(encoding="utf-8"))
    review = _approved_g7_status_upgrade_review(
        commit_sha=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
    )
    invariants = review["evidence_ref"]["operator_status_review"]["non_expansion_invariants"]
    invariants.pop("ordinary_user_platform_multi_run_orchestration_exposure")
    invariants["ordinary_user_platform_multi_run_orchestration_enabled"] = False

    audit = build_g7_b3_completion_audit(
        runtime_observation={
            "source_marker_commit": evidence["source_ref"]["runtime_source_marker"],
            "runtime_image": evidence["source_ref"]["image"],
            "runtime_image_labels": evidence["source_ref"]["image_labels"],
            "api_env": evidence["source_ref"]["safe_live_runtime_env"],
            "reviewed_release_evidence": evidence,
            "foundation_runtime_concurrency_evidence": frc_evidence,
        },
        capacity_profile_readiness=None,
        g7_status_upgrade_review=review,
        current_source_commit=PRIOR_CLEAN_MAIN_G7_B3_RUNTIME_SOURCE,
    )

    assert audit["g7"]["status_upgrade_review"]["status"] == "not_accepted"
    assert "g7_status_upgrade_review_ordinary_user_platform_multi_run_orchestration_exposure_invalid" in audit[
        "g7"
    ]["status_upgrade_review"]["input_errors"]
    assert audit["does_not_close_g7"] is True


def test_cli_reports_invalid_json_without_echoing_input(tmp_path):
    runtime_path = tmp_path / "runtime-observation.json"
    runtime_path.write_text('{"CALLBACK_TOKEN": "must-not-leak"', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/g7_b3_completion_audit.py",
            "--runtime-observation-json",
            str(runtime_path),
            "--current-source-commit",
            CURRENT_SOURCE,
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "failed to read JSON input" in result.stderr
    assert "must-not-leak" not in result.stderr

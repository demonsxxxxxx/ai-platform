import json
import subprocess
import sys

from app.g7_b3_completion_audit import (
    build_g7_b3_completion_audit,
    render_g7_b3_completion_audit_markdown,
)


CURRENT_SOURCE = "3071a02945c84370f62a9b36884a0a2df8ea9c45"
RUNTIME_SUBJECT = "d318f9f6a68b4c17e221eb32705b3f31d349227a"
LEGACY_LABEL_SUBJECT = "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c"


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
    assert "live_api_uses_fake_sandbox_provider" in audit["g7"]["blocking_reasons"]
    assert "reviewed_local_release_evidence_entry_missing" in audit["g7"]["blocking_reasons"]
    assert audit["g7"]["live_api_sandbox_provider"] == "fake"
    assert audit["g7"]["required_next_steps"] == [
        "reconcile current-main source marker, runtime image labels, and reviewed release-evidence binding",
        "rerun reviewed G7 Docker sandbox hardening verifier on the Docker-capable target",
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
        "ordinary_user_multi_agent_enabled",
    ]
    assert "b3_recorded_load_test_gates_missing" in audit["b3"]["blocking_reasons"]
    assert "b3_10x4_sdk_subagents_profile_evidence_missing" in audit["b3"]["blocking_reasons"]
    assert audit["b3"]["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"

    serialized = json.dumps(audit, ensure_ascii=False).lower()
    assert "211 verified" not in serialized
    assert "gate closable" not in serialized
    assert "must-not-leak" not in serialized
    assert "callback_token" not in serialized
    assert "akia_should_not_print" not in serialized
    assert "secretpass" not in serialized
    assert "openai_base_url" not in serialized


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
    assert "`live_api_uses_fake_sandbox_provider`" in markdown
    assert "`b3_recorded_load_test_gates_missing`" in markdown
    assert "does not close G7: `true`" in markdown
    assert "does not close B3: `true`" in markdown
    assert "211 verified" not in markdown.lower()
    assert "gate closable" not in markdown.lower()


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
        "ordinary_user_multi_agent_enabled",
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

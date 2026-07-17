import json
from pathlib import Path
import subprocess
import sys

from app.foundation_runtime_concurrency import (
    FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
    build_foundation_runtime_concurrency_readiness,
    render_foundation_runtime_concurrency_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_CONCURRENCY_EVIDENCE_DIR = (
    ROOT
    / "docs/release-evidence/foundation-runtime-concurrency/"
    "3843395b180324b165cbca7c59b6d7e1a934e290-fr-concurrency-local-20260614-0035"
)


def read_json_fixture(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        extended_path = "\\\\?\\" + str(path.resolve())
        with open(extended_path, encoding="utf-8") as handle:
            return json.load(handle)


def complete_evidence(**overrides):
    payload = {
        "schema_version": FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
        "artifact_kind": "foundation_runtime_concurrency",
        "commit_sha": "3843395b180324b165cbca7c59b6d7e1a934e290",
        "runtime_subject_commit_sha": "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
        "source_tree_commit_sha": "3843395b180324b165cbca7c59b6d7e1a934e290",
        "summary": {
            "tenant_count": 2,
            "user_count": 4,
            "session_count": 12,
            "run_count": 12,
            "concurrent_request_count": 12,
            "max_observed_concurrency": 12,
            "concurrency_probe_source": "client_case_timestamps",
            "concurrency_window_sample_count": 12,
        },
        "scenario_counts": {
            "run_creation": 12,
            "execution": 8,
            "cancel": 2,
            "retry": 2,
        },
        "checks": {
            "queue_admission": {
                "status": "passed",
                "admission_limit_violations": 0,
                "cross_tenant_queue_leaks": 0,
                "stale_queue_entries": 0,
                "queue_position_sample_count": 12,
                "queue_position_duplicate_count": 0,
                "queue_probe_sample_count": 12,
                "queue_probe_source": "redis_metadata",
                "cancel_action_statuses": [200, 200],
                "cancel_effect_statuses": ["cancel_requested", "cancelled"],
                "cancel_effect_run_count": 2,
                "retry_action_statuses": [200, 200],
                "retry_created_run_count": 2,
            },
            "sandbox_workspace": {
                "status": "passed",
                "workspace_scope_sample_count": 12,
                "sandbox_lease_sample_count": 12,
                "active_lease_count": 0,
                "cross_scope_lease_leaks": 0,
                "workspace_scope_collisions": 0,
                "lease_probe_source": "runtime_run_detail",
            },
            "memory_context": {
                "status": "passed",
                "context_snapshot_count": 12,
                "context_snapshot_public_projection_count": 12,
                "context_pack_version_sample_count": 12,
                "missing_context_pack_version_count": 0,
                "unsafe_context_pack_version_count": 0,
                "missing_public_summary_fields": [],
                "context_scope_probe_count": 12,
                "cross_scope_context_leaks": 0,
                "long_term_cross_session_memory_read": False,
            },
            "artifact_acl": {
                "status": "passed",
                "owner_statuses": [200, 200, 200],
                "cross_user_statuses": [404, 404],
                "cross_tenant_statuses": [404, 404],
                "preview_cross_user_statuses": [404],
                "preview_cross_tenant_statuses": [404],
            },
            "tool_permission": {
                "status": "passed",
                "zero_click_write_probe_count": 12,
                "zero_click_write_410_count": 12,
                "zero_click_write_unexpected_status_count": 0,
            },
            "skill_snapshots": {
                "status": "passed",
                "run_skill_snapshot_count": 12,
                "used_count": 12,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
                "snapshot_binding_sample_count": 12,
            },
            "run_playback": {
                "status": "passed",
                "event_order_violations": 0,
                "private_payload_leak_count": 0,
            },
        },
        "non_expansion_invariants": {
            "production_concurrency_increase_allowed": False,
            "ordinary_user_multi_agent_allowed": False,
            "docker_sandbox_hardened_claim_allowed": False,
            "department_rollout_allowed": False,
            "long_term_cross_session_memory_enabled": False,
        },
        "role_provenance": {
            "run_creation_role": "user",
            "public_probe_role": "user",
            "admin_probe_role": None,
            "ordinary_user_multi_agent_opened": False,
        },
    }
    payload.update(overrides)
    return payload


def test_foundation_runtime_concurrency_missing_evidence_fails_closed():
    readiness = build_foundation_runtime_concurrency_readiness()

    assert readiness["schema_version"] == FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA
    assert readiness["status"] == "missing_foundation_runtime_concurrency_evidence"
    assert readiness["verified"] is False
    assert readiness["requirements"]["minimum_concurrent_requests"] == 10
    assert readiness["requirements"]["minimum_tenants"] == 2
    assert readiness["requirements"]["required_scenarios"] == [
        "run_creation",
        "execution",
        "cancel",
        "retry",
    ]
    assert "missing_evidence" in readiness["failures"]
    assert readiness["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] is False
    assert readiness["non_expansion_invariants"]["production_concurrency_increase_allowed"] is False


def test_foundation_runtime_concurrency_accepts_complete_12_case_evidence():
    readiness = build_foundation_runtime_concurrency_readiness(complete_evidence())

    assert readiness["status"] == "verified_foundation_runtime_concurrency"
    assert readiness["verified"] is True
    assert readiness["failures"] == []
    assert readiness["summary"]["tenant_count"] == 2
    assert readiness["summary"]["run_count"] == 12
    assert readiness["checks"]["memory_context"]["context_snapshot_public_projection_count"] == 12
    assert readiness["checks"]["memory_context"]["context_pack_version_sample_count"] == 12
    assert readiness["checks"]["memory_context"]["missing_context_pack_version_count"] == 0
    assert readiness["checks"]["memory_context"]["unsafe_context_pack_version_count"] == 0
    assert readiness["checks"]["artifact_acl"]["cross_tenant_statuses"] == [404, 404]
    assert readiness["checks"]["tool_permission"]["zero_click_write_410_count"] == 12
    assert readiness["checks"]["skill_snapshots"]["run_skill_snapshot_count"] == 12


def test_foundation_runtime_concurrency_rejects_legacy_context_count_only_evidence():
    weak = complete_evidence()
    weak["checks"]["memory_context"].pop("context_snapshot_public_projection_count")
    weak["checks"]["memory_context"].pop("context_pack_version_sample_count")

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "memory_context_public_projection_count_insufficient" in readiness["failures"]
    assert "memory_context_pack_version_samples_insufficient" in readiness["failures"]


def test_committed_legacy_concurrency_evidence_is_rejected_without_zero_click_probe():
    evidence_path = LEGACY_CONCURRENCY_EVIDENCE_DIR / "foundation-runtime-concurrency-evidence-211-20260614-013347.json"
    readiness_path = LEGACY_CONCURRENCY_EVIDENCE_DIR / "foundation-runtime-concurrency-readiness-211-20260614-013347.json"
    evidence = read_json_fixture(evidence_path)
    committed_readiness = read_json_fixture(readiness_path)

    current_readiness = build_foundation_runtime_concurrency_readiness(evidence)

    assert committed_readiness["verified"] is False
    assert current_readiness["verified"] is False
    assert current_readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "tool_permission_zero_click_probe_missing" in current_readiness["failures"]
    assert "tool_permission_zero_click_410_missing" in current_readiness["failures"]


def test_foundation_runtime_concurrency_rejects_missing_or_unsafe_context_pack_versions():
    weak = complete_evidence()
    weak["checks"]["memory_context"]["missing_context_pack_version_count"] = 1
    weak["checks"]["memory_context"]["unsafe_context_pack_version_count"] = 1
    weak["checks"]["memory_context"]["missing_public_summary_fields"] = ["context_pack_version"]

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "memory_context_pack_version_missing" in readiness["failures"]
    assert "memory_context_pack_version_unsafe" in readiness["failures"]
    assert "memory_context_public_summary_fields_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_unproven_queue_sandbox_memory_and_skill_claims():
    weak = complete_evidence()
    for key in ("queue_position_sample_count", "queue_position_duplicate_count", "queue_probe_source", "queue_probe_sample_count"):
        weak["checks"]["queue_admission"].pop(key)
    for key in ("sandbox_lease_sample_count", "lease_probe_source"):
        weak["checks"]["sandbox_workspace"].pop(key)
    weak["checks"]["memory_context"].pop("context_scope_probe_count")
    weak["checks"]["skill_snapshots"].pop("snapshot_binding_sample_count")

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "queue_admission_position_samples_missing" in readiness["failures"]
    assert "queue_admission_probe_source_missing" in readiness["failures"]
    assert "queue_admission_probe_samples_missing" in readiness["failures"]
    assert "sandbox_lease_samples_missing" in readiness["failures"]
    assert "sandbox_lease_probe_source_missing" in readiness["failures"]
    assert "memory_context_scope_probe_missing" in readiness["failures"]
    assert "skill_snapshot_binding_samples_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_synthetic_concurrency_claim():
    weak = complete_evidence()
    weak["summary"].pop("concurrency_probe_source")
    weak["summary"].pop("concurrency_window_sample_count")

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "concurrency_probe_source_missing" in readiness["failures"]
    assert "concurrency_window_samples_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_submit_only_queue_probe():
    weak = complete_evidence()
    weak["checks"]["queue_admission"]["queue_probe_source"] = "submit_response"

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "queue_admission_probe_source_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_post_run_sandbox_probe_as_execution_lease():
    weak = complete_evidence()
    weak["checks"]["sandbox_workspace"]["lease_probe_source"] = "sandbox_leases"

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "sandbox_lease_probe_source_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_missing_zero_click_tool_permission_probe():
    weak = complete_evidence()
    for key in ("zero_click_write_probe_count", "zero_click_write_410_count"):
        weak["checks"]["tool_permission"].pop(key)

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "tool_permission_zero_click_probe_missing" in readiness["failures"]
    assert "tool_permission_zero_click_410_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_terminal_run_failures_explicitly():
    weak = complete_evidence(
        terminal_run_failures=[
            {
                "run_id": "run-failed",
                "status": "failed",
                "error_code": "claude_agent_sdk_runtime_error",
                "error_message": "API Error: 402 Insufficient Balance",
            }
        ]
    )

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "run_terminal_failures" in readiness["failures"]
    assert readiness["terminal_run_failures"][0]["error_code"] == "claude_agent_sdk_runtime_error"


def test_foundation_runtime_concurrency_rejects_missing_artifact_acl_samples_explicitly():
    weak = complete_evidence()
    weak["checks"]["artifact_acl"] = {
        "status": "passed",
        "owner_statuses": [],
        "cross_user_statuses": [],
        "cross_tenant_statuses": [],
        "preview_cross_user_statuses": [],
        "preview_cross_tenant_statuses": [],
    }

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "artifact_acl_samples_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_does_not_add_denial_failures_when_artifact_samples_missing():
    weak = complete_evidence(
        terminal_run_failures=[
            {
                "run_id": "run-failed",
                "status": "failed",
                "raw_status": "failed",
                "error_code": "claude_agent_sdk_runtime_error",
                "error_message_summary": "API Error: 402 Insufficient Balance",
            }
        ]
    )
    weak["checks"]["artifact_acl"] = {
        "status": "passed",
        "owner_statuses": [],
        "cross_user_statuses": [],
        "cross_tenant_statuses": [],
        "preview_cross_user_statuses": [],
        "preview_cross_tenant_statuses": [],
    }

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert "artifact_acl_samples_missing" in readiness["failures"]
    assert "artifact_acl_cross_user_not_denied" not in readiness["failures"]
    assert "artifact_acl_cross_tenant_not_denied" not in readiness["failures"]
    assert "artifact_preview_cross_user_not_denied" not in readiness["failures"]
    assert "artifact_preview_cross_tenant_not_denied" not in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_weak_or_leaky_evidence():
    weak = complete_evidence()
    weak["summary"]["concurrent_request_count"] = 9
    weak["summary"]["tenant_count"] = 1
    weak["scenario_counts"]["retry"] = 0
    weak["checks"]["artifact_acl"]["cross_tenant_statuses"] = [200]
    weak["checks"]["tool_permission"]["zero_click_write_unexpected_status_count"] = 1
    weak["checks"]["skill_snapshots"]["global_mutable_skill_lookup_used"] = True
    weak["checks"]["memory_context"]["long_term_cross_session_memory_read"] = True

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "minimum_concurrent_requests_not_met" in readiness["failures"]
    assert "minimum_tenants_not_met" in readiness["failures"]
    assert "scenario_retry_missing" in readiness["failures"]
    assert "artifact_acl_cross_tenant_not_denied" in readiness["failures"]
    assert "tool_permission_zero_click_write_unexpected_status" in readiness["failures"]
    assert "skill_snapshots_used_global_mutable_lookup" in readiness["failures"]
    assert "long_term_cross_session_memory_not_fail_closed" in readiness["failures"]


def test_foundation_runtime_concurrency_cli_outputs_safe_json(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(complete_evidence()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/foundation_runtime_concurrency.py",
            "--evidence-json",
            str(evidence_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["status"] == "verified_foundation_runtime_concurrency"
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "authorization" not in serialized
    assert "bearer" + " " not in serialized
    assert "database_url" not in serialized
    assert "sandbox" + "_workdir" not in serialized
    assert "raw" + "_storage_key" not in serialized


def test_foundation_runtime_concurrency_markdown_names_blocked_expansions():
    markdown = render_foundation_runtime_concurrency_markdown(
        build_foundation_runtime_concurrency_readiness(complete_evidence())
    )

    assert "# Foundation Runtime Concurrency Readiness" in markdown
    assert "`ordinary_user_multi_agent_allowed`: `False`" in markdown
    assert "`production_concurrency_increase_allowed`: `False`" in markdown
    assert "broaden ordinary-user platform-level multi-run orchestration exposure" in markdown
    assert "open ordinary-user multi-agent" not in markdown
    assert "verified_foundation_runtime_concurrency" in markdown

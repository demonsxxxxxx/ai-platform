import json
import subprocess
import sys

from app.foundation_runtime_concurrency import (
    FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
    build_foundation_runtime_concurrency_readiness,
    render_foundation_runtime_concurrency_markdown,
)


def complete_evidence(**overrides):
    payload = {
        "schema_version": FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
        "artifact_kind": "foundation_runtime_concurrency",
        "commit_sha": "3843395b180324b165cbca7c59b6d7e1a934e290-fr-concurrency-local-20260614-0035",
        "runtime_subject_commit_sha": "ac9a86bbea14a28748867cade8d80b2f9ff420ec",
        "source_tree_commit_sha": "3843395b180324b165cbca7c59b6d7e1a934e290",
        "summary": {
            "tenant_count": 2,
            "user_count": 4,
            "session_count": 12,
            "run_count": 12,
            "concurrent_request_count": 12,
            "max_observed_concurrency": 12,
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
                "cancel_action_statuses": [200, 200],
                "cancel_effect_statuses": ["cancel_requested", "cancelled"],
                "cancel_effect_run_count": 2,
                "retry_action_statuses": [200, 200],
                "retry_created_run_count": 2,
            },
            "sandbox_workspace": {
                "status": "passed",
                "workspace_scope_sample_count": 12,
                "active_lease_count": 0,
                "cross_scope_lease_leaks": 0,
                "workspace_scope_collisions": 0,
            },
            "memory_context": {
                "status": "passed",
                "context_snapshot_count": 12,
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
                "decision_sample_count": 12,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            },
            "skill_snapshots": {
                "status": "passed",
                "run_skill_snapshot_count": 12,
                "used_count": 12,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
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
            "developer_role_used_only_for_fixture_agent_selection": False,
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
    assert readiness["summary"] == {
        "tenant_count": 2,
        "user_count": 4,
        "session_count": 12,
        "run_count": 12,
        "concurrent_request_count": 12,
        "max_observed_concurrency": 12,
    }
    assert readiness["scenario_counts"]["cancel"] == 2
    assert readiness["scenario_counts"]["retry"] == 2
    assert readiness["checks"]["artifact_acl"]["cross_tenant_statuses"] == [404, 404]
    assert readiness["checks"]["tool_permission"]["allow_once_reuse_violations"] == 0
    assert readiness["checks"]["skill_snapshots"]["run_skill_snapshot_count"] == 12


def test_foundation_runtime_concurrency_rejects_weak_or_leaky_evidence():
    weak = complete_evidence()
    weak["summary"]["concurrent_request_count"] = 9
    weak["summary"]["tenant_count"] = 1
    weak["scenario_counts"]["retry"] = 0
    weak["checks"]["artifact_acl"]["cross_tenant_statuses"] = [200]
    weak["checks"]["tool_permission"]["allow_once_reuse_violations"] = 1
    weak["checks"]["skill_snapshots"]["global_mutable_skill_lookup_used"] = True
    weak["checks"]["memory_context"]["long_term_cross_session_memory_read"] = True

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert readiness["verified"] is False
    assert "minimum_concurrent_requests_not_met" in readiness["failures"]
    assert "minimum_tenants_not_met" in readiness["failures"]
    assert "scenario_retry_missing" in readiness["failures"]
    assert "artifact_acl_cross_tenant_not_denied" in readiness["failures"]
    assert "tool_permission_allow_once_reused" in readiness["failures"]
    assert "skill_snapshots_used_global_mutable_lookup" in readiness["failures"]
    assert "long_term_cross_session_memory_not_fail_closed" in readiness["failures"]


def test_foundation_runtime_concurrency_requires_explicit_non_expansion_invariants():
    weak = complete_evidence()
    weak.pop("non_expansion_invariants")

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "missing_non_expansion_invariant_production_concurrency_increase_allowed" in readiness["failures"]
    assert "missing_non_expansion_invariant_ordinary_user_multi_agent_allowed" in readiness["failures"]


def test_foundation_runtime_concurrency_requires_fail_closed_role_provenance():
    weak = complete_evidence()
    weak.pop("role_provenance")

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "missing_role_provenance" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_ordinary_user_multi_agent_provenance():
    weak = complete_evidence()
    weak["role_provenance"]["ordinary_user_multi_agent_opened"] = True

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "ordinary_user_multi_agent_opened" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_unknown_source_binding():
    weak = complete_evidence(
        commit_sha="unknown",
        source_tree_commit_sha="unknown",
        runtime_subject_commit_sha="unknown",
    )

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "invalid_commit_sha" in readiness["failures"]
    assert "invalid_source_tree_commit_sha" in readiness["failures"]
    assert "invalid_runtime_subject_commit_sha" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_missing_required_samples():
    weak = complete_evidence()
    weak["checks"]["sandbox_workspace"]["workspace_scope_sample_count"] = 0
    weak["checks"]["memory_context"]["context_snapshot_count"] = 3
    weak["checks"]["tool_permission"]["decision_sample_count"] = 0
    weak["checks"]["queue_admission"]["cancel_action_statuses"] = []
    weak["checks"]["queue_admission"]["cancel_effect_statuses"] = []
    weak["checks"]["queue_admission"]["cancel_effect_run_count"] = 0
    weak["checks"]["queue_admission"]["retry_action_statuses"] = []
    weak["checks"]["queue_admission"]["retry_created_run_count"] = 0

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "sandbox_workspace_samples_missing" in readiness["failures"]
    assert "memory_context_snapshot_count_insufficient" in readiness["failures"]
    assert "tool_permission_decision_samples_missing" in readiness["failures"]
    assert "run_control_cancel_samples_missing" in readiness["failures"]
    assert "run_control_cancel_effect_missing" in readiness["failures"]
    assert "run_control_retry_samples_missing" in readiness["failures"]
    assert "run_control_retry_created_run_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_rejects_control_status_without_effect():
    weak = complete_evidence()
    weak["checks"]["queue_admission"]["cancel_action_statuses"] = [409]
    weak["checks"]["queue_admission"]["cancel_effect_statuses"] = []
    weak["checks"]["queue_admission"]["cancel_effect_run_count"] = 0
    weak["checks"]["queue_admission"]["retry_action_statuses"] = [409]
    weak["checks"]["queue_admission"]["retry_created_run_count"] = 0

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "run_control_cancel_effect_missing" in readiness["failures"]
    assert "run_control_retry_created_run_missing" in readiness["failures"]


def test_foundation_runtime_concurrency_requires_cancel_effect_per_cancel_run():
    weak = complete_evidence()
    weak["scenario_counts"]["cancel"] = 2
    weak["checks"]["queue_admission"]["cancel_action_statuses"] = [200, 200]
    weak["checks"]["queue_admission"]["cancel_effect_statuses"] = ["cancel_requested", "cancelled"]
    weak["checks"]["queue_admission"]["cancel_effect_run_count"] = 1

    readiness = build_foundation_runtime_concurrency_readiness(weak)

    assert readiness["status"] == "blocked_foundation_runtime_concurrency_evidence"
    assert "run_control_cancel_effect_missing" in readiness["failures"]


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
    assert "bearer " not in serialized
    assert "database_url" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "raw_storage_key" not in serialized


def test_foundation_runtime_concurrency_markdown_names_blocked_expansions():
    markdown = render_foundation_runtime_concurrency_markdown(
        build_foundation_runtime_concurrency_readiness(complete_evidence())
    )

    assert "# Foundation Runtime Concurrency Readiness" in markdown
    assert "`ordinary_user_multi_agent_allowed`: `False`" in markdown
    assert "`production_concurrency_increase_allowed`: `False`" in markdown
    assert "verified_foundation_runtime_concurrency" in markdown

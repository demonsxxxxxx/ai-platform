import json
import subprocess
import sys
from pathlib import Path
from urllib import parse
from unittest.mock import patch

from app.b1_memory_context_readiness import (
    _has_required_structured_runtime_evidence,
    _runtime_acceptance_evidence,
    _runtime_acceptance_evidence_rank,
    _status_for_local_controls,
    build_b1_memory_context_readiness,
    render_b1_memory_context_readiness_markdown,
)
from tools import verify_b1_memory_context_workflow as verifier


FORBIDDEN_PRIVATE_MARKERS = [
    "executor" + "_private_payload",
    "raw" + "_storage_key",
    "sandbox" + "_workdir",
    "callback" + "-secret",
    "c:\\users",
    "/home/",
    "/tmp/",
    "/var/lib/ai-platform",
    "authorization",
    "bearer",
    "api_key",
    "password",
    "callback_token",
]

CURRENT_B1_EVIDENCE_PATH = Path(
    "docs/release-evidence/b1-memory-context/"
    "96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c/"
    "2026-07-01-211-b1-memory-context-workflow-smoke-96f27bb.json"
)


def _assert_no_private_markers(value):
    serialized = json.dumps(value, ensure_ascii=False).lower()
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in serialized


def _public_context_payload(*, memory_record_count: int) -> dict:
    return {
        "referenced_materials": {
            "message_count": 0,
            "file_count": 1,
            "artifact_count": 0,
            "memory_record_count": memory_record_count,
        },
        "used_context_summary": {
            "source": "runs_api",
            "input_keys": ["task", "memory"],
            "memory_policy_source": "user",
            "long_term_memory_read": False,
        },
        "context_pack_version": "context-pack-v1",
        "context_pack_generated_at": "2026-07-05T00:00:00Z",
        "execution_tier": "worker",
    }


def _run_detail_payload() -> dict:
    return {
        "run_id": "run-doc",
        "agent_id": "document-review",
        "capability_id": "document_review",
        "status": "succeeded",
        "artifacts": [{"artifact_id": "artifact-1", "kind": "document_review"}],
        "steps": [{"step_id": "worker-step-1"}],
        "events": [{"event_type": "worker_started"}],
        "context_ref": {
            "context_snapshot_id": "snapshot-1",
            "context_pack_version": "context-pack-v1",
            "context_pack_generated_at": "2026-07-05T00:00:00Z",
        },
        "result": {
            "executor": {
                "executor_type": "worker",
                "schema_version": "ai-platform.executor-result.v1",
            }
        },
    }


def _structured_runtime_evidence() -> dict:
    checks = {
        "admin_runtime_visibility": True,
        "context_snapshot_public_provenance": True,
        "create_governed_run": True,
        "cross_tenant_context_denied": True,
        "cross_user_context_denied": True,
        "deleted_memory_absent_from_future_context": True,
        "live_worker_payload": True,
        "long_term_memory_fail_closed": True,
        "memory_policy_disabled_blocks_create": True,
        "memory_policy_disabled_blocks_list": True,
        "memory_policy_enabled_for_governed_scope": True,
        "memory_record_create_and_list": True,
        "no_private_projection_leakage": True,
        "ordinary_user_admin_visibility_denied": True,
        "playback_public_projection": True,
        "rollback_disable_behavior": True,
        "same_tenant_context_boundary": True,
    }
    return {
        "schema_version": "ai-platform.b1-memory-context-workflow-smoke.v1",
        "ok": True,
        "target": "211_api_memory_context_workflow",
        "acceptance_gap": "211_memory_enabled_document_workflow_smoke",
        "redaction_scan_status": "passed",
        "memory_record_count": 1,
        "workflow": {
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "capability_id": "document_review",
            "run_id_present": True,
            "session_id_present": True,
            "document_run_id_present": True,
            "document_session_id_present": True,
            "probe_file_bound": True,
            "memory_record_created": True,
        },
        "checks": checks,
        "live_worker_payload": {
            "document_workflow": True,
            "live_worker_run_observed": True,
            "worker_started_event_observed": True,
            "context_snapshot_id_present": True,
            "context_pack_schema_present": True,
            "artifact_count": 1,
        },
        "provenance": {
            "context_snapshot_public_provenance": True,
            "playback_public_projection": True,
            "memory_policy_source_present": True,
            "context_pack_version_present": True,
            "context_pack_generated_at_present": True,
            "worker_context_pack_version_present": True,
            "worker_context_pack_generated_at_present": True,
        },
        "delete_redaction": {
            "deleted_memory_absent_from_future_context": True,
            "redaction_scan_status": "passed",
            "private_projection_terms_present": False,
            "future_context_memory_count": 0,
            "latest_listed_context_memory_count": 0,
        },
        "rollback_disable": {
            "memory_policy_disabled_blocks_create": True,
            "memory_policy_disabled_blocks_list": True,
            "memory_policy_reenabled_for_governed_scope": True,
            "public_projections_hide_private_context_material": True,
        },
        "same_tenant_boundary": {
            "owner_context_visible": True,
            "same_tenant_cross_user_denied": True,
            "cross_tenant_context_denied": True,
        },
        "admin_visibility": {
            "admin_run_detail_visible": True,
            "admin_runtime_overview_visible": True,
            "ordinary_user_admin_overview_denied": True,
            "admin_projection_redacted": True,
        },
        "deny_path": {
            "cross_user_context_denied": True,
            "cross_tenant_context_denied": True,
            "memory_policy_disabled_blocks_create": True,
            "memory_policy_disabled_blocks_list": True,
            "ordinary_user_admin_overview_denied": True,
            "long_term_memory_fail_closed": True,
        },
        "policy_posture": {
            "session_workspace_scope": True,
            "retention_days": 30,
            "retention_policy_present": True,
            "opt_out_disable_policy_present": True,
            "export_projection_only": True,
            "delete_redaction_posture_present": True,
            "long_term_memory_fail_closed": True,
        },
        "non_expansion_invariants": {
            "frontend_state_is_canonical_context": False,
            "gate_closure_claimed": False,
            "long_term_cross_session_memory_enabled": False,
            "stores_private_executor_material_as_memory": False,
        },
        "does_not_close_b1_gate": True,
        "remaining_gate_boundaries": [
            "issue review and closure evidence",
            "runtime evidence review against merged source",
            "memory export boundary",
            "rollback boundary",
        ],
    }


def _structured_runtime_evidence_with_verifier_style_checks() -> dict:
    evidence = _structured_runtime_evidence()
    evidence["checks"] = {
        name: {"passed": passed, "status": 200 if passed else 500}
        for name, passed in evidence["checks"].items()
    }
    return evidence


def test_current_b1_release_evidence_is_historical_and_lacks_structured_worker_sections():
    payload = json.loads(CURRENT_B1_EVIDENCE_PATH.read_text(encoding="utf-8"))
    runtime_smoke = payload["evidence_ref"]["runtime_checks"][
        "211_memory_enabled_document_workflow_smoke"
    ]

    assert payload["review_status"] == "reviewed"
    assert payload["redaction_scan_status"] == "passed"
    assert runtime_smoke["workflow"]["agent_id"] == "general-agent"
    for required_section in (
        "live_worker_payload",
        "provenance",
        "delete_redaction",
        "rollback_disable",
        "same_tenant_boundary",
        "admin_visibility",
        "deny_path",
    ):
        assert required_section not in runtime_smoke
    assert _runtime_acceptance_evidence(Path.cwd()) == {}
    _assert_no_private_markers(payload)


def test_b1_readiness_stays_local_partial_until_structured_runtime_smoke_exists():
    readiness = build_b1_memory_context_readiness()

    assert readiness["schema_version"] == "ai-platform.b1-memory-context-readiness.v1"
    assert readiness["backend_stage"] == "B1 memory/context usable"
    assert readiness["status"] == "local_controls_ready_runtime_smoke_required"
    assert readiness["status_label"] == "local partial"
    assert readiness["admin_runtime_projection"] == "/api/ai/admin/runtime/overview"
    assert readiness["ordinary_user_policy"] == "session_scoped_memory_with_public_provenance"
    assert readiness["runtime_acceptance"]["status"] == "missing_211_memory_enabled_document_workflow_smoke"
    assert "status_label_after_smoke" not in readiness["runtime_acceptance"]
    assert readiness["runtime_acceptance"]["selected_workflow"] == {
        "workflow": "internal_document_review",
        "agent_id": "document-review",
        "capability_id": "document_review",
        "memory_scope": "session_workspace",
    }
    assert readiness["open_gaps"] == [
        "211_memory_enabled_document_workflow_smoke",
        "b1_runtime_evidence_review_against_merged_source",
    ]
    assert readiness["runtime_acceptance_evidence"] == {}

    boundary_evidence = readiness["gate_boundary_evidence"]
    assert boundary_evidence["b1_memory_export_boundary"]["status"] == "recorded_local_contract"
    assert boundary_evidence["b1_rollback_boundary"]["status"] == "recorded_local_contract"
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["status"] == (
        "recorded_issue_closure_evidence"
    )
    assert boundary_evidence["b1_runtime_evidence_review_against_merged_source"]["status"] == (
        "open_missing_runtime_subject_evidence"
    )
    assert "b1_runtime_evidence_review_against_merged_source" not in readiness[
        "closed_gate_boundary_gaps"
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "211 verified" not in serialized
    assert "gate closable" not in serialized
    _assert_no_private_markers(readiness)


def test_b1_readiness_markdown_is_gap_first_and_does_not_overclaim_runtime():
    markdown = render_b1_memory_context_readiness_markdown(build_b1_memory_context_readiness())

    assert "# ai-platform B1 Memory/Context Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    open_gap_section = markdown.split("## Closed Gate Boundary Gaps", 1)[0]
    assert "- 211_memory_enabled_document_workflow_smoke" in open_gap_section
    assert "- b1_runtime_evidence_review_against_merged_source" in open_gap_section
    assert "- b1_memory_export_boundary" not in open_gap_section
    assert "- b1_rollback_boundary" not in open_gap_section
    assert "document-review" in markdown
    assert "document_review" in markdown
    assert "tools/verify_b1_memory_context_workflow.py" in markdown
    assert "missing_211_memory_enabled_document_workflow_smoke" in markdown
    assert "211 verified" not in markdown.lower()
    assert "gate closable" not in markdown.lower()
    assert "does not enable long-term cross-session memory by default" in markdown
    assert "does not store executor-private payloads as memory" in markdown
    assert "does not make frontend state canonical context" in markdown


def test_b1_readiness_cli_outputs_conservative_json_without_private_markers():
    result = subprocess.run(
        [sys.executable, "tools/b1_memory_context_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.b1-memory-context-readiness.v1"
    assert payload["status"] == "local_controls_ready_runtime_smoke_required"
    assert payload["status_label"] == "local partial"
    assert payload["runtime_acceptance"]["status"] == "missing_211_memory_enabled_document_workflow_smoke"
    assert payload["runtime_acceptance"]["selected_workflow"]["agent_id"] == "document-review"
    assert payload["runtime_acceptance"]["selected_workflow"]["capability_id"] == "document_review"
    assert payload["open_gaps"] == [
        "211_memory_enabled_document_workflow_smoke",
        "b1_runtime_evidence_review_against_merged_source",
    ]
    assert "b1_runtime_evidence_review_against_merged_source" not in payload[
        "closed_gate_boundary_gaps"
    ]
    assert payload["runtime_acceptance_evidence"] == {}
    assert "211 verified" not in result.stdout.lower()
    _assert_no_private_markers(payload)


def test_b1_issue_closure_gap_stays_open_without_valid_local_closure_evidence(tmp_path):
    readiness = build_b1_memory_context_readiness(repo_root=tmp_path)

    assert "b1_issue_review_and_closure_evidence" in readiness["open_gaps"]
    closure_evidence = readiness["gate_boundary_evidence"]["b1_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "open_missing_issue_closure_evidence"
    assert closure_evidence["closed_gap"] is None
    assert closure_evidence["required_next_step"] == (
        "record reviewed local issue-closure evidence for #75 before closing this boundary"
    )


def test_structured_runtime_evidence_requires_document_workflow_governance_sections():
    evidence = _structured_runtime_evidence()
    assert _has_required_structured_runtime_evidence(evidence) is True

    for section in (
        "rollback_disable",
        "same_tenant_boundary",
        "admin_visibility",
        "policy_posture",
    ):
        incomplete = _structured_runtime_evidence()
        incomplete.pop(section)
        assert _has_required_structured_runtime_evidence(incomplete) is False

    wrong_workflow = _structured_runtime_evidence()
    wrong_workflow["workflow"]["agent_id"] = "general-agent"
    assert _has_required_structured_runtime_evidence(wrong_workflow) is False


def test_runtime_acceptance_evidence_accepts_structured_document_workflow_artifact(tmp_path):
    evidence_root = (
        tmp_path
        / "docs"
        / "release-evidence"
        / "b1-memory-context"
        / "source-sha"
    )
    evidence_root.mkdir(parents=True)
    artifact = evidence_root / "structured-b1-document-workflow-smoke.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.release-evidence-entry.v1",
                "gate": "B1 memory/context usable",
                "artifact_kind": "211_memory_enabled_document_workflow_smoke",
                "captured_at": "2026-07-05T00:00:00+00:00",
                "evidence_id": "structured-b1-document-workflow-smoke",
                "review_status": "reviewed",
                "redaction_scan_status": "passed",
                "runtime_subject_commit_sha": "source-sha",
                "source_ref": {
                    "branch": "main",
                    "runtime_source_marker": "source-sha",
                    "source_tree_dirty": False,
                    "image": "ai-platform:source-sha",
                    "source_snapshot": {
                        "runtime_subject_commit_sha": "source-sha",
                        "source_tree_dirty": False,
                        "runtime_affecting_changes_since_runtime_subject": [],
                        "runtime_affecting_dirty_paths": [],
                    },
                },
                "evidence_ref": {
                    "verifier": "tools/verify_b1_memory_context_workflow.py",
                    "result": "ok:true",
                    "runtime_checks": {
                        "211_memory_enabled_document_workflow_smoke": _structured_runtime_evidence()
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch(
        "app.b1_memory_context_readiness._resolve_source_tree_revision",
        return_value="source-sha",
    ):
        selected = _runtime_acceptance_evidence(tmp_path)[
            "211_memory_enabled_document_workflow_smoke"
        ]

    assert selected["status"] == "verified_211_runtime_acceptance"
    assert selected["evidence_id"] == "structured-b1-document-workflow-smoke"
    assert selected["workflow"]["agent_id"] == "document-review"
    assert selected["workflow"]["capability_id"] == "document_review"
    assert selected["memory_record_count"] == 1
    assert selected["checks"]["admin_runtime_visibility"] is True
    assert selected["checks"]["ordinary_user_admin_visibility_denied"] is True
    assert selected["checks"]["same_tenant_context_boundary"] is True
    assert selected["checks"]["cross_tenant_context_denied"] is True
    assert selected["structured_evidence"]["live_worker_payload"]["document_workflow"] is True
    assert selected["structured_evidence"]["provenance"]["context_snapshot_public_provenance"] is True
    assert selected["structured_evidence"]["rollback_disable"][
        "memory_policy_disabled_blocks_create"
    ] is True
    assert selected["structured_evidence"]["same_tenant_boundary"][
        "cross_tenant_context_denied"
    ] is True
    assert selected["structured_evidence"]["admin_visibility"][
        "ordinary_user_admin_overview_denied"
    ] is True
    assert selected["structured_evidence"]["policy_posture"] == {
        "session_workspace_scope": True,
        "retention_days": 30,
        "retention_policy_present": True,
        "opt_out_disable_policy_present": True,
        "export_projection_only": True,
        "delete_redaction_posture_present": True,
        "long_term_memory_fail_closed": True,
    }
    assert selected["redaction_scan_status"] == "passed"
    assert selected["does_not_close_b1_gate"] is True
    _assert_no_private_markers(selected)


def test_runtime_acceptance_evidence_accepts_verifier_style_check_objects(tmp_path):
    evidence_root = (
        tmp_path
        / "docs"
        / "release-evidence"
        / "b1-memory-context"
        / "source-sha"
    )
    evidence_root.mkdir(parents=True)
    artifact = evidence_root / "structured-b1-document-workflow-verifier-output.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.release-evidence-entry.v1",
                "gate": "B1 memory/context usable",
                "artifact_kind": "211_memory_enabled_document_workflow_smoke",
                "captured_at": "2026-07-05T00:00:00+00:00",
                "evidence_id": "structured-b1-document-workflow-verifier-output",
                "review_status": "reviewed",
                "redaction_scan_status": "passed",
                "runtime_subject_commit_sha": "source-sha",
                "source_ref": {
                    "branch": "main",
                    "runtime_source_marker": "source-sha",
                    "source_tree_dirty": False,
                    "image": "ai-platform:source-sha",
                    "source_snapshot": {
                        "runtime_subject_commit_sha": "source-sha",
                        "source_tree_dirty": False,
                        "runtime_affecting_changes_since_runtime_subject": [],
                        "runtime_affecting_dirty_paths": [],
                    },
                },
                "evidence_ref": {
                    "verifier": "tools/verify_b1_memory_context_workflow.py",
                    "result": "ok:true",
                    "runtime_checks": {
                        "211_memory_enabled_document_workflow_smoke": (
                            _structured_runtime_evidence_with_verifier_style_checks()
                        )
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch(
        "app.b1_memory_context_readiness._resolve_source_tree_revision",
        return_value="source-sha",
    ):
        selected = _runtime_acceptance_evidence(tmp_path)[
            "211_memory_enabled_document_workflow_smoke"
        ]

    assert selected["evidence_id"] == "structured-b1-document-workflow-verifier-output"
    assert selected["checks"]["live_worker_payload"] is True
    assert selected["checks"]["rollback_disable_behavior"] is True
    assert selected["checks"]["admin_runtime_visibility"] is True


def test_verifier_emits_document_workflow_governance_sections(monkeypatch):
    state = {"policy_enabled": False, "deleted": False}

    def fake_json_request(method, url, *, headers, payload=None, timeout_seconds=10.0):
        parsed = parse.urlsplit(url)
        path = parsed.path
        user_id = headers.get("X-AI-User-ID")
        tenant_id = headers.get("X-AI-Tenant-ID")
        roles = headers.get("X-AI-Roles", "")

        if method == "GET" and path == "/api/ai/admin/runtime/overview":
            if "admin" not in roles:
                return 403, {"detail": "admin_required"}
            return 200, {
                "schema_version": "ai-platform.admin-runtime-overview.v1",
                "memory_context": {"status": "visible", "workflow": "document-review"},
            }
        if method == "POST" and path == "/api/ai/runs":
            if payload["agent_id"] == "document-review":
                return 200, {"run_id": "run-doc", "session_id": "session-doc"}
            return 200, {"run_id": "run-main", "session_id": "session-main"}
        if method == "PUT" and path == "/api/ai/memory/policy":
            state["policy_enabled"] = bool(payload["memory_enabled"])
            return 200, {
                "memory_policy": {
                    "workspace_id": payload["workspace_id"],
                    "agent_id": payload["agent_id"],
                    "memory_enabled": payload["memory_enabled"],
                    "long_term_memory_enabled": False,
                    "source": "user",
                }
            }
        if method == "POST" and path == "/api/ai/memory/records":
            if not state["policy_enabled"]:
                return 403, {"detail": "memory_policy_disabled"}
            return 200, {"memory_record": {"memory_record_id": "memory-1"}}
        if method == "GET" and path == "/api/ai/memory/records":
            if not state["policy_enabled"] or state["deleted"]:
                return 200, {"memory_records": []}
            return 200, {"memory_records": [{"memory_record_id": "memory-1"}]}
        if method == "POST" and path.endswith("/context/snapshots"):
            included = payload.get("included_memory_record_ids") or []
            return 200, {
                "context_snapshot": {
                    "payload": _public_context_payload(
                        memory_record_count=1 if included else 0
                    )
                }
            }
        if method == "GET" and path.endswith("/playback"):
            return 200, {
                "contract_version": "ai-platform.run-playback.v1",
                "context_ref": _public_context_payload(memory_record_count=1),
            }
        if method == "GET" and path.endswith("/context/snapshots"):
            if tenant_id != "tenant-a" or user_id == "same-tenant-cross-user":
                return 403, {"detail": "context_not_found"}
            return 200, {
                "context_snapshots": [
                    {"payload": _public_context_payload(memory_record_count=0)}
                ]
            }
        if method == "DELETE" and path.startswith("/api/ai/memory/records/"):
            state["deleted"] = True
            return 200, {"memory_record": {"memory_record_id": "memory-1", "status": "deleted"}}
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(verifier, "_json_request", fake_json_request)
    monkeypatch.setattr(
        verifier,
        "_poll_run_detail",
        lambda **_: (200, _run_detail_payload()),
    )

    evidence = verifier.build_b1_memory_context_workflow_smoke(
        base_url="http://127.0.0.1:8020",
        gateway_secret="",
        commit_sha="source-sha",
        runtime_subject_commit_sha="runtime-sha",
        image="ai-platform:source-sha",
        tenant_id="tenant-a",
        other_tenant_id="tenant-b",
        workspace_id="workspace-a",
        probe_file_id="file-1",
        user_id="owner-user",
        cross_user_id="same-tenant-cross-user",
        operator_user_id="operator-user",
        timeout_seconds=0.1,
        wait_seconds=0.1,
    )

    assert evidence["ok"] is True
    assert evidence["workflow"]["agent_id"] == "document-review"
    assert evidence["workflow"]["capability_id"] == "document_review"
    assert evidence["memory_record_count"] == 1
    assert evidence["live_worker_payload"]["document_workflow"] is True
    assert evidence["provenance"]["context_snapshot_public_provenance"] is True
    assert evidence["delete_redaction"]["redaction_scan_status"] == "passed"
    assert evidence["policy_posture"] == {
        "session_workspace_scope": True,
        "retention_days": 30,
        "retention_policy_present": True,
        "opt_out_disable_policy_present": True,
        "export_projection_only": True,
        "delete_redaction_posture_present": True,
        "long_term_memory_fail_closed": True,
    }
    assert evidence["rollback_disable"] == {
        "memory_policy_disabled_blocks_create": True,
        "memory_policy_disabled_blocks_list": True,
        "memory_policy_reenabled_for_governed_scope": True,
        "public_projections_hide_private_context_material": True,
    }
    assert evidence["same_tenant_boundary"] == {
        "owner_context_visible": True,
        "same_tenant_cross_user_denied": True,
        "cross_tenant_context_denied": True,
        "owner_context_status": 200,
        "same_tenant_cross_user_status": 403,
        "cross_tenant_context_status": 403,
    }
    assert evidence["admin_visibility"] == {
        "admin_run_detail_visible": True,
        "admin_runtime_overview_visible": True,
        "ordinary_user_admin_overview_denied": True,
        "admin_projection_redacted": True,
        "admin_run_detail_status": 200,
        "admin_runtime_overview_status": 200,
        "ordinary_user_admin_overview_status": 403,
    }
    for check in (
        "admin_runtime_visibility",
        "cross_tenant_context_denied",
        "live_worker_payload",
        "ordinary_user_admin_visibility_denied",
        "rollback_disable_behavior",
        "same_tenant_context_boundary",
    ):
        assert evidence["checks"][check]["passed"] is True
    _assert_no_private_markers(evidence)


def test_b1_verifier_contract_fixture_outputs_local_source_contract():
    result = subprocess.run(
        [
            sys.executable,
            "tools/verify_b1_memory_context_workflow.py",
            "--contract-fixture",
            "--commit-sha",
            "local-contract",
            "--runtime-subject-commit-sha",
            "local-contract",
            "--image",
            "ai-platform:local-contract",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["target"] == "source_contract_fixture"
    assert payload["does_not_run_live_target"] is True
    assert payload["does_not_close_b1_gate"] is True
    assert payload["workflow"]["agent_id"] == "document-review"
    assert payload["workflow"]["capability_id"] == "document_review"
    assert payload["memory_record_count"] == 1
    assert payload["admin_visibility"]["ordinary_user_admin_overview_denied"] is True
    assert payload["same_tenant_boundary"]["same_tenant_cross_user_denied"] is True
    assert payload["same_tenant_boundary"]["cross_tenant_context_denied"] is True
    assert payload["rollback_disable"]["memory_policy_disabled_blocks_create"] is True
    assert payload["policy_posture"]["retention_days"] == 30
    assert payload["policy_posture"]["opt_out_disable_policy_present"] is True
    assert payload["policy_posture"]["export_projection_only"] is True
    assert payload["redaction_scan_status"] == "passed"
    _assert_no_private_markers(payload)


def test_b1_runtime_acceptance_rank_prefers_newer_review_for_same_subject():
    old_review = {
        "captured_at": "2026-06-18T19:37:24+08:00",
        "path": "docs/release-evidence/b1-memory-context/subject/2026-06-18-old.json",
        "runtime_subject_commit_sha": "52ac62cfbbab47172a659dda11e41aa4b2a5d699",
    }
    new_review = {
        "captured_at": "2026-06-19T00:13:25+08:00",
        "path": "docs/release-evidence/b1-memory-context/subject/2026-06-19-new.json",
        "runtime_subject_commit_sha": "52ac62cfbbab47172a659dda11e41aa4b2a5d699",
    }

    selected = min(
        [old_review, new_review],
        key=lambda summary: _runtime_acceptance_evidence_rank(
            summary,
            "52ac62cfbbab47172a659dda11e41aa4b2a5d699",
        ),
    )

    assert selected is new_review


def test_b1_runtime_acceptance_rank_orders_runtime_source_relation_classes():
    current_review = {
        "captured_at": "2026-06-18T00:00:00+08:00",
        "path": "docs/release-evidence/b1-memory-context/current.json",
        "runtime_subject_commit_sha": "current-source",
    }
    runtime_neutral_review = {
        "captured_at": "2026-06-19T00:00:00+08:00",
        "path": "docs/release-evidence/b1-memory-context/runtime-neutral.json",
        "runtime_subject_commit_sha": "runtime-neutral",
    }
    unknown_delta_review = {
        "captured_at": "2026-06-20T00:00:00+08:00",
        "path": "docs/release-evidence/b1-memory-context/unknown-delta.json",
        "runtime_subject_commit_sha": "unknown-delta",
    }
    runtime_affecting_review = {
        "captured_at": "2026-06-21T00:00:00+08:00",
        "path": "docs/release-evidence/b1-memory-context/runtime-affecting.json",
        "runtime_subject_commit_sha": "runtime-affecting",
    }
    missing_subject_review = {
        "captured_at": "2026-06-22T00:00:00+08:00",
        "path": "docs/release-evidence/b1-memory-context/missing-subject.json",
    }

    def runtime_delta(runtime_subject: str, current_source: str):
        assert current_source == "current-source"
        return {
            "runtime-neutral": [],
            "unknown-delta": None,
            "runtime-affecting": ["app/runtime.py"],
        }[runtime_subject]

    with patch(
        "app.b1_memory_context_readiness._resolve_runtime_affecting_changes_between",
        side_effect=runtime_delta,
    ):
        selected = sorted(
            [
                missing_subject_review,
                runtime_affecting_review,
                unknown_delta_review,
                runtime_neutral_review,
                current_review,
            ],
            key=lambda summary: _runtime_acceptance_evidence_rank(
                summary,
                "current-source",
            ),
        )

    assert selected == [
        current_review,
        runtime_neutral_review,
        unknown_delta_review,
        runtime_affecting_review,
        missing_subject_review,
    ]


def test_b1_memory_context_readiness_status_degrades_for_missing_local_evidence():
    assert (
        _status_for_local_controls(
            {"missing_evidence_markers": ["memory_delete_evidence"]},
            {"open_gaps": []},
        )
        == "blocked_missing_local_evidence"
    )
    assert (
        _status_for_local_controls(
            {"missing_evidence_markers": []},
            {"open_gaps": ["executor_context_pack_211_acceptance"]},
        )
        == "blocked_missing_context_pack_evidence"
    )
    assert (
        _status_for_local_controls(
            {"missing_evidence_markers": []},
            {"open_gaps": []},
        )
        == "local_controls_ready_runtime_smoke_required"
    )

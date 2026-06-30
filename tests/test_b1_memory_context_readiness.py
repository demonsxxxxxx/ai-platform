import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from app.b1_memory_context_readiness import (
    _runtime_acceptance_evidence_rank,
    _runtime_acceptance_evidence,
    _status_for_local_controls,
    build_b1_memory_context_readiness,
    render_b1_memory_context_readiness_markdown,
)


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
    "f67986a6fcf009d5e22c38be4ed71cc979f24f27/"
    "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a.json"
)

B1_GATE_BOUNDARY_GAPS: list[str] = []


def test_current_b1_release_evidence_excludes_raw_paths_and_private_markers():
    payload = json.loads(CURRENT_B1_EVIDENCE_PATH.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()

    assert "raw_evidence_path" not in serialized
    assert "runtime-evidence/" not in serialized
    assert payload["review_status"] == "reviewed"
    assert payload["redaction_scan_status"] == "passed"
    source_ref = payload["source_ref"]
    assert source_ref["runtime_source_marker"] == payload["runtime_subject_commit_sha"]
    assert source_ref["runtime_commit"] == payload["runtime_subject_commit_sha"]
    assert source_ref["source_snapshot"]["source_tree_commit_sha"] == payload["runtime_subject_commit_sha"]
    assert source_ref["source_snapshot"]["runtime_affecting_changes_since_runtime_subject"] == []
    assert source_ref["source_snapshot"]["runtime_affecting_dirty_paths"] == []
    assert source_ref["source_snapshot"]["source_tree_dirty"] is False
    assert source_ref["source_tree_dirty"] is False
    assert payload["evidence_ref"]["runtime_checks"] == {
        "211_memory_enabled_document_workflow_smoke": payload["evidence_ref"]["runtime_checks"][
            "211_memory_enabled_document_workflow_smoke"
        ]
    }
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in serialized


def test_b1_memory_context_readiness_records_reviewed_211_smoke_without_closing_b1_gate():
    readiness = build_b1_memory_context_readiness()

    assert readiness["schema_version"] == "ai-platform.b1-memory-context-readiness.v1"
    assert readiness["backend_stage"] == "B1 memory/context usable"
    assert readiness["status"] == "runtime_acceptance_recorded"
    assert readiness["status_label"] == "local partial"
    assert readiness["issue"] == "#75"
    assert readiness["admin_runtime_projection"] == "/api/ai/admin/runtime/overview"
    assert readiness["ordinary_user_policy"] == "session_scoped_memory_with_public_provenance"

    implemented = readiness["implemented_controls"]
    for control in (
        "tenant_workspace_user_session_scoped_memory_policy",
        "memory_policy_disabled_blocks_reads_and_writes",
        "ordinary_user_session_scoped_soft_delete",
        "retention_cleanup_admin_and_worker",
        "memory_export_excludes_deleted_and_expired_records",
        "memory_redaction_preview_and_audit",
        "context_pack_persistence_version_generated_at_provenance",
        "context_snapshot_public_provenance_projection",
        "worker_loads_scoped_db_context_snapshot",
        "long_term_cross_session_memory_fail_closed",
    ):
        assert control in implemented

    assert readiness["local_evidence"]["memory_erasure_readiness"]["status"] == "partial_blocked"
    assert readiness["local_evidence"]["office_context_readiness"]["status"] == "runtime_acceptance_recorded"
    assert readiness["local_evidence"]["memory_erasure_readiness"]["missing_evidence_marker_count"] == 0
    assert readiness["local_evidence"]["office_context_readiness"]["closed_runtime_gap_count"] >= 2
    assert readiness["local_evidence"]["office_context_readiness"]["open_gap_count"] == 0

    assert readiness["runtime_acceptance"]["required"] is True
    assert readiness["runtime_acceptance"]["status"] == "verified_211_runtime_acceptance"
    assert readiness["runtime_acceptance"]["acceptance_gap"] == "211_memory_enabled_document_workflow_smoke"
    assert readiness["runtime_acceptance"]["verifier_script"] == "tools/verify_b1_memory_context_workflow.py"
    assert (
        readiness["runtime_acceptance"]["verifier_schema_version"]
        == "ai-platform.b1-memory-context-workflow-smoke.v1"
    )
    assert readiness["runtime_acceptance"]["target"] == "211_api_memory_context_workflow"
    assert readiness["runtime_acceptance"]["status_label_before_smoke"] == "local partial"
    assert readiness["runtime_acceptance"]["status_label_after_smoke"] == "211 verified"
    assert readiness["runtime_acceptance"]["does_not_close_b1_gate"] is True
    assert "issue review and closure evidence" not in readiness["runtime_acceptance"]["remaining_gate_boundaries"]
    assert "runtime evidence review against merged source" not in readiness["runtime_acceptance"]["remaining_gate_boundaries"]
    assert "rollback boundary" not in readiness["runtime_acceptance"]["remaining_gate_boundaries"]
    assert "memory export boundary" not in readiness["runtime_acceptance"]["remaining_gate_boundaries"]
    assert readiness["open_gaps"] == B1_GATE_BOUNDARY_GAPS
    assert "211_memory_enabled_document_workflow_smoke" not in readiness["open_gaps"]
    assert "b1_memory_export_boundary" not in readiness["open_gaps"]
    assert "b1_runtime_evidence_review_against_merged_source" not in readiness["open_gaps"]
    boundary_evidence = readiness["gate_boundary_evidence"]
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["status"] == (
        "recorded_issue_closure_evidence"
    )
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["closed_gap"] == (
        "b1_issue_review_and_closure_evidence"
    )
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["issue"] == "#75"
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["issue_state"] == "closed"
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["does_not_close_b1_gate"] is True
    assert boundary_evidence["b1_issue_review_and_closure_evidence"]["evidence_refs"] == [
        "docs/release-evidence/b1-memory-context/"
        "52ac62cfbbab47172a659dda11e41aa4b2a5d699/"
        "2026-06-19-211-b1-memory-context-workflow-smoke-52ac62c.json"
    ]
    assert "does_not_claim_production_readiness" in boundary_evidence[
        "b1_issue_review_and_closure_evidence"
    ]["residual_caveats"]
    assert "docs/release-evidence/backend-stage-closures" in boundary_evidence[
        "b1_issue_review_and_closure_evidence"
    ]["path"]
    assert boundary_evidence["b1_memory_export_boundary"]["status"] == "recorded_local_contract"
    assert boundary_evidence["b1_memory_export_boundary"]["closed_gap"] == "b1_memory_export_boundary"
    assert boundary_evidence["b1_memory_export_boundary"]["does_not_close_b1_gate"] is True
    assert boundary_evidence["b1_memory_export_boundary"]["source_readiness"] == (
        "ai-platform.memory-erasure-readiness.v1"
    )
    assert boundary_evidence["b1_memory_export_boundary"]["required_controls"] == [
        "ordinary_user_export_excludes_deleted_and_expired_records",
        "ordinary_user_export_requires_session_scope_and_enabled_policy",
        "admin_export_operator_projection_without_content_or_metadata",
    ]
    runtime_review = boundary_evidence["b1_runtime_evidence_review_against_merged_source"]
    assert runtime_review["status"] == "recorded_local_contract"
    assert runtime_review["runtime_subject_commit_sha"] == (
        "f67986a6fcf009d5e22c38be4ed71cc979f24f27"
    )
    assert runtime_review["current_source_commit_sha"]
    runtime_delta = runtime_review["runtime_affecting_changes_since_runtime_subject"]
    assert runtime_delta == []
    assert runtime_review["required_next_step"] == (
        "record issue closure evidence after final issue review"
    )
    assert runtime_review["closed_gap"] == "b1_runtime_evidence_review_against_merged_source"
    rollback_boundary = boundary_evidence["b1_rollback_boundary"]
    assert rollback_boundary["status"] == "recorded_local_contract"
    assert rollback_boundary["closed_gap"] == "b1_rollback_boundary"
    assert rollback_boundary["does_not_close_b1_gate"] is True
    assert rollback_boundary["rollback_controls"] == [
        "disable_memory_policy_for_governed_workflow",
        "disable_context_pack_injection_for_governed_workflow",
        "pause_memory_retention_worker_cleanup",
        "verify_existing_memory_records_remain_scoped_and_exportable",
        "verify_public_projections_hide_private_context_material",
        "restore_previous_runtime_configuration_from_release_evidence",
    ]
    assert rollback_boundary["operator_steps"] == [
        "capture current source/runtime subject and Admin Runtime memory/context status",
        "disable selected workflow memory policy before disabling context-pack injection",
        "restart or reload API and worker runtime if configuration changed",
        "run B1 verifier or reduced deny-path smoke to confirm no new memory reads or writes",
        "record issue comment with source/runtime subject, verification result, and residual caveats",
    ]
    assert "211_memory_enabled_document_workflow_smoke" in readiness["closed_runtime_gaps"]
    assert "b1_issue_review_and_closure_evidence" in readiness["closed_gate_boundary_gaps"]
    assert "b1_memory_export_boundary" in readiness["closed_gate_boundary_gaps"]
    assert "b1_rollback_boundary" in readiness["closed_gate_boundary_gaps"]
    assert "b1_runtime_evidence_review_against_merged_source" in readiness["closed_gate_boundary_gaps"]
    assert set(readiness["local_evidence"]["memory_erasure_readiness"]["closed_runtime_gaps"]).issubset(
        set(readiness["closed_runtime_gaps"])
    )
    smoke_evidence = readiness["runtime_acceptance_evidence"]["211_memory_enabled_document_workflow_smoke"]
    assert smoke_evidence["status"] == "verified_211_runtime_acceptance"
    assert smoke_evidence["artifact_kind"] == "211_memory_enabled_document_workflow_smoke"
    assert smoke_evidence["verifier"] == "tools/verify_b1_memory_context_workflow.py"
    assert smoke_evidence["evidence_id"] == "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a"
    assert smoke_evidence["runtime_subject"] == "f67986a-b0-current-main-runtime-only-v2"
    assert smoke_evidence["runtime_subject_commit_sha"] == (
        "f67986a6fcf009d5e22c38be4ed71cc979f24f27"
    )
    assert smoke_evidence["memory_record_count"] == 1
    assert smoke_evidence["checks"]["playback_public_projection"] is True
    assert smoke_evidence["checks"]["memory_policy_disabled_blocks_list"] is True
    assert smoke_evidence["redaction_scan_status"] == "passed"
    assert smoke_evidence["does_not_close_b1_gate"] is True
    assert smoke_evidence["path"].endswith(
        "docs/release-evidence/b1-memory-context/"
        "f67986a6fcf009d5e22c38be4ed71cc979f24f27/"
        "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a.json"
    )

    assert readiness["non_expansion_invariants"] == {
        "long_term_cross_session_memory_enabled": False,
        "public_projection_only_for_ordinary_users": True,
        "stores_private_executor_material_as_memory": False,
        "frontend_state_is_canonical_context": False,
        "production_claim_allowed": False,
    }

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "211 verified" in serialized
    assert "recorded_issue_closure_evidence" in serialized
    assert "b1_runtime_evidence_review_against_merged_source" in serialized
    assert "recorded_local_contract" in serialized
    assert "runtime_affecting_delta_requires_fresh_211_smoke" not in serialized
    assert "closed_gate_boundary_gaps" in serialized
    assert "b1_rollback_boundary" in serialized
    assert "gate closable" not in serialized
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in serialized


def test_b1_memory_context_readiness_markdown_is_gap_first_and_boundary_explicit():
    markdown = render_b1_memory_context_readiness_markdown(build_b1_memory_context_readiness())

    assert "# ai-platform B1 Memory/Context Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    open_gap_section = markdown.split("## Closed Gate Boundary Gaps", 1)[0]
    assert "- b1_issue_review_and_closure_evidence" not in open_gap_section
    assert "- b1_runtime_evidence_review_against_merged_source" not in open_gap_section
    assert "- b1_rollback_boundary" not in markdown.split("## Closed Gate Boundary Gaps", 1)[0]
    assert "- b1_memory_export_boundary" not in markdown.split("## Closed Gate Boundary Gaps", 1)[0]
    assert "## Closed Gate Boundary Gaps" in markdown
    assert "- b1_runtime_evidence_review_against_merged_source" in markdown.split("## Closed Gate Boundary Gaps", 1)[1]
    assert "- b1_memory_export_boundary" in markdown.split("## Closed Gate Boundary Gaps", 1)[1]
    assert "- b1_rollback_boundary" in markdown.split("## Closed Gate Boundary Gaps", 1)[1]
    assert "- b1_issue_review_and_closure_evidence" in markdown.split("## Closed Gate Boundary Gaps", 1)[1]
    assert "ordinary_user_export_excludes_deleted_and_expired_records" in markdown
    assert "disable_memory_policy_for_governed_workflow" in markdown
    assert "- none" in open_gap_section
    assert "## Runtime Acceptance" in markdown
    assert "### B1 Runtime Evidence Review Against Merged Source" in markdown
    assert "### B1 Issue Closure Evidence" in markdown
    assert "docs/release-evidence/backend-stage-closures/b1-memory-context" in markdown
    assert "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a.json" in markdown
    assert "does_not_claim_production_readiness" in markdown
    assert "runtime_affecting_delta_requires_fresh_211_smoke" not in markdown
    assert "f67986a6fcf009d5e22c38be4ed71cc979f24f27" in markdown
    assert "record issue closure evidence after final issue review" in markdown
    assert "verified_211_runtime_acceptance" in markdown
    assert "tools/verify_b1_memory_context_workflow.py" in markdown
    assert "Does not close B1 gate: `true`" in markdown
    assert "211 verified" in markdown.lower()
    assert "gate closable" not in markdown.lower()
    assert "does not enable long-term cross-session memory by default" in markdown
    assert "does not store executor-private payloads as memory" in markdown
    assert "does not make frontend state canonical context" in markdown
    assert "c:\\users" not in markdown.lower()


def test_b1_memory_context_readiness_cli_outputs_json_without_private_markers():
    result = subprocess.run(
        [sys.executable, "tools/b1_memory_context_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.b1-memory-context-readiness.v1"
    assert payload["status"] == "runtime_acceptance_recorded"
    assert payload["status_label"] == "local partial"
    assert payload["runtime_acceptance"]["acceptance_gap"] == "211_memory_enabled_document_workflow_smoke"
    assert payload["runtime_acceptance"]["verifier_script"] == "tools/verify_b1_memory_context_workflow.py"
    assert payload["runtime_acceptance"]["does_not_close_b1_gate"] is True
    assert payload["open_gaps"] == B1_GATE_BOUNDARY_GAPS
    assert "211_memory_enabled_document_workflow_smoke" not in payload["open_gaps"]
    assert "b1_memory_export_boundary" not in payload["open_gaps"]
    assert "b1_runtime_evidence_review_against_merged_source" not in payload["open_gaps"]
    assert "211_memory_enabled_document_workflow_smoke" in payload["closed_runtime_gaps"]
    assert "b1_issue_review_and_closure_evidence" in payload["closed_gate_boundary_gaps"]
    assert "b1_memory_export_boundary" in payload["closed_gate_boundary_gaps"]
    assert "b1_rollback_boundary" in payload["closed_gate_boundary_gaps"]
    assert "b1_runtime_evidence_review_against_merged_source" in payload["closed_gate_boundary_gaps"]
    assert payload["gate_boundary_evidence"]["b1_memory_export_boundary"]["status"] == (
        "recorded_local_contract"
    )
    assert payload["gate_boundary_evidence"]["b1_rollback_boundary"]["status"] == (
        "recorded_local_contract"
    )
    assert payload["gate_boundary_evidence"]["b1_issue_review_and_closure_evidence"]["status"] == (
        "recorded_issue_closure_evidence"
    )
    assert payload["gate_boundary_evidence"]["b1_runtime_evidence_review_against_merged_source"]["status"] == (
        "recorded_local_contract"
    )
    runtime_delta = payload["gate_boundary_evidence"]["b1_runtime_evidence_review_against_merged_source"][
        "runtime_affecting_changes_since_runtime_subject"
    ]
    assert runtime_delta == []
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in result.stdout.lower()


def test_b1_issue_closure_gap_stays_open_without_valid_local_closure_evidence(tmp_path):
    readiness = build_b1_memory_context_readiness(repo_root=tmp_path)

    assert "b1_issue_review_and_closure_evidence" in readiness["open_gaps"]
    closure_evidence = readiness["gate_boundary_evidence"]["b1_issue_review_and_closure_evidence"]
    assert closure_evidence["status"] == "open_missing_issue_closure_evidence"
    assert closure_evidence["closed_gap"] is None
    assert closure_evidence["required_next_step"] == (
        "record reviewed local issue-closure evidence for #75 before closing this boundary"
    )


def test_b1_runtime_acceptance_evidence_prefers_current_subject_when_history_exists():
    selected = _runtime_acceptance_evidence(Path.cwd())["211_memory_enabled_document_workflow_smoke"]

    assert selected["evidence_id"] == "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a"
    assert selected["runtime_subject_commit_sha"] == "f67986a6fcf009d5e22c38be4ed71cc979f24f27"
    assert selected["path"].endswith(
        "docs/release-evidence/b1-memory-context/"
        "f67986a6fcf009d5e22c38be4ed71cc979f24f27/"
        "2026-06-30-211-b1-memory-context-workflow-smoke-f67986a.json"
    )


def test_b1_runtime_review_gap_closes_for_current_source_without_runtime_affecting_delta():
    readiness = build_b1_memory_context_readiness()

    runtime_review = readiness["gate_boundary_evidence"][
        "b1_runtime_evidence_review_against_merged_source"
    ]
    assert runtime_review["status"] == "recorded_local_contract"
    assert runtime_review["closed_gap"] == "b1_runtime_evidence_review_against_merged_source"
    assert runtime_review["runtime_subject_commit_sha"] == (
        "f67986a6fcf009d5e22c38be4ed71cc979f24f27"
    )
    runtime_delta = runtime_review["runtime_affecting_changes_since_runtime_subject"]
    assert runtime_delta == []
    assert runtime_review["required_next_step"] == (
        "record issue closure evidence after final issue review"
    )
    assert "b1_runtime_evidence_review_against_merged_source" not in readiness["open_gaps"]
    assert (
        "b1_runtime_evidence_review_against_merged_source"
        in readiness["closed_gate_boundary_gaps"]
    )


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

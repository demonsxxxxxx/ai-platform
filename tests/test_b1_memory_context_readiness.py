import json
import subprocess
import sys

from app.b1_memory_context_readiness import (
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
]


def test_b1_memory_context_readiness_aggregates_local_controls_and_keeps_211_gap_open():
    readiness = build_b1_memory_context_readiness()

    assert readiness["schema_version"] == "ai-platform.b1-memory-context-readiness.v1"
    assert readiness["backend_stage"] == "B1 memory/context usable"
    assert readiness["status"] == "local_controls_ready_runtime_smoke_required"
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
    assert readiness["runtime_acceptance"]["status"] == "missing_211_memory_enabled_document_workflow_smoke"
    assert readiness["runtime_acceptance"]["acceptance_gap"] == "211_memory_enabled_document_workflow_smoke"
    assert readiness["runtime_acceptance"]["status_label_before_smoke"] == "local partial"
    assert "status_label_after_smoke" not in readiness["runtime_acceptance"]
    assert readiness["open_gaps"] == ["211_memory_enabled_document_workflow_smoke"]
    assert readiness["closed_runtime_gaps"] == readiness["local_evidence"]["memory_erasure_readiness"]["closed_runtime_gaps"]

    assert readiness["non_expansion_invariants"] == {
        "long_term_cross_session_memory_enabled": False,
        "public_projection_only_for_ordinary_users": True,
        "stores_private_executor_material_as_memory": False,
        "frontend_state_is_canonical_context": False,
        "production_claim_allowed": False,
    }

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "211 verified" not in serialized
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in serialized


def test_b1_memory_context_readiness_markdown_is_gap_first_and_boundary_explicit():
    markdown = render_b1_memory_context_readiness_markdown(build_b1_memory_context_readiness())

    assert "# ai-platform B1 Memory/Context Readiness" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## Open Gaps" in markdown
    assert "- 211_memory_enabled_document_workflow_smoke" in markdown
    assert "## Runtime Acceptance" in markdown
    assert "missing_211_memory_enabled_document_workflow_smoke" in markdown
    assert "211 verified" not in markdown.lower()
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
    assert payload["status"] == "local_controls_ready_runtime_smoke_required"
    assert payload["runtime_acceptance"]["acceptance_gap"] == "211_memory_enabled_document_workflow_smoke"
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in result.stdout.lower()


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

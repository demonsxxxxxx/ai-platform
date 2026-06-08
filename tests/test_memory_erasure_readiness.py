import json
import subprocess
import sys

from app.memory_erasure_readiness import (
    build_memory_erasure_readiness,
    render_memory_erasure_readiness_markdown,
)


FORBIDDEN_PRIVATE_MARKERS = [
    "executor" + "_private_payload",
    "raw" + "_storage_key",
    "sandbox" + "_workdir",
    "callback" + "-secret",
]


def test_memory_erasure_readiness_records_delete_retention_evidence_without_private_payloads():
    readiness = build_memory_erasure_readiness()

    assert readiness["schema_version"] == "ai-platform.memory-erasure-readiness.v1"
    assert readiness["status"] == "partial_blocked"
    assert readiness["admin_runtime_projection"] == "/api/ai/admin/runtime/overview"
    assert readiness["ordinary_user_policy"] == "session_scoped_delete_only"

    implemented = readiness["implemented_controls"]
    assert "ordinary_user_session_scoped_soft_delete" in implemented
    assert "admin_same_tenant_soft_delete" in implemented
    assert "admin_retention_cleanup_soft_delete" in implemented
    assert "worker_retention_cleanup_across_scopes" in implemented
    assert "ordinary_user_export_excludes_deleted_and_expired_records" in implemented
    assert "admin_export_operator_projection_without_content_or_metadata" in implemented
    assert "delete_and_cleanup_projection_without_content_or_metadata" in implemented
    assert "delete_and_cleanup_audit_payload_allowlist" in implemented
    assert "memory_redaction_policy_admin_preview_and_audit" in implemented
    assert "office_context_pack_architecture_readiness_snapshot" in implemented

    markers = {item["name"]: item for item in readiness["evidence_markers"]}
    assert set(markers) == {
        "ordinary_user_delete_route",
        "admin_delete_route",
        "admin_retention_cleanup_route",
        "worker_retention_cleanup",
        "ordinary_user_export_query",
        "ordinary_user_export_route_policy",
        "admin_export_operator_projection",
        "repository_soft_delete_without_content_returning",
        "repository_export_erasure_tests",
        "route_delete_tests",
        "route_export_erasure_tests",
        "admin_redaction_preview_audit_route",
        "route_redaction_preview_audit_tests",
        "worker_cleanup_tests",
    }
    assert all(item["status"] == "present" for item in markers.values())
    assert all(item["missing_markers"] == [] for item in markers.values())

    assert readiness["open_gaps"] == [
        "office_context_pack_runtime_implementation_and_acceptance",
        "document_centric_followup_state",
        "sandbox_cold_start_latency_split",
        "frontend_context_provenance_acceptance",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in serialized
    assert "c:\\users" not in serialized


def test_render_memory_erasure_readiness_markdown_is_gap_first_and_operator_readable():
    markdown = render_memory_erasure_readiness_markdown(build_memory_erasure_readiness())

    assert "# ai-platform Memory Erasure Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "memory_export_erasure_evidence" not in markdown
    assert "ordinary_user_export_excludes_deleted_and_expired_records" in markdown
    assert "ordinary_user_session_scoped_soft_delete" in markdown
    assert "worker_retention_cleanup_across_scopes" in markdown
    assert "memory_redaction_policy_admin_preview_and_audit" in markdown
    assert "office_context_pack_runtime_implementation_and_acceptance" in markdown
    assert "c:\\users" not in markdown.lower()


def test_memory_erasure_readiness_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [sys.executable, "tools/memory_erasure_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.memory-erasure-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert "memory_export_erasure_evidence" not in payload["open_gaps"]
    assert "memory_redaction_policy_admin_preview_and_audit" not in payload["open_gaps"]
    assert "bounded_context_pack_product_contract_for_office_workflows" not in payload["open_gaps"]
    assert "office_context_pack_runtime_implementation_and_acceptance" in payload["open_gaps"]
    assert "memory_redaction_policy_admin_preview_and_audit" in payload["implemented_controls"]
    assert "office_context_pack_architecture_readiness_snapshot" in payload["implemented_controls"]
    assert "ordinary_user_export_excludes_deleted_and_expired_records" in payload["implemented_controls"]
    for marker in FORBIDDEN_PRIVATE_MARKERS:
        assert marker not in result.stdout

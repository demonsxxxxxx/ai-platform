import json
import subprocess
import sys

import pytest

from app.b5_file_tool_readiness import (
    build_b5_file_tool_readiness,
    render_b5_file_tool_readiness_markdown,
)


def test_b5_file_tool_readiness_exposes_file_and_tool_boundaries():
    readiness = build_b5_file_tool_readiness()

    assert readiness["schema_version"] == "ai-platform.b5-file-tool-readiness.v1"
    assert readiness["backend_stage"] == "B5 files/artifacts/tool permission governance"
    assert readiness["status"] == "partial_blocked"
    assert readiness["status_label"] == "local partial"
    assert readiness["claim_boundary"]["does_not_create_211_verified"] is True
    assert readiness["claim_boundary"]["does_not_close_b5_g6_g7_g9"] is True
    assert readiness["claim_boundary"]["does_not_enable_product_beta"] is True

    file_authority = readiness["domains"]["file_artifact_authority"]
    assert file_authority["gate_slice"] == "B5a file/artifact authority"
    assert file_authority["status"] == "local_contract_recorded"
    assert "artifact_owner_tenant_acl_download" in file_authority["implemented_controls"]
    assert "artifact_preview_owner_acl_and_content_type_allowlist" in file_authority["implemented_controls"]
    assert "file_upload_namespace_retention_runtime_smoke" in file_authority["open_gaps"]
    assert "211_file_to_artifact_unauthorized_denial_smoke" in file_authority["open_gaps"]

    tool_authority = readiness["domains"]["exact_tool_permission"]
    assert tool_authority["gate_slice"] == "B5b exact tool permission"
    assert tool_authority["status"] == "local_contract_recorded"
    assert "exact_tool_permission_decision_lookup_source_tests" in tool_authority["implemented_controls"]
    assert "allow_once_replay_denial_source_tests" in tool_authority["implemented_controls"]
    assert "shell_network_filesystem_mcp_runtime_replay_denial_smoke" in tool_authority["open_gaps"]

    assert readiness["open_gaps"] == [
        "file_upload_namespace_retention_runtime_smoke",
        "artifact_preview_download_unauthorized_denial_211_smoke",
        "exact_tool_permission_runtime_replay_denial_smoke",
        "projection_redaction_runtime_acceptance",
        "b5_issue_review_and_closure_evidence",
    ]


def test_b5_file_tool_readiness_markdown_is_operator_readable():
    markdown = render_b5_file_tool_readiness_markdown(build_b5_file_tool_readiness())

    assert "# ai-platform B5 File/Tool Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "Status label: `local partial`" in markdown
    assert "## B5a File And Artifact Authority" in markdown
    assert "## B5b Exact Tool Permission" in markdown
    assert "file_upload_namespace_retention_runtime_smoke" in markdown
    assert "exact_tool_permission_runtime_replay_denial_smoke" in markdown
    assert "does not create `211 verified`" in markdown
    assert "does not close B5/G6/G7/G9" in markdown


def test_b5_file_tool_readiness_markdown_fails_closed_on_boundary_regression():
    readiness = build_b5_file_tool_readiness()
    readiness["claim_boundary"]["does_not_create_211_verified"] = False

    with pytest.raises(RuntimeError, match="b5_claim_boundary_regression"):
        render_b5_file_tool_readiness_markdown(readiness)


def test_b5_file_tool_readiness_cli_outputs_json_without_private_markers():
    result = subprocess.run(
        [sys.executable, "tools/b5_file_tool_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.b5-file-tool-readiness.v1"
    assert payload["status_label"] == "local partial"
    assert "C:\\Users" not in result.stdout
    assert "/home/xinlin.jiang" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "executor_private_payload" not in result.stdout

import json
import subprocess
import sys

import pytest

import app.foundation_alpha_readiness as foundation_alpha_readiness
from app.foundation_alpha_readiness import (
    build_foundation_alpha_readiness,
    render_foundation_alpha_readiness_markdown,
)


class SecretBearingSettings:
    sandbox_container_provider = "docker://token@internal/path"
    sandbox_callback_token = "callback-secret"
    sandbox_workspace_root = "/tmp/tenant-secret/workspaces"
    anthropic_auth_token = "anthropic-secret"
    llm_gateway_provider = "openai_compatible"
    model_gateway_request_concurrency_limit = 0
    memory_retention_worker_cleanup_enabled = True
    memory_retention_worker_cleanup_limit = 200
    multi_agent_dispatch_worker_enabled = False


def test_foundation_alpha_readiness_aggregates_current_poc_evidence_without_overclaiming():
    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["schema_version"] == "ai-platform.foundation-alpha-poc-readiness.v1"
    assert readiness["stage"] == "Foundation Alpha POC"
    assert readiness["status"] == "211_verified_followups_open"
    assert readiness["runtime_subject_commit_sha"] == "8c0cffca63bc747fad0a5771f209acc8a608ab9e"
    assert readiness["decision"] == {
        "controlled_poc_loop_verified": True,
        "can_enter_next_stage_without_restrictions": False,
        "production_claim_allowed": False,
        "ordinary_user_multi_agent_allowed": False,
        "docker_sandbox_hardened_claim_allowed": False,
        "capacity_default_increase_allowed": False,
    }

    assert set(readiness["domains"]) == {
        "g0_g1_source_authority_security",
        "g2_g4_control_plane_contracts",
        "g5_run_lifecycle_worker_runtime",
        "g6_poc_governance",
        "g9_admin_runtime_observability",
        "frontend_poc",
    }
    assert readiness["domains"]["g0_g1_source_authority_security"]["status"] == "poc_verified_keep_under_regression"
    assert readiness["domains"]["g5_run_lifecycle_worker_runtime"]["evidence"]["document_review_attachment_run"] == {
        "status": "succeeded",
        "skill_id": "qa-file-reviewer",
        "artifact_types": [
            "report_txt",
            "result_json",
            "reviewed_docx",
        ],
        "playback_contract_version": "ai-platform.run-playback.v1",
    }
    assert readiness["domains"]["frontend_poc"]["evidence"]["same_origin_api_health"]["payload_status"] == "ok"
    assert readiness["domains"]["g6_poc_governance"]["evidence"]["governance_readiness_status"] == "partial_blocked"
    assert readiness["domains"]["g9_admin_runtime_observability"]["evidence"]["observability_readiness_status"] == "partial_blocked"

    assert "#21_recorded_capacity_evidence" in readiness["open_followups"]
    assert "g7_docker_sandbox_hardening" in readiness["open_followups"]
    assert "g8_ordinary_user_multi_agent_exposure" in readiness["open_followups"]
    assert "g9_runtime_export_and_retention_acceptance" in readiness["open_followups"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "anthropic-secret" not in serialized
    assert "docker://token" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized
    assert "c:\\users" not in serialized


def test_foundation_alpha_readiness_markdown_and_cli_are_operator_usable():
    readiness = build_foundation_alpha_readiness(SecretBearingSettings())
    markdown = render_foundation_alpha_readiness_markdown(readiness)

    assert "# ai-platform Foundation Alpha POC Readiness" in markdown
    assert "Schema: `ai-platform.foundation-alpha-poc-readiness.v1`" in markdown
    assert "Status: `211_verified_followups_open`" in markdown
    assert "Current decision" in markdown
    assert "`production_claim_allowed`: `False`" in markdown
    assert "#21_recorded_capacity_evidence" in markdown

    json_result = subprocess.run(
        [sys.executable, "tools/foundation_alpha_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(json_result.stdout)
    assert payload["schema_version"] == "ai-platform.foundation-alpha-poc-readiness.v1"
    assert payload["decision"]["controlled_poc_loop_verified"] is True

    markdown_result = subprocess.run(
        [sys.executable, "tools/foundation_alpha_readiness.py", "--format", "markdown"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "# ai-platform Foundation Alpha POC Readiness" in markdown_result.stdout


def test_foundation_alpha_readiness_fails_closed_when_optional_readiness_dependencies_are_unavailable(monkeypatch):
    def missing_governance(_: object | None = None):
        raise ModuleNotFoundError("No module named 'pydantic'")

    def missing_observability(_: object | None = None):
        raise ModuleNotFoundError("No module named 'pydantic'")

    monkeypatch.setattr(foundation_alpha_readiness, "_build_governance_summary", missing_governance)
    monkeypatch.setattr(foundation_alpha_readiness, "_build_observability_summary", missing_observability)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["domains"]["g6_poc_governance"]["status"] == "partial_followups_open"
    assert readiness["domains"]["g6_poc_governance"]["evidence"] == {
        "governance_readiness_status": "dependency_unavailable",
        "ordinary_user_policy": "fail_closed_until_projection_mapping_and_acceptance_pass",
        "open_gap_count": 1,
        "dependency_error_class": "ModuleNotFoundError",
        "skill_snapshot_run_seen": True,
        "tool_permission_decision_audit_required": True,
        "memory_long_term_default_fail_closed": True,
    }
    assert readiness["domains"]["g9_admin_runtime_observability"]["evidence"] == {
        "observability_readiness_status": "dependency_unavailable",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "open_gap_count": 1,
        "dependency_error_class": "ModuleNotFoundError",
        "release_evidence_result": "ok:true",
    }

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "pydantic" not in serialized
    assert "traceback" not in serialized

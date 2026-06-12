import json
import subprocess
import sys

import pytest

import app.foundation_alpha_readiness as foundation_alpha_readiness
from app.foundation_alpha_readiness import (
    build_foundation_alpha_readiness,
    render_foundation_alpha_readiness_markdown,
)

ACTIVE_RUNTIME_SUBJECT_SHA = "6088d5d179c422a6d753e1b77079410503e58925"
HISTORICAL_RUNTIME_SUBJECT_SHA = "8c0cffca63bc747fad0a5771f209acc8a608ab9e"
RUNTIME_SUBJECT_SHA = HISTORICAL_RUNTIME_SUBJECT_SHA
CURRENT_SOURCE_SHA = "a3f1d739e12686cba2e0b309de26a4e1127bd3a5"
NEWER_SOURCE_SHA = "78362bcb380da67408ff7298cbdf24978d370992"
DEFAULT_FRONTEND_PROJECTION_AUDIT_SUMMARY = {
    "status": "test_default_blocked",
    "ordinary_user_acceptance": "blocked_active_legacy_routes_or_projection_audit",
    "active_legacy_route_count": None,
    "active_forbidden_projection_violation_count": None,
    "ci_verify_includes_projection_audit": False,
    "open_gap_count": 1,
    "open_gaps": ["frontend_projection_audit_not_exercised_in_unit_default"],
}


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


@pytest.fixture(autouse=True)
def _default_no_runtime_affecting_dirty_paths(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_dirty_paths",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_projection_audit_summary",
        _default_frontend_projection_audit_summary,
        raising=False,
    )


def _default_frontend_projection_audit_summary() -> dict:
    return {
        **DEFAULT_FRONTEND_PROJECTION_AUDIT_SUMMARY,
        "open_gaps": list(DEFAULT_FRONTEND_PROJECTION_AUDIT_SUMMARY["open_gaps"]),
    }


def test_foundation_alpha_readiness_unit_default_uses_blocked_projection_audit_stub():
    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert (
        readiness["domains"]["frontend_poc"]["evidence"]["frontend_projection_audit"]
        == DEFAULT_FRONTEND_PROJECTION_AUDIT_SUMMARY
    )
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" in readiness["open_followups"]


def _minimal_smoke_payload(commit_sha: str, *, image: str, captured_at: str = "2026-06-11T10:00:00+08:00") -> dict:
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-smoke",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "211_runtime_smoke",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
            "repo_local_env_present": False,
        },
        "evidence_ref": {
            "result": "ok:true",
            "runtime_checks": {
                "lambchat_frontend": {"status": 200},
                "frontend_dist_api_boundary": {"forbidden_reference_count": 0},
                "same_origin_api_health": {"status": 200, "payload_status": "ok"},
                "general_chat_run": "succeeded",
                "upload_attachment_chat": {
                    "upload_status": 200,
                    "chat_status": 200,
                    "run_status": "succeeded",
                    "executor_type": "claude-agent-worker",
                },
                "document_review_attachment_run": {
                    "status": "succeeded",
                    "skill_id": "qa-file-reviewer",
                    "artifact_types": ["reviewed_docx"],
                    "playback_contract_version": "ai-platform.run-playback.v1",
                    "private_payload_leaked": False,
                },
                "artifact_download_isolation": {
                    "owner_statuses": [200],
                    "cross_user_statuses": [404],
                },
                "artifact_preview_isolation": {
                    "owner_statuses": [200],
                    "cross_user_statuses": [404],
                    "cache_control": "no-store",
                },
                "context_snapshot_public_projection": {
                    "status": 200,
                    "ok": True,
                    "snapshot_count": 1,
                    "referenced_material_counts": {
                        "message_count": 1,
                        "file_count": 1,
                        "artifact_count": 1,
                        "memory_record_count": 1,
                    },
                    "raw_material_id_fields_present": False,
                    "forbidden_projection_leaks": [],
                    "summary_source": "stored_context_snapshot",
                    "input_keys": ["attachments", "message"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": False,
                    "execution_tier": "sdk_only_writing",
                    "context_pack_generated_at_present": True,
                },
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _minimal_auth_payload(commit_sha: str, *, image: str, captured_at: str = "2026-06-11T10:01:00+08:00") -> dict:
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-auth-rbac-smoke",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "211_runtime_smoke",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "runtime_image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
        },
        "evidence_ref": {
            "result": "ok:true",
            "runtime_checks": {
                "unauthenticated_auth_me": {"route": "/api/auth/me", "status": 401},
                "authenticated_auth_me": {
                    "route": "/api/ai/auth/me",
                    "status": 200,
                    "tenant_matches_requested": True,
                    "user_matches_requested": True,
                    "forbidden_projection_terms_present": False,
                },
                "invalid_gateway_secret_auth_me": {"route": "/api/ai/auth/me", "status": 403},
                "ordinary_admin_runtime": {"status": 403},
                "admin_runtime": {
                    "status": 200,
                    "required_sections_present": True,
                    "tenant_matches_requested": True,
                    "forbidden_projection_terms_present": False,
                },
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _minimal_governance_payload(
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:02:00+08:00",
) -> dict:
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-governance-runtime-smoke",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "211_runtime_smoke",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
            "repo_local_env_present": False,
        },
        "evidence_ref": {
            "verifier": "tools/verify_governance_runtime_smoke.py",
            "result": "ok:true",
            "schema_version": "ai-platform.governance-runtime-smoke.v1",
            "runtime_checks": {
                "ordinary_admin_runtime": {
                    "status": 403,
                    "expected_status": 403,
                },
                "admin_runtime_governance": {
                    "status": 200,
                    "expected_status": 200,
                    "tenant_matches_requested": True,
                    "governance_schema_version": "ai-platform.governance-readiness.v1",
                    "governance_status_allowed": True,
                    "required_domains_present": True,
                    "forbidden_projection_terms_present": False,
                    "missing_domains": [],
                    "tool_permission": {
                        "taxonomy_present": True,
                        "bulk_review_present": True,
                    },
                    "skill_governance": {
                        "release_readiness_present": True,
                        "dashboard_present": True,
                    },
                    "memory_governance": {
                        "long_term_fail_closed_present": True,
                        "context_provenance_present": True,
                        "office_context_readiness_present": True,
                    },
                },
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _valid_release_evidence_runtime_acceptance() -> dict:
    return {
        "schema_version": "ai-platform.release-evidence-runtime-acceptance.v1",
        "ok": True,
        "status": "accepted_for_operator_review",
        "source": {
            "commit_sha": CURRENT_SOURCE_SHA,
            "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
            "image": "ai-platform:a3f1d73-foundation-alpha-poc",
            "evidence_root": "C:\\workspace\\ai-platform\\docs\\release-evidence",
        },
        "checks": {
            "runtime_export_acceptance": {
                "status": "ready_for_operator_review",
                "export_policy": "safe_reviewed_index_only_not_runtime_export",
                "safe_entry_count": 1,
                "blocked_entry_count": 0,
                "excluded_entry_count": 0,
                "safe_entry_fields_only": True,
                "does_not_export_raw_runtime_payloads": True,
            },
            "retention_runtime_acceptance": {
                "status": "accepted_review_first_policy",
                "schema_version": "ai-platform.release-evidence-retention-policy.v1",
                "policy_status": "contract_only_not_runtime_enforced",
                "default_retention_days": 180,
                "minimum_retention_days": 30,
                "requires_review_before_delete": True,
                "delete_only_reviewed_redacted_entries": True,
                "forbidden_delete_targets_present": True,
            },
        },
        "open_gaps": [],
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }


def _minimal_release_evidence_runtime_acceptance_payload(
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:03:00+08:00",
    acceptance: dict | None = None,
) -> dict:
    acceptance = acceptance or _valid_release_evidence_runtime_acceptance()
    acceptance["source"]["commit_sha"] = commit_sha
    acceptance["source"]["runtime_subject_commit_sha"] = commit_sha
    acceptance["source"]["image"] = image
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-release-evidence-runtime-acceptance",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "211_runtime_smoke",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
            "repo_local_env_present": False,
        },
        "evidence_ref": {
            "verifier": "tools/verify_release_evidence_runtime_acceptance.py",
            "result": "ok:true",
            "schema_version": "ai-platform.release-evidence-runtime-acceptance.v1",
            "runtime_checks": {
                "release_evidence_runtime_acceptance": acceptance,
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _valid_alert_trace_export_runtime_acceptance() -> dict:
    return {
        "schema_version": "ai-platform.alert-trace-export-runtime-acceptance.v1",
        "ok": True,
        "status": "accepted_for_operator_review",
        "redaction_scan_status": "passed",
        "source": {
            "commit_sha": CURRENT_SOURCE_SHA,
            "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
            "image": "ai-platform:a3f1d73-foundation-alpha-poc",
            "tenant_id": "default",
            "gateway_secret_supplied": True,
        },
        "checks": {
            "ordinary_admin_runtime": {
                "route": "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
                "status": 403,
                "expected_status": 403,
            },
            "admin_runtime_alerts_and_exports": {
                "route": "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
                "status": 200,
                "expected_status": 200,
                "tenant_matches_requested": True,
                "observability_schema_version": "ai-platform.observability-readiness.v1",
                "alerts_domain_status": "partial_blocked",
                "alert_rules_status": "partial_blocked",
                "alert_rule_count": 7,
                "alert_delivery_policy_status": "contract_only_not_enabled",
                "alert_delivery_not_enabled": True,
                "slo_threshold_runtime_calibration_gap_present": True,
                "trace_export_status": "partial_blocked",
                "trace_export_contract_schema_version": "ai-platform.trace-audit-export-contract.v1",
                "trace_export_not_raw_runtime_payloads": True,
                "trace_export_sources_public_only": True,
                "forbidden_projection_terms_present": False,
            },
        },
        "open_gaps": [],
        "does_not_enable_alert_delivery": True,
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }


def _minimal_alert_trace_export_runtime_acceptance_payload(
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:04:00+08:00",
    acceptance: dict | None = None,
) -> dict:
    acceptance = acceptance or _valid_alert_trace_export_runtime_acceptance()
    acceptance["source"]["commit_sha"] = commit_sha
    acceptance["source"]["runtime_subject_commit_sha"] = commit_sha
    acceptance["source"]["image"] = image
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-alert-trace-export-runtime-acceptance",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "alert_trace_export_runtime_acceptance",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
            "repo_local_env_present": False,
        },
        "evidence_ref": {
            "verifier": "tools/verify_alert_trace_export_runtime_acceptance.py",
            "result": "ok:true",
            "schema_version": "ai-platform.alert-trace-export-runtime-acceptance.v1",
            "runtime_checks": {
                "alert_trace_export_runtime_acceptance": acceptance,
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _write_governance_evidence(
    base_root,
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:02:00+08:00",
):
    commit_root = base_root / commit_sha
    commit_root.mkdir(parents=True, exist_ok=True)
    path = commit_root / f"{commit_sha[:7]}-governance-runtime-smoke.json"
    path.write_text(
        json.dumps(_minimal_governance_payload(commit_sha, image=image, captured_at=captured_at)),
        encoding="utf-8",
    )
    return path


def _write_release_evidence_runtime_acceptance(
    base_root,
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:03:00+08:00",
    acceptance: dict | None = None,
):
    commit_root = base_root / commit_sha
    commit_root.mkdir(parents=True, exist_ok=True)
    path = commit_root / "release-evidence-runtime-acceptance.json"
    path.write_text(
        json.dumps(
            _minimal_release_evidence_runtime_acceptance_payload(
                commit_sha,
                image=image,
                captured_at=captured_at,
                acceptance=acceptance,
            )
        ),
        encoding="utf-8",
    )
    return path


def _write_alert_trace_export_runtime_acceptance(
    base_root,
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:04:00+08:00",
    acceptance: dict | None = None,
):
    commit_root = base_root / commit_sha
    commit_root.mkdir(parents=True, exist_ok=True)
    path = commit_root / "alert-trace-export-runtime-acceptance.json"
    path.write_text(
        json.dumps(
            _minimal_alert_trace_export_runtime_acceptance_payload(
                commit_sha,
                image=image,
                captured_at=captured_at,
                acceptance=acceptance,
            )
        ),
        encoding="utf-8",
    )
    return path


def _minimal_frontend_packaged_runtime_smoke_payload(
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:04:00+08:00",
    runtime_host: str = "211",
) -> dict:
    smoke = {
        "commit_sha": commit_sha,
        "runtime_host": runtime_host,
        "image_tag": f"ai-platform-frontend:{commit_sha[:7]}-smoke",
        "docker_build": {"exit_code": 0, "log_tail": "wrote frontend/web/dist/ai-platform-build-provenance.json"},
        "image_inspect": {"revision": commit_sha},
        "build_provenance": {
            "schema_version": "ai-platform.frontend-build-provenance.v1",
            "git": {"commit": commit_sha, "dirty": False},
            "source_hashes": {
                "package_json_sha256": "a" * 64,
                "pnpm_lock_sha256": "b" * 64,
            },
        },
        "runtime_smoke": {
            "network": "ai-platform-phaseb_default",
            "healthz": {"status_code": 200, "body": "ok"},
            "index": {"status_code": 200},
            "api_health": {"status_code": 200, "body": {"status": "ok"}},
            "build_provenance_endpoint": {"status_code": 200},
        },
        "leak_scan": {"status": "passed", "forbidden_markers": []},
        "cleanup": {"container_removed": True},
    }
    return {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": f"{commit_sha[:7]}-frontend-packaged-runtime-smoke",
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": commit_sha,
        "gate": "Foundation Alpha POC",
        "artifact_kind": "frontend_packaged_runtime_smoke",
        "captured_at": captured_at,
        "source_ref": {
            "branch": "main",
            "runtime_commit": commit_sha,
            "runtime_source_marker": commit_sha,
            "image": image,
            "image_id": f"sha256:{commit_sha[:12]}",
            "image_labels": {
                "ai-platform.source-revision": commit_sha,
                "org.opencontainers.image.revision": commit_sha,
            },
            "repo_local_env_present": False,
        },
        "evidence_ref": {
            "verifier": "tools/frontend_packaged_runtime_smoke.py",
            "result": "ok:true",
            "schema_version": "ai-platform.frontend-packaged-runtime-smoke.v1",
            "runtime_checks": {
                "frontend_packaged_runtime_smoke": smoke,
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }


def _write_frontend_packaged_runtime_smoke(
    base_root,
    commit_sha: str,
    *,
    image: str,
    captured_at: str = "2026-06-11T10:04:00+08:00",
    runtime_host: str = "211",
):
    commit_root = base_root / commit_sha
    commit_root.mkdir(parents=True, exist_ok=True)
    path = commit_root / f"{commit_sha[:7]}-frontend-packaged-runtime-smoke.json"
    path.write_text(
        json.dumps(
            _minimal_frontend_packaged_runtime_smoke_payload(
                commit_sha,
                image=image,
                captured_at=captured_at,
                runtime_host=runtime_host,
            )
        ),
        encoding="utf-8",
    )
    return path


def _write_release_evidence_pair(
    base_root,
    commit_sha: str,
    *,
    image: str,
    smoke_captured_at: str = "2026-06-11T10:00:00+08:00",
    auth_captured_at: str = "2026-06-11T10:01:00+08:00",
):
    commit_root = base_root / commit_sha
    commit_root.mkdir(parents=True, exist_ok=True)
    smoke_path = commit_root / f"{commit_sha[:7]}-smoke.json"
    auth_path = commit_root / f"{commit_sha[:7]}-auth-rbac-smoke.json"
    smoke_path.write_text(
        json.dumps(_minimal_smoke_payload(commit_sha, image=image, captured_at=smoke_captured_at)),
        encoding="utf-8",
    )
    auth_path.write_text(
        json.dumps(_minimal_auth_payload(commit_sha, image=image, captured_at=auth_captured_at)),
        encoding="utf-8",
    )
    return smoke_path, auth_path


def test_foundation_alpha_readiness_accepts_release_evidence_runtime_acceptance_for_same_runtime_subject(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_governance_evidence(evidence_root, CURRENT_SOURCE_SHA, image=image)
    runtime_acceptance_path = _write_release_evidence_runtime_acceptance(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert "release_evidence_runtime_acceptance" in readiness["evidence_entries"]
    assert readiness["evidence_entries"][
        "release_evidence_runtime_acceptance"
    ] == foundation_alpha_readiness._path_for_output(runtime_acceptance_path)
    g9 = readiness["domains"]["g9_admin_runtime_observability"]
    assert "g9_runtime_export_and_retention_acceptance" not in g9["open_followups"]
    assert "alert_delivery_and_trace_export_211_acceptance" in g9["open_followups"]
    assert "g9_runtime_export_and_retention_acceptance" not in readiness["decision"]["stage_acceptance_blockers"]
    assert "alert_delivery_and_trace_export_211_acceptance" in readiness["decision"]["stage_acceptance_blockers"]
    assert "g9_runtime_export_and_retention_acceptance" not in readiness["open_followups"]
    assert "g9_runtime_export_and_retention_acceptance" not in readiness["operator_context"][
        "next_recommended_slices"
    ]
    assert (
        g9["evidence"]["release_evidence_runtime_acceptance"]["status"]
        == "verified_release_evidence_runtime_acceptance"
    )
    assert g9["evidence"]["release_evidence_runtime_acceptance"]["verified"] is True
    assert g9["evidence"]["release_evidence_runtime_acceptance"]["safe_entry_count"] == 1

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "source_ref" not in json.dumps(
        g9["evidence"]["release_evidence_runtime_acceptance"],
        ensure_ascii=False,
    ).lower()
    assert "raw_storage_key" not in serialized


def test_foundation_alpha_readiness_keeps_g9_runtime_blocker_without_valid_release_acceptance(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    invalid_acceptance = _valid_release_evidence_runtime_acceptance()
    invalid_acceptance["ok"] = False
    invalid_acceptance["open_gaps"] = ["release_evidence_runtime_export_acceptance"]
    _write_release_evidence_runtime_acceptance(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        acceptance=invalid_acceptance,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert "release_evidence_runtime_acceptance" not in readiness["evidence_entries"]
    g9 = readiness["domains"]["g9_admin_runtime_observability"]
    assert "g9_runtime_export_and_retention_acceptance" in g9["open_followups"]
    assert "g9_runtime_export_and_retention_acceptance" in readiness["decision"]["stage_acceptance_blockers"]
    assert "g9_runtime_export_and_retention_acceptance" in readiness["open_followups"]
    assert (
        g9["evidence"]["release_evidence_runtime_acceptance"]["status"]
        == "missing_release_evidence_runtime_acceptance"
    )
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized


def test_foundation_alpha_readiness_accepts_alert_trace_export_runtime_acceptance_for_same_runtime_subject(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    alert_trace_path = _write_alert_trace_export_runtime_acceptance(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert "alert_trace_export_runtime_acceptance" in readiness["evidence_entries"]
    assert readiness["evidence_entries"][
        "alert_trace_export_runtime_acceptance"
    ] == foundation_alpha_readiness._path_for_output(alert_trace_path)
    g9 = readiness["domains"]["g9_admin_runtime_observability"]
    assert "alert_delivery_and_trace_export_211_acceptance" not in g9["open_followups"]
    assert "alert_delivery_and_trace_export_211_acceptance" not in readiness["decision"]["stage_acceptance_blockers"]
    assert "alert_delivery_and_trace_export_211_acceptance" not in readiness["open_followups"]
    assert "alert_delivery_and_trace_export_211_acceptance" not in readiness["operator_context"][
        "next_recommended_slices"
    ]
    assert (
        g9["evidence"]["alert_trace_export_runtime_acceptance"]["status"]
        == "verified_alert_trace_export_runtime_acceptance"
    )
    assert g9["evidence"]["alert_trace_export_runtime_acceptance"]["verified"] is True
    assert g9["evidence"]["alert_trace_export_runtime_acceptance"]["alert_delivery_not_enabled"] is True
    assert g9["evidence"]["alert_trace_export_runtime_acceptance"]["trace_export_sources_public_only"] is True

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "source_ref" not in json.dumps(
        g9["evidence"]["alert_trace_export_runtime_acceptance"],
        ensure_ascii=False,
    ).lower()
    assert "raw_storage_key" not in serialized


def test_foundation_alpha_readiness_keeps_alert_trace_blocker_without_valid_runtime_acceptance(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    invalid_acceptance = _valid_alert_trace_export_runtime_acceptance()
    invalid_acceptance["ok"] = False
    invalid_acceptance["checks"]["admin_runtime_alerts_and_exports"][
        "forbidden_projection_terms_present"
    ] = True
    invalid_acceptance["open_gaps"] = ["trace_audit_export_211_acceptance"]
    _write_alert_trace_export_runtime_acceptance(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        acceptance=invalid_acceptance,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert "alert_trace_export_runtime_acceptance" not in readiness["evidence_entries"]
    g9 = readiness["domains"]["g9_admin_runtime_observability"]
    assert "alert_delivery_and_trace_export_211_acceptance" in g9["open_followups"]
    assert "alert_delivery_and_trace_export_211_acceptance" in readiness["decision"]["stage_acceptance_blockers"]
    assert (
        g9["evidence"]["alert_trace_export_runtime_acceptance"]["status"]
        == "missing_alert_trace_export_runtime_acceptance"
    )
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized


def test_foundation_alpha_readiness_accepts_governance_runtime_smoke_for_same_runtime_subject(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    governance_path = _write_governance_evidence(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert (
        readiness["domains"]["g6_poc_governance"]["evidence"]["governance_runtime_smoke"]["status"]
        == "verified_admin_runtime_governance_projection"
    )
    assert "governance_runtime_smoke" in readiness["evidence_entries"]
    assert readiness["evidence_entries"]["governance_runtime_smoke"] == foundation_alpha_readiness._path_for_output(
        governance_path
    )
    assert (
        "runtime_admin_dashboard_acceptance_for_governance"
        not in readiness["domains"]["g6_poc_governance"]["open_followups"]
    )
    assert "runtime_admin_dashboard_acceptance_for_governance" not in readiness["decision"]["stage_acceptance_blockers"]
    assert "signed_skill_package_or_sbom_review_evidence" in readiness["decision"]["stage_acceptance_blockers"]
    assert "g6_runtime_admin_dashboard_acceptance_for_governance" not in readiness["operator_context"][
        "next_recommended_slices"
    ]


def test_foundation_alpha_readiness_removes_signed_skill_followup_when_release_evidence_gap_is_closed(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    _write_governance_evidence(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_governance_summary",
        lambda _settings: {
            "governance_readiness_status": "partial_blocked",
            "ordinary_user_policy": "fail_closed_until_projection_mapping_and_acceptance_pass",
            "open_gap_count": 1,
            "open_gaps": ["admin_skill_release_dashboard_211_acceptance"],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    g6_followups = readiness["domains"]["g6_poc_governance"]["open_followups"]
    assert g6_followups == []
    assert "signed_skill_package_or_sbom_review_evidence" not in g6_followups
    assert "signed_skill_package_or_sbom_review_evidence" not in readiness["decision"]["stage_acceptance_blockers"]


def test_foundation_alpha_readiness_keeps_governance_runtime_blocker_without_valid_smoke(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert (
        readiness["domains"]["g6_poc_governance"]["evidence"]["governance_runtime_smoke"]["status"]
        == "missing_governance_runtime_smoke"
    )
    assert "governance_runtime_smoke" not in readiness["evidence_entries"]
    assert "runtime_admin_dashboard_acceptance_for_governance" in readiness["domains"]["g6_poc_governance"][
        "open_followups"
    ]
    assert "runtime_admin_dashboard_acceptance_for_governance" in readiness["decision"]["stage_acceptance_blockers"]
    assert "g6_runtime_admin_dashboard_acceptance_for_governance" in readiness["operator_context"][
        "next_recommended_slices"
    ]


def test_foundation_alpha_readiness_classifies_source_metadata_paths_as_runtime_neutral():
    assert foundation_alpha_readiness._is_runtime_affecting_path(".gitignore") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("AGENTS.md") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("app/foundation_alpha_readiness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("app/capacity_bounded_load_harness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("docs/agent-rules/ai-platform-guardrails.md") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("docs/agent-rules/github-issue-pr-workflow.md") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("docs/release-evidence/README.md") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tests/test_foundation_alpha_readiness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tests/test_source_authority_docs.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/frontend_release_traceability.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/verify_auth_rbac_smoke.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/verify_governance_runtime_smoke.py") is False
    assert (
        foundation_alpha_readiness._is_runtime_affecting_path(
            "assets/ai-platform-architecture-illustrations/01-controlled-execution-cabin.svg"
        )
        is False
    )
    assert foundation_alpha_readiness._is_runtime_affecting_path("ai-platform-cdc09ba.tar") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("app/routes/runs.py") is True


def test_foundation_alpha_readiness_default_runtime_subject_tracks_active_211_evidence():
    assert foundation_alpha_readiness.RUNTIME_SUBJECT_COMMIT_SHA == ACTIVE_RUNTIME_SUBJECT_SHA


def test_foundation_alpha_readiness_selects_current_source_release_evidence_pair(monkeypatch, tmp_path):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "211_verified_followups_open"
    assert readiness["source_tree_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["runtime_subject_commit_sha"] == CURRENT_SOURCE_SHA
    assert "runtime_image" not in readiness
    assert readiness["verified_runtime_subject"] == {
        "commit_sha": CURRENT_SOURCE_SHA,
        "image": "ai-platform:a3f1d73-foundation-alpha-poc",
        "image_id": f"sha256:{CURRENT_SOURCE_SHA[:12]}",
        "evidence_scope": "current_source_tree",
    }
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": True,
        "runtime_relevant_source_matches": True,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "runtime_current_for_source_tree",
    }
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is True
    assert readiness["current_source_verified_by_running_runtime"] is True
    assert readiness["controlled_poc_loop_verified_for_current_source"] is True
    assert readiness["runtime_relevant_source_verified_by_running_runtime"] is True
    assert readiness["foundation_alpha_stage_complete"] is False
    assert CURRENT_SOURCE_SHA in readiness["evidence_entries"]["poc_smoke"]
    assert CURRENT_SOURCE_SHA in readiness["evidence_entries"]["auth_rbac_smoke"]
    assert RUNTIME_SUBJECT_SHA not in readiness["evidence_entries"]["poc_smoke"]


def test_foundation_alpha_readiness_does_not_overclaim_dirty_source_tree(monkeypatch, tmp_path):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: True, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_dirty_paths",
        lambda: ["app/routes/runs.py"],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "source_tree_runtime_affecting_uncommitted_changes_pending_followups_open"
    assert readiness["source_tree_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["source_tree_dirty"] is True
    assert readiness["runtime_subject_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": True,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": False,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": ["app/routes/runs.py"],
        "status": "source_tree_runtime_affecting_uncommitted_changes_pending",
    }
    assert (
        readiness["domains"]["g0_g1_source_authority_security"]["status"]
        == "source_tree_runtime_affecting_uncommitted_changes_pending"
    )
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is True


def test_foundation_alpha_readiness_accepts_runtime_neutral_uncommitted_records(monkeypatch, tmp_path):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: True, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_dirty_paths",
        lambda: [],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "runtime_current_for_runtime_relevant_source_followups_open"
    assert readiness["source_tree_dirty"] is True
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": True,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": True,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "runtime_current_for_runtime_relevant_source",
    }
    assert readiness["verified_runtime_subject"]["evidence_scope"] == "current_runtime_relevant_source"
    assert readiness["decision"]["runtime_relevant_source_verified_by_running_runtime"] is True
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["current_source_exact_runtime_commit_match"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is False


def test_foundation_alpha_readiness_fails_closed_when_dirty_state_is_unknown(monkeypatch, tmp_path):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: None, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_dirty_paths",
        lambda: [],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "source_synced_runtime_pending_followups_open"
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": None,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": False,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "source_synced_runtime_pending",
    }
    assert readiness["verified_runtime_subject"]["evidence_scope"] == "reviewed_historical_runtime_evidence"
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is True


def test_foundation_alpha_readiness_uses_valid_source_snapshot_marker_when_git_is_unavailable(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T10:00:00+08:00",
        auth_captured_at="2026-06-11T10:01:00+08:00",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    marker = {
        "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "note": "source archive contains only docs/tests/readiness records after runtime subject",
    }
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_read_source_revision_marker",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )

    def git_unavailable(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(foundation_alpha_readiness.subprocess, "run", git_unavailable)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "runtime_current_for_runtime_relevant_source_followups_open"
    assert readiness["source_tree_commit_sha"] == NEWER_SOURCE_SHA
    assert readiness["source_tree_dirty"] is False
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": True,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "runtime_current_for_runtime_relevant_source",
    }
    assert readiness["verified_runtime_subject"]["evidence_scope"] == "current_runtime_relevant_source"
    assert readiness["decision"]["runtime_relevant_source_verified_by_running_runtime"] is True
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False


def test_foundation_alpha_readiness_distinguishes_runtime_relevant_source_from_stage_closure(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T10:00:00+08:00",
        auth_captured_at="2026-06-11T10:01:00+08:00",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    marker = {
        "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
    }
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_read_source_revision_marker",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )

    def git_unavailable(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(foundation_alpha_readiness.subprocess, "run", git_unavailable)

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["decision"]["runtime_relevant_source_verified_by_running_runtime"] is True
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["foundation_alpha_stage_complete"] is False
    assert readiness["decision"]["foundation_alpha_stage_status"] == "core_poc_loop_verified_followups_open"
    assert "runtime_admin_dashboard_acceptance_for_governance" in readiness["decision"]["stage_acceptance_blockers"]
    assert readiness["operator_context"]["poc_loop_status"] == "core_loop_verified_for_runtime_relevant_source"
    assert readiness["operator_context"]["stage_acceptance_status"] == "core_poc_loop_verified_followups_open"


def test_foundation_alpha_readiness_prefers_git_head_over_stale_source_revision_marker(monkeypatch, tmp_path):
    marker_path = tmp_path / ".ai-platform-source-revision"
    marker_path.write_text(f"{RUNTIME_SUBJECT_SHA}\n", encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_REVISION_MARKER", marker_path, raising=False)

    def git_head(command, **_kwargs):
        assert command == ["git", "rev-parse", "HEAD"]
        return subprocess.CompletedProcess(command, 0, stdout=f"{ACTIVE_RUNTIME_SUBJECT_SHA}\n", stderr="")

    monkeypatch.setattr(foundation_alpha_readiness.subprocess, "run", git_head)

    assert foundation_alpha_readiness._resolve_source_tree_revision() == ACTIVE_RUNTIME_SUBJECT_SHA


def test_foundation_alpha_readiness_rejects_source_snapshot_marker_for_wrong_commit(
    monkeypatch,
    tmp_path,
):
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
                "source_tree_commit_sha": "wrong",
                "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
                "source_tree_dirty": False,
                "runtime_affecting_changes_since_runtime_subject": [],
                "runtime_affecting_dirty_paths": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_read_source_revision_marker",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )

    assert foundation_alpha_readiness._source_snapshot_marker_for_source_tree(NEWER_SOURCE_SHA) is None


def test_foundation_alpha_readiness_prefers_source_snapshot_runtime_subject_over_latest_history(monkeypatch, tmp_path):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T10:00:00+08:00",
        auth_captured_at="2026-06-11T10:01:00+08:00",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    marker = {
        "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "runtime_subject_commit_sha": RUNTIME_SUBJECT_SHA,
        "source_tree_dirty": False,
        "runtime_affecting_changes_since_runtime_subject": ["app/routes/runs.py"],
        "runtime_affecting_dirty_paths": [],
    }
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_changes_since",
        lambda _: ["app/routes/runs.py"],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "source_synced_runtime_pending_followups_open"
    assert readiness["source_tree_commit_sha"] == NEWER_SOURCE_SHA
    assert readiness["runtime_subject_commit_sha"] == RUNTIME_SUBJECT_SHA
    assert "runtime_image" not in readiness
    assert readiness["verified_runtime_subject"] == {
        "commit_sha": RUNTIME_SUBJECT_SHA,
        "image": "ai-platform:8c0cffc-foundation-alpha-poc",
        "image_id": f"sha256:{RUNTIME_SUBJECT_SHA[:12]}",
        "evidence_scope": "reviewed_historical_runtime_evidence",
    }
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": RUNTIME_SUBJECT_SHA,
        "runtime_source_marker": RUNTIME_SUBJECT_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": False,
        "runtime_affecting_changes_since_runtime_subject": ["app/routes/runs.py"],
        "runtime_affecting_dirty_paths": [],
        "status": "source_synced_runtime_pending",
    }
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is True
    assert readiness["decision"]["controlled_poc_loop_verified_for_current_source"] is False
    assert readiness["decision"]["reviewed_poc_loop_evidence_available"] is True
    assert RUNTIME_SUBJECT_SHA in readiness["evidence_entries"]["poc_smoke"]
    assert CURRENT_SOURCE_SHA not in readiness["evidence_entries"]["poc_smoke"]


def test_foundation_alpha_readiness_falls_back_to_latest_when_configured_runtime_subject_missing(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_changes_since",
        lambda _: ["app/routes/runs.py"],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "source_synced_runtime_pending_followups_open"
    assert readiness["source_tree_commit_sha"] == NEWER_SOURCE_SHA
    assert readiness["runtime_subject_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["verified_runtime_subject"] == {
        "commit_sha": CURRENT_SOURCE_SHA,
        "image": "ai-platform:a3f1d73-foundation-alpha-poc",
        "image_id": f"sha256:{CURRENT_SOURCE_SHA[:12]}",
        "evidence_scope": "reviewed_historical_runtime_evidence",
    }
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is True
    assert CURRENT_SOURCE_SHA in readiness["evidence_entries"]["poc_smoke"]
    assert RUNTIME_SUBJECT_SHA not in readiness["evidence_entries"]["poc_smoke"]


def test_foundation_alpha_readiness_accepts_evidence_only_record_commit_without_runtime_rollout(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T10:00:00+08:00",
        auth_captured_at="2026-06-11T10:01:00+08:00",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    marker = {
        "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
    }
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_changes_since",
        lambda _: [],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "runtime_current_for_runtime_relevant_source_followups_open"
    assert readiness["source_tree_commit_sha"] == NEWER_SOURCE_SHA
    assert readiness["runtime_subject_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": True,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "runtime_current_for_runtime_relevant_source",
    }
    assert (
        readiness["domains"]["g0_g1_source_authority_security"]["status"]
        == "runtime_current_for_runtime_relevant_source"
    )
    assert readiness["decision"]["runtime_relevant_source_verified_by_running_runtime"] is True
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["current_source_exact_runtime_commit_match"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is False


def test_foundation_alpha_readiness_aggregates_current_poc_evidence_without_overclaiming(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_changes_since",
        lambda _: [],
        raising=False,
    )
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "dist_status": "built",
            "dist_build_provenance_status": "verified",
            "dist_build_verified_same_commit": True,
            "ci_verify_includes_projection_audit": True,
            "workflow_status": "present",
            "packaged_frontend_image_status": "configured",
            "blockers": [],
            "open_gap_count": 0,
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["schema_version"] == "ai-platform.foundation-alpha-poc-readiness.v1"
    assert readiness["stage"] == "Foundation Alpha POC"
    assert readiness["status"] == "211_verified_followups_open"
    assert readiness["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert readiness["source_tree_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": ACTIVE_RUNTIME_SUBJECT_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": ACTIVE_RUNTIME_SUBJECT_SHA,
        "runtime_source_marker": ACTIVE_RUNTIME_SUBJECT_SHA,
        "runtime_matches_source_tree": True,
        "runtime_relevant_source_matches": True,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "status": "runtime_current_for_source_tree",
    }
    assert readiness["decision"] == {
        "reviewed_poc_loop_evidence_available": True,
        "controlled_poc_loop_verified_for_current_source": True,
        "controlled_core_poc_loop_verified_for_runtime_relevant_source": True,
        "runtime_relevant_source_verified_by_running_runtime": True,
        "current_source_verified_by_running_runtime": True,
        "current_source_exact_runtime_commit_match": True,
        "runtime_rollout_required_for_current_source": False,
        "foundation_alpha_stage_complete": False,
        "foundation_alpha_stage_status": "core_poc_loop_verified_followups_open",
        "stage_acceptance_blockers": [
            "signed_skill_package_or_sbom_review_evidence",
            "ordinary_user_acceptance_for_quarantined_legacy_routes",
            "g9_admin_runtime_observability_partial_followups_open",
            "packaged_frontend_image_release_acceptance",
        ],
        "can_enter_next_stage_without_restrictions": False,
        "production_claim_allowed": False,
        "ordinary_user_multi_agent_allowed": False,
        "docker_sandbox_hardened_claim_allowed": False,
        "capacity_default_increase_allowed": False,
    }
    assert readiness["operator_context"] == {
        "poc_scope": "foundation_alpha_controlled_internal_poc",
        "poc_loop_status": "core_loop_verified_for_current_source_tree",
        "current_runtime_relation": "runtime_current_for_source_tree",
        "stage_acceptance_status": "core_poc_loop_verified_followups_open",
        "stage_gate": "foundation_alpha_poc_not_production",
        "verified_poc_capabilities": [
            "source_authority_security_baseline",
            "control_plane_public_admin_projection_contracts",
            "queue_worker_document_task_artifact_loop",
            "frontend_public_projection_poc",
        ],
        "blocked_expansions": [
            "production_concurrency_increase",
            "docker_sandbox_hardening_claim",
            "ordinary_user_multi_agent_exposure",
            "department_rollout",
        ],
        "next_recommended_slices": [
            "packaged_frontend_image_release_acceptance",
            "broader_auth_session_rbac_tenant_redaction_regression",
        ],
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
    assert readiness["domains"]["g0_g1_source_authority_security"]["evidence"]["auth_rbac"][
        "unauthenticated_auth_me_status"
    ] == 401
    assert readiness["domains"]["g0_g1_source_authority_security"]["evidence"]["auth_rbac"][
        "ordinary_admin_runtime_status"
    ] == 403
    assert readiness["domains"]["g0_g1_source_authority_security"]["evidence"]["auth_rbac"][
        "admin_runtime_status"
    ] == 200
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
    assert (
        readiness["domains"]["g5_run_lifecycle_worker_runtime"]["status"]
        == "poc_verified_capacity_baseline_keep_defaults_locked"
    )
    assert readiness["domains"]["g5_run_lifecycle_worker_runtime"]["open_followups"] == []
    assert (
        readiness["domains"]["g5_run_lifecycle_worker_runtime"]["evidence"]["capacity_default_policy"]
        == "do_not_raise_without_separate_recorded_profile_evidence"
    )
    assert readiness["domains"]["frontend_poc"]["evidence"]["same_origin_api_health"]["payload_status"] == "ok"
    assert readiness["domains"]["frontend_poc"]["evidence"]["frontend_http_status"] == 200
    assert readiness["domains"]["frontend_poc"]["evidence"]["forbidden_reference_count"] == 0
    assert (
        readiness["domains"]["g2_g4_control_plane_contracts"]["evidence"]["artifact_preview_isolation"][
            "checked_artifacts"
        ]
        == 1
    )
    assert readiness["domains"]["g6_poc_governance"]["evidence"]["governance_readiness_status"] == "partial_blocked"
    assert readiness["domains"]["g6_poc_governance"]["evidence"]["context_snapshot_public_projection"] == {
        "status": "verified_public_context_projection",
        "referenced_material_counts": {
            "message_count": 1,
            "file_count": 1,
            "artifact_count": 0,
            "memory_record_count": 0,
        },
        "raw_material_id_fields_present": False,
        "forbidden_projection_leak_count": 0,
        "summary_source": "chat_stream",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "default",
        "long_term_memory_read": False,
        "execution_tier": "sdk_only_writing",
        "context_pack_generated_at_present": True,
        "missing_public_summary_fields": [],
    }
    assert readiness["domains"]["g9_admin_runtime_observability"]["evidence"]["observability_readiness_status"] == "partial_blocked"

    assert "#21_recorded_capacity_evidence" not in readiness["open_followups"]
    assert "g7_docker_sandbox_hardening" in readiness["open_followups"]
    assert "g8_ordinary_user_multi_agent_exposure" in readiness["open_followups"]
    assert "g9_runtime_export_and_retention_acceptance" not in readiness["open_followups"]
    assert "packaged_frontend_image_release_acceptance" in readiness["open_followups"]
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" in readiness["open_followups"]

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


def test_frontend_release_traceability_summary_is_secret_safe_and_operator_sized():
    trace = {
        "schema_version": "ai-platform.frontend-release-traceability.v1",
        "frontend_path": "frontend/web",
        "package_name": "lamb-agent-frontend",
        "package_version": "2.3.0",
        "package_manager": "pnpm@10.32.1",
        "git": {"commit": ACTIVE_RUNTIME_SUBJECT_SHA, "dirty": False},
        "source_hashes": {"package_json_sha256": "package-hash", "pnpm_lock_sha256": "lock-hash"},
        "scripts": {
            "lint": "eslint .",
            "build": "tsc -b && vite build && node scripts/write-build-provenance.mjs",
            "projection:audit": "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json",
            "ci:verify": (
                "node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json "
                "&& eslint . && tsc -b && vite build && node scripts/write-build-provenance.mjs"
            ),
        },
        "workflow": {
            "path": ".github/workflows/ai-platform-frontend.yml",
            "status": "present",
            "sha256": "workflow-hash",
            "blockers": [],
        },
        "dist": {
            "status": "built",
            "file_count": 283,
            "total_bytes": 123456,
            "manifest_sha256": "dist-manifest-hash",
            "files": [{"path": "assets/index.js", "sha256": "asset-hash"}],
            "build_provenance": {
                "path": "dist/ai-platform-build-provenance.json",
                "status": "verified",
                "verified_same_commit": True,
                "build_commit": ACTIVE_RUNTIME_SUBJECT_SHA,
                "blockers": [],
            },
            "blockers": [],
        },
        "packaged_frontend_image": {
            "status": "configured",
            "dockerfile": {"path": "frontend/web/Dockerfile", "sha256": "dockerfile-hash"},
            "compose_overlay": {
                "path": "deploy/ai-platform/docker-compose.frontend.yml",
                "sha256": "compose-hash",
            },
            "contract_scan": {"status": "pass", "forbidden_findings": []},
            "blockers": [],
        },
        "release_policy": "tie_frontend_api_worker_artifacts_to_same_git_commit",
    }

    summary = foundation_alpha_readiness._frontend_release_traceability_summary(trace)

    assert summary == {
        "status": "verified_packaged_release_followup_open",
        "schema_version": "ai-platform.frontend-release-traceability.v1",
        "frontend_path": "frontend/web",
        "package_name": "lamb-agent-frontend",
        "package_version": "2.3.0",
        "package_manager": "pnpm@10.32.1",
        "git_commit": ACTIVE_RUNTIME_SUBJECT_SHA,
        "git_dirty": False,
        "ci_verify_script_present": True,
        "ci_verify_includes_projection_audit": True,
        "projection_audit_script_present": True,
        "dist_status": "built",
        "dist_file_count": 283,
        "dist_build_provenance_status": "verified",
        "dist_build_commit": ACTIVE_RUNTIME_SUBJECT_SHA,
        "dist_build_verified_same_commit": True,
        "workflow_status": "present",
        "workflow_path": ".github/workflows/ai-platform-frontend.yml",
        "packaged_frontend_image_status": "configured",
        "packaged_contract_scan_status": "pass",
        "blockers": [],
        "open_gap_count": 0,
    }
    serialized = json.dumps(summary, ensure_ascii=False).lower()
    assert "assets/index.js" not in serialized
    assert "asset-hash" not in serialized
    assert "package-hash" not in serialized
    assert "dockerfile-hash" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "c:\\users" not in serialized


def test_foundation_alpha_readiness_embeds_frontend_release_traceability_summary(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    frontend_traceability_summary = {
        "status": "verified_packaged_release_followup_open",
        "frontend_path": "frontend/web",
        "package_manager": "pnpm@10.32.1",
        "git_commit": ACTIVE_RUNTIME_SUBJECT_SHA,
        "git_dirty": False,
        "ci_verify_script_present": True,
        "ci_verify_includes_projection_audit": True,
        "dist_status": "built",
        "dist_build_provenance_status": "verified",
        "dist_build_verified_same_commit": True,
        "workflow_status": "present",
        "packaged_frontend_image_status": "configured",
        "packaged_contract_scan_status": "pass",
        "blockers": [],
        "open_gap_count": 0,
    }
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: frontend_traceability_summary,
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert (
        readiness["domains"]["frontend_poc"]["evidence"]["frontend_release_traceability"]
        == frontend_traceability_summary
    )


def test_foundation_alpha_readiness_accepts_211_packaged_frontend_runtime_smoke(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_governance_evidence(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_release_evidence_runtime_acceptance(evidence_root, CURRENT_SOURCE_SHA, image=image)
    packaged_path = _write_frontend_packaged_runtime_smoke(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        runtime_host="211",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    frontend = readiness["domains"]["frontend_poc"]
    packaged = frontend["evidence"]["frontend_packaged_runtime_smoke"]
    assert packaged["status"] == "ready_for_operator_review"
    assert packaged["verified"] is True
    assert "211_packaged_frontend_runtime_smoke" in packaged["closed_evidence_items"]
    assert "packaged_frontend_image_release_acceptance" not in frontend["open_followups"]
    assert "packaged_frontend_image_release_acceptance" not in readiness["decision"]["stage_acceptance_blockers"]
    assert "packaged_frontend_image_release_acceptance" not in readiness["open_followups"]
    assert readiness["evidence_entries"]["frontend_packaged_runtime_smoke"] == (
        foundation_alpha_readiness._path_for_output(packaged_path)
    )


def test_foundation_alpha_readiness_accepts_clean_ordinary_user_frontend_projection(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_frontend_packaged_runtime_smoke(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        runtime_host="211",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_projection_audit_summary",
        lambda: {
            "status": "pass_with_policy_gaps",
            "ordinary_user_acceptance": "accepted_active_legacy_routes_clear",
            "active_legacy_route_count": 0,
            "ci_verify_includes_projection_audit": True,
            "open_gap_count": 1,
            "open_gaps": ["quarantined_legacy_sources_need_ai_platform_projection_remap"],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    frontend = readiness["domains"]["frontend_poc"]
    assert frontend["evidence"]["frontend_projection_audit"]["ordinary_user_acceptance"] == (
        "accepted_active_legacy_routes_clear"
    )
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" not in frontend["open_followups"]
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" not in readiness["open_followups"]
    assert (
        "ordinary_user_acceptance_for_quarantined_legacy_routes"
        not in readiness["decision"]["stage_acceptance_blockers"]
    )


def test_foundation_alpha_readiness_accepts_permission_gated_active_legacy_routes(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_frontend_packaged_runtime_smoke(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        runtime_host="211",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_projection_audit_summary",
        lambda: {
            "status": "pass_with_policy_gaps",
            "ordinary_user_acceptance": "accepted_active_legacy_routes_permission_gated",
            "active_legacy_route_count": 14,
            "ordinary_user_reachable_legacy_route_count": 0,
            "permission_gated_active_legacy_route_count": 14,
            "active_forbidden_projection_violation_count": 0,
            "ci_verify_includes_projection_audit": True,
            "open_gap_count": 2,
            "open_gaps": [
                "legacy_routes_need_policy_enforcement_or_ai_platform_remap",
                "quarantined_legacy_sources_need_ai_platform_projection_remap",
            ],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    frontend = readiness["domains"]["frontend_poc"]
    assert frontend["evidence"]["frontend_projection_audit"]["ordinary_user_acceptance"] == (
        "accepted_active_legacy_routes_permission_gated"
    )
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" not in frontend["open_followups"]
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" not in readiness["open_followups"]
    assert (
        "ordinary_user_acceptance_for_quarantined_legacy_routes"
        not in readiness["decision"]["stage_acceptance_blockers"]
    )


def test_foundation_alpha_readiness_keeps_packaged_frontend_blocker_without_211_smoke(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    image = "ai-platform:a3f1d73-foundation-alpha-poc"
    smoke_path, auth_path = _write_release_evidence_pair(evidence_root, CURRENT_SOURCE_SHA, image=image)
    _write_frontend_packaged_runtime_smoke(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image=image,
        runtime_host="docker-lab",
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", auth_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: CURRENT_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "open_gap_count": 0,
            "blockers": [],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    frontend = readiness["domains"]["frontend_poc"]
    packaged = frontend["evidence"]["frontend_packaged_runtime_smoke"]
    assert packaged["status"] == "ready_for_operator_review"
    assert packaged["verified"] is False
    assert "docker_lab_packaged_frontend_runtime_smoke" in packaged["closed_evidence_items"]
    assert "211_packaged_frontend_runtime_smoke" not in packaged["closed_evidence_items"]
    assert "packaged_frontend_image_release_acceptance" in frontend["open_followups"]
    assert "packaged_frontend_image_release_acceptance" in readiness["decision"]["stage_acceptance_blockers"]


def test_context_projection_summary_verifies_file_context_only_with_attachment_signal():
    summary = foundation_alpha_readiness._context_projection_summary(
        {
            "context_snapshot_public_projection": {
                "ok": True,
                "referenced_material_counts": {
                    "message_count": 1,
                    "file_count": 1,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "raw_material_id_fields_present": False,
                "forbidden_projection_leaks": [],
                "summary_source": "chat_stream",
                "input_keys": ["attachments", "message"],
                "memory_policy_source": "default",
                "long_term_memory_read": False,
                "execution_tier": "sdk_only_writing",
                "context_pack_generated_at_present": True,
            },
        }
    )

    assert summary["status"] == "verified_public_context_projection"
    assert summary["input_keys"] == ["attachments", "message"]
    assert summary["missing_public_summary_fields"] == []


def test_context_projection_summary_rejects_unsafe_input_keys_even_with_attachment_signal():
    summary = foundation_alpha_readiness._context_projection_summary(
        {
            "context_snapshot_public_projection": {
                "ok": True,
                "referenced_material_counts": {
                    "message_count": 1,
                    "file_count": 1,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "raw_material_id_fields_present": False,
                "forbidden_projection_leaks": [],
                "summary_source": "chat_stream",
                "input_keys": [
                    "attachments",
                    "includedFileIds",
                    "raw_storage_key",
                    "absoluteRuntimePaths",
                    "secretLikeValues",
                    "file id",
                    "message",
                ],
                "memory_policy_source": "default",
                "long_term_memory_read": False,
                "execution_tier": "sdk_only_writing",
                "context_pack_generated_at_present": True,
            },
        }
    )

    assert summary["status"] == "context_snapshot_public_projection_followup_required"
    assert summary["input_keys"] == ["attachments", "message"]
    assert summary["unsafe_input_keys"] == [
        "absoluteRuntimePaths",
        "file id",
        "includedFileIds",
        "raw_storage_key",
        "secretLikeValues",
    ]
    assert summary["missing_public_summary_fields"] == ["unsafe_input_keys"]


def test_context_projection_summary_rejects_non_integer_material_counts():
    summary = foundation_alpha_readiness._context_projection_summary(
        {
            "context_snapshot_public_projection": {
                "ok": True,
                "referenced_material_counts": {
                    "message_count": True,
                    "file_count": "1",
                    "artifact_count": 1.2,
                    "memory_record_count": 0,
                },
                "raw_material_id_fields_present": False,
                "forbidden_projection_leaks": [],
                "summary_source": "chat_stream",
                "input_keys": ["attachments", "message"],
                "memory_policy_source": "default",
                "long_term_memory_read": False,
                "execution_tier": "sdk_only_writing",
                "context_pack_generated_at_present": True,
            },
        }
    )

    assert summary["status"] == "context_snapshot_public_projection_followup_required"
    assert summary["invalid_referenced_material_count_fields"] == [
        "artifact_count",
        "file_count",
        "message_count",
    ]
    assert summary["missing_public_summary_fields"] == ["referenced_material_counts"]


def test_foundation_alpha_readiness_downgrades_frontend_poc_when_traceability_has_open_gaps(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "frontend_release_traceability_followup_required",
            "ci_verify_script_present": True,
            "ci_verify_includes_projection_audit": True,
            "dist_status": "built_unverified",
            "dist_build_provenance_status": "mismatch",
            "dist_build_verified_same_commit": False,
            "workflow_status": "present",
            "packaged_frontend_image_status": "configured",
            "blockers": ["dist_built_from_dirty_worktree"],
            "open_gap_count": 1,
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["domains"]["frontend_poc"]["status"] == "partial_followups_open"


def test_frontend_release_traceability_summary_adds_policy_blockers_for_missing_contracts():
    summary = foundation_alpha_readiness._frontend_release_traceability_summary(
        {
            "schema_version": "ai-platform.frontend-release-traceability.v1",
            "frontend_path": "frontend/web",
            "scripts": {"lint": "eslint ."},
            "git": {"commit": ACTIVE_RUNTIME_SUBJECT_SHA, "dirty": False},
            "workflow": {"status": "missing", "blockers": []},
            "dist": {
                "status": "built_unverified",
                "build_provenance": {
                    "status": "mismatch",
                    "verified_same_commit": False,
                    "blockers": [],
                },
                "blockers": [],
            },
            "packaged_frontend_image": {
                "status": "not_configured",
                "contract_scan": {"status": "fail"},
                "blockers": [],
            },
        }
    )

    assert summary["status"] == "frontend_release_traceability_followup_required"
    assert summary["open_gap_count"] == 7
    assert summary["blockers"] == [
        "frontend_ci_verify_projection_audit_missing",
        "frontend_ci_verify_script_missing",
        "frontend_dist_build_provenance_not_verified",
        "frontend_dist_not_built",
        "frontend_packaged_contract_scan_failed",
        "frontend_packaged_image_not_configured",
        "frontend_workflow_not_present",
    ]


def test_foundation_alpha_readiness_frontend_traceability_fails_closed_when_dependency_unavailable(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    def missing_frontend_traceability():
        raise ModuleNotFoundError("No module named 'node_modules'")

    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        missing_frontend_traceability,
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["domains"]["frontend_poc"]["evidence"]["frontend_release_traceability"] == {
        "status": "dependency_unavailable",
        "open_gap_count": 1,
        "dependency_error_class": "ModuleNotFoundError",
        "ci_verify_script_present": False,
        "ci_verify_includes_projection_audit": False,
        "dist_build_verified_same_commit": False,
        "blockers": ["frontend_release_traceability_dependency_unavailable"],
    }


def test_foundation_alpha_readiness_frontend_traceability_fails_closed_when_traceability_errors(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    def broken_frontend_traceability():
        raise FileNotFoundError("C:\\Users\\person\\secret\\frontend\\package.json")

    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        broken_frontend_traceability,
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    summary = readiness["domains"]["frontend_poc"]["evidence"]["frontend_release_traceability"]
    assert summary["status"] == "dependency_unavailable"
    assert summary["dependency_error_class"] == "FileNotFoundError"
    assert summary["blockers"] == ["frontend_release_traceability_dependency_unavailable"]
    serialized = json.dumps(summary, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "secret" not in serialized


def test_foundation_alpha_readiness_frontend_projection_audit_fails_closed_when_dependency_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    def missing_frontend_projection_audit():
        raise ModuleNotFoundError("No module named 'tools.frontend_projection_audit'")

    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_projection_audit_summary",
        missing_frontend_projection_audit,
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    summary = readiness["domains"]["frontend_poc"]["evidence"]["frontend_projection_audit"]
    assert summary == {
        "status": "dependency_unavailable",
        "ordinary_user_acceptance": "blocked_projection_audit_dependency_unavailable",
        "active_legacy_route_count": None,
        "ordinary_user_reachable_legacy_route_count": None,
        "permission_gated_active_legacy_route_count": None,
        "active_forbidden_projection_violation_count": None,
        "ci_verify_includes_projection_audit": False,
        "open_gap_count": 1,
        "open_gaps": ["frontend_projection_audit_dependency_unavailable"],
        "dependency_error_class": "ModuleNotFoundError",
    }
    assert "ordinary_user_acceptance_for_quarantined_legacy_routes" in readiness["open_followups"]


def test_foundation_alpha_readiness_marks_source_synced_runtime_pending_without_overclaiming(
    monkeypatch,
    tmp_path,
):
    evidence_root = tmp_path / "docs/release-evidence/foundation-alpha-poc"
    old_smoke_path, old_auth_path = _write_release_evidence_pair(
        evidence_root,
        RUNTIME_SUBJECT_SHA,
        image="ai-platform:8c0cffc-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T10:00:00+08:00",
        auth_captured_at="2026-06-11T10:01:00+08:00",
    )
    _write_release_evidence_pair(
        evidence_root,
        CURRENT_SOURCE_SHA,
        image="ai-platform:a3f1d73-foundation-alpha-poc",
        smoke_captured_at="2026-06-11T15:19:22+08:00",
        auth_captured_at="2026-06-11T15:18:58+08:00",
    )
    marker = {
        "schema_version": foundation_alpha_readiness.SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_affecting_changes_since_runtime_subject": ["app/worker.py"],
        "runtime_affecting_dirty_paths": [],
    }
    marker_path = tmp_path / ".ai-platform-source-snapshot.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(foundation_alpha_readiness, "_EVIDENCE_BASE_ROOT", evidence_root, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SMOKE_EVIDENCE", old_smoke_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_AUTH_RBAC_EVIDENCE", old_auth_path, raising=False)
    monkeypatch.setattr(foundation_alpha_readiness, "_SOURCE_SNAPSHOT_MARKER", marker_path, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: NEWER_SOURCE_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_runtime_affecting_changes_since",
        lambda _: ["app/worker.py"],
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())

    assert readiness["status"] == "source_synced_runtime_pending_followups_open"
    assert readiness["source_tree_commit_sha"] == NEWER_SOURCE_SHA
    assert readiness["runtime_subject_commit_sha"] == CURRENT_SOURCE_SHA
    assert readiness["runtime_source_relation"] == {
        "source_tree_commit_sha": NEWER_SOURCE_SHA,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": CURRENT_SOURCE_SHA,
        "runtime_source_marker": CURRENT_SOURCE_SHA,
        "runtime_matches_source_tree": False,
        "runtime_relevant_source_matches": False,
        "runtime_affecting_changes_since_runtime_subject": ["app/worker.py"],
        "runtime_affecting_dirty_paths": [],
        "status": "source_synced_runtime_pending",
    }
    assert (
        readiness["domains"]["g0_g1_source_authority_security"]["status"]
        == "source_synced_runtime_pending"
    )
    assert (
        readiness["domains"]["g0_g1_source_authority_security"]["evidence"]["runtime_source_relation"]
        == "source_synced_runtime_pending"
    )
    assert readiness["decision"]["reviewed_poc_loop_evidence_available"] is True
    assert readiness["decision"]["controlled_poc_loop_verified_for_current_source"] is False
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is False
    assert readiness["decision"]["current_source_exact_runtime_commit_match"] is False
    assert readiness["decision"]["runtime_rollout_required_for_current_source"] is True
    assert readiness["decision"]["production_claim_allowed"] is False
    assert readiness["decision"]["can_enter_next_stage_without_restrictions"] is False
    assert readiness["operator_context"]["poc_loop_status"] == "runtime_rollout_required"
    assert readiness["operator_context"]["current_runtime_relation"] == "source_synced_runtime_pending"
    assert "production_concurrency_increase" in readiness["operator_context"]["blocked_expansions"]


def test_foundation_alpha_readiness_markdown_and_cli_are_operator_usable(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: ACTIVE_RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_build_frontend_traceability_summary",
        lambda: {
            "status": "verified_packaged_release_followup_open",
            "dist_status": "built",
            "dist_build_provenance_status": "verified",
            "dist_build_verified_same_commit": True,
            "ci_verify_includes_projection_audit": True,
            "workflow_status": "present",
            "packaged_frontend_image_status": "configured",
            "blockers": [],
        },
        raising=False,
    )

    readiness = build_foundation_alpha_readiness(SecretBearingSettings())
    markdown = render_foundation_alpha_readiness_markdown(readiness)

    assert "# ai-platform Foundation Alpha POC Readiness" in markdown
    assert "Schema: `ai-platform.foundation-alpha-poc-readiness.v1`" in markdown
    assert "Status: `211_verified_followups_open`" in markdown
    assert f"Source tree: `{ACTIVE_RUNTIME_SUBJECT_SHA}`" in markdown
    assert "Verified Runtime Subject" in markdown
    assert "Evidence scope: `current_source_tree`" in markdown
    assert "Current decision" in markdown
    assert "`current_source_verified_by_running_runtime`: `True`" in markdown
    assert "`controlled_poc_loop_verified_for_current_source`: `True`" in markdown
    assert "Runtime source relation: `runtime_current_for_source_tree`" in markdown
    assert "POC loop status: `core_loop_verified_for_current_source_tree`" in markdown
    assert "Stage acceptance status: `core_poc_loop_verified_followups_open`" in markdown
    assert "Context snapshot public projection: `verified_public_context_projection`" in markdown
    assert "Context referenced material counts: `message=1, file=1, artifact=0, memory=0`" in markdown
    assert "Frontend release traceability: `verified_packaged_release_followup_open`" in markdown
    assert "Frontend build summary:" in markdown
    assert "Missing context public summary fields:" not in markdown
    assert "`production_claim_allowed`: `False`" in markdown
    assert "`capacity_default_increase_allowed`: `False`" in markdown
    assert "#21_recorded_capacity_evidence" not in markdown

    json_result = subprocess.run(
        [sys.executable, "tools/foundation_alpha_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(json_result.stdout)
    assert payload["schema_version"] == "ai-platform.foundation-alpha-poc-readiness.v1"
    assert payload["decision"]["reviewed_poc_loop_evidence_available"] is True
    assert payload["current_source_verified_by_running_runtime"] == payload["decision"]["current_source_verified_by_running_runtime"]
    assert (
        payload["runtime_relevant_source_verified_by_running_runtime"]
        == payload["decision"]["runtime_relevant_source_verified_by_running_runtime"]
    )
    assert (
        payload["controlled_poc_loop_verified_for_current_source"]
        == payload["decision"]["controlled_poc_loop_verified_for_current_source"]
    )
    assert payload["foundation_alpha_stage_complete"] == payload["decision"]["foundation_alpha_stage_complete"]
    assert payload["runtime_subject_commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert payload["verified_runtime_subject"]["commit_sha"] == ACTIVE_RUNTIME_SUBJECT_SHA
    assert "release_evidence_runtime_acceptance" in payload["evidence_entries"]
    assert "g9_runtime_export_and_retention_acceptance" not in payload["decision"]["stage_acceptance_blockers"]
    assert "runtime_image" not in payload
    assert payload["verified_runtime_subject"]["evidence_scope"] in {
        "current_source_tree",
        "current_runtime_relevant_source",
        "reviewed_historical_runtime_evidence",
    }

    markdown_result = subprocess.run(
        [sys.executable, "tools/foundation_alpha_readiness.py", "--format", "markdown"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "# ai-platform Foundation Alpha POC Readiness" in markdown_result.stdout


def test_foundation_alpha_readiness_fails_closed_when_optional_readiness_dependencies_are_unavailable(monkeypatch):
    monkeypatch.setattr(
        foundation_alpha_readiness,
        "_resolve_source_tree_revision",
        lambda: RUNTIME_SUBJECT_SHA,
        raising=False,
    )
    monkeypatch.setattr(foundation_alpha_readiness, "_resolve_source_tree_dirty", lambda: False, raising=False)

    def missing_governance(_: object | None = None):
        raise ModuleNotFoundError("No module named 'pydantic'")

    def missing_observability(
        _: object | None = None,
        *,
        release_evidence_runtime_acceptance: dict | None = None,
    ):
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
        "context_snapshot_public_projection": {
            "status": "missing_context_snapshot_public_projection",
            "referenced_material_counts": {},
            "raw_material_id_fields_present": None,
            "forbidden_projection_leak_count": None,
            "summary_source": None,
            "input_keys": [],
            "memory_policy_source": None,
            "long_term_memory_read": None,
            "execution_tier": None,
            "context_pack_generated_at_present": False,
            "missing_public_summary_fields": [
                "context_pack_generated_at",
                "execution_tier",
                "input_keys",
                "long_term_memory_read",
                "memory_policy_source",
                "summary_source",
            ],
        },
        "governance_runtime_smoke": {
            "status": "missing_governance_runtime_smoke",
            "schema_version": None,
            "ordinary_admin_runtime_status": None,
            "admin_runtime_governance_status": None,
            "governance_schema_version": None,
            "required_domains_present": None,
            "forbidden_projection_terms_present": None,
            "verified": False,
        },
    }
    assert readiness["domains"]["g9_admin_runtime_observability"]["evidence"] == {
        "observability_readiness_status": "dependency_unavailable",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "open_gap_count": 1,
        "dependency_error_class": "ModuleNotFoundError",
        "release_evidence_result": "ok:true",
        "release_evidence_runtime_acceptance": {
            "status": "missing_release_evidence_runtime_acceptance",
            "schema_version": None,
            "runtime_export_status": None,
            "retention_status": None,
            "safe_entry_count": None,
            "blocked_entry_count": None,
            "verified": False,
        },
        "alert_trace_export_runtime_acceptance": {
            "status": "missing_alert_trace_export_runtime_acceptance",
            "schema_version": None,
            "redaction_scan_status": None,
            "ordinary_admin_runtime_status": None,
            "admin_runtime_status": None,
            "alert_delivery_not_enabled": None,
            "trace_export_sources_public_only": None,
            "verified": False,
        },
    }

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "pydantic" not in serialized
    assert "traceback" not in serialized


def test_foundation_alpha_readiness_summaries_do_not_require_runtime_settings_import():
    script = (
        "import builtins\n"
        "real_import = builtins.__import__\n"
        "def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    if name == 'app.settings':\n"
        "        raise ModuleNotFoundError(\"No module named 'pydantic_settings'\")\n"
        "    return real_import(name, globals, locals, fromlist, level)\n"
        "builtins.__import__ = guarded_import\n"
        "from app.foundation_alpha_readiness import _build_governance_summary, _build_observability_summary\n"
        "governance = _build_governance_summary(None)\n"
        "observability = _build_observability_summary(None)\n"
        "assert governance['governance_readiness_status'] == 'partial_blocked'\n"
        "assert governance['open_gap_count'] > 1\n"
        "assert observability['observability_readiness_status'] == 'partial_blocked'\n"
        "assert observability['open_gap_count'] > 1\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_auth_rbac_summary_reports_platform_principal_tenant_and_gateway_checks():
    summary = foundation_alpha_readiness._auth_rbac_summary(
        {
            "unauthenticated_auth_me": {"route": "/api/auth/me", "status": 401},
            "authenticated_auth_me": {
                "route": "/api/ai/auth/me",
                "status": 200,
                "tenant_matches_requested": True,
                "user_matches_requested": True,
                "forbidden_projection_terms_present": False,
            },
            "invalid_gateway_secret_auth_me": {"route": "/api/ai/auth/me", "status": 403},
            "ordinary_admin_runtime": {"status": 403},
            "admin_runtime": {
                "status": 200,
                "required_sections_present": True,
                "tenant_matches_requested": True,
                "forbidden_projection_terms_present": False,
            },
        }
    )

    assert summary == {
        "unauthenticated_auth_me_status": 401,
        "authenticated_auth_me_status": 200,
        "authenticated_auth_me_route": "/api/ai/auth/me",
        "authenticated_auth_me_tenant_matches_requested": True,
        "authenticated_auth_me_user_matches_requested": True,
        "authenticated_auth_me_forbidden_projection_terms_present": False,
        "invalid_gateway_secret_auth_me_status": 403,
        "ordinary_admin_runtime_status": 403,
        "admin_runtime_status": 200,
        "admin_required_sections_present": True,
        "admin_tenant_matches_requested": True,
        "admin_forbidden_projection_terms_present": False,
    }

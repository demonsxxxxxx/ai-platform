import json
import subprocess
import sys

import pytest

import app.foundation_alpha_readiness as foundation_alpha_readiness
from app.foundation_alpha_readiness import (
    build_foundation_alpha_readiness,
    render_foundation_alpha_readiness_markdown,
)

ACTIVE_RUNTIME_SUBJECT_SHA = "2384e19dcac2e39fbcf9c27dc990f5774d391422"
HISTORICAL_RUNTIME_SUBJECT_SHA = "8c0cffca63bc747fad0a5771f209acc8a608ab9e"
RUNTIME_SUBJECT_SHA = HISTORICAL_RUNTIME_SUBJECT_SHA
CURRENT_SOURCE_SHA = "a3f1d739e12686cba2e0b309de26a4e1127bd3a5"
NEWER_SOURCE_SHA = "78362bcb380da67408ff7298cbdf24978d370992"


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


def test_foundation_alpha_readiness_classifies_source_metadata_paths_as_runtime_neutral():
    assert foundation_alpha_readiness._is_runtime_affecting_path(".gitignore") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("app/foundation_alpha_readiness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("app/capacity_bounded_load_harness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("docs/release-evidence/README.md") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tests/test_foundation_alpha_readiness.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tests/test_source_authority_docs.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/frontend_release_traceability.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/verify_auth_rbac_smoke.py") is False
    assert foundation_alpha_readiness._is_runtime_affecting_path("tools/verify_governance_runtime_smoke.py") is False
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
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is True
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
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is True


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
    assert readiness["decision"]["current_source_verified_by_running_runtime"] is True
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
        "current_source_verified_by_running_runtime": True,
        "current_source_exact_runtime_commit_match": True,
        "runtime_rollout_required_for_current_source": False,
        "can_enter_next_stage_without_restrictions": False,
        "production_claim_allowed": False,
        "ordinary_user_multi_agent_allowed": False,
        "docker_sandbox_hardened_claim_allowed": False,
        "capacity_default_increase_allowed": False,
    }
    assert readiness["operator_context"] == {
        "poc_scope": "foundation_alpha_controlled_internal_poc",
        "poc_loop_status": "verified_for_current_source",
        "current_runtime_relation": "runtime_current_for_source_tree",
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
            "g6_runtime_admin_dashboard_acceptance_for_governance",
            "g9_runtime_export_and_retention_acceptance",
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
        "input_keys": ["message"],
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
    assert "POC loop status: `verified_for_current_source`" in markdown
    assert "Context snapshot public projection: `verified_public_context_projection`" in markdown
    assert "Context referenced material counts: `message=1, file=1, artifact=0, memory=0`" in markdown
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

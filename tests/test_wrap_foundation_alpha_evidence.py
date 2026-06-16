import pytest

from tools.wrap_foundation_alpha_evidence import build_release_evidence_entry


COMMIT = "8e0389ea621a57f3ded2044e410943cc0d298571"
IMAGE = "ai-platform:8e0389e-main-runtime-rebase"
IMAGE_ID = "sha256:02d2a32bad783857cf140f5bbc20369603e96617b34dc3cdcbf2b8be7728cf0a"


def image_labels(commit: str = COMMIT) -> dict:
    return {
        "ai-platform.source-revision": commit,
        "ai-platform.runtime-subject": commit,
        "ai-platform.runtime_subject": commit,
        "ai-platform.source_revision": commit,
        "ai-platform.source_tree_commit": commit,
        "org.opencontainers.image.revision": commit,
    }


def source_snapshot(commit: str = COMMIT) -> dict:
    return {
        "schema_version": "ai-platform.source-snapshot.v1",
        "source_tree_commit_sha": commit,
        "source_tree_dirty": False,
        "runtime_subject_commit_sha": commit,
        "runtime_affecting_changes_since_runtime_subject": [],
        "runtime_affecting_dirty_paths": [],
        "sync_method": "git_bundle_fast_forward",
    }


def test_wraps_auth_rbac_verifier_output_as_reviewed_release_evidence_entry():
    verifier_output = {
        "schema_version": "ai-platform.auth-rbac-smoke.v1",
        "ok": True,
        "redaction_scan_status": "passed",
        "source": {
            "commit_sha": COMMIT,
            "gateway_secret_supplied": True,
            "image": IMAGE,
        },
        "checks": {
            "unauthenticated_auth_me": {"status": 401},
            "ordinary_admin_runtime": {"status": 403},
            "admin_runtime": {"status": 200},
        },
    }

    entry = build_release_evidence_entry(
        evidence_id="2026-06-16-211-foundation-alpha-poc-8e0389e-auth-rbac-smoke",
        verifier="tools/verify_auth_rbac_smoke.py",
        artifact_kind="211_runtime_smoke",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-16T22:45:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 tools/verify_auth_rbac_smoke.py --base-url http://127.0.0.1:8020",
        review_status="reviewed",
    )

    assert entry["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert entry["gate"] == "Foundation Alpha POC"
    assert entry["commit_sha"] == COMMIT
    assert entry["runtime_subject_commit_sha"] == COMMIT
    assert entry["redaction_scan_status"] == "passed"
    assert entry["review_status"] == "reviewed"
    assert entry["source_ref"]["image"] == IMAGE
    assert entry["source_ref"]["image_id"] == IMAGE_ID
    assert entry["source_ref"]["runtime_source_marker"] == COMMIT
    assert entry["source_ref"]["image_labels"]["ai-platform.source-revision"] == COMMIT
    assert entry["source_ref"]["source_snapshot"]["sync_method"] == "git_bundle_fast_forward"
    assert entry["evidence_ref"]["verifier"] == "tools/verify_auth_rbac_smoke.py"
    assert entry["evidence_ref"]["schema_version"] == "ai-platform.auth-rbac-smoke.v1"
    assert entry["evidence_ref"]["result"] == "ok:true"
    assert entry["evidence_ref"]["runtime_checks"] == verifier_output["checks"]
    assert entry["evidence_ref"]["runtime_source"] == {
        "commit_sha": COMMIT,
        "image": IMAGE,
    }


def test_rejects_image_label_mismatch_for_runtime_subject():
    labels = image_labels()
    labels["org.opencontainers.image.revision"] = "6ae06fdb624636e4255a1fe3bc8a0c188bd3ef6b"

    with pytest.raises(ValueError, match="image_label_mismatch"):
        build_release_evidence_entry(
            evidence_id="bad-labels",
            verifier="tools/verify_auth_rbac_smoke.py",
            artifact_kind="211_runtime_smoke",
            verifier_output={
                "schema_version": "ai-platform.auth-rbac-smoke.v1",
                "ok": True,
                "redaction_scan_status": "passed",
                "checks": {},
            },
            commit_sha=COMMIT,
            runtime_subject_commit_sha=COMMIT,
            captured_at="2026-06-16T22:45:00+08:00",
            image=IMAGE,
            image_id=IMAGE_ID,
            image_labels=labels,
            source_snapshot=source_snapshot(),
            command="python3 tools/verify_auth_rbac_smoke.py",
            review_status="reviewed",
        )


def test_normalizes_poc_gate_list_into_runtime_checks_map():
    verifier_output = {
        "ok": True,
        "gates": [
            {"name": "lambchat_frontend", "ok": True, "evidence": {"status": 200}},
            {
                "name": "word_review_attachment_chat",
                "ok": True,
                "evidence": {"run": {"status": "succeeded"}},
            },
        ],
    }

    entry = build_release_evidence_entry(
        evidence_id="2026-06-16-211-foundation-alpha-poc-8e0389e-runtime-poc-smoke",
        verifier="tools/verify_poc_gate.py",
        artifact_kind="211_runtime_smoke",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-16T22:45:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 tools/verify_poc_gate.py --api-url http://127.0.0.1:8020",
        redaction_scan_status="passed",
        review_status="reviewed",
    )

    assert entry["evidence_ref"]["schema_version"] == "ai-platform.poc-gate.v1"
    assert entry["evidence_ref"]["runtime_checks"] == {
        "lambchat_frontend": {"status": 200},
        "word_review_attachment_chat": {"run": {"status": "succeeded"}},
    }


def test_redacts_runtime_paths_and_storage_keys_from_wrapped_checks():
    verifier_output = {
        "ok": True,
        "gates": [
            {
                "name": "lambchat_frontend_dist_api_boundary",
                "ok": True,
                "evidence": {
                    "path": "/opt/ai-platform/frontend-dist-ai-platform",
                    "forbidden_reference_count": 0,
                },
            },
            {
                "name": "review_artifact",
                "ok": True,
                "evidence": {
                    "artifact_storage_key": (
                        "tenants/default/workspaces/default/sessions/ses_123/"
                        "runs/run_123/artifacts/1/result.txt"
                    ),
                    "artifact_id": "art_123",
                    "status": "succeeded",
                },
            },
            {
                "name": "runtime_config",
                "ok": True,
                "evidence": {
                    "env_path": "/opt/ai-platform/deploy/ai-platform/.env",
                    "claude_agent_sdk_enabled": True,
                    "source": {
                        "base_url": "http://127.0.0.1:8020",
                        "commit_sha": COMMIT,
                        "gateway_secret_supplied": True,
                        "image": IMAGE,
                        "tenant_id": "default",
                    },
                },
            },
        ],
    }

    entry = build_release_evidence_entry(
        evidence_id="redacted-paths",
        verifier="tools/verify_poc_gate.py",
        artifact_kind="211_runtime_smoke",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-16T22:45:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 tools/verify_poc_gate.py",
        redaction_scan_status="passed",
        review_status="reviewed",
    )

    serialized = str(entry["evidence_ref"]["runtime_checks"])
    assert "/opt/ai-platform/" not in serialized
    assert "artifact_storage_key" not in serialized
    assert "tenants/default" not in serialized
    assert "base_url" not in serialized
    assert "tenant_id" not in serialized
    assert "gateway_secret_supplied" not in serialized
    assert entry["evidence_ref"]["runtime_checks"]["lambchat_frontend_dist_api_boundary"]["path"] == "<redacted-path>"
    assert entry["evidence_ref"]["runtime_checks"]["runtime_config"]["env_path"] == "<redacted-path>"
    assert entry["evidence_ref"]["runtime_checks"]["runtime_config"]["source"] == {
        "commit_sha": COMMIT,
        "image": IMAGE,
    }


def test_requires_explicit_review_status_before_marking_evidence_reviewed():
    verifier_output = {
        "schema_version": "ai-platform.auth-rbac-smoke.v1",
        "ok": True,
        "redaction_scan_status": "passed",
        "checks": {"admin_runtime": {"status": 200}},
    }

    with pytest.raises(ValueError, match="review_status_required"):
        build_release_evidence_entry(
            evidence_id="missing-review",
            verifier="tools/verify_auth_rbac_smoke.py",
            artifact_kind="211_runtime_smoke",
            verifier_output=verifier_output,
            commit_sha=COMMIT,
            runtime_subject_commit_sha=COMMIT,
            captured_at="2026-06-16T22:45:00+08:00",
            image=IMAGE,
            image_id=IMAGE_ID,
            image_labels=image_labels(),
            source_snapshot=source_snapshot(),
            command="python3 tools/verify_auth_rbac_smoke.py",
        )


def test_requires_explicit_redaction_scan_status_when_verifier_omits_it():
    verifier_output = {
        "schema_version": "ai-platform.poc-gate.v1",
        "ok": True,
        "checks": {"lambchat_frontend": {"status": 200}},
    }

    with pytest.raises(ValueError, match="redaction_scan_status_required"):
        build_release_evidence_entry(
            evidence_id="missing-redaction",
            verifier="tools/verify_poc_gate.py",
            artifact_kind="211_runtime_smoke",
            verifier_output=verifier_output,
            commit_sha=COMMIT,
            runtime_subject_commit_sha=COMMIT,
            captured_at="2026-06-16T22:45:00+08:00",
            image=IMAGE,
            image_id=IMAGE_ID,
            image_labels=image_labels(),
            source_snapshot=source_snapshot(),
            command="python3 tools/verify_poc_gate.py",
            review_status="reviewed",
        )


def test_redacts_runtime_source_metadata_before_writing_evidence_ref():
    verifier_output = {
        "schema_version": "ai-platform.auth-rbac-smoke.v1",
        "ok": True,
        "redaction_scan_status": "passed",
        "source": {
            "base_url": "http://127.0.0.1:8020",
            "env_path": "/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform/.env",
            "callback_token": "secret-callback",
            "gateway_secret_supplied": True,
            "nested": {
                "storage_key": "tenants/default/workspaces/default/private",
                "path": "C:\\Users\\Xinlin.jiang\\secret.txt",
            },
        },
        "checks": {"admin_runtime": {"status": 200}},
    }

    entry = build_release_evidence_entry(
        evidence_id="redacted-source",
        verifier="tools/verify_auth_rbac_smoke.py",
        artifact_kind="211_runtime_smoke",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-16T22:45:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 tools/verify_auth_rbac_smoke.py",
        review_status="reviewed",
    )

    runtime_source = entry["evidence_ref"]["runtime_source"]
    serialized = str(runtime_source)
    assert runtime_source == {}
    assert "callback_token" not in serialized
    assert "storage_key" not in serialized
    assert "base_url" not in serialized
    assert "tenant_id" not in serialized
    assert "gateway_secret_supplied" not in serialized
    assert "/home/xinlin.jiang" not in serialized
    assert "C:\\Users" not in serialized
    assert "tenants/default" not in serialized

import json
import subprocess
import sys

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


def test_wraps_skill_dependency_runtime_acceptance_under_g6_gate():
    runtime_acceptance = {
        "schema_version": "ai-platform.skill-dependency-review-runtime-acceptance.v1",
        "status": "verified_runtime_acceptance",
        "target": "211_api_admin_runtime",
        "runtime_acceptance_requires_real_admin_runtime_payload": False,
        "does_not_close_runtime_acceptance": False,
        "runtime_payload_verified": True,
        "checks": {
            "ordinary_user_admin_runtime_denied": True,
            "same_tenant_admin_runtime_projection": True,
            "skill_release_readiness_present": True,
            "dependency_review_policy_present": True,
            "review_manifest_flags_projected": True,
            "skill_inventory_summary_projected": True,
            "raw_skill_package_storage_absent": True,
            "executor_private_material_absent": True,
            "sandbox_working_directory_absent": True,
            "secret_like_values_absent": True,
        },
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "long_term_cross_session_memory_enabled": False,
            "production_concurrency_defaults_raised": False,
            "docker_sandbox_production_hardening_claimed": False,
        },
    }
    verifier_output = {
        "schema_version": "ai-platform.governance-runtime-smoke.v1",
        "ok": True,
        "redaction_scan_status": "passed",
        "source": {
            "commit_sha": COMMIT,
            "gateway_secret_supplied": True,
            "image": IMAGE,
        },
        "checks": {
            "ordinary_admin_runtime": {"status": 403, "detail": "not_ai_admin"},
            "admin_runtime_governance": {
                "status": 200,
                "tenant_matches_requested": True,
                "governance_schema_version": "ai-platform.governance-readiness.v1",
                "governance_status_allowed": True,
                "required_domains_present": True,
                "forbidden_projection_terms_present": False,
            },
            "skill_dependency_review_policy_runtime_acceptance": runtime_acceptance,
            "verifier_checks": [
                {"name": "check_admin_runtime_governance_projection", "passed": True},
                {"name": "check_skill_dependency_review_runtime_acceptance", "passed": True},
                {"name": "check_no_secret_leakage", "passed": True},
            ],
        },
    }

    entry = build_release_evidence_entry(
        evidence_id="2026-06-17-211-skill-release-8e0389e-dependency-review-runtime-acceptance",
        verifier="tools/verify_governance_runtime_smoke.py",
        artifact_kind="skill_dependency_review_policy_runtime_acceptance",
        gate="G6 Skill Release / Dependency Governance",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-17T10:00:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 tools/verify_governance_runtime_smoke.py --base-url http://127.0.0.1:8020",
        review_status="reviewed",
        issue_refs=["#22"],
        pr_refs=["#52"],
    )

    assert entry["schema_version"] == "ai-platform.release-evidence-entry.v1"
    assert entry["gate"] == "G6 Skill Release / Dependency Governance"
    assert entry["artifact_kind"] == "skill_dependency_review_policy_runtime_acceptance"
    assert entry["evidence_ref"]["verifier"] == "tools/verify_governance_runtime_smoke.py"
    assert entry["evidence_ref"]["schema_version"] == "ai-platform.governance-runtime-smoke.v1"
    assert entry["evidence_ref"]["runtime_checks"][
        "skill_dependency_review_policy_runtime_acceptance"
    ] == runtime_acceptance
    assert {"name": "check_skill_dependency_review_runtime_acceptance", "passed": True} in entry[
        "evidence_ref"
    ]["runtime_checks"]["verifier_checks"]


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


def test_wraps_list_style_verifier_checks_and_attached_runtime_payload():
    verifier_output = {
        "schema_version": "ai-platform.executor-context-pack-211-verifier.v1",
        "checks": [
            {
                "name": "check_executor_context_pack_evidence",
                "passed": True,
                "message": "executor context-pack live worker-run evidence present",
            },
            {
                "name": "check_no_secret_leakage",
                "passed": True,
                "message": "no sensitive evidence detected",
            },
        ],
        "redaction_scan_status": "passed",
    }
    runtime_payload = {
        "schema_version": "ai-platform.executor-context-pack-211.v1",
        "run_id": "run-live",
        "public_context_summary": {
            "input_keys": ["attachments", "message"],
            "referenced_material_counts": {"artifact_count": 1},
        },
        "raw_storage_key": "tenants/default/private/raw-object",
        "executor_private_payload": {
            "prompt": "internal prompt material",
            "token": "secret-token",
        },
        "sandbox_workdir": "/tmp/ai-platform/private/run-live",
    }

    entry = build_release_evidence_entry(
        evidence_id="2026-06-17-211-office-context-executor-context-pack-runtime-acceptance",
        verifier="scripts/verify_executor_context_pack_211.py",
        artifact_kind="executor_context_pack_211_acceptance",
        verifier_output=verifier_output,
        commit_sha=COMMIT,
        runtime_subject_commit_sha=COMMIT,
        captured_at="2026-06-17T10:00:00+08:00",
        image=IMAGE,
        image_id=IMAGE_ID,
        image_labels=image_labels(),
        source_snapshot=source_snapshot(),
        command="python3 scripts/verify_executor_context_pack_211.py --run-id run-live --require-live-run-payload --json",
        gate="G6/G9/#22 Office Context Pack Architecture",
        runtime_check_payloads={"executor_context_pack_211_acceptance": runtime_payload},
        review_status="reviewed",
    )

    runtime_checks = entry["evidence_ref"]["runtime_checks"]
    serialized = json.dumps(entry, ensure_ascii=False)
    assert entry["gate"] == "G6/G9/#22 Office Context Pack Architecture"
    assert entry["artifact_kind"] == "executor_context_pack_211_acceptance"
    assert entry["evidence_ref"]["result"] == "ok:true"
    assert runtime_checks["verifier_checks"] == verifier_output["checks"]
    assert runtime_checks["executor_context_pack_211_acceptance"]["run_id"] == "run-live"
    assert "raw_storage_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "internal prompt material" not in serialized
    assert "tenants/default/private" not in serialized


def test_requires_real_source_snapshot_for_release_evidence_entry():
    verifier_output = {
        "schema_version": "ai-platform.executor-context-pack-211-verifier.v1",
        "checks": [
            {
                "name": "check_executor_context_pack_evidence",
                "passed": True,
                "message": "executor context-pack live worker-run evidence present",
            },
        ],
        "redaction_scan_status": "passed",
    }

    with pytest.raises(ValueError, match="source_snapshot_required"):
        build_release_evidence_entry(
            evidence_id="missing-source-snapshot",
            verifier="scripts/verify_executor_context_pack_211.py",
            artifact_kind="executor_context_pack_211_acceptance",
            verifier_output=verifier_output,
            commit_sha=COMMIT,
            runtime_subject_commit_sha=COMMIT,
            captured_at="2026-06-17T10:00:00+08:00",
            image=IMAGE,
            image_id=IMAGE_ID,
            image_labels=image_labels(),
            source_snapshot=None,
            command="python3 scripts/verify_executor_context_pack_211.py --run-id run-live --json",
            gate="G6/G9/#22 Office Context Pack Architecture",
            review_status="reviewed",
        )


def test_wrap_cli_accepts_executor_context_runtime_payload_file(tmp_path):
    verifier_output_path = tmp_path / "verifier-output.json"
    runtime_payload_path = tmp_path / "runtime-payload.json"
    labels_path = tmp_path / "labels.json"
    snapshot_path = tmp_path / "source-snapshot.json"
    output_path = tmp_path / "entry.json"
    verifier_output_path.write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "name": "check_executor_context_pack_evidence",
                        "passed": True,
                        "message": "executor context-pack live worker-run evidence present",
                    },
                    {
                        "name": "check_no_secret_leakage",
                        "passed": True,
                        "message": "no sensitive evidence detected",
                    },
                ],
                "redaction_scan_status": "passed",
            }
        ),
        encoding="utf-8",
    )
    runtime_payload_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.executor-context-pack-211.v1",
                "run_id": "run-live",
                "public_context_summary": {
                    "input_keys": ["attachments", "message"],
                    "referenced_material_counts": {"artifact_count": 1},
                },
                "raw_storage_key": "tenants/default/private/raw-object",
                "executor_private_payload": {
                    "prompt": "internal prompt material",
                    "token": "secret-token",
                },
                "sandbox_workdir": "/tmp/ai-platform/private/run-live",
            }
        ),
        encoding="utf-8",
    )
    labels_path.write_text(json.dumps(image_labels()), encoding="utf-8")
    snapshot_path.write_text(json.dumps(source_snapshot()), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "tools/wrap_foundation_alpha_evidence.py",
            "--verifier-output",
            str(verifier_output_path),
            "--verifier",
            "scripts/verify_executor_context_pack_211.py",
            "--artifact-kind",
            "executor_context_pack_211_acceptance",
            "--evidence-id",
            "2026-06-17-211-office-context-executor-context-pack-runtime-acceptance",
            "--commit-sha",
            COMMIT,
            "--runtime-subject-commit-sha",
            COMMIT,
            "--captured-at",
            "2026-06-17T10:00:00+08:00",
            "--image",
            IMAGE,
            "--image-id",
            IMAGE_ID,
            "--image-labels-json",
            str(labels_path),
            "--source-snapshot-json",
            str(snapshot_path),
            "--command",
            "python3 scripts/verify_executor_context_pack_211.py --run-id run-live --require-live-run-payload --json",
            "--gate",
            "G6/G9/#22 Office Context Pack Architecture",
            "--runtime-check-payload",
            f"executor_context_pack_211_acceptance={runtime_payload_path}",
            "--review-status",
            "reviewed",
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    entry = json.loads(output_path.read_text(encoding="utf-8"))
    runtime_checks = entry["evidence_ref"]["runtime_checks"]
    serialized = json.dumps(entry, ensure_ascii=False)
    assert entry["gate"] == "G6/G9/#22 Office Context Pack Architecture"
    assert runtime_checks["verifier_checks"][0]["passed"] is True
    assert runtime_checks["executor_context_pack_211_acceptance"]["run_id"] == "run-live"
    assert "raw_storage_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "internal prompt material" not in serialized
    assert "tenants/default/private" not in serialized

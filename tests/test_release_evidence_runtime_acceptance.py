import json
import subprocess
import sys
from pathlib import Path

from app.release_evidence_runtime_acceptance import build_release_evidence_runtime_acceptance


VALID_COMMIT = "948179c73734aa61ed764fb3485f5415fca8f193"


def _valid_entry(**overrides):
    entry = {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": "test-runtime-acceptance-smoke",
        "commit_sha": VALID_COMMIT,
        "runtime_subject_commit_sha": VALID_COMMIT,
        "gate": "Foundation Alpha POC",
        "issue_refs": ["#15", "#16", "#17"],
        "pr_refs": [],
        "artifact_kind": "211_runtime_smoke",
        "captured_at": "2026-06-12T15:30:00+08:00",
        "source_ref": {
            "branch": "main",
            "runtime_commit": VALID_COMMIT,
            "runtime_source_marker": VALID_COMMIT,
            "image": "ai-platform:948179c-skill-release-scaffold",
            "image_id": "sha256:1f9e5bdfd6788486ad36c31fc543a7806b3028c8e3261442c3262e55a46ef94e",
            "image_labels": {
                "ai-platform.source-revision": VALID_COMMIT,
                "org.opencontainers.image.revision": VALID_COMMIT,
            },
        },
        "evidence_ref": {
            "verifier": "tools/verify_poc_gate.py",
            "result": "ok:true",
            "runtime_checks": {
                "private_payload_leaked": False,
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
    }
    entry.update(overrides)
    return entry


def _write_entry(root: Path, entry: dict):
    entry_dir = root / "foundation-alpha-poc" / entry["commit_sha"]
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_path = entry_dir / f"{entry['evidence_id']}.json"
    entry_path.write_text(json.dumps(entry), encoding="utf-8")
    (root / "README.md").write_text("# ai-platform Release Evidence Index\n", encoding="utf-8")
    return entry_path


def test_release_evidence_runtime_acceptance_accepts_safe_index_and_retention_policy(tmp_path):
    _write_entry(tmp_path, _valid_entry())

    acceptance = build_release_evidence_runtime_acceptance(
        evidence_root=tmp_path,
        commit_sha=VALID_COMMIT,
        runtime_subject_commit_sha=VALID_COMMIT,
        image="ai-platform:948179c-skill-release-scaffold",
    )

    assert acceptance["schema_version"] == "ai-platform.release-evidence-runtime-acceptance.v1"
    assert acceptance["ok"] is True
    assert acceptance["status"] == "accepted_for_operator_review"
    assert acceptance["source"] == {
        "commit_sha": VALID_COMMIT,
        "runtime_subject_commit_sha": VALID_COMMIT,
        "image": "ai-platform:948179c-skill-release-scaffold",
        "evidence_root": str(tmp_path),
    }
    assert acceptance["checks"]["runtime_export_acceptance"] == {
        "status": "ready_for_operator_review",
        "export_policy": "safe_reviewed_index_only_not_runtime_export",
        "safe_entry_count": 1,
        "blocked_entry_count": 0,
        "excluded_entry_count": 0,
        "safe_entry_fields_only": True,
        "does_not_export_raw_runtime_payloads": True,
    }
    assert acceptance["checks"]["retention_runtime_acceptance"] == {
        "status": "accepted_review_first_policy",
        "schema_version": "ai-platform.release-evidence-retention-policy.v1",
        "policy_status": "contract_only_not_runtime_enforced",
        "default_retention_days": 180,
        "minimum_retention_days": 30,
        "requires_review_before_delete": True,
        "delete_only_reviewed_redacted_entries": True,
        "forbidden_delete_targets_present": True,
    }
    assert acceptance["open_gaps"] == []
    assert acceptance["does_not_export_raw_runtime_payloads"] is True
    assert acceptance["does_not_close_g9"] is True

    serialized = json.dumps(acceptance, ensure_ascii=False).lower()
    assert "source_ref" not in serialized
    assert "evidence_ref" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "api_key" not in serialized


def test_release_evidence_runtime_acceptance_fails_closed_when_export_index_is_blocked(tmp_path):
    _write_entry(
        tmp_path,
        _valid_entry(
            evidence_ref={
                "verifier": "tools/verify_poc_gate.py",
                "result": "ok:true",
                "runtime_checks": {
                    "private_payload": {
                        "raw_storage_key": "tenants/default/raw-secret",
                    },
                },
            }
        ),
    )

    acceptance = build_release_evidence_runtime_acceptance(
        evidence_root=tmp_path,
        commit_sha=VALID_COMMIT,
        runtime_subject_commit_sha=VALID_COMMIT,
        image="ai-platform:948179c-skill-release-scaffold",
    )

    assert acceptance["ok"] is False
    assert acceptance["status"] == "blocked_runtime_acceptance"
    assert "release_evidence_runtime_export_acceptance" in acceptance["open_gaps"]
    assert acceptance["checks"]["runtime_export_acceptance"]["status"] == "blocked_forbidden_evidence"
    assert acceptance["checks"]["runtime_export_acceptance"]["blocked_entry_count"] == 1

    serialized = json.dumps(acceptance, ensure_ascii=False).lower()
    assert "raw-secret" not in serialized
    assert "raw_storage_key" not in serialized
    assert "private_payload" not in serialized


def test_release_evidence_runtime_acceptance_cli_outputs_safe_json(tmp_path):
    _write_entry(tmp_path, _valid_entry())

    result = subprocess.run(
        [
            sys.executable,
            "tools/verify_release_evidence_runtime_acceptance.py",
            "--format",
            "json",
            "--evidence-root",
            str(tmp_path),
            "--commit-sha",
            VALID_COMMIT,
            "--runtime-subject-commit-sha",
            VALID_COMMIT,
            "--image",
            "ai-platform:948179c-skill-release-scaffold",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.release-evidence-runtime-acceptance.v1"
    assert payload["ok"] is True
    assert payload["checks"]["runtime_export_acceptance"]["safe_entry_count"] == 1
    assert "source_ref" not in result.stdout
    assert "evidence_ref" not in result.stdout
    assert "raw_storage_key" not in result.stdout

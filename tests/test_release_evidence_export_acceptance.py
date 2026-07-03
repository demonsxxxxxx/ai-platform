import json
import subprocess
import sys
from pathlib import Path

from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance


VALID_COMMIT = "d95107da2b5691781518bdbb8c4e5e76409869f3"


def _valid_entry(**overrides):
    entry = {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": "test-release-evidence",
        "commit_sha": VALID_COMMIT,
        "runtime_subject_commit_sha": VALID_COMMIT,
        "gate": "Foundation Alpha POC",
        "issue_refs": ["#15"],
        "pr_refs": ["#30"],
        "artifact_kind": "211_runtime_smoke",
        "captured_at": "2026-06-12T05:24:02+08:00",
        "source_ref": {
            "branch": "main",
            "runtime_commit": VALID_COMMIT,
            "runtime_source_marker": VALID_COMMIT,
            "image": "ai-platform:d95107d-context-projection",
            "image_id": "sha256:1c6bad9766cacb4d7bebfed38b3616dc559e04c155f2ccf495d5b84ce58d2815",
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


def test_release_evidence_export_acceptance_indexes_current_tree_without_raw_payloads():
    acceptance = build_release_evidence_export_acceptance()

    assert acceptance["schema_version"] == "ai-platform.release-evidence-export-acceptance.v1"
    assert acceptance["status"] == "ready_for_operator_review"
    assert acceptance["entry_count"] >= 1
    assert acceptance["blockers"] == []
    assert acceptance["blocked_entries"] == []
    assert acceptance["excluded_entry_count"] >= 1
    assert acceptance["does_not_close_g9"] is True
    assert acceptance["does_not_export_raw_runtime_payloads"] is True
    assert acceptance["export_policy"] == "safe_reviewed_index_only_not_runtime_export"
    assert "release_evidence_runtime_export_acceptance" in acceptance["open_gaps"]

    first_entry = acceptance["entries"][0]
    assert set(first_entry) == {
        "path",
        "evidence_id",
        "commit_sha",
        "runtime_subject_commit_sha",
        "gate",
        "issue_refs",
        "pr_refs",
        "artifact_kind",
        "captured_at",
        "redaction_scan_status",
        "review_status",
    }
    assert "source_ref" not in first_entry
    assert "evidence_ref" not in first_entry

    serialized = json.dumps(acceptance, ensure_ascii=False).lower()
    assert "c:\\users" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "sk-secret" not in serialized


def test_release_evidence_export_acceptance_fails_closed_on_private_payload(tmp_path):
    unsafe_entry = _valid_entry(
        evidence_ref={
            "verifier": "tools/verify_poc_gate.py",
            "result": "ok:true",
            "runtime_checks": {
                "private_payload": {
                    "raw_storage_key": "tenant/raw/secret-value",
                },
            },
        }
    )
    _write_entry(tmp_path, unsafe_entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_forbidden_evidence"
    assert acceptance["entry_count"] == 1
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blockers"]
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["entries"] == []
    assert acceptance["does_not_export_raw_runtime_payloads"] is True

    serialized = json.dumps(acceptance, ensure_ascii=False).lower()
    assert "secret-value" not in serialized
    assert "tenant/raw" not in serialized
    assert "private_payload" not in serialized
    assert "raw_storage_key" not in serialized


def test_release_evidence_export_acceptance_fails_closed_on_host_and_socket_paths(tmp_path):
    unsafe_entry = _valid_entry(
        evidence_ref={
            "verifier": "tools/verify_poc_gate.py",
            "result": "ok:true",
            "runtime_checks": {
                "linux_home_path": "/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform/.env",
                "docker_socket": "/var/run/docker.sock",
                "windows_home_path": r"C:\Users\Xinlin.jiang\Desktop\secret.txt",
            },
        }
    )
    _write_entry(tmp_path, unsafe_entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_forbidden_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == ["forbidden_marker_detected"]

    serialized = json.dumps(acceptance, ensure_ascii=False).lower()
    assert "/home/xinlin" not in serialized
    assert "/var/run/docker.sock" not in serialized
    assert "c:\\users" not in serialized


def test_release_evidence_export_acceptance_fails_closed_on_each_host_or_socket_marker(tmp_path):
    unsafe_values = [
        "/home/service/ai-platform/.env",
        "/Users/service/ai-platform/.env",
        "/var/run/docker.sock",
        "/tmp/ai-platform-compose.env",
    ]

    for index, unsafe_value in enumerate(unsafe_values):
        evidence_root = tmp_path / f"case-{index}"
        unsafe_entry = _valid_entry(
            evidence_id=f"unsafe-marker-{index}",
            evidence_ref={
                "verifier": "tools/verify_poc_gate.py",
                "result": "ok:true",
                "runtime_checks": {
                    "single_unsafe_marker": unsafe_value,
                },
            },
        )
        _write_entry(evidence_root, unsafe_entry)

        acceptance = build_release_evidence_export_acceptance(evidence_root=evidence_root)

        assert acceptance["status"] == "blocked_forbidden_evidence"
        assert acceptance["safe_entry_count"] == 0
        assert acceptance["blocked_entry_count"] == 1
        assert acceptance["blocked_entries"][0]["reasons"] == ["forbidden_marker_detected"]
        assert unsafe_value.lower() not in json.dumps(acceptance, ensure_ascii=False).lower()


def test_release_evidence_export_acceptance_excludes_non_entry_evidence_namespaces(tmp_path):
    _write_entry(tmp_path, _valid_entry())
    skill_release_dir = tmp_path / "skill-release" / "qa-file-reviewer"
    skill_release_dir.mkdir(parents=True, exist_ok=True)
    (skill_release_dir / "sbom.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.skill-release-source-sbom.v1",
                "skill": "qa-file-reviewer",
                "review_status": "generated",
            }
        ),
        encoding="utf-8",
    )

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "ready_for_operator_review"
    assert acceptance["safe_entry_count"] == 1
    assert acceptance["blocked_entry_count"] == 0
    assert acceptance["excluded_entry_count"] == 1
    assert acceptance["excluded_entries"] == [
        {
            "path": "skill-release/qa-file-reviewer/sbom.json",
            "reasons": ["non_release_evidence_entry_path"],
        }
    ]


def test_release_evidence_export_acceptance_excludes_dedicated_foundation_runtime_concurrency_namespace(tmp_path):
    _write_entry(tmp_path, _valid_entry())
    concurrency_dir = (
        tmp_path
        / "foundation-runtime-concurrency"
        / f"{VALID_COMMIT}-frc-b0-20260629"
    )
    concurrency_dir.mkdir(parents=True, exist_ok=True)
    (concurrency_dir / "foundation-runtime-concurrency.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.foundation-runtime-concurrency.v1",
                "artifact_kind": "foundation_runtime_concurrency",
                "commit_sha": VALID_COMMIT,
                "runtime_subject_commit_sha": VALID_COMMIT,
                "source_tree_commit_sha": VALID_COMMIT,
            }
        ),
        encoding="utf-8",
    )

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "ready_for_operator_review"
    assert acceptance["safe_entry_count"] == 1
    assert acceptance["blocked_entry_count"] == 0
    assert acceptance["excluded_entries"] == [
        {
            "path": (
                "foundation-runtime-concurrency/"
                f"{VALID_COMMIT}-frc-b0-20260629/"
                "foundation-runtime-concurrency.json"
            ),
            "reasons": ["non_release_evidence_entry_path"],
        }
    ]


def test_release_evidence_export_acceptance_blocks_raw_frc_under_release_entry_namespace(tmp_path):
    raw_dir = tmp_path / "foundation-alpha-poc" / VALID_COMMIT
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "foundation-runtime-concurrency.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.foundation-runtime-concurrency.v1",
                "artifact_kind": "foundation_runtime_concurrency",
                "commit_sha": VALID_COMMIT,
                "runtime_subject_commit_sha": VALID_COMMIT,
                "source_tree_commit_sha": VALID_COMMIT,
            }
        ),
        encoding="utf-8",
    )

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_invalid_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == [
        "invalid_schema_version",
        "missing_captured_at",
        "missing_evidence_id",
        "missing_evidence_ref",
        "missing_gate",
        "missing_issue_refs",
        "missing_redaction_scan_status",
        "missing_review_status",
        "missing_source_ref",
        "redaction_scan_not_passed",
        "review_status_not_accepted",
    ]


def test_release_evidence_export_acceptance_requires_runtime_subject_for_b1_smoke(tmp_path):
    entry = _valid_entry(
        evidence_id="b1-memory-context-smoke",
        artifact_kind="211_memory_enabled_document_workflow_smoke",
    )
    entry.pop("runtime_subject_commit_sha")
    _write_entry(tmp_path, entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_invalid_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == ["missing_runtime_subject_commit_sha"]


def test_release_evidence_export_acceptance_requires_runtime_subject_for_b2_sandbox_smoke(tmp_path):
    entry = _valid_entry(
        evidence_id="b2-sandbox-runtime-smoke",
        artifact_kind="211_sandbox_runtime_smoke",
    )
    entry.pop("runtime_subject_commit_sha")
    _write_entry(tmp_path, entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_invalid_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == ["missing_runtime_subject_commit_sha"]


def test_release_evidence_export_acceptance_requires_runtime_subject_for_identity_label_repair(tmp_path):
    entry = _valid_entry(
        evidence_id="runtime-identity-label-repair",
        artifact_kind="211_runtime_identity_label_repair",
    )
    entry.pop("runtime_subject_commit_sha")
    _write_entry(tmp_path, entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_invalid_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == ["missing_runtime_subject_commit_sha"]


def test_release_evidence_export_acceptance_requires_runtime_subject_for_deployment_cleanup(tmp_path):
    entry = _valid_entry(
        evidence_id="deployment-image-cleanup",
        artifact_kind="211_deployment_image_cleanup",
    )
    entry.pop("runtime_subject_commit_sha")
    _write_entry(tmp_path, entry)

    acceptance = build_release_evidence_export_acceptance(evidence_root=tmp_path)

    assert acceptance["status"] == "blocked_invalid_evidence"
    assert acceptance["safe_entry_count"] == 0
    assert acceptance["blocked_entry_count"] == 1
    assert acceptance["blocked_entries"][0]["reasons"] == ["missing_runtime_subject_commit_sha"]


def test_release_evidence_export_acceptance_cli_outputs_safe_json(tmp_path):
    _write_entry(tmp_path, _valid_entry())

    result = subprocess.run(
        [
            sys.executable,
            "tools/release_evidence_export_acceptance.py",
            "--format",
            "json",
            "--evidence-root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.release-evidence-export-acceptance.v1"
    assert payload["status"] == "ready_for_operator_review"
    assert payload["entry_count"] == 1
    assert payload["safe_entry_count"] == 1
    assert "executor_private_payload" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "sandbox_workdir" not in result.stdout

import base64
import json
import os
import subprocess
import sys

from app.skills.release_readiness import (
    build_skill_release_readiness,
    build_skill_version_release_review,
    write_skill_release_evidence_scaffold,
    build_skill_release_review_template,
    render_skill_release_readiness_markdown,
)
from app.skills.release_dashboard_readiness import build_skill_release_dashboard_readiness


_DASHBOARD_GAPS = [
    "admin_skill_release_dashboard_visual_acceptance",
    "admin_skill_release_dashboard_211_acceptance",
]


def _write_skill(root, skill_id: str, description: str, extra_files: dict[str, str] | None = None) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: {description}\n---\n\n"
        "# Skill\n\n"
        "Do not echo token=secret or absolute runtime paths in readiness output.\n",
        encoding="utf-8",
    )
    for relative_path, content in (extra_files or {}).items():
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _passed_review_manifest(
    skill_id: str,
    evidence_files: dict[str, list[str]],
    *,
    sbom_reviewed=True,
    license_policy_reviewed=True,
    vulnerability_reviewed=True,
) -> str:
    return json.dumps(
        {
            "schema_version": "ai-platform.skill-release-review.v1",
            "status": "passed",
            "skill_id": skill_id,
            "reviewer": "release-admin",
            "reviewed_at": "2026-06-08T10:00:00Z",
            "sbom_reviewed": sbom_reviewed,
            "license_policy_reviewed": license_policy_reviewed,
            "vulnerability_reviewed": vulnerability_reviewed,
            "evidence_files": evidence_files,
        },
        ensure_ascii=False,
    )


def _signed_package_evidence(**overrides):
    payload = {
        "package_artifact_ref": "artifacts/general-chat/package.tgz",
        "package_digest_sha256": "a" * 64,
        "signature_artifact_ref": "artifacts/general-chat/package.sig",
        "signer_identity": "release-admin",
        "signing_key_or_certificate_ref": "artifacts/general-chat/cert.pem",
        "transparency_log_or_attestation_ref": "artifact://skill-release/general-chat/attestation",
        "verification_status": "verified",
        "review_status": "reviewed",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _skill_version(
    *,
    skill_id: str = "general-chat",
    version: str = "hash-reviewed",
    content_hash: str = "hash-reviewed",
    files: list[dict] | None = None,
) -> dict:
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": content_hash,
        "description": "Reviewed Skill version.",
        "source": {
            "kind": "uploaded",
            "files": files
            or [
                {
                    "relative_path": "SKILL.md",
                    "content_base64": "c2tpbGw=",
                    "size_bytes": 5,
                }
            ],
        },
        "dependency_ids": [],
        "status": "active",
        "created_by": "dev-admin",
        "created_at": None,
    }


def _source_file(relative_path: str, text: str) -> dict:
    content = text.encode("utf-8")
    return {
        "relative_path": relative_path,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "size_bytes": len(content),
    }


def _valid_skill_dependency_runtime_acceptance() -> dict:
    return {
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


def _write_skill_dependency_runtime_entry(repo_root, *, runtime_payload=None) -> None:
    evidence_dir = repo_root / "docs" / "release-evidence" / "skill-release-runtime" / "8e0389e"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ai-platform.release-evidence-entry.v1",
        "evidence_id": "2026-06-17-211-skill-release-8e0389e-dependency-review-runtime-acceptance",
        "commit_sha": "8e0389ea621a57f3ded2044e410943cc0d298571",
        "runtime_subject_commit_sha": "8e0389ea621a57f3ded2044e410943cc0d298571",
        "gate": "G6 Skill Release / Dependency Governance",
        "issue_refs": ["#22"],
        "pr_refs": ["#52"],
        "artifact_kind": "skill_dependency_review_policy_runtime_acceptance",
        "captured_at": "2026-06-17T10:00:00+08:00",
        "source_ref": {
            "runtime_source_marker": "8e0389ea621a57f3ded2044e410943cc0d298571",
            "image": "ai-platform:8e0389e-main-runtime-rebase",
            "containers": ["ai-platform-api", "ai-platform-worker"],
            "source_tree_dirty": False,
        },
        "evidence_ref": {
            "verifier": "tools/verify_governance_runtime_smoke.py",
            "schema_version": "ai-platform.governance-runtime-smoke.v1",
            "result": "ok:true",
            "runtime_checks": {
                "skill_dependency_review_policy_runtime_acceptance": runtime_payload
                or _valid_skill_dependency_runtime_acceptance(),
                "verifier_checks": [
                    {"name": "check_admin_runtime_governance_projection", "passed": True},
                    {"name": "check_skill_dependency_review_runtime_acceptance", "passed": True},
                    {"name": "check_no_secret_leakage", "passed": True},
                ],
            },
        },
        "redaction_scan_status": "passed",
        "review_status": "reviewed",
        "review_notes": [
            "Reviewed 211 Admin Runtime projection for Skill dependency review policy acceptance.",
            "This closes only skill_dependency_review_policy_runtime_acceptance and does not close G6.",
        ],
    }
    target = evidence_dir / f"{payload['evidence_id']}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_skill_release_dashboard_readiness_contract_is_safe_and_does_not_close_g6():
    readiness = build_skill_release_dashboard_readiness()

    assert readiness["schema_version"] == "ai-platform.skill-release-dashboard-readiness.v1"
    assert readiness["status"] == "partial_blocked"
    assert readiness["policy"] == "source_runtime_acceptance_recorded_not_visual_or_211"
    assert readiness["does_not_close_g6"] is True
    assert readiness["dashboard_contract"] == {
        "schema_version": "ai-platform.skill-release-dashboard-contract.v1",
        "admin_only": True,
        "same_tenant_only": True,
        "source_routes": [
            "GET /api/ai/admin/skills/{skill_id}",
            "POST /api/ai/admin/skills/sync-builtin",
            "POST /api/ai/admin/skills/{skill_id}/versions/upload",
            "GET /api/ai/admin/skills/{skill_id}/versions/diff",
            "POST /api/ai/admin/skills/{skill_id}/promote",
            "POST /api/ai/admin/skills/{skill_id}/rollback",
        ],
        "required_inputs": [
            "skill_inventory_summary",
            "skill_dependency_policy",
            "release_review_evidence_summary",
            "version_diff_summary",
            "promote_rollback_policy",
            "runtime_materialization_status",
        ],
        "allowed_dashboard_fields": [
            "skill_id",
            "name",
            "description",
            "current_version",
            "previous_version",
            "rollout_percent",
            "status",
            "dependency_ids",
            "dependency_policy_status",
            "release_review_status",
            "blockers",
            "created_by",
            "created_at",
        ],
        "required_dashboard_controls": [
            "skill_filter",
            "dependency_policy_filter",
            "release_review_status_filter",
            "version_diff_preview",
            "promote_confirmation",
            "rollback_confirmation",
            "review_evidence_drilldown",
        ],
    }
    assert readiness["runtime_acceptance"] == {
        "schema_version": "ai-platform.skill-release-dashboard-runtime-acceptance.v1",
        "status": "source_route_tests_recorded",
        "evidence_strength": "source_route_tests",
        "source_routes": [
            "GET /api/ai/admin/skills/{skill_id}",
            "POST /api/ai/admin/skills/sync-builtin",
            "POST /api/ai/admin/skills/{skill_id}/versions/upload",
            "GET /api/ai/admin/skills/{skill_id}/versions/diff",
            "POST /api/ai/admin/skills/{skill_id}/promote",
            "POST /api/ai/admin/skills/{skill_id}/rollback",
        ],
        "covered_runtime_controls": [
            "ordinary_user_denied_detail",
            "same_tenant_admin_detail_projection",
            "sync_builtin_dependency_policy_enforced",
            "upload_admin_only_and_dependency_policy_enforced",
            "version_diff_admin_only_projection",
            "promote_policy_and_audit_controls",
            "rollback_policy_and_audit_controls",
            "materialization_fail_closed",
        ],
        "source_tests": [
            "tests/test_admin_skills.py::test_admin_skill_detail_requires_admin",
            "tests/test_admin_skills.py::test_admin_skill_detail_returns_skill_versions_and_snapshots",
            "tests/test_admin_skills.py::test_admin_sync_builtin_skills_records_registry_versions_dependencies_and_snapshots",
            "tests/test_admin_skills.py::test_admin_sync_builtin_skills_rejects_dependency_policy_violation",
            "tests/test_admin_skills.py::test_admin_upload_skill_package_requires_admin",
            "tests/test_admin_skills.py::test_admin_upload_skill_package_rejects_missing_internal_dependency",
            "tests/test_admin_skills.py::test_admin_upload_skill_package_stores_object_and_upserts_skill_version",
            "tests/test_admin_skills.py::test_admin_upload_skill_package_rejects_unknown_skill_before_storage",
            "tests/test_admin_skills.py::test_admin_skill_release_routes_require_admin",
            "tests/test_admin_skills.py::test_admin_skill_version_diff_returns_manifest_changes",
            "tests/test_admin_skills.py::test_admin_promote_skill_version_sets_release_policy_and_audit",
            "tests/test_admin_skills.py::test_admin_promote_rejects_inactive_skill_version",
            "tests/test_admin_skills.py::test_admin_promote_rejects_builtin_version_that_cannot_be_materialized",
            "tests/test_admin_skills.py::test_admin_rollback_skill_version_sets_release_policy_and_audit",
            "tests/test_admin_skills.py::test_admin_rollback_requires_existing_policy",
            "tests/test_admin_skills.py::test_admin_rollback_missing_version_returns_404",
        ],
        "forbidden_payload_classes": readiness["forbidden_payload_classes"],
        "does_not_close_g6": True,
        "does_not_close_visual_acceptance": True,
        "does_not_close_211_acceptance": True,
    }
    assert readiness["open_gaps"] == [
        "admin_skill_release_dashboard_visual_acceptance",
        "admin_skill_release_dashboard_211_acceptance",
    ]
    assert "admin_skill_release_dashboard_runtime_acceptance" not in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "storage_key" not in serialized
    assert "package_sha256" not in serialized
    assert "content_base64" not in serialized
    assert "bearer" not in serialized


def test_skill_release_readiness_records_policy_gaps_without_secret_or_absolute_paths(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "qa-file-reviewer", "Review Word documents.")
    _write_skill(
        skills_root,
        "minimax-docx",
        "Internal DOCX engine.",
        {
            "_meta.json": '{"slug":"minimax-docx","version":"1.0.0"}',
            "requirements.txt": "python-docx==1.2.0\n",
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["schema_version"] == "ai-platform.skill-release-readiness.v1"
    assert readiness["status"] == "partial_blocked"
    assert readiness["summary"] == {
        "total_skills": 2,
        "public_workbench_skills": 1,
        "internal_dependency_skills": 1,
        "skills_with_declared_dependencies": 1,
        "skills_with_package_metadata": 1,
        "skills_with_requirements": 1,
        "skills_with_sbom_evidence": 0,
        "skills_with_signed_package_evidence": 0,
        "skills_with_license_evidence": 0,
        "skills_with_vulnerability_evidence": 0,
    }
    assert readiness["open_gaps"] == [
        "signed_skill_package_or_sbom_release_gate",
        "dependency_vulnerability_or_license_policy",
        "skill_dependency_review_policy_runtime_acceptance",
        *_DASHBOARD_GAPS,
    ]
    policy = readiness["dependency_review_policy"]
    assert policy["schema_version"] == "ai-platform.skill-dependency-review-policy.v1"
    assert policy["status"] == "contract_only_not_runtime_satisfied"
    assert policy["required_review_manifest_schema"] == "ai-platform.skill-release-review.v1"
    assert policy["required_review_flags"] == [
        "sbom_reviewed",
        "license_policy_reviewed",
        "vulnerability_reviewed",
    ]
    assert policy["required_evidence_categories"] == [
        "sbom_or_signed_package",
        "license_policy",
        "vulnerability_scan",
    ]
    assert policy["evidence_files_must_match_skill_inventory"] is True
    assert policy["rejects_placeholder_evidence_refs"] is True
    assert policy["rejects_secret_like_evidence_refs"] is True
    assert policy["does_not_close_g6"] is True
    runtime_contract = readiness["dependency_review_runtime_acceptance_contract"]
    assert runtime_contract["schema_version"] == (
        "ai-platform.skill-dependency-review-runtime-acceptance.v1"
    )
    assert runtime_contract["verifier_script"] == "tools/verify_governance_runtime_smoke.py"
    assert runtime_contract["verifier_schema_version"] == "ai-platform.governance-runtime-smoke.v1"
    assert runtime_contract["runtime_payload_schema_version"] == (
        "ai-platform.skill-dependency-review-runtime-acceptance.v1"
    )
    assert runtime_contract["target"] == "211_api_admin_runtime"
    assert runtime_contract["acceptance_gap"] == "skill_dependency_review_policy_runtime_acceptance"
    assert runtime_contract["runtime_acceptance_requires_real_admin_runtime_payload"] is True
    assert "check_skill_dependency_review_runtime_acceptance" in runtime_contract[
        "required_verifier_checks"
    ]
    assert "review_manifest_flags_projected" in runtime_contract["required_runtime_checks"]
    assert runtime_contract["non_expansion_invariants"] == {
        "ordinary_user_multi_agent_allowed": False,
        "long_term_cross_session_memory_enabled": False,
        "production_concurrency_defaults_raised": False,
        "docker_sandbox_production_hardening_claimed": False,
    }
    assert runtime_contract["does_not_close_g6"] is True
    assert readiness["runtime_acceptance_evidence"] == {}
    assert readiness["closed_runtime_gaps"] == []
    signed_contract = policy["signed_package_evidence_contract"]
    assert signed_contract["schema_version"] == "ai-platform.skill-signed-package-evidence-contract.v1"
    assert signed_contract["status"] == "source_validation_enabled_not_evidence_satisfied"
    assert signed_contract["evidence_category"] == "sbom_or_signed_package"
    assert signed_contract["required_review_manifest_schema"] == "ai-platform.skill-release-review.v1"
    assert signed_contract["required_review_flag"] == "sbom_reviewed"
    assert signed_contract["candidate_evidence_file_names"] == [
        "ai-platform-signed-package-evidence.json",
        "signed-package-evidence.json",
    ]
    assert "cosign.bundle" not in signed_contract["candidate_evidence_file_names"]
    assert "cosign.bundle" in signed_contract["external_attestation_reference_examples"]
    assert signed_contract["required_fields"] == [
        "package_artifact_ref",
        "package_digest_sha256",
        "signature_artifact_ref",
        "signer_identity",
        "signing_key_or_certificate_ref",
        "transparency_log_or_attestation_ref",
        "verification_status",
        "review_status",
    ]
    assert signed_contract["safe_reference_policy"] == {
        "relative_or_artifact_refs_only": True,
        "raw_object_storage_refs_forbidden": True,
        "executor_private_runtime_payload_forbidden": True,
        "sandbox_working_directory_forbidden": True,
        "secret_like_values_forbidden": True,
    }
    assert signed_contract["runtime_validation"] == "enabled_for_repository_signed_package_evidence_json"
    assert signed_contract["remaining_acceptance_gap"] == "real_reviewed_signed_package_evidence_missing"
    assert signed_contract["does_not_close_g6"] is True
    dashboard = readiness["admin_skill_release_dashboard"]
    assert dashboard["schema_version"] == "ai-platform.skill-release-dashboard-readiness.v1"
    assert dashboard["dashboard_contract"]["schema_version"] == "ai-platform.skill-release-dashboard-contract.v1"
    assert dashboard["open_gaps"] == _DASHBOARD_GAPS
    assert dashboard["does_not_close_g6"] is True

    qa_skill = next(item for item in readiness["skills"] if item["skill_id"] == "qa-file-reviewer")
    assert qa_skill["public"] is True
    assert qa_skill["internal_dependency"] is False
    assert qa_skill["manifest"]["description_present"] is True
    assert qa_skill["dependency_policy"]["dependency_ids"] == ["minimax-docx"]
    assert qa_skill["dependency_policy"]["dependency_details"][0]["status"] == "allowed"
    assert "signed_package_or_sbom_evidence_missing" in qa_skill["blockers"]
    assert "dependency_license_policy_evidence_missing" in qa_skill["blockers"]
    assert "dependency_vulnerability_evidence_missing" in qa_skill["blockers"]

    internal_skill = next(item for item in readiness["skills"] if item["skill_id"] == "minimax-docx")
    assert internal_skill["public"] is False
    assert internal_skill["internal_dependency"] is True
    assert internal_skill["package_evidence"]["metadata_files"] == ["_meta.json"]
    assert internal_skill["package_evidence"]["requirements_files"] == ["requirements.txt"]

    serialized = json.dumps(readiness, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "token=secret" not in serialized
    assert "runtime paths" not in serialized
    assert ".claude/skills" not in serialized


def test_skill_release_readiness_markdown_is_gap_first_and_operator_readable(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")

    markdown = render_skill_release_readiness_markdown(build_skill_release_readiness(skills_root=skills_root))

    assert "# ai-platform Skill Release Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "signed_skill_package_or_sbom_release_gate" in markdown
    assert "dependency_vulnerability_or_license_policy" in markdown
    assert "ai-platform.skill-dependency-review-policy.v1" in markdown
    assert "ai-platform.skill-signed-package-evidence-contract.v1" in markdown
    assert "## Dependency Review Runtime Acceptance" in markdown
    assert "ai-platform.skill-dependency-review-runtime-acceptance.v1" in markdown
    assert "tools/verify_governance_runtime_smoke.py" in markdown
    assert "skill_dependency_review_policy_runtime_acceptance" in markdown
    assert "check_skill_dependency_review_runtime_acceptance" in markdown
    assert "Runtime evidence: none" in markdown
    assert "ai-platform.skill-release-dashboard-contract.v1" in markdown
    assert "source_route_tests_recorded" in markdown
    open_gap_section = markdown.split("## Open Gaps", 1)[1].split("## Summary", 1)[0]
    assert "admin_skill_release_dashboard_runtime_acceptance" not in open_gap_section
    assert "source_validation_enabled_not_evidence_satisfied" in markdown
    assert "enabled_for_repository_signed_package_evidence_json" in markdown
    assert "general-chat" in markdown
    assert "missing evidence `sbom_or_signed_package, license_policy, vulnerability_scan`" in markdown
    assert "missing review flags `sbom_reviewed, license_policy_reviewed, vulnerability_reviewed`" in markdown
    assert str(tmp_path) not in markdown
    assert "token=secret" not in markdown


def test_skill_release_readiness_cli_outputs_json_without_secret_markers(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    env = os.environ.copy()
    env["SKILL_STAGING_SUBDIR"] = ".claude/skills/token=secret"

    result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_readiness.py",
            "--skills-root",
            str(skills_root),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.skill-release-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert payload["admin_skill_release_dashboard"]["schema_version"] == (
        "ai-platform.skill-release-dashboard-readiness.v1"
    )
    assert payload["admin_skill_release_dashboard"]["open_gaps"] == _DASHBOARD_GAPS
    assert payload["skills"][0]["skill_id"] == "general-chat"
    assert str(tmp_path) not in result.stdout
    assert "token=secret" not in result.stdout
    assert ".claude/skills" not in result.stdout


def test_skill_release_dashboard_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["SKILL_STAGING_SUBDIR"] = ".claude/skills/token=secret"

    result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_dashboard_readiness.py",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.skill-release-dashboard-readiness.v1"
    assert payload["dashboard_contract"]["schema_version"] == "ai-platform.skill-release-dashboard-contract.v1"
    assert payload["open_gaps"] == _DASHBOARD_GAPS
    assert "token=secret" not in result.stdout
    assert ".claude/skills" not in result.stdout
    assert "executor_private_payload" not in result.stdout
    assert "raw_storage_key" not in result.stdout
    assert "sandbox_workdir" not in result.stdout


def test_skill_release_readiness_fails_closed_when_inventory_is_missing(tmp_path):
    readiness = build_skill_release_readiness(skills_root=tmp_path / "missing-skills")

    assert readiness["status"] == "partial_blocked"
    assert readiness["summary"]["total_skills"] == 0
    assert "skill_inventory_missing_or_empty" in readiness["open_gaps"]
    assert readiness["source"]["inventory_present"] is False


def test_skill_release_readiness_fails_closed_for_blocked_dependency_policy(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "qa-file-reviewer", "Review Word documents.")

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert "skill_dependency_policy_blocked" in readiness["open_gaps"]
    qa_skill = readiness["skills"][0]
    assert qa_skill["skill_id"] == "qa-file-reviewer"
    assert qa_skill["dependency_policy"]["dependency_details"][0]["status"] == "blocked"
    assert "skill_dependency_policy_blocked" in qa_skill["blockers"]


def test_skill_release_readiness_does_not_clear_review_gates_from_filenames_only(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "sbom.json": "{}",
            "LICENSE": "Placeholder license text",
            "npm-audit.json": "{}",
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == [
        "signed_skill_package_or_sbom_release_gate",
        "dependency_vulnerability_or_license_policy",
        "skill_dependency_review_policy_runtime_acceptance",
        *_DASHBOARD_GAPS,
    ]
    skill = readiness["skills"][0]
    assert skill["package_evidence"]["sbom_files"] == ["sbom.json"]
    assert skill["package_evidence"]["license_files"] == ["LICENSE"]
    assert skill["package_evidence"]["vulnerability_evidence_files"] == ["npm-audit.json"]
    assert skill["release_review"]["status"] == "missing"
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]


def test_skill_release_readiness_redacts_secret_like_evidence_path_segments(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "token=secret/sbom.json": "{}",
            "private/.env/requirements.txt": "requests==2.32.0\n",
            "secret-folder/npm-audit.json": "{}",
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "token=secret" not in serialized
    assert "private/.env" not in serialized
    assert "secret-folder" not in serialized
    assert readiness["skills"][0]["package_evidence"]["sbom_files"] == ["sbom.json"]
    assert readiness["skills"][0]["package_evidence"]["requirements_files"] == ["requirements.txt"]
    assert readiness["skills"][0]["package_evidence"]["vulnerability_evidence_files"] == ["npm-audit.json"]


def test_skill_release_review_template_is_pending_and_does_not_clear_gate():
    template = build_skill_release_review_template(skill_id="general-chat")

    assert template["schema_version"] == "ai-platform.skill-release-review.v1"
    assert template["status"] == "pending"
    assert template["skill_id"] == "general-chat"
    assert template["sbom_reviewed"] is False
    assert template["license_policy_reviewed"] is False
    assert template["vulnerability_reviewed"] is False
    assert template["does_not_close_gate_by_itself"] is True
    assert template["required_evidence"]["sbom_or_signed_package"]
    assert template["required_evidence"]["license_policy"]
    assert template["required_evidence"]["vulnerability_scan"]

    serialized = json.dumps(template, ensure_ascii=False)
    assert "token=secret" not in serialized
    assert ".env" not in serialized
    assert ".claude/skills" not in serialized
    assert "work_dir" not in serialized


def test_skill_release_review_template_rejects_secret_like_or_path_skill_ids():
    for skill_id in ("token=secret", "../general-chat", "private/.env", "general chat"):
        try:
            build_skill_release_review_template(skill_id=skill_id)
        except ValueError as exc:
            assert "Invalid skill_id" in str(exc)
        else:
            raise AssertionError(f"expected invalid skill_id to be rejected: {skill_id}")


def test_skill_release_readiness_cli_outputs_review_template_json_without_writing_files(tmp_path):
    output_path = tmp_path / "ai-platform-skill-release-review.json"

    result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_readiness.py",
            "--review-template",
            "--skill-id",
            "general-chat",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.skill-release-review.v1"
    assert payload["status"] == "pending"
    assert payload["skill_id"] == "general-chat"
    assert payload["sbom_reviewed"] is False
    assert payload["license_policy_reviewed"] is False
    assert payload["vulnerability_reviewed"] is False
    assert output_path.exists() is False
    assert str(tmp_path) not in result.stdout
    assert "token=secret" not in result.stdout


def test_skill_release_readiness_cli_can_write_review_template_when_output_is_explicit(tmp_path):
    output_path = tmp_path / "ai-platform-skill-release-review.json"

    result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_readiness.py",
            "--review-template",
            "--skill-id",
            "general-chat",
            "--format",
            "json",
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "pending"
    assert payload["skill_id"] == "general-chat"
    assert "token=secret" not in output_path.read_text(encoding="utf-8")


def test_skill_release_review_manifest_does_not_clear_gate_with_empty_evidence_files(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "sbom.json": "{}",
            "LICENSE": "reviewed license text",
            "npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": [],
                    "license_policy": [],
                    "vulnerability_scan": [],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["status"] == "partial_blocked"
    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]


def test_skill_release_review_manifest_does_not_clear_gate_with_placeholder_evidence_files(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "sbom.json": "{}",
            "LICENSE": "reviewed license text",
            "npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["<sbom.json>"],
                    "license_policy": ["${license_policy}"],
                    "vulnerability_scan": ["artifact://skill-review/npm-audit.json=<scan>"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_or_license_policy" in readiness["open_gaps"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "<sbom.json>" not in serialized
    assert "${license_policy}" not in serialized
    assert "<scan>" not in serialized


def test_skill_release_review_manifest_does_not_clear_gate_with_unmatched_evidence_files(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "sbom.json": "{}",
            "LICENSE": "reviewed license text",
            "npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["release/sbom.json"],
                    "license_policy": ["docs/LICENSE"],
                    "vulnerability_scan": ["reports/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert readiness["status"] == "partial_blocked"


def test_skill_release_review_manifest_clears_review_gate_with_matched_evidence_files(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": "{}",
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == [
        "skill_dependency_review_policy_runtime_acceptance",
        *_DASHBOARD_GAPS,
    ]
    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "passed"
    assert skill["release_review"]["evidence_files_verified"] is True
    assert skill["blockers"] == []


def test_skill_release_review_manifest_accepts_valid_signed_package_evidence(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/ai-platform-signed-package-evidence.json": _signed_package_evidence(),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/ai-platform-signed-package-evidence.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == [
        "skill_dependency_review_policy_runtime_acceptance",
        *_DASHBOARD_GAPS,
    ]
    assert readiness["summary"]["skills_with_signed_package_evidence"] == 1
    skill = readiness["skills"][0]
    assert skill["package_evidence"]["signed_package_evidence_files"] == [
        "ai-platform-signed-package-evidence.json"
    ]
    assert skill["release_review"]["status"] == "passed"
    assert skill["release_review"]["evidence_files_verified"] is True
    assert skill["blockers"] == []


def test_skill_version_release_review_rejects_missing_or_pending_evidence(tmp_path):
    missing_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=tmp_path / "release-evidence" / "skill-release",
    )

    assert missing_review["schema_version"] == "ai-platform.skill-version-release-review.v1"
    assert missing_review["status"] == "blocked"
    assert missing_review["skill_id"] == "general-chat"
    assert missing_review["version"] == "hash-reviewed"
    assert missing_review["content_hash"] == "hash-reviewed"
    assert "signed_package_or_sbom_evidence_missing" in missing_review["blockers"]
    assert "dependency_license_policy_evidence_missing" in missing_review["blockers"]
    assert "dependency_vulnerability_evidence_missing" in missing_review["blockers"]

    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("sbom.json").write_text(
        json.dumps({"status": "pending_review"}, ensure_ascii=False),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("third-party-notices.txt").write_text(
        "Status: pending_review\nOperator action: set license_policy_reviewed=true.\n",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("vulnerability-report.json").write_text(
        json.dumps({"review_required": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["external-release-evidence/general-chat/sbom.json"],
                "license_policy": ["external-release-evidence/general-chat/third-party-notices.txt"],
                "vulnerability_scan": ["external-release-evidence/general-chat/vulnerability-report.json"],
            },
        ),
        encoding="utf-8",
    )

    pending_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=evidence_root,
    )

    assert pending_review["status"] == "blocked"
    assert pending_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "sbom_or_signed_package_evidence_not_reviewed" in pending_review["release_review"][
        "evidence_file_errors"
    ]
    assert "dependency_license_policy_review_not_verified" in pending_review["blockers"]


def test_skill_version_release_review_handles_malformed_source_file_base64_fail_closed(tmp_path):
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["evidence/sbom.json"],
                "license_policy": ["legal/LICENSE"],
                "vulnerability_scan": ["security/vulnerability-report.json"],
            },
        )
    )
    review["skill_content_hash"] = "hash-reviewed"
    files = [
        _source_file("SKILL.md", "# general-chat\n"),
        {
            "relative_path": "evidence/sbom.json",
            "content_base64": "not valid base64",
            "size_bytes": 16,
        },
        _source_file("legal/LICENSE", "Reviewed license policy evidence."),
        _source_file("security/vulnerability-report.json", '{"status": "reviewed"}'),
        _source_file("ai-platform-skill-release-review.json", json.dumps(review, ensure_ascii=False)),
    ]

    release_review = build_skill_version_release_review(
        _skill_version(files=files),
        skill_release_evidence_root=tmp_path / "release-evidence" / "skill-release",
    )

    assert release_review["status"] == "blocked"
    assert release_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "sbom_or_signed_package_evidence_file_unreadable" in release_review["release_review"][
        "evidence_file_errors"
    ]
    assert "signed_package_or_sbom_review_not_verified" in release_review["blockers"]


def test_skill_version_release_review_requires_matching_target_content_hash(tmp_path):
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("sbom.json").write_text("{}", encoding="utf-8")
    skill_evidence_dir.joinpath("third-party-notices.txt").write_text(
        "reviewed license text",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("vulnerability-report.json").write_text("{}", encoding="utf-8")
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["external-release-evidence/general-chat/sbom.json"],
                "license_policy": ["external-release-evidence/general-chat/third-party-notices.txt"],
                "vulnerability_scan": ["external-release-evidence/general-chat/vulnerability-report.json"],
            },
        )
    )
    review["skill_content_hash"] = "different-hash"
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        json.dumps(review, ensure_ascii=False),
        encoding="utf-8",
    )

    release_review = build_skill_version_release_review(
        _skill_version(content_hash="hash-reviewed"),
        skill_release_evidence_root=evidence_root,
    )

    assert release_review["status"] == "blocked"
    assert release_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "review_content_hash_mismatch" in release_review["release_review"]["review_flag_errors"]
    assert "signed_package_or_sbom_review_not_verified" in release_review["blockers"]


def test_skill_version_release_review_accepts_reviewed_uploaded_source_files_with_external_evidence(tmp_path):
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("evidence").mkdir()
    skill_evidence_dir.joinpath("legal").mkdir()
    skill_evidence_dir.joinpath("security").mkdir()
    skill_evidence_dir.joinpath("evidence/sbom.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.skill-release-source-sbom.v1",
                "metadata": {"component": {"name": "general-chat", "version": "hash-reviewed"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("legal/LICENSE").write_text(
        "Reviewed license policy evidence.",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("security/vulnerability-report.json").write_text(
        json.dumps({"status": "reviewed", "finding_count": 0, "findings": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": [
                    "external-release-evidence/general-chat/evidence/sbom.json"
                ],
                "license_policy": ["external-release-evidence/general-chat/legal/LICENSE"],
                "vulnerability_scan": [
                    "external-release-evidence/general-chat/security/vulnerability-report.json"
                ],
            },
        )
    )
    review["skill_content_hash"] = "hash-reviewed"
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        json.dumps(review, ensure_ascii=False),
        encoding="utf-8",
    )

    release_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=evidence_root,
    )

    assert release_review == {
        "schema_version": "ai-platform.skill-version-release-review.v1",
        "status": "passed",
        "skill_id": "general-chat",
        "version": "hash-reviewed",
        "content_hash": "hash-reviewed",
        "package_evidence": {
            "metadata_files": [],
            "requirements_files": [],
            "sbom_files": ["sbom.json"],
            "signed_package_evidence_files": [],
            "license_files": ["LICENSE"],
            "vulnerability_evidence_files": ["vulnerability-report.json"],
        },
        "release_review": {
            "status": "passed",
            "files": ["ai-platform-skill-release-review.json"],
            "evidence_files_verified": True,
            "review_flag_errors": [],
            "sbom_reviewed": True,
            "license_policy_reviewed": True,
            "vulnerability_reviewed": True,
            "content_hash_verified": True,
        },
        "blockers": [],
        "does_not_close_g6": True,
    }


def test_skill_release_readiness_closes_only_runtime_gap_from_reviewed_runtime_acceptance(
    tmp_path,
):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/ai-platform-signed-package-evidence.json": _signed_package_evidence(),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/ai-platform-signed-package-evidence.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )
    _write_skill_dependency_runtime_entry(tmp_path)

    readiness = build_skill_release_readiness(
        skills_root=skills_root,
        skill_release_evidence_root=tmp_path / "docs" / "release-evidence" / "skill-release",
        runtime_evidence_root=tmp_path / "docs" / "release-evidence" / "skill-release-runtime",
    )

    assert "skill_dependency_review_policy_runtime_acceptance" not in readiness["open_gaps"]
    assert readiness["closed_runtime_gaps"] == [
        "skill_dependency_review_policy_runtime_acceptance"
    ]
    assert readiness["runtime_acceptance_evidence"][
        "skill_dependency_review_policy_runtime_acceptance"
    ] == {
        "artifact_kind": "skill_dependency_review_policy_runtime_acceptance",
        "does_not_close_g6": True,
        "evidence_id": "2026-06-17-211-skill-release-8e0389e-dependency-review-runtime-acceptance",
        "path": "docs/release-evidence/skill-release-runtime/8e0389e/2026-06-17-211-skill-release-8e0389e-dependency-review-runtime-acceptance.json",
        "runtime_payload_verified": True,
        "runtime_subject": "8e0389e-main-runtime-rebase",
        "status": "verified_211_runtime_acceptance",
        "target": "211_api_admin_runtime",
        "verifier": "tools/verify_governance_runtime_smoke.py",
    }
    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == _DASHBOARD_GAPS


def test_skill_dependency_runtime_acceptance_fails_closed_on_expansion_invariant(
    tmp_path,
):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": "{}",
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )
    runtime_payload = _valid_skill_dependency_runtime_acceptance()
    runtime_payload["non_expansion_invariants"]["ordinary_user_multi_agent_allowed"] = True
    _write_skill_dependency_runtime_entry(tmp_path, runtime_payload=runtime_payload)

    readiness = build_skill_release_readiness(
        skills_root=skills_root,
        runtime_evidence_root=tmp_path / "docs" / "release-evidence" / "skill-release-runtime",
    )

    assert "skill_dependency_review_policy_runtime_acceptance" in readiness["open_gaps"]
    assert readiness["closed_runtime_gaps"] == []
    assert readiness["runtime_acceptance_evidence"] == {}


def test_skill_release_review_manifest_rejects_invalid_signed_package_evidence(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/ai-platform-signed-package-evidence.json": _signed_package_evidence(
                package_digest_sha256="sha256:placeholder",
                signature_artifact_ref="raw_storage_key://secret/package.sig",
            ),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/ai-platform-signed-package-evidence.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert readiness["status"] == "partial_blocked"
    assert skill["package_evidence"]["signed_package_evidence_files"] == []
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "signed_package_evidence_invalid_digest" in skill["release_review"]["evidence_file_errors"]
    assert "signed_package_evidence_forbidden_reference" in skill["release_review"]["evidence_file_errors"]
    assert "signed_package_or_sbom_evidence_missing" in skill["blockers"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "raw_storage_key" not in serialized
    assert "sha256:placeholder" not in serialized


def test_skill_release_review_manifest_rejects_path_like_artifact_signed_package_refs(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/ai-platform-signed-package-evidence.json": _signed_package_evidence(
                package_artifact_ref="artifact://../package.tgz",
                signature_artifact_ref="artifact://C:/Users/release/package.sig",
            ),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/ai-platform-signed-package-evidence.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["package_evidence"]["signed_package_evidence_files"] == []
    assert "signed_package_evidence_forbidden_reference" in skill["release_review"]["evidence_file_errors"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "../package.tgz" not in serialized
    assert "C:/Users" not in serialized


def test_skill_release_review_manifest_does_not_accept_raw_attestation_as_signed_package_wrapper(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/cosign.bundle": "{}",
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/cosign.bundle"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["package_evidence"]["signed_package_evidence_files"] == []
    assert "sbom_or_signed_package_evidence_file_unmatched" in skill["release_review"]["evidence_file_errors"]
    assert "signed_package_or_sbom_evidence_missing" in skill["blockers"]


def test_skill_release_review_manifest_requires_final_signed_package_review_status(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/ai-platform-signed-package-evidence.json": _signed_package_evidence(
                review_status="approved_for_operator_review",
            ),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/ai-platform-signed-package-evidence.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert "signed_package_evidence_invalid_review_status" in skill["release_review"]["evidence_file_errors"]


def test_skill_release_review_manifest_uses_later_valid_review_when_earlier_file_is_invalid(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": "{}",
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["<sbom.json>"],
                    "license_policy": [],
                    "vulnerability_scan": [],
                },
            ),
            "skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    assert readiness["status"] == "partial_blocked"
    assert readiness["open_gaps"] == [
        "skill_dependency_review_policy_runtime_acceptance",
        *_DASHBOARD_GAPS,
    ]
    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "passed"
    assert skill["release_review"]["evidence_files_verified"] is True
    assert skill["blockers"] == []


def test_skill_release_review_manifest_requires_exact_boolean_true_review_flags(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": "{}",
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
                sbom_reviewed="false",
                license_policy_reviewed="yes",
                vulnerability_reviewed=1,
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert readiness["status"] == "partial_blocked"
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "review_flags_missing_or_invalid" in skill["release_review"]["review_flag_errors"]
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]


def test_skill_release_review_manifest_rejects_secret_like_actual_evidence_paths(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "token=secret/sbom.json": "{}",
            "private/.env/LICENSE": "reviewed license text",
            "secret-folder/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["sbom.json"],
                    "license_policy": ["LICENSE"],
                    "vulnerability_scan": ["npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert readiness["status"] == "partial_blocked"
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "sbom_or_signed_package_evidence_file_forbidden_actual_path" in skill["release_review"][
        "evidence_file_errors"
    ]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "token=secret" not in serialized
    assert "private/.env" not in serialized
    assert "secret-folder" not in serialized


def test_skill_release_review_manifest_rejects_placeholder_actual_evidence_paths(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "placeholder/sbom.json": "{}",
            "todo/LICENSE": "reviewed license text",
            "replace-me/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["sbom.json"],
                    "license_policy": ["LICENSE"],
                    "vulnerability_scan": ["npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert readiness["status"] == "partial_blocked"
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "sbom_or_signed_package_evidence_file_placeholder_actual_path" in skill["release_review"][
        "evidence_file_errors"
    ]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "placeholder/sbom.json" not in serialized
    assert "todo/LICENSE" not in serialized
    assert "replace-me/npm-audit.json" not in serialized


def test_skill_release_review_manifest_rejects_runtime_bytecode_entries_in_sbom(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": json.dumps(
                {
                    "schema_version": "ai-platform.skill-release-source-sbom.v1",
                    "components": [
                        {"type": "file", "name": "scripts/run_generation_pipeline.py"},
                        {"type": "file", "name": "__pycache__/run_generation_pipeline.cpython-313.pyc"},
                    ],
                },
                ensure_ascii=False,
            ),
            "legal/LICENSE": "reviewed license text",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert "sbom_or_signed_package_evidence_file_runtime_bytecode_content" in skill["release_review"][
        "evidence_file_errors"
    ]


def test_skill_release_review_manifest_rejects_personal_machine_paths_in_reviewed_evidence(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "general-chat",
        "Default chat capability.",
        {
            "evidence/sbom.json": "{}",
            "legal/LICENSE": "Reviewed on C:/Users/release-admin/Desktop/private-builds/license-check.txt",
            "security/npm-audit.json": "{}",
            "ai-platform-skill-release-review.json": _passed_review_manifest(
                "general-chat",
                {
                    "sbom_or_signed_package": ["evidence/sbom.json"],
                    "license_policy": ["legal/LICENSE"],
                    "vulnerability_scan": ["security/npm-audit.json"],
                },
            ),
        },
    )

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert "license_policy_evidence_file_personal_machine_path_content" in skill["release_review"][
        "evidence_file_errors"
    ]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert "C:/Users/release-admin/Desktop/private-builds/license-check.txt" not in serialized


def test_skill_release_evidence_scaffold_records_external_evidence_without_clearing_gate(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"

    scaffold = write_skill_release_evidence_scaffold(
        skills_root=skills_root,
        evidence_root=evidence_root,
        skill_id="general-chat",
        generated_at="2026-06-12T10:00:00Z",
    )

    assert scaffold["schema_version"] == "ai-platform.skill-release-evidence-scaffold.v1"
    assert scaffold["status"] == "pending_review"
    assert scaffold["skill_id"] == "general-chat"
    assert scaffold["does_not_close_gate_by_itself"] is True
    assert scaffold["written_files"] == [
        "external-release-evidence/general-chat/ai-platform-skill-release-review.json",
        "external-release-evidence/general-chat/sbom.json",
        "external-release-evidence/general-chat/third-party-notices.txt",
        "external-release-evidence/general-chat/vulnerability-report.json",
    ]

    skill_evidence_dir = evidence_root / "general-chat"
    review = json.loads(
        (skill_evidence_dir / "ai-platform-skill-release-review.json").read_text(encoding="utf-8")
    )
    assert review["status"] == "pending"
    assert review["sbom_reviewed"] is False
    assert review["license_policy_reviewed"] is False
    assert review["vulnerability_reviewed"] is False
    assert review["evidence_files"] == {
        "sbom_or_signed_package": ["external-release-evidence/general-chat/sbom.json"],
        "license_policy": ["external-release-evidence/general-chat/third-party-notices.txt"],
        "vulnerability_scan": ["external-release-evidence/general-chat/vulnerability-report.json"],
    }

    readiness = build_skill_release_readiness(
        skills_root=skills_root,
        skill_release_evidence_root=evidence_root,
    )

    assert readiness["status"] == "partial_blocked"
    assert readiness["summary"]["skills_with_sbom_evidence"] == 1
    assert readiness["summary"]["skills_with_license_evidence"] == 1
    assert readiness["summary"]["skills_with_vulnerability_evidence"] == 1
    assert "signed_skill_package_or_sbom_release_gate" in readiness["open_gaps"]
    assert "dependency_vulnerability_or_license_policy" in readiness["open_gaps"]
    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "pending_review"
    assert skill["release_review"]["evidence_files_verified"] is True
    assert skill["release_review"]["invalid_files"] == []
    assert skill["release_review"]["evidence_file_errors"] == []
    assert skill["release_review"]["review_flag_errors"] == ["review_flags_missing_or_invalid"]
    assert "signed_package_or_sbom_evidence_missing" not in skill["blockers"]
    assert "dependency_license_policy_evidence_missing" not in skill["blockers"]
    assert "dependency_vulnerability_evidence_missing" not in skill["blockers"]
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "token=secret" not in serialized


def test_skill_release_readiness_exposes_safe_operator_evidence_plan_for_blocked_skill(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")

    readiness = build_skill_release_readiness(skills_root=skills_root)

    skill = readiness["skills"][0]
    plan = skill["operator_evidence_plan"]
    assert plan == {
        "schema_version": "ai-platform.skill-release-operator-evidence-plan.v1",
        "status": "evidence_required",
        "skill_id": "general-chat",
        "missing_evidence_categories": [
            "sbom_or_signed_package",
            "license_policy",
            "vulnerability_scan",
        ],
        "missing_review_flags": [
            "sbom_reviewed",
            "license_policy_reviewed",
            "vulnerability_reviewed",
        ],
        "recommended_commands": [
            (
                "python tools/skill_release_readiness.py --skill-id general-chat "
                "--write-evidence-scaffold --format json"
            ),
            (
                "python tools/skill_release_readiness.py --skill-id general-chat "
                "--review-template --format json"
            ),
        ],
        "evidence_root": "docs/release-evidence/skill-release",
        "does_not_close_g6": True,
        "does_not_close_gate_by_itself": True,
    }
    serialized = json.dumps(plan, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "storage_key" not in serialized
    assert "token=secret" not in serialized


def test_skill_release_readiness_operator_plan_tracks_pending_review_flags(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"

    write_skill_release_evidence_scaffold(
        skills_root=skills_root,
        evidence_root=evidence_root,
        skill_id="general-chat",
        generated_at="2026-06-12T10:00:00Z",
    )

    readiness = build_skill_release_readiness(
        skills_root=skills_root,
        skill_release_evidence_root=evidence_root,
    )

    skill = readiness["skills"][0]
    plan = skill["operator_evidence_plan"]
    assert plan["missing_evidence_categories"] == []
    assert plan["missing_review_flags"] == [
        "sbom_reviewed",
        "license_policy_reviewed",
        "vulnerability_reviewed",
    ]
    assert plan["status"] == "review_required"
    assert plan["does_not_close_g6"] is True
    assert "signed_package_or_sbom_evidence_missing" not in skill["blockers"]
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]


def test_skill_release_review_manifest_rejects_pending_scaffold_evidence_even_with_review_flags(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    write_skill_release_evidence_scaffold(
        skills_root=skills_root,
        evidence_root=evidence_root,
        skill_id="general-chat",
        generated_at="2026-06-12T10:00:00Z",
    )
    review_path = evidence_root / "general-chat" / "ai-platform-skill-release-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["status"] = "passed"
    review["reviewer"] = "release-admin"
    review["sbom_reviewed"] = True
    review["license_policy_reviewed"] = True
    review["vulnerability_reviewed"] = True
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    readiness = build_skill_release_readiness(
        skills_root=skills_root,
        skill_release_evidence_root=evidence_root,
    )

    skill = readiness["skills"][0]
    assert readiness["status"] == "partial_blocked"
    assert "signed_skill_package_or_sbom_release_gate" in readiness["open_gaps"]
    assert "dependency_vulnerability_or_license_policy" in readiness["open_gaps"]
    assert skill["release_review"]["status"] == "invalid_or_incomplete"
    assert skill["release_review"]["evidence_files_verified"] is False
    assert "sbom_or_signed_package_evidence_not_reviewed" in skill["release_review"]["evidence_file_errors"]
    assert "license_policy_evidence_not_reviewed" in skill["release_review"]["evidence_file_errors"]
    assert "vulnerability_scan_evidence_not_reviewed" in skill["release_review"]["evidence_file_errors"]
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert "dependency_license_policy_review_not_verified" in skill["blockers"]
    assert "dependency_vulnerability_review_not_verified" in skill["blockers"]


def test_skill_release_readiness_cli_can_write_external_evidence_scaffold(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"

    result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_readiness.py",
            "--skills-root",
            str(skills_root),
            "--evidence-root",
            str(evidence_root),
            "--write-evidence-scaffold",
            "--skill-id",
            "general-chat",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.skill-release-evidence-scaffold.v1"
    assert payload["status"] == "pending_review"
    assert (evidence_root / "general-chat" / "sbom.json").is_file()
    assert (evidence_root / "general-chat" / "third-party-notices.txt").is_file()
    assert (evidence_root / "general-chat" / "vulnerability-report.json").is_file()
    assert (evidence_root / "general-chat" / "ai-platform-skill-release-review.json").is_file()

    readiness_result = subprocess.run(
        [
            sys.executable,
            "tools/skill_release_readiness.py",
            "--skills-root",
            str(skills_root),
            "--evidence-root",
            str(evidence_root),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    readiness = json.loads(readiness_result.stdout)
    skill = readiness["skills"][0]
    assert skill["skill_id"] == "general-chat"
    assert "signed_package_or_sbom_evidence_missing" not in skill["blockers"]
    assert "signed_package_or_sbom_review_not_verified" in skill["blockers"]
    assert str(tmp_path) not in readiness_result.stdout


def test_skill_release_evidence_scaffold_rejects_invalid_or_unknown_skill_id(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")

    for skill_id, expected in (
        ("../general-chat", "Invalid skill_id"),
        ("unknown-skill", "Unknown skill_id"),
    ):
        try:
            write_skill_release_evidence_scaffold(
                skills_root=skills_root,
                evidence_root=tmp_path / "release-evidence" / "skill-release",
                skill_id=skill_id,
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"expected {skill_id} to be rejected")


def test_skill_release_evidence_scaffold_refuses_to_overwrite_existing_evidence(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"

    write_skill_release_evidence_scaffold(
        skills_root=skills_root,
        evidence_root=evidence_root,
        skill_id="general-chat",
        generated_at="2026-06-12T10:00:00Z",
    )

    try:
        write_skill_release_evidence_scaffold(
            skills_root=skills_root,
            evidence_root=evidence_root,
            skill_id="general-chat",
            generated_at="2026-06-12T10:01:00Z",
        )
    except FileExistsError as exc:
        assert "skill_release_evidence_scaffold_exists" in str(exc)
    else:
        raise AssertionError("expected scaffold generation to refuse overwriting existing evidence")

    review = json.loads(
        (evidence_root / "general-chat" / "ai-platform-skill-release-review.json").read_text(encoding="utf-8")
    )
    assert review["reviewed_at"] == "2026-06-12T10:00:00Z"


def test_skill_release_readiness_cli_refuses_to_overwrite_external_evidence_scaffold(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "general-chat", "Default chat capability.")
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    command = [
        sys.executable,
        "tools/skill_release_readiness.py",
        "--skills-root",
        str(skills_root),
        "--evidence-root",
        str(evidence_root),
        "--write-evidence-scaffold",
        "--skill-id",
        "general-chat",
        "--format",
        "json",
    ]

    subprocess.run(command, check=True, capture_output=True, text=True)
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 2
    assert "skill_release_evidence_scaffold_exists" in result.stderr
    assert "Traceback" not in result.stderr

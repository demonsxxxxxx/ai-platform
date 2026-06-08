import json
import os
import subprocess
import sys

from app.skills.release_readiness import (
    build_skill_release_readiness,
    build_skill_release_review_template,
    render_skill_release_readiness_markdown,
)


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
        "skills_with_license_evidence": 0,
        "skills_with_vulnerability_evidence": 0,
    }
    assert readiness["open_gaps"] == [
        "signed_skill_package_or_sbom_release_gate",
        "dependency_vulnerability_or_license_policy",
        "skill_dependency_review_policy_runtime_acceptance",
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
    assert "contract_only_not_runtime_satisfied" in markdown
    assert "general-chat" in markdown
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
    assert payload["skills"][0]["skill_id"] == "general-chat"
    assert str(tmp_path) not in result.stdout
    assert "token=secret" not in result.stdout
    assert ".claude/skills" not in result.stdout


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
    assert readiness["open_gaps"] == ["skill_dependency_review_policy_runtime_acceptance"]
    skill = readiness["skills"][0]
    assert skill["release_review"]["status"] == "passed"
    assert skill["release_review"]["evidence_files_verified"] is True
    assert skill["blockers"] == []


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
    assert readiness["open_gaps"] == ["skill_dependency_review_policy_runtime_acceptance"]
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

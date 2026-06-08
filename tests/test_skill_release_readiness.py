import json
import os
import subprocess
import sys

from app.skills.release_readiness import build_skill_release_readiness, render_skill_release_readiness_markdown


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
    ]

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

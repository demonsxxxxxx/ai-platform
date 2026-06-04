from pathlib import Path

import pytest

from app.skills.registry import BuiltinSkill
from app.skills.stager import SkillStager


def skill(source: Path, name: str = "qa-file-reviewer") -> BuiltinSkill:
    return BuiltinSkill(
        name=name,
        description="Review Word documents.",
        path=source,
        version="hash",
        source={"kind": "builtin"},
        entry={"kind": "filesystem", "path": str(source)},
    )


def symlink_or_skip(target, link):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation not available: {exc}")


def test_stager_copies_skill_to_claude_skills_directory(tmp_path):
    source = tmp_path / "source" / "qa-file-reviewer"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: Review Word documents.\n---\n",
        encoding="utf-8",
    )
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    staged = SkillStager().stage_skills(workspace=workspace, skills=[skill(source)])

    assert staged == ["qa-file-reviewer"]
    assert (workspace / ".claude" / "skills" / "qa-file-reviewer" / "SKILL.md").is_file()
    assert (workspace / ".claude" / "skills" / "qa-file-reviewer" / "scripts" / "run.py").read_text(
        encoding="utf-8"
    ) == "print('ok')\n"


def test_stager_replaces_existing_staged_skill(tmp_path):
    source = tmp_path / "source" / "qa-file-reviewer"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: Review Word documents.\n---\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    stale = workspace / ".claude" / "skills" / "qa-file-reviewer" / "old.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")

    SkillStager().stage_skills(workspace=workspace, skills=[skill(source)])

    assert not stale.exists()
    assert (workspace / ".claude" / "skills" / "qa-file-reviewer" / "SKILL.md").is_file()


def test_stager_rejects_workspace_escape_subdir():
    with pytest.raises(ValueError, match="inside the run workspace"):
        SkillStager("../skills")


def test_stager_rejects_skill_without_skill_markdown(tmp_path):
    source = tmp_path / "source" / "qa-file-reviewer"
    source.mkdir(parents=True)

    with pytest.raises(ValueError, match="without SKILL.md"):
        SkillStager().stage_skills(workspace=tmp_path / "workspace", skills=[skill(source)])


def test_stager_rejects_symlinked_staging_parent(tmp_path):
    source = tmp_path / "source" / "qa-file-reviewer"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: Review Word documents.\n---\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.mkdir()
    symlink_or_skip(outside, workspace / ".claude")

    with pytest.raises(ValueError, match="run workspace"):
        SkillStager().stage_skills(workspace=workspace, skills=[skill(source)])

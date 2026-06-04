import pytest
from pathlib import Path

from app.skills.registry import BuiltinSkillRegistry, parse_skill_markdown_front_matter


def test_parse_skill_front_matter_description():
    metadata = parse_skill_markdown_front_matter(
        """---
name: qa-file-reviewer
description: Use when reviewing Word documents.
---

# QA File Reviewer
"""
    )

    assert metadata["name"] == "qa-file-reviewer"
    assert metadata["description"] == "Use when reviewing Word documents."


def test_parse_skill_front_matter_handles_bom_and_crlf():
    metadata = parse_skill_markdown_front_matter(
        "\ufeff---\r\nname: minimax-docx\r\ndescription: Word document generation.\r\n---\r\n"
    )

    assert metadata["name"] == "minimax-docx"
    assert metadata["description"] == "Word document generation."


def test_builtin_registry_discovers_skill_from_platform_root(tmp_path):
    skill_dir = tmp_path / "qa-file-reviewer"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: qa-file-reviewer
description: Use when the user asks to review a Word document.
---

# QA File Reviewer
""",
        encoding="utf-8",
    )

    registry = BuiltinSkillRegistry(skills_root=tmp_path)
    skills = registry.list_builtin_skills()

    assert [skill.name for skill in skills] == ["qa-file-reviewer"]
    assert skills[0].description == "Use when the user asks to review a Word document."
    assert skills[0].source["kind"] == "builtin"
    assert str(skills[0].path).endswith("qa-file-reviewer")
    assert len(skills[0].version) == 64


def test_shipped_platform_skills_include_general_chat():
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    skills = BuiltinSkillRegistry(skills_root=skills_root).list_builtin_skills()
    skill_names = {skill.name for skill in skills}
    descriptions = {skill.name: skill.description for skill in skills}

    assert "general-chat" in skill_names
    assert "baoyu-translate" in skill_names
    assert "ragflow-knowledge-search" in skill_names
    assert "read-only company SOP and policy knowledge" in descriptions["ragflow-knowledge-search"]


def test_builtin_registry_rejects_missing_skill_markdown(tmp_path):
    (tmp_path / "broken-skill").mkdir()
    registry = BuiltinSkillRegistry(skills_root=tmp_path)

    with pytest.raises(ValueError, match="missing SKILL.md"):
        registry.list_builtin_skills()


def test_builtin_registry_rejects_manifest_name_mismatch(tmp_path):
    skill_dir = tmp_path / "qa-file-reviewer"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: wrong-name
description: Use when the user asks to review a Word document.
---
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name mismatch"):
        BuiltinSkillRegistry(skills_root=tmp_path).list_builtin_skills()


def test_builtin_registry_rejects_missing_description(tmp_path):
    skill_dir = tmp_path / "qa-file-reviewer"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: qa-file-reviewer
---
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing description"):
        BuiltinSkillRegistry(skills_root=tmp_path).list_builtin_skills()

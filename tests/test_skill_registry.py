import pytest

from app.bootstrap.skills import configure_skill_markdown
from app.skills.application import skill_markdown
from app.skills.application.skill_markdown import parse_skill_markdown_front_matter
from app.skills.infrastructure.skill_markdown_yaml import load_skill_markdown_metadata
from app.skills.registry import BuiltinSkillRegistry


@pytest.fixture(autouse=True)
def configured_skill_markdown_loader(monkeypatch):
    monkeypatch.setattr(skill_markdown, "_yaml_metadata_loader", load_skill_markdown_metadata)


def test_parse_skill_front_matter_requires_loader(monkeypatch):
    monkeypatch.setattr(skill_markdown, "_yaml_metadata_loader", None)
    with pytest.raises(RuntimeError, match="skill_markdown_loader_not_configured"):
        parse_skill_markdown_front_matter("---\nname: qa-file-reviewer\n---\n")


def test_skill_markdown_loader_bootstrap(monkeypatch):
    monkeypatch.setattr(skill_markdown, "_yaml_metadata_loader", None)
    configure_skill_markdown()
    assert parse_skill_markdown_front_matter(
        "---\nname: qa-file-reviewer\ndescription: >-\n  Review files.\n---\n"
    )["description"] == "Review files."


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


@pytest.mark.parametrize(
    ("indicator", "expected"),
    [(">-", "First line second line"), ("|", "First line\nsecond line")],
)
def test_parse_skill_front_matter_multiline_description(indicator, expected):
    metadata = parse_skill_markdown_front_matter(
        f"---\nname: ctd-review\ndescription: {indicator}\n  First line\n  second line\n---\n"
    )

    assert metadata["description"] == expected


def test_parse_skill_front_matter_quoted_description_with_colon():
    metadata = parse_skill_markdown_front_matter(
        '---\nname: ctd-review\ndescription: "Review: Word documents"\n---\n'
    )

    assert metadata["description"] == "Review: Word documents"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_parse_skill_front_matter_preserves_inline_delimiters(newline):
    content = newline.join([
        "---", "name: valid---skill", "description: before---after", "---", "# Body",
    ])

    assert parse_skill_markdown_front_matter(content) == {
        "name": "valid---skill", "description": "before---after",
    }


@pytest.mark.parametrize("closing", ["---", "---\n", "--- \t\n"])
def test_parse_skill_front_matter_accepts_closing_delimiter_line(closing):
    assert parse_skill_markdown_front_matter(
        "---\nname: valid-skill\n" + closing
    ) == {"name": "valid-skill"}


def test_parse_skill_front_matter_preserves_indented_delimiter_in_block_scalar():
    assert parse_skill_markdown_front_matter(
        "---\nname: valid-skill\ndescription: |\n  before\n  ---\n  after\n---\n"
    )["description"] == "before\n---\nafter"


@pytest.mark.parametrize("content", [
    "---",
    "---\n---\n",
    "name: valid-skill\n---\n",
    "prefix---\nname: valid-skill\n---\n",
    "---inline\nname: valid-skill\n---\n",
    "---\nname: valid---skill\n",
    "---\nname: valid-skill\n---suffix\n",
    "---\nname: valid-skill\n  ---\n",
])
def test_parse_skill_front_matter_requires_complete_delimiter_lines(content):
    assert parse_skill_markdown_front_matter(content) == {}


def test_parse_skill_front_matter_does_not_parse_markdown_after_closing_delimiter():
    assert parse_skill_markdown_front_matter(
        "---\nname: valid-skill\n---\ndescription: [invalid\n---\n"
    ) == {"name": "valid-skill"}


def test_parse_skill_front_matter_rejects_invalid_yaml():
    with pytest.raises(ValueError, match="skill_front_matter_invalid_yaml"):
        parse_skill_markdown_front_matter("---\nname: ctd-review\ndescription: [invalid\n---\n")


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

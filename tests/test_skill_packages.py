import base64
import io
import zipfile

import pytest

from app.skills.packages import parse_skill_package_zip


def package_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def skill_md(name: str = "qa-file-reviewer", description: str = "Review Word documents.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def test_parse_skill_package_zip_requires_skill_md_and_matching_name():
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "references/guide.md": "review guide",
        }
    )

    parsed = parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")

    assert parsed.skill_id == "qa-file-reviewer"
    assert parsed.description == "Review Word documents."
    assert len(parsed.content_hash) == 64
    assert parsed.size_bytes == len(skill_md().encode("utf-8")) + len("review guide".encode("utf-8"))
    assert [item["relative_path"] for item in parsed.files] == ["SKILL.md", "references/guide.md"]
    assert base64.b64decode(parsed.files[0]["content_base64"]) == skill_md().encode("utf-8")


def test_parse_skill_package_zip_rejects_path_escape():
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "../evil.txt": "evil",
        }
    )

    with pytest.raises(ValueError, match="skill_package_path_escape"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_missing_description():
    content = package_zip({"SKILL.md": "---\nname: qa-file-reviewer\n---\n\n# Skill\n"})

    with pytest.raises(ValueError, match="skill_package_description_required"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_name_mismatch():
    content = package_zip({"SKILL.md": skill_md(name="other-skill")})

    with pytest.raises(ValueError, match="skill_package_name_mismatch"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_invalid_utf8_skill_md():
    content = package_zip({"SKILL.md": b"\xff\xfe\x00"})

    with pytest.raises(ValueError, match="skill_package_invalid_utf8"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_oversized_archive(monkeypatch):
    skill_content = skill_md()
    content = package_zip({"SKILL.md": skill_content})
    assert len(content) > len(skill_content.encode("utf-8"))
    monkeypatch.setattr("app.skills.packages.MAX_SKILL_PACKAGE_TOTAL_BYTES", len(skill_content.encode("utf-8")) + 1)

    with pytest.raises(ValueError, match="skill_package_too_large"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_oversized_file(monkeypatch):
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "large.bin": b"123456789",
        }
    )
    monkeypatch.setattr("app.skills.packages.MAX_SKILL_PACKAGE_FILE_BYTES", 8)

    with pytest.raises(ValueError, match="skill_package_file_too_large"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")

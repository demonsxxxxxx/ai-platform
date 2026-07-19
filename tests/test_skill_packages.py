import base64
import io
import stat
import zipfile

import pytest

from app.skills import packages as skill_packages
from app.skills.packages import (
    build_skill_package_contract,
    parse_skill_package_zip,
    validate_skill_package_contract,
)


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


def test_parse_skill_package_zip_can_infer_skill_name():
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "references/guide.md": "review guide",
        }
    )

    parsed = parse_skill_package_zip(content)

    assert parsed.skill_id == "qa-file-reviewer"
    assert parsed.description == "Review Word documents."
    assert [item["relative_path"] for item in parsed.files] == ["SKILL.md", "references/guide.md"]


def test_parse_skill_package_zip_accepts_one_wrapped_skill_directory():
    content = package_zip(
        {
            "qa-file-reviewer/SKILL.md": skill_md(),
            "qa-file-reviewer/references/guide.md": "review guide",
        }
    )

    parsed = parse_skill_package_zip(content)

    assert parsed.skill_id == "qa-file-reviewer"
    assert [item["relative_path"] for item in parsed.files] == ["SKILL.md", "references/guide.md"]


def test_parse_skill_package_zip_rejects_ambiguous_multiple_wrapped_skills():
    content = package_zip(
        {
            "skill-a/SKILL.md": skill_md(name="skill-a"),
            "skill-b/SKILL.md": skill_md(name="skill-b"),
        }
    )

    with pytest.raises(ValueError, match="skill_package_multiple_skills_not_supported"):
        parse_skill_package_zip(content)


def test_parse_skill_package_zip_rejects_files_outside_the_single_wrapped_root():
    content = package_zip(
        {
            "qa-file-reviewer/SKILL.md": skill_md(),
            "outside.txt": "must not be silently imported",
        }
    )

    with pytest.raises(ValueError, match="skill_package_mixed_root"):
        parse_skill_package_zip(content)


def test_parse_skill_package_zip_rejects_path_escape():
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "../evil.txt": "evil",
        }
    )

    with pytest.raises(ValueError, match="skill_package_path_escape"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_casefold_duplicate_paths():
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "references/Guide.md": "first",
            "references/guide.MD": "second",
        }
    )

    with pytest.raises(ValueError, match="skill_package_duplicate_path"):
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


def test_parse_skill_package_zip_rejects_excessive_entry_count(monkeypatch):
    content = package_zip(
        {
            "SKILL.md": skill_md(),
            "references/guide.md": "review guide",
        }
    )
    monkeypatch.setattr("app.skills.packages.MAX_SKILL_PACKAGE_FILES", 1)

    with pytest.raises(ValueError, match="skill_package_too_many_files"):
        parse_skill_package_zip(content, expected_skill_id="qa-file-reviewer")


def test_parse_skill_package_zip_rejects_unsupported_compression_method():
    buffer = io.BytesIO()
    info = zipfile.ZipInfo("SKILL.md")
    info.compress_type = zipfile.ZIP_BZIP2
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, skill_md())

    with pytest.raises(ValueError, match="skill_package_unsupported_compression"):
        parse_skill_package_zip(buffer.getvalue(), expected_skill_id="qa-file-reviewer")


@pytest.mark.parametrize(
    ("info", "expected_error"),
    [
        (zipfile.ZipInfo("SKILL.md"), "skill_package_encrypted_entry"),
        (zipfile.ZipInfo("scripts/run.sh"), "skill_package_non_regular_entry"),
    ],
)
def test_skill_package_entry_validation_rejects_encrypted_and_non_regular_entries(info, expected_error):
    if expected_error == "skill_package_encrypted_entry":
        info.flag_bits = 0x1
    else:
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16

    with pytest.raises(ValueError, match=expected_error):
        skill_packages._validate_zip_entry(info)


def test_parse_skill_package_zip_preserves_native_skill_scripts_and_assets_byte_for_byte():
    script = b"#!/bin/sh\nprintf '\\x00native artifact'\n"
    asset = b"\x89PNG\r\n\x1a\nasset-bytes"
    parsed = parse_skill_package_zip(
        package_zip(
            {
                "native-review/SKILL.md": skill_md(name="native-review", description="Review with local tools."),
                "native-review/scripts/run-review.sh": script,
                "native-review/assets/template.bin": asset,
                "native-review/references/guide.md": "Use the packaged command.",
            }
        ),
        expected_skill_id="native-review",
    )

    files = {item["relative_path"]: base64.b64decode(item["content_base64"]) for item in parsed.files}
    assert files["scripts/run-review.sh"] == script
    assert files["assets/template.bin"] == asset


def test_skill_package_contract_round_trips_safe_upload_metadata():
    parsed = parse_skill_package_zip(
        package_zip(
            {
                "SKILL.md": skill_md(),
                "references/guide.md": "review guide",
                "sbom.json": "{}",
                "third-party-notices.txt": "none",
                "vulnerability-report.json": "{}",
            }
        ),
        expected_skill_id="qa-file-reviewer",
    )

    contract = build_skill_package_contract(
        parsed,
        package_sha256="zip-sha256",
        storage_key=f"skills/{parsed.skill_id}/versions/{parsed.content_hash}/package.zip",
        uploaded_by="dev-admin",
    )

    assert contract == validate_skill_package_contract(
        contract,
        skill_id="qa-file-reviewer",
        content_hash=parsed.content_hash,
    )
    assert contract["schema_version"] == "ai-platform.skill-package-contract.v1"
    assert contract["skill_id"] == "qa-file-reviewer"
    assert contract["version"] == parsed.content_hash
    assert contract["content_hash"] == parsed.content_hash
    assert contract["package_sha256"] == "zip-sha256"
    assert contract["storage_key"] == f"skills/{parsed.skill_id}/versions/{parsed.content_hash}/package.zip"
    assert contract["uploaded_by"] == "dev-admin"
    assert contract["file_count"] == 5
    assert contract["size_bytes"] == parsed.size_bytes
    assert contract["evidence_files"] == {
        "sbom_or_signed_package": ["sbom.json"],
        "license_policy": ["third-party-notices.txt"],
        "vulnerability_scan": ["vulnerability-report.json"],
    }


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ({"skill_id": "other-skill"}, "skill_package_contract_skill_mismatch"),
        ({"content_hash": "other-hash"}, "skill_package_contract_hash_mismatch"),
        ({"package_sha256": ""}, "skill_package_contract_package_sha256_required"),
        ({"storage_key": "../package.zip"}, "skill_package_contract_storage_key_invalid"),
    ],
)
def test_validate_skill_package_contract_rejects_mismatched_or_unsafe_metadata(mutation, expected_error):
    parsed = parse_skill_package_zip(package_zip({"SKILL.md": skill_md()}), expected_skill_id="qa-file-reviewer")
    contract = build_skill_package_contract(
        parsed,
        package_sha256="zip-sha256",
        storage_key=f"skills/{parsed.skill_id}/versions/{parsed.content_hash}/package.zip",
        uploaded_by="dev-admin",
    )
    contract.update(mutation)

    with pytest.raises(ValueError, match=expected_error):
        validate_skill_package_contract(
            contract,
            skill_id="qa-file-reviewer",
            content_hash=parsed.content_hash,
        )

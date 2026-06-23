import io
import zipfile

import pytest

from app.skills.github_import import GitHubImportError, discover_github_skill_packages, github_repo_archive_url


def archive_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def skill_md(name: str, description: str = "Imported skill.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def test_github_repo_archive_url_accepts_public_repo_and_branch_path():
    repo_url, archive_url, branch = github_repo_archive_url("https://github.com/example/skills", "feature/imports")

    assert repo_url == "https://github.com/example/skills"
    assert archive_url == "https://github.com/example/skills/archive/refs/heads/feature/imports.zip"
    assert branch == "feature/imports"


def test_github_repo_archive_url_rejects_non_github_or_escaping_branch():
    with pytest.raises(GitHubImportError) as non_github:
        github_repo_archive_url("https://example.com/example/skills", "main")
    assert non_github.value.status_code == 400
    assert non_github.value.detail == "github_import_repo_url_unsupported"

    with pytest.raises(GitHubImportError) as escaping_branch:
        github_repo_archive_url("https://github.com/example/skills", "../main")
    assert escaping_branch.value.status_code == 400
    assert escaping_branch.value.detail == "github_import_branch_unsupported"

    with pytest.raises(GitHubImportError) as reserved_branch:
        github_repo_archive_url("https://github.com/example/skills", "main?x=1")
    assert reserved_branch.value.status_code == 400
    assert reserved_branch.value.detail == "github_import_branch_unsupported"


def test_discover_github_skill_packages_strips_archive_root_and_keeps_package_files():
    content = archive_zip(
        {
            "repo-main/skills/qa-file-reviewer/references/first.md": "first file",
            "repo-main/skills/qa-file-reviewer/SKILL.md": skill_md("qa-file-reviewer"),
            "repo-main/skills/other-skill/SKILL.md": skill_md("other-skill"),
        }
    )

    packages = discover_github_skill_packages(content)

    assert [(item.path, item.package.skill_id) for item in packages] == [
        ("skills/other-skill", "other-skill"),
        ("skills/qa-file-reviewer", "qa-file-reviewer"),
    ]
    qa_package = next(item.package for item in packages if item.package.skill_id == "qa-file-reviewer")
    assert [item["relative_path"] for item in qa_package.files] == ["SKILL.md", "references/first.md"]


def test_discover_github_skill_packages_rejects_duplicate_skill_ids():
    content = archive_zip(
        {
            "repo-main/skills/a/SKILL.md": skill_md("qa-file-reviewer", "First package."),
            "repo-main/skills/b/SKILL.md": skill_md("qa-file-reviewer", "Second package."),
        }
    )

    with pytest.raises(GitHubImportError) as exc:
        discover_github_skill_packages(content)
    assert exc.value.status_code == 400
    assert exc.value.detail == "github_import_duplicate_skill_id"


def test_discover_github_skill_packages_rejects_path_escape():
    content = archive_zip(
        {
            "repo-main/skills/qa-file-reviewer/SKILL.md": skill_md("qa-file-reviewer"),
            "../evil.md": "evil",
        }
    )

    with pytest.raises(GitHubImportError) as exc:
        discover_github_skill_packages(content)
    assert exc.value.status_code == 400
    assert exc.value.detail == "github_import_archive_path_escape"

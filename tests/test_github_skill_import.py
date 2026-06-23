import base64
import io
import zipfile

import httpx
import pytest

from app.skills.github_import import (
    GitHubImportError,
    discover_github_skill_packages,
    download_github_archive_from_api,
    github_repo_archive_url,
)
from app.skills.packages import MAX_SKILL_PACKAGE_TOTAL_BYTES


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
    assert archive_url == "https://codeload.github.com/example/skills/zip/refs/heads/feature/imports"
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


@pytest.mark.asyncio
async def test_download_github_archive_from_api_builds_parseable_skill_archive(monkeypatch):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/repos/example/skills/git/ref/heads/feature-branch":
            return httpx.Response(200, json={"object": {"sha": "commit-sha"}})
        if request.url.path == "/repos/example/skills/git/trees/commit-sha":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "skills/qa-file-reviewer/SKILL.md",
                            "type": "blob",
                            "sha": "skill-md-sha",
                            "size": 70,
                        },
                        {
                            "path": "skills/qa-file-reviewer/references/api.md",
                            "type": "blob",
                            "sha": "api-ref-sha",
                            "size": 9,
                        },
                    ],
                },
            )
        if request.url.path == "/repos/example/skills/git/blobs/skill-md-sha":
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(skill_md("qa-file-reviewer").encode()).decode()},
            )
        if request.url.path == "/repos/example/skills/git/blobs/api-ref-sha":
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(b"API guide").decode()},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    content = await download_github_archive_from_api("https://github.com/example/skills", "feature-branch")
    packages = discover_github_skill_packages(content)

    assert [(item.path, item.package.skill_id) for item in packages] == [
        ("skills/qa-file-reviewer", "qa-file-reviewer")
    ]
    assert [item["relative_path"] for item in packages[0].package.files] == ["SKILL.md", "references/api.md"]
    assert requested == [
        "https://api.github.com/repos/example/skills/git/ref/heads/feature-branch",
        "https://api.github.com/repos/example/skills/git/trees/commit-sha?recursive=1",
        "https://api.github.com/repos/example/skills/git/blobs/skill-md-sha",
        "https://api.github.com/repos/example/skills/git/blobs/api-ref-sha",
    ]


@pytest.mark.asyncio
async def test_download_github_archive_from_api_ignores_unrelated_large_tree_files(monkeypatch):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/repos/example/skills/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "commit-sha"}})
        if request.url.path == "/repos/example/skills/git/trees/commit-sha":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "docs/large.bin",
                            "type": "blob",
                            "sha": "large-sha",
                            "size": MAX_SKILL_PACKAGE_TOTAL_BYTES + 1,
                        },
                        {
                            "path": "skills/qa-file-reviewer/SKILL.md",
                            "type": "blob",
                            "sha": "skill-md-sha",
                            "size": 70,
                        },
                    ],
                },
            )
        if request.url.path == "/repos/example/skills/git/blobs/skill-md-sha":
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(skill_md("qa-file-reviewer").encode()).decode()},
            )
        if request.url.path == "/repos/example/skills/git/blobs/large-sha":
            raise AssertionError("unrelated large blob should not be downloaded")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    content = await download_github_archive_from_api("https://github.com/example/skills", "main")
    packages = discover_github_skill_packages(content)

    assert [(item.path, item.package.skill_id) for item in packages] == [
        ("skills/qa-file-reviewer", "qa-file-reviewer")
    ]
    assert "https://api.github.com/repos/example/skills/git/blobs/large-sha" not in requested


@pytest.mark.asyncio
async def test_download_github_archive_from_api_accepts_wrapped_base64_blob_content(monkeypatch):
    wrapped_skill_content = base64.b64encode(skill_md("qa-file-reviewer").encode()).decode() + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/example/skills/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "commit-sha"}})
        if request.url.path == "/repos/example/skills/git/trees/commit-sha":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "skills/qa-file-reviewer/SKILL.md",
                            "type": "blob",
                            "sha": "skill-md-sha",
                            "size": len(skill_md("qa-file-reviewer").encode()),
                        },
                    ],
                },
            )
        if request.url.path == "/repos/example/skills/git/blobs/skill-md-sha":
            return httpx.Response(200, json={"encoding": "base64", "content": wrapped_skill_content})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    content = await download_github_archive_from_api("https://github.com/example/skills", "main")
    packages = discover_github_skill_packages(content)

    assert [(item.path, item.package.skill_id) for item in packages] == [
        ("skills/qa-file-reviewer", "qa-file-reviewer")
    ]

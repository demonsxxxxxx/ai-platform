import json
from pathlib import Path
import subprocess
import sys

import yaml

from tools.release_authority import (
    ReleaseAuthorityError,
    assert_clean_commit,
    build_image_references,
    build_parity_report,
    preserve_dirty_source,
)


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.yml"
LEGACY_FRONTEND_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.frontend.yml"


def test_repo_local_compose_is_the_only_frontend_owner_and_binds_one_commit():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert "frontend" in services
    assert not LEGACY_FRONTEND_COMPOSE.exists()
    for service_name in ("api", "worker", "frontend"):
        labels = services[service_name]["labels"]
        assert labels["ai-platform.source-commit"] == "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}"
        assert labels["ai-platform.source-dirty"] == "false"
        assert labels["ai-platform.release-owner"] == "repo-local-compose"
        assert labels["ai-platform.release-role"] == service_name


def test_repo_local_compose_requires_immutable_backend_and_frontend_images():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["api"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert services["worker"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert services["frontend"]["image"] == "${AI_PLATFORM_FRONTEND_IMAGE:?set AI_PLATFORM_FRONTEND_IMAGE}"


def test_backend_and_frontend_images_publish_release_authority_labels():
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "web" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile, role in ((backend, "backend"), (frontend, "frontend")):
        assert "ARG AI_PLATFORM_BUILD_REPOSITORY=unknown" in dockerfile
        assert "LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
        assert 'LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"' in dockerfile
        assert "LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY" in dockerfile
        assert f"LABEL ai-platform.release-role={role}" in dockerfile


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "baseline")
    return _git(repo, "rev-parse", "HEAD")


def test_clean_commit_and_immutable_image_reference_contract(tmp_path):
    repo = tmp_path / "repo"
    commit = _init_repo(repo)

    assert assert_clean_commit(repo, commit) == commit
    assert build_image_references(commit) == {
        "backend": f"ai-platform:{commit}",
        "frontend": f"ai-platform-frontend:{commit}",
    }

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    try:
        assert_clean_commit(repo, commit)
    except ReleaseAuthorityError as exc:
        assert "dirty source is forbidden" in str(exc)
    else:
        raise AssertionError("dirty source must be rejected")


def test_preserve_dirty_source_writes_hashed_manifest_without_cleaning_repo(tmp_path):
    repo = tmp_path / "repo"
    commit = _init_repo(repo)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "notes.txt").write_text("preserve me\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")

    output = preserve_dirty_source(repo, tmp_path / "preserved")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    inventory = json.loads((output / "inventory.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "ai-platform.release-authority-preservation.v1"
    assert manifest["source_head"] == commit
    assert manifest["source_was_dirty"] is True
    assert manifest["artifacts"]["tracked.patch"]["sha256"]
    assert manifest["artifacts"]["untracked.tar"]["sha256"]
    env_record = next(item for item in inventory if item["path"] == ".env")
    assert env_record["content_preserved"] is False
    assert env_record["sha256"] is None
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (repo / "notes.txt").is_file()
    assert (repo / ".env").is_file()


def test_parity_report_rejects_manual_frontend_and_commit_mismatch():
    commit = "a" * 40
    source = {"commit": commit, "dirty": False}
    images = {
        "backend": {"id": "sha256:backend", "labels": {"ai-platform.source-commit": commit, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend"}},
    }
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml"}
    containers = {
        "api": {"image_id": "sha256:backend", "labels": {**common, "ai-platform.release-role": "api"}},
        "worker": {"image_id": "sha256:backend", "labels": {**common, "ai-platform.release-role": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "labels": {**common, "ai-platform.release-owner": "manual", "ai-platform.release-role": "frontend"}},
    }
    runtime = {"api_commit": commit, "worker_commit": commit, "frontend_commit": "b" * 40}

    report = build_parity_report(
        expected_commit=commit,
        source=source,
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir,
    )

    assert report["verified"] is False
    assert "frontend_container_not_repo_local_compose_owned" in report["mismatches"]
    assert "frontend_runtime_commit_mismatch" in report["mismatches"]


def test_parity_report_verifies_one_clean_repo_local_compose_commit():
    commit = "c" * 40
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    images = {
        "backend": {"id": "sha256:backend", "labels": {"ai-platform.source-commit": commit, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend"}},
    }
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml"}
    containers = {
        "api": {"image_id": "sha256:backend", "labels": {**common, "ai-platform.release-role": "api"}},
        "worker": {"image_id": "sha256:backend", "labels": {**common, "ai-platform.release-role": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "labels": {**common, "ai-platform.release-role": "frontend"}},
    }

    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images=images,
        containers=containers,
        runtime={"api_commit": commit, "worker_commit": commit, "frontend_commit": commit},
        expected_compose_dir=compose_dir,
    )

    assert report["verified"] is True
    assert report["mismatches"] == []


def test_release_authority_cli_exposes_preserve_deploy_and_verify_commands():
    result = subprocess.run(
        [sys.executable, "tools/release_authority.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "preserve-dirty" in result.stdout
    assert "deploy" in result.stdout
    assert "verify" in result.stdout

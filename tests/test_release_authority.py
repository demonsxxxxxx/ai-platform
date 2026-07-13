import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import yaml

import tools.release_authority as release_authority

from tools.release_authority import (
    ReleaseAuthorityError,
    assert_clean_commit,
    build_image_references,
    build_parity_report,
    collect_live_parity,
    deploy_clean_commit,
    preserve_dirty_source,
)


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.yml"
LEGACY_FRONTEND_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.frontend.yml"
AUTHORITATIVE_REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"


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
    for service_name in ("api", "worker"):
        assert services[service_name]["environment"]["AI_PLATFORM_RUNTIME_COMMIT"] == (
            "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}"
        )


def test_release_authority_rejects_non_authoritative_origin(monkeypatch, tmp_path):
    from tools.release_authority import authoritative_repository

    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: "https://example.invalid/fork.git\n",
    )

    try:
        authoritative_repository(tmp_path)
    except ReleaseAuthorityError as exc:
        assert "authoritative repository mismatch" in str(exc)
    else:
        raise AssertionError("a local origin rewrite must not redefine release authority")


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

    backend_stage = backend.split("FROM python:3.11-slim", 1)[1]
    assert "ARG AI_PLATFORM_BUILD_COMMIT=unknown" in backend_stage
    assert "ARG AI_PLATFORM_BUILD_DIRTY=unknown" in backend_stage
    assert "ARG AI_PLATFORM_BUILD_REPOSITORY=unknown" in backend_stage


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


def test_clean_commit_uses_git_porcelain_flag_supported_by_211(monkeypatch, tmp_path):
    commands: list[tuple[str, ...]] = []

    def fake_git(repo_root: Path, *args: str, text: bool = True):
        commands.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return "d" * 40 + "\n"
        if args[:2] == ("status", "--porcelain"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr("tools.release_authority._git", fake_git)

    assert assert_clean_commit(tmp_path, "d" * 40) == "d" * 40
    assert ("status", "--porcelain", "--untracked-files=all") in commands
    assert all("--porcelain=v1" not in args for args in commands)


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
    repository = AUTHORITATIVE_REPOSITORY
    source = {"commit": commit, "dirty": False}
    images = {
        "backend": {"id": "sha256:backend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml", "com.docker.compose.project": "ai-platform-phaseb", "com.docker.compose.oneoff": "False", "com.docker.compose.config-hash": "config-hash"}
    containers = {
        "api": {"image_id": "sha256:backend", "running": True, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
        "worker": {"image_id": "sha256:backend", "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "running": True, "labels": {**common, "ai-platform.release-owner": "manual", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    runtime = {
        "api_commit": commit,
        "api_health_status": "ok",
        "worker_commit": commit,
        "worker_running": True,
        "frontend_commit": "b" * 40,
    }

    report = build_parity_report(
        expected_commit=commit,
        source=source,
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is False
    assert "frontend_container_not_repo_local_compose_owned" in report["mismatches"]
    assert "frontend_runtime_commit_mismatch" in report["mismatches"]


def test_parity_report_verifies_one_clean_repo_local_compose_commit():
    commit = "c" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    images = {
        "backend": {"id": "sha256:backend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml", "com.docker.compose.project": "ai-platform-phaseb", "com.docker.compose.oneoff": "False", "com.docker.compose.config-hash": "config-hash"}
    containers = {
        "api": {"image_id": "sha256:backend", "running": True, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
        "worker": {"image_id": "sha256:backend", "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "running": True, "labels": {**common, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }

    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images=images,
        containers=containers,
        runtime={"api_commit": commit, "api_health_status": "ok", "worker_commit": commit, "worker_running": True, "frontend_commit": commit},
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is True
    assert report["mismatches"] == []


def test_parity_report_rejects_stopped_release_container():
    commit = "6" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = "/srv/ai-platform/deploy/ai-platform"
    image_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
    }
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {"id": "sha256:backend", "labels": {**image_labels, "ai-platform.release-role": "backend"}},
            "frontend": {"id": "sha256:frontend", "labels": {**image_labels, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        containers={
            "api": {"image_id": "sha256:backend", "running": False, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
            "worker": {"image_id": "sha256:backend", "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
            "frontend": {"image_id": "sha256:frontend", "running": False, "labels": {**common, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        runtime={
            "api_commit": commit,
            "api_health_status": "ok",
            "worker_commit": commit,
            "worker_running": True,
            "frontend_commit": commit,
        },
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is False
    assert "api_container_not_running" in report["mismatches"]
    assert "frontend_container_not_running" in report["mismatches"]


def test_parity_report_rejects_incomplete_compose_identity():
    commit = "a" * 40
    compose_dir = "/srv/ai-platform/deploy/ai-platform"
    image_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.build-dirty": "false",
    }
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
    }
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {"id": "sha256:backend", "labels": {**image_labels, "ai-platform.release-role": "backend"}},
            "frontend": {"id": "sha256:frontend", "labels": {**image_labels, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        containers={
            role: {
                "image_id": "sha256:frontend" if role == "frontend" else "sha256:backend",
                "running": True,
                "labels": {**common, "ai-platform.release-role": role, "com.docker.compose.service": role},
            }
            for role in ("api", "worker", "frontend")
        },
        runtime={
            "api_commit": commit,
            "api_health_status": "ok",
            "worker_commit": commit,
            "worker_running": True,
            "frontend_commit": commit,
        },
        expected_compose_dir=compose_dir,
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert report["verified"] is False
    assert "api_compose_project_mismatch" in report["mismatches"]
    assert "worker_compose_oneoff_mismatch" in report["mismatches"]
    assert "frontend_compose_config_hash_missing" in report["mismatches"]


def test_collect_live_parity_derives_repo_local_compose_and_live_endpoints(monkeypatch, tmp_path):
    commit = "d" * 40
    observed_urls: list[str] = []
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "reference": image,
            "id": "sha256:frontend" if "frontend" in image else "sha256:backend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )
    compose_dir = str((tmp_path / "deploy" / "ai-platform").resolve()).replace("\\", "/")
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    monkeypatch.setattr(
        "tools.release_authority._container_record",
        lambda docker, name: {
            "name": name,
            "image_id": "sha256:frontend" if name.endswith("frontend") else "sha256:backend",
            "labels": {**common, "ai-platform.release-role": name.removeprefix("ai-platform-"), "com.docker.compose.service": name.removeprefix("ai-platform-")},
            "running": True,
            "pid": 1234 if name.endswith("worker") else 4321,
            "health": "healthy" if not name.endswith("worker") else "",
            "ports": {
                "8080/tcp" if name.endswith("frontend") else "8020/tcp": [
                    {
                        "HostIp": "0.0.0.0",
                        "HostPort": "18001" if name.endswith("frontend") else "8020",
                    },
                    {
                        "HostIp": "::",
                        "HostPort": "18001" if name.endswith("frontend") else "8020",
                    },
                ]
            },
        },
    )
    monkeypatch.setattr(
        "tools.release_authority._container_json_file",
        lambda docker, name, path: {
            "schema_version": "ai-platform.worker-runtime-heartbeat.v1",
            "worker_id": "worker-a",
            "runtime_commit": commit,
            "pid": 1234,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr("tools.release_authority._container_process_alive", lambda docker, name, pid: True)

    def fake_http_json(url: str):
        observed_urls.append(url)
        if url.endswith("/api/ai/health"):
            return {"status": "ok", "runtime_commit": commit}
        return {
            "schema_version": "ai-platform.frontend-build-provenance.v1",
            "frontend_path": "frontend/web",
            "git": {"commit": commit, "dirty": False},
        }

    monkeypatch.setattr("tools.release_authority._http_json", fake_http_json)

    report = collect_live_parity(
        tmp_path,
        commit,
        docker_cmd="docker",
    )

    assert report["verified"] is True
    assert observed_urls == [
        "http://127.0.0.1:8020/api/ai/health",
        "http://127.0.0.1:18001/ai-platform-build-provenance.json",
    ]
    assert report["runtime"]["frontend_commit"] == commit
    assert report["runtime"]["api_health_status"] == "ok"
    assert report["runtime"]["worker_running"] is True


def test_collect_live_parity_rejects_stale_worker_heartbeat(monkeypatch, tmp_path):
    commit = "9" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = str((tmp_path / "deploy" / "ai-platform").resolve()).replace("\\", "/")
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else "sha256:backend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )
    monkeypatch.setattr(
        "tools.release_authority._container_record",
        lambda docker, name: {
            "name": name,
            "image_id": "sha256:frontend" if name.endswith("frontend") else "sha256:backend",
            "labels": {**common, "ai-platform.release-role": name.removeprefix("ai-platform-"), "com.docker.compose.service": name.removeprefix("ai-platform-")},
            "running": True,
            "pid": 2222 if name.endswith("worker") else 1111,
            "health": "healthy",
            "ports": {
                "8080/tcp" if name.endswith("frontend") else "8020/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "18001" if name.endswith("frontend") else "8020"}
                ]
            },
        },
    )
    monkeypatch.setattr(
        "tools.release_authority._http_json",
        lambda url: (
            {"status": "ok", "runtime_commit": commit}
            if url.endswith("/api/ai/health")
            else {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": commit, "dirty": False},
            }
        ),
    )
    monkeypatch.setattr(
        "tools.release_authority._container_json_file",
        lambda docker, name, path: {
            "schema_version": "ai-platform.worker-runtime-heartbeat.v1",
            "worker_id": "worker-a",
            "runtime_commit": commit,
            "pid": 1111,
            "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        },
    )
    monkeypatch.setattr("tools.release_authority._container_process_alive", lambda docker, name, pid: False)

    try:
        collect_live_parity(tmp_path, commit, docker_cmd="docker")
    except ReleaseAuthorityError as exc:
        assert "worker runtime heartbeat" in str(exc)
    else:
        raise AssertionError("stale or wrong-process worker heartbeat must be rejected")


def test_published_url_supports_ipv6_only_binding():
    from tools.release_authority import _published_loopback_url

    url = _published_loopback_url(
        {"ports": {"8080/tcp": [{"HostIp": "::", "HostPort": "18001"}]}},
        "8080/tcp",
        "/healthz",
    )

    assert url == "http://[::1]:18001/healthz"


def test_container_process_alive_uses_shell_builtin_kill(monkeypatch):
    from tools.release_authority import _container_process_alive

    observed: list[str] = []

    def fake_run(command, **kwargs):
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    assert _container_process_alive(["sudo", "-n", "docker"], "ai-platform-worker", 1234) is True
    assert observed == [
        "sudo",
        "-n",
        "docker",
        "exec",
        "ai-platform-worker",
        "/bin/sh",
        "-c",
        'kill -0 "$1"',
        "sh",
        "1234",
    ]


def test_deploy_rejects_unexpected_manual_frontend_identity(monkeypatch, tmp_path):
    commit = "e" * 40
    removed: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:image",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"Config": {"Image": "ai-platform-frontend:unexpected", "Labels": {}}}]),
                stderr="",
            )
        if "rm" in command:
            removed.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=True,
            expected_manual_frontend_image="ai-platform-frontend:d189877-20260709-main",
            expected_manual_frontend_image_id="sha256:f2476f83d139f721cf3adb3e7664dd431082aea459b6205bcc9d35f04a524e25",
        )
    except ReleaseAuthorityError as exc:
        assert "manual frontend identity mismatch" in str(exc)
    else:
        raise AssertionError("an unexpected manual frontend must not be removed")

    assert removed == []


def test_deploy_reuses_valid_existing_commit_tag_without_rebuilding(monkeypatch, tmp_path):
    commit = "1" * 40
    build_commands: list[list[str]] = []
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else "sha256:backend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if "build" in command:
            build_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
    )

    assert build_commands == []


def test_deploy_rejects_existing_commit_tag_with_wrong_provenance(monkeypatch, tmp_path):
    commit = "3" * 40
    repository = AUTHORITATIVE_REPOSITORY
    build_commands: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:wrong",
            "labels": {
                "ai-platform.source-commit": "4" * 40,
                "org.opencontainers.image.revision": "4" * 40,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if "build" in command:
            build_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "backend image label mismatch" in str(exc)
    else:
        raise AssertionError("an existing commit tag with different provenance must be rejected")

    assert build_commands == []


def test_deploy_rejects_spoofed_repo_owned_frontend(monkeypatch, tmp_path):
    commit = "2" * 40
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:image",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:old",
                            "Config": {
                                "Image": "ai-platform-frontend:old",
                                "Labels": {
                                    "ai-platform.release-owner": "repo-local-compose",
                                    "com.docker.compose.project.working_dir": "/legacy/deploy/ai-platform",
                                    "com.docker.compose.project.config_files": "/legacy/deploy/ai-platform/docker-compose.yml",
                                },
                            },
                        }
                    ]
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "frontend compose ownership mismatch" in str(exc)
    else:
        raise AssertionError("spoofed repo-local ownership must be rejected")


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

    deploy_help = subprocess.run(
        [sys.executable, "tools/release_authority.py", "deploy", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--expected-manual-frontend-image" in deploy_help
    assert "--expected-manual-frontend-image-id" in deploy_help

    verify_help = subprocess.run(
        [sys.executable, "tools/release_authority.py", "verify", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--compose-dir" not in verify_help
    assert "--frontend-provenance-url" not in verify_help


def test_deploy_uses_211_sudo_env_compose_command(monkeypatch, tmp_path):
    commit = "5" * 40
    repository = AUTHORITATIVE_REPOSITORY
    commands: list[list[str]] = []
    image_records = {
        f"ai-platform:{commit}": {
            "id": "sha256:backend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "backend",
            },
        },
        f"ai-platform-frontend:{commit}": {
            "id": "sha256:frontend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend",
            },
        },
    }

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr("tools.release_authority._image_record", lambda docker, image: image_records[image])

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    env_file = tmp_path / ".env"
    deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="sudo -n docker",
        env_file=env_file,
        replace_known_manual_frontend=False,
    )

    compose = next(command for command in commands if "compose" in command)
    assert compose[:3] == ["sudo", "-n", "env"]
    assert f"AI_PLATFORM_IMAGE=ai-platform:{commit}" in compose
    assert f"AI_PLATFORM_FRONTEND_IMAGE=ai-platform-frontend:{commit}" in compose
    assert compose[compose.index("compose") :] == [
        "compose",
        "-p",
        "ai-platform-phaseb",
        "--env-file",
        str(env_file.resolve()),
        "-f",
        str((tmp_path / "deploy" / "ai-platform" / "docker-compose.yml").resolve()),
        "up",
        "-d",
        "--no-build",
    ]


def _install_checkout_git_runner(
    monkeypatch,
    *,
    commit: str,
    origin: str = AUTHORITATIVE_REPOSITORY,
    head: str | None = None,
    status: str = "",
    ancestor_returncode: int = 0,
):
    commands: list[tuple[list[str], Path | None, bool]] = []

    def fake_run(command, *, cwd=None, check=True, text=True, env=None):
        command = list(command)
        cwd_path = Path(cwd) if cwd is not None else None
        commands.append((command, cwd_path, check))
        stdout = ""
        returncode = 0
        if command[:2] == ["git", "init"]:
            assert cwd_path is not None
            (cwd_path / ".git").mkdir()
        elif command[1:4] == ["config", "--get", "remote.origin.url"]:
            stdout = origin + "\n"
        elif command[1:3] == ["rev-parse", "HEAD"]:
            stdout = (head or commit) + "\n"
        elif command[1:3] == ["status", "--porcelain"]:
            stdout = status
        elif command[1:3] == ["merge-base", "--is-ancestor"]:
            returncode = ancestor_returncode
        result = subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command, output=stdout, stderr="")
        return result

    monkeypatch.setattr("tools.release_authority._run", fake_run)
    return commands


def test_materialize_main_checkout_fetches_explicit_refspec_into_isolated_commit_dir(
    monkeypatch,
    tmp_path,
):
    commit = "6" * 40
    commands = _install_checkout_git_runner(monkeypatch, commit=commit)
    release_root = tmp_path / "releases"

    checkout = release_authority.materialize_main_checkout(release_root, commit)

    assert checkout == release_root / commit
    assert checkout.is_dir()
    assert (checkout / ".git").is_dir()
    git_commands = [command for command, _, _ in commands if command[0] == "git"]
    assert ["git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"] in git_commands
    assert ["git", "cat-file", "-e", f"{commit}^{{commit}}"] in git_commands
    assert ["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"] in git_commands
    assert ["git", "checkout", "--detach", commit] in git_commands
    assert not any(command[1] in {"archive", "worktree"} for command in git_commands)


def test_materialize_main_checkout_reuses_only_clean_matching_checkout(monkeypatch, tmp_path):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    commands = _install_checkout_git_runner(monkeypatch, commit=commit)

    assert release_authority.materialize_main_checkout(release_root, commit) == checkout

    git_commands = [command for command, _, _ in commands]
    assert ["git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"] in git_commands
    assert ["git", "init"] not in git_commands
    assert ["git", "checkout", "--detach", commit] not in git_commands


def test_materialize_main_checkout_rejects_dirty_or_non_main_reuse(monkeypatch, tmp_path):
    commit = "8" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    _install_checkout_git_runner(monkeypatch, commit=commit, status=" M tracked.txt\n")

    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert "dirty source" in str(exc)
    else:
        raise AssertionError("a dirty versioned checkout must not be reused")

    monkeypatch.undo()
    _install_checkout_git_runner(monkeypatch, commit=commit, ancestor_returncode=1)
    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert "fetched main" in str(exc)
    else:
        raise AssertionError("a commit outside fetched main must be rejected")


def test_materialize_main_checkout_rejects_checkout_head_mismatch(monkeypatch, tmp_path):
    commit = "f" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    _install_checkout_git_runner(monkeypatch, commit=commit, head="0" * 40)

    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert "does not match requested commit" in str(exc)
    else:
        raise AssertionError("a version directory with a mismatched HEAD must fail closed")


def test_materialize_main_checkout_rejects_residue_invalid_commit_and_path_escape(
    monkeypatch,
    tmp_path,
):
    commit = "9" * 40
    release_root = tmp_path / "releases"
    release_root.mkdir()
    (release_root / f".{commit}.incoming").mkdir()
    monkeypatch.setattr(
        "tools.release_authority._run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )

    for root, requested, expected in (
        (release_root, commit, "interrupted"),
        (tmp_path / "uncreated", "../main; touch owned", "full 40-character"),
        (tmp_path / "releases" / ".." / "escape", commit, "normalized absolute"),
    ):
        try:
            release_authority.materialize_main_checkout(root, requested)
        except ReleaseAuthorityError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("unsafe release materialization must fail closed")


def test_materialize_main_checkout_rejects_symlink_release_root(monkeypatch, tmp_path):
    commit = "a" * 40
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError:
        linked = actual
        monkeypatch.setattr(
            "tools.release_authority._is_link_or_junction",
            lambda path: Path(path) == linked,
        )
    monkeypatch.setattr(
        "tools.release_authority._run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )

    try:
        release_authority.materialize_main_checkout(linked, commit)
    except ReleaseAuthorityError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("a symlink release root must fail closed")


def test_deploy_main_commit_delegates_existing_deploy_and_parity_authorities(monkeypatch, tmp_path):
    commit = "b" * 40
    checkout = tmp_path / "releases" / commit
    calls: list[tuple[str, Path, str]] = []
    monkeypatch.setattr(
        "tools.release_authority.materialize_main_checkout",
        lambda root, requested: checkout,
    )

    def fake_deploy(repo_root, requested, **kwargs):
        calls.append(("deploy", repo_root, requested))
        return {"commit": requested}

    def fake_parity(repo_root, requested, **kwargs):
        calls.append(("parity", repo_root, requested))
        return {"verified": True, "mismatches": []}

    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", fake_deploy)
    monkeypatch.setattr("tools.release_authority.collect_live_parity", fake_parity)

    result = release_authority.deploy_main_commit(
        tmp_path / "releases",
        commit,
        docker_cmd="sudo -n docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
    )

    assert calls == [("deploy", checkout, commit), ("parity", checkout, commit)]
    assert result["checkout"] == str(checkout)
    assert result["parity"]["verified"] is True


def test_deploy_main_commit_fails_closed_when_live_parity_does_not_verify(monkeypatch, tmp_path):
    commit = "c" * 40
    checkout = tmp_path / "releases" / commit
    monkeypatch.setattr("tools.release_authority.materialize_main_checkout", lambda root, requested: checkout)
    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "tools.release_authority.collect_live_parity",
        lambda *args, **kwargs: {"verified": False, "mismatches": ["api_runtime_commit_mismatch"]},
    )

    try:
        release_authority.deploy_main_commit(
            tmp_path / "releases",
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "api_runtime_commit_mismatch" in str(exc)
    else:
        raise AssertionError("a non-verifying rollout must fail closed")


def test_image_validation_rejects_stale_underscore_compatibility_aliases():
    commit = "d" * 40
    canonical = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": "backend",
    }
    aliases = (
        "ai-platform.source_revision",
        "ai-platform.source_commit",
        "ai-platform.runtime_subject",
        "ai-platform.source_tree_commit",
        "ai_platform_source_revision",
        "ai_platform_source_commit",
        "ai_platform_runtime_subject",
        "ai_platform_source_tree_commit",
    )

    for alias in aliases:
        image = {"labels": {**canonical, alias: "e" * 40}}
        try:
            release_authority._validate_release_image(
                image,
                commit=commit,
                repository=AUTHORITATIVE_REPOSITORY,
                role="backend",
            )
        except ReleaseAuthorityError as exc:
            assert alias in str(exc)
        else:
            raise AssertionError(f"stale compatibility label {alias} must fail closed")


def test_parity_report_records_stale_underscore_compatibility_alias():
    commit = "1" * 40
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {
                "labels": {
                    "ai-platform.source-commit": commit,
                    "org.opencontainers.image.revision": commit,
                    "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                    "ai-platform.build-dirty": "false",
                    "ai-platform.release-role": "backend",
                    "ai-platform.source_revision": "2" * 40,
                }
            },
            "frontend": {"labels": {}},
        },
        containers={},
        runtime={},
        expected_compose_dir="/srv/ai-platform/releases/commit/deploy/ai-platform",
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert "backend_image_compatibility_commit_mismatch" in report["mismatches"]


def test_release_authority_cli_exposes_git_native_main_commit_deploy():
    help_text = subprocess.run(
        [sys.executable, "tools/release_authority.py", "deploy-main-commit", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "--release-root" in help_text
    assert "--repo-root" not in help_text
    assert "--commit" in help_text

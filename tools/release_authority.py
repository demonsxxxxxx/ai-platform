"""Clean-commit deployment, dirty-source preservation, and runtime parity checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
from typing import Any, Sequence


SCHEMA_VERSION = "ai-platform.release-authority.v1"
PRESERVATION_SCHEMA_VERSION = "ai-platform.release-authority-preservation.v1"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_COMPOSE_RELATIVE_PATH = Path("deploy/ai-platform/docker-compose.yml")
SECRET_PATH_NAMES = {".env", ".env.local", ".env.production", ".env.development"}


class ReleaseAuthorityError(RuntimeError):
    """Raised when a release-authority invariant is not satisfied."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=text,
        env=env,
    )


def _git(repo_root: Path, *args: str, text: bool = True) -> str | bytes:
    result = _run(["git", *args], cwd=repo_root, text=text)
    return result.stdout


def _normalize_commit(value: str) -> str:
    commit = value.strip().lower()
    if not FULL_COMMIT_RE.fullmatch(commit):
        raise ReleaseAuthorityError("release commit must be a full 40-character lowercase SHA")
    return commit


def assert_clean_commit(repo_root: Path, requested_commit: str) -> str:
    """Require a clean checkout whose HEAD exactly matches the requested commit."""
    repo_root = repo_root.resolve()
    commit = _normalize_commit(requested_commit)
    head = str(_git(repo_root, "rev-parse", "HEAD")).strip().lower()
    if head != commit:
        raise ReleaseAuthorityError(
            f"source HEAD {head or 'unknown'} does not match requested commit {commit}"
        )
    status = str(_git(repo_root, "status", "--porcelain", "--untracked-files=all"))
    if status.strip():
        raise ReleaseAuthorityError("dirty source is forbidden for release deployment")
    return commit


def build_image_references(commit: str) -> dict[str, str]:
    """Return immutable backend and frontend image tags for one full commit."""
    normalized = _normalize_commit(commit)
    return {
        "backend": f"ai-platform:{normalized}",
        "frontend": f"ai-platform-frontend:{normalized}",
    }


def _is_secret_path(relative_path: str) -> bool:
    path = Path(relative_path)
    name = path.name.lower()
    return name in SECRET_PATH_NAMES or name.startswith(".env.")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, content: bytes) -> dict[str, Any]:
    path.write_bytes(content)
    return {"size": path.stat().st_size, "sha256": _sha256_path(path)}


def _git_paths(repo_root: Path, *args: str) -> list[str]:
    raw = bytes(_git(repo_root, *args, "-z", text=False))
    return [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]


def preserve_dirty_source(repo_root: Path, output_root: Path) -> Path:
    """Preserve dirty Git evidence without changing or cleaning the source tree."""
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    head = str(_git(repo_root, "rev-parse", "HEAD")).strip().lower()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_root / f"{timestamp}-{head}"
    destination.mkdir(parents=True, exist_ok=False)

    status = str(_git(repo_root, "status", "--short", "--branch", "--untracked-files=all"))
    status_bytes = status.encode("utf-8")
    tracked_patch = bytes(_git(repo_root, "diff", "--binary", text=False))
    staged_patch = bytes(_git(repo_root, "diff", "--cached", "--binary", text=False))
    modified = set(_git_paths(repo_root, "diff", "--name-only"))
    staged = set(_git_paths(repo_root, "diff", "--cached", "--name-only"))
    untracked = set(_git_paths(repo_root, "ls-files", "--others", "--exclude-standard"))

    inventory: list[dict[str, Any]] = []
    for relative_path in sorted(modified | staged | untracked):
        path = repo_root / relative_path
        secret = _is_secret_path(relative_path)
        category = "untracked" if relative_path in untracked else "tracked"
        if relative_path in staged:
            category = "staged" if category == "tracked" else f"{category}+staged"
        record: dict[str, Any] = {
            "path": relative_path,
            "category": category,
            "exists": path.exists(),
            "content_preserved": bool(path.is_file() and not secret),
            "secret_path_excluded": secret,
            "size": path.stat().st_size if path.is_file() else None,
            "mode": oct(path.stat().st_mode & 0o777) if path.exists() else None,
            "sha256": _sha256_path(path) if path.is_file() and not secret else None,
        }
        inventory.append(record)

    inventory_path = destination / "inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = {
        "status.txt": _write_bytes(destination / "status.txt", status_bytes),
        "tracked.patch": _write_bytes(destination / "tracked.patch", tracked_patch),
        "staged.patch": _write_bytes(destination / "staged.patch", staged_patch),
        "inventory.json": {
            "size": inventory_path.stat().st_size,
            "sha256": _sha256_path(inventory_path),
        },
    }

    tar_path = destination / "untracked.tar"
    with tarfile.open(tar_path, "w") as archive:
        for relative_path in sorted(untracked):
            path = repo_root / relative_path
            if path.is_file() and not _is_secret_path(relative_path):
                archive.add(path, arcname=relative_path, recursive=False)
    artifacts["untracked.tar"] = {"size": tar_path.stat().st_size, "sha256": _sha256_path(tar_path)}

    manifest = {
        "schema_version": PRESERVATION_SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(repo_root),
        "source_head": head,
        "source_was_dirty": bool(status.strip()),
        "source_tree_unchanged_by_preservation": True,
        "secret_path_policy": "record_metadata_only_without_hash_or_archive_content",
        "artifacts": artifacts,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def build_parity_report(
    *,
    expected_commit: str,
    source: dict[str, Any],
    images: dict[str, dict[str, Any]],
    containers: dict[str, dict[str, Any]],
    runtime: dict[str, Any],
    expected_compose_dir: str,
) -> dict[str, Any]:
    """Build a strict same-commit report for source, images, and runtime subjects."""
    commit = _normalize_commit(expected_commit)
    mismatches: list[str] = []
    if source.get("commit") != commit:
        mismatches.append("source_commit_mismatch")
    if source.get("dirty") is not False:
        mismatches.append("source_not_clean")

    for role in ("backend", "frontend"):
        image = images.get(role, {})
        labels = image.get("labels") if isinstance(image.get("labels"), dict) else {}
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_image_commit_mismatch")
        if labels.get("ai-platform.build-dirty") != "false":
            mismatches.append(f"{role}_image_dirty_label_mismatch")
        if labels.get("ai-platform.release-role") != role:
            mismatches.append(f"{role}_image_role_mismatch")

    expected_image_roles = {"api": "backend", "worker": "backend", "frontend": "frontend"}
    for role, image_role in expected_image_roles.items():
        container = containers.get(role, {})
        labels = container.get("labels") if isinstance(container.get("labels"), dict) else {}
        if labels.get("ai-platform.release-owner") != "repo-local-compose":
            mismatches.append(f"{role}_container_not_repo_local_compose_owned")
        if labels.get("ai-platform.release-role") != role:
            mismatches.append(f"{role}_container_role_mismatch")
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_container_commit_mismatch")
        if labels.get("ai-platform.source-dirty") != "false":
            mismatches.append(f"{role}_container_dirty_label_mismatch")
        if labels.get("com.docker.compose.project.working_dir") != expected_compose_dir:
            mismatches.append(f"{role}_compose_working_dir_mismatch")
        config_files = str(labels.get("com.docker.compose.project.config_files") or "")
        if config_files != f"{expected_compose_dir}/docker-compose.yml":
            mismatches.append(f"{role}_compose_config_mismatch")
        expected_image_id = images.get(image_role, {}).get("id")
        if not expected_image_id or container.get("image_id") != expected_image_id:
            mismatches.append(f"{role}_container_image_mismatch")

    for role in ("api", "worker", "frontend"):
        if runtime.get(f"{role}_commit") != commit:
            mismatches.append(f"{role}_runtime_commit_mismatch")

    return {
        "schema_version": SCHEMA_VERSION,
        "expected_commit": commit,
        "verified": not mismatches,
        "mismatches": sorted(set(mismatches)),
        "source": source,
        "images": images,
        "containers": containers,
        "runtime": runtime,
    }


def _docker_base(docker_cmd: str) -> list[str]:
    command = shlex.split(docker_cmd)
    if not command or command[-1] != "docker":
        raise ReleaseAuthorityError("docker command must end with the docker executable")
    return command


def _docker_json(docker: list[str], *args: str) -> Any:
    result = _run([*docker, *args])
    return json.loads(result.stdout)


def _image_record(docker: list[str], image: str) -> dict[str, Any]:
    payload = _docker_json(docker, "image", "inspect", image)[0]
    return {"reference": image, "id": payload.get("Id"), "labels": payload.get("Config", {}).get("Labels") or {}}


def _container_record(docker: list[str], name: str) -> dict[str, Any]:
    payload = _docker_json(docker, "container", "inspect", name)[0]
    return {"name": name, "image_id": payload.get("Image"), "labels": payload.get("Config", {}).get("Labels") or {}}


def _container_file_commit(docker: list[str], name: str, path: str) -> str:
    result = _run([*docker, "exec", name, "cat", path])
    if path.endswith(".json"):
        payload = json.loads(result.stdout)
        if "git" in payload:
            return str(payload.get("git", {}).get("commit") or "")
        return str(payload.get("source_tree_commit_sha") or "")
    return result.stdout.strip()


def collect_live_parity(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    compose_dir: str,
) -> dict[str, Any]:
    """Collect live Docker and embedded provenance for the strict parity report."""
    normalized = assert_clean_commit(repo_root, commit)
    docker = _docker_base(docker_cmd)
    refs = build_image_references(normalized)
    images = {
        "backend": _image_record(docker, refs["backend"]),
        "frontend": _image_record(docker, refs["frontend"]),
    }
    containers = {
        "api": _container_record(docker, "ai-platform-api"),
        "worker": _container_record(docker, "ai-platform-worker"),
        "frontend": _container_record(docker, "ai-platform-frontend"),
    }
    runtime = {
        "api_commit": _container_file_commit(docker, "ai-platform-api", "/app/.ai-platform-source-revision"),
        "worker_commit": _container_file_commit(docker, "ai-platform-worker", "/app/.ai-platform-source-revision"),
        "frontend_commit": _container_file_commit(
            docker,
            "ai-platform-frontend",
            "/usr/share/nginx/html/ai-platform-build-provenance.json",
        ),
    }
    return build_parity_report(
        expected_commit=normalized,
        source={"commit": normalized, "dirty": False, "path": str(repo_root.resolve())},
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir.rstrip("/"),
    )


def deploy_clean_commit(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    env_file: Path,
    replace_known_manual_frontend: bool,
) -> dict[str, Any]:
    """Build immutable images and recreate the repo-local compose release."""
    normalized = assert_clean_commit(repo_root, commit)
    docker = _docker_base(docker_cmd)
    refs = build_image_references(normalized)
    repository = str(_git(repo_root, "config", "--get", "remote.origin.url")).strip()
    common_args = [
        "--build-arg", f"AI_PLATFORM_BUILD_COMMIT={normalized}",
        "--build-arg", "AI_PLATFORM_BUILD_DIRTY=false",
        "--build-arg", f"AI_PLATFORM_BUILD_REPOSITORY={repository}",
    ]
    _run([*docker, "build", *common_args, "-t", refs["backend"], "-f", "Dockerfile", "."], cwd=repo_root)
    _run([*docker, "build", *common_args, "-t", refs["frontend"], "-f", "frontend/web/Dockerfile", "."], cwd=repo_root)

    images = {role: _image_record(docker, image) for role, image in refs.items()}
    for role, image in images.items():
        labels = image["labels"]
        if labels.get("ai-platform.source-commit") != normalized:
            raise ReleaseAuthorityError(f"{role} image commit label mismatch")
        if labels.get("ai-platform.build-dirty") != "false":
            raise ReleaseAuthorityError(f"{role} image dirty label mismatch")
        if labels.get("ai-platform.release-role") != role:
            raise ReleaseAuthorityError(f"{role} image role label mismatch")

    existing = _run([*docker, "container", "inspect", "ai-platform-frontend"], check=False)
    if existing.returncode == 0:
        payload = json.loads(existing.stdout)[0]
        labels = payload.get("Config", {}).get("Labels") or {}
        if labels.get("ai-platform.release-owner") != "repo-local-compose":
            if not replace_known_manual_frontend:
                raise ReleaseAuthorityError("manual frontend container is forbidden; rerun with explicit replacement")
            _run([*docker, "container", "rm", "-f", "ai-platform-frontend"])

    compose_file = repo_root / DEFAULT_COMPOSE_RELATIVE_PATH
    compose_environment = [
        f"AI_PLATFORM_IMAGE={refs['backend']}",
        f"AI_PLATFORM_FRONTEND_IMAGE={refs['frontend']}",
        f"AI_PLATFORM_SOURCE_COMMIT={normalized}",
        f"AI_PLATFORM_BUILD_COMMIT={normalized}",
        "AI_PLATFORM_BUILD_DIRTY=false",
    ]
    if docker[:2] == ["sudo", "-n"]:
        compose_command = ["sudo", "-n", "env", *compose_environment, "docker"]
    else:
        compose_command = ["env", *compose_environment, *docker]
    _run(
        [
            *compose_command,
            "compose",
            "--env-file",
            str(env_file.resolve()),
            "-f",
            str(compose_file.resolve()),
            "up",
            "-d",
            "--no-build",
        ],
        cwd=compose_file.parent,
    )
    return {"commit": normalized, "images": refs, "compose_file": str(compose_file.resolve())}


def _write_json(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")


def main() -> int:
    """Run the release-authority preservation, deployment, or verification command."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preserve = subparsers.add_parser("preserve-dirty", help="Preserve dirty source without cleaning it")
    preserve.add_argument("--repo-root", type=Path, required=True)
    preserve.add_argument("--output-root", type=Path, required=True)

    deploy = subparsers.add_parser("deploy", help="Build and deploy one clean commit")
    deploy.add_argument("--repo-root", type=Path, required=True)
    deploy.add_argument("--commit", required=True)
    deploy.add_argument("--docker-cmd", default="docker")
    deploy.add_argument("--env-file", type=Path, required=True)
    deploy.add_argument("--replace-known-manual-frontend", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify source/image/runtime commit parity")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--docker-cmd", default="docker")
    verify.add_argument("--compose-dir", required=True)
    verify.add_argument("--output", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "preserve-dirty":
            destination = preserve_dirty_source(args.repo_root, args.output_root)
            _write_json({"preserved": True, "path": str(destination)}, None)
        elif args.command == "deploy":
            _write_json(
                deploy_clean_commit(
                    args.repo_root,
                    args.commit,
                    docker_cmd=args.docker_cmd,
                    env_file=args.env_file,
                    replace_known_manual_frontend=args.replace_known_manual_frontend,
                ),
                None,
            )
        else:
            report = collect_live_parity(
                args.repo_root,
                args.commit,
                docker_cmd=args.docker_cmd,
                compose_dir=args.compose_dir,
            )
            _write_json(report, args.output)
            return 0 if report["verified"] else 1
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ReleaseAuthorityError) as exc:
        _write_json({"verified": False, "error": str(exc), "command": args.command}, None)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

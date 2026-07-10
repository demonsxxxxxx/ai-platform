"""Clean-commit deployment, dirty-source preservation, and runtime parity checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
from urllib.request import urlopen


SCHEMA_VERSION = "ai-platform.release-authority.v1"
PRESERVATION_SCHEMA_VERSION = "ai-platform.release-authority-preservation.v1"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_COMPOSE_RELATIVE_PATH = Path("deploy/ai-platform/docker-compose.yml")
COMPOSE_PROJECT = "ai-platform-phaseb"
AUTHORITATIVE_REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
AUTHORITATIVE_REPOSITORY_ALIASES = {
    AUTHORITATIVE_REPOSITORY,
    "git@github.com:demonsxxxxxx/ai-platform.git",
    "ssh://git@github.com/demonsxxxxxx/ai-platform.git",
}
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


def authoritative_repository(repo_root: Path) -> str:
    origin = str(_git(repo_root, "config", "--get", "remote.origin.url")).strip().rstrip("/")
    if origin not in AUTHORITATIVE_REPOSITORY_ALIASES:
        raise ReleaseAuthorityError("authoritative repository mismatch")
    return AUTHORITATIVE_REPOSITORY


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
    expected_repository: str,
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
        if labels.get("org.opencontainers.image.revision") != commit:
            mismatches.append(f"{role}_image_oci_revision_mismatch")
        if labels.get("ai-platform.source-repository") != expected_repository:
            mismatches.append(f"{role}_image_repository_mismatch")
        if labels.get("ai-platform.build-dirty") != "false":
            mismatches.append(f"{role}_image_dirty_label_mismatch")
        if labels.get("ai-platform.release-role") != role:
            mismatches.append(f"{role}_image_role_mismatch")

    expected_image_roles = {"api": "backend", "worker": "backend", "frontend": "frontend"}
    for role, image_role in expected_image_roles.items():
        container = containers.get(role, {})
        labels = container.get("labels") if isinstance(container.get("labels"), dict) else {}
        if container.get("running") is not True:
            mismatches.append(f"{role}_container_not_running")
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
        if labels.get("com.docker.compose.project") != COMPOSE_PROJECT:
            mismatches.append(f"{role}_compose_project_mismatch")
        if labels.get("com.docker.compose.service") != role:
            mismatches.append(f"{role}_compose_service_mismatch")
        if labels.get("com.docker.compose.oneoff") != "False":
            mismatches.append(f"{role}_compose_oneoff_mismatch")
        if not str(labels.get("com.docker.compose.config-hash") or "").strip():
            mismatches.append(f"{role}_compose_config_hash_missing")
        expected_image_id = images.get(image_role, {}).get("id")
        if not expected_image_id or container.get("image_id") != expected_image_id:
            mismatches.append(f"{role}_container_image_mismatch")

    for role in ("api", "worker", "frontend"):
        if runtime.get(f"{role}_commit") != commit:
            mismatches.append(f"{role}_runtime_commit_mismatch")
    if runtime.get("api_health_status") != "ok":
        mismatches.append("api_health_not_ok")
    if runtime.get("worker_running") is not True:
        mismatches.append("worker_not_running")

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


def _validate_release_image(image: dict[str, Any], *, commit: str, repository: str, role: str) -> None:
    labels = image.get("labels") if isinstance(image.get("labels"), dict) else {}
    expected = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": role,
    }
    for label, value in expected.items():
        if labels.get(label) != value:
            raise ReleaseAuthorityError(f"{role} image label mismatch: {label}")


def _existing_release_image(
    docker: list[str],
    reference: str,
    *,
    commit: str,
    repository: str,
    role: str,
) -> dict[str, Any] | None:
    try:
        image = _image_record(docker, reference)
    except subprocess.CalledProcessError:
        return None
    _validate_release_image(image, commit=commit, repository=repository, role=role)
    return image


def _container_record(docker: list[str], name: str) -> dict[str, Any]:
    payload = _docker_json(docker, "container", "inspect", name)[0]
    state = payload.get("State") or {}
    return {
        "name": name,
        "image_id": payload.get("Image"),
        "labels": payload.get("Config", {}).get("Labels") or {},
        "running": state.get("Running") is True,
        "pid": state.get("Pid"),
        "health": (state.get("Health") or {}).get("Status") or "",
        "ports": (payload.get("NetworkSettings") or {}).get("Ports") or {},
    }


def _container_file_commit(docker: list[str], name: str, path: str) -> str:
    result = _run([*docker, "exec", name, "cat", path])
    if path.endswith(".json"):
        payload = json.loads(result.stdout)
        if "git" in payload:
            return str(payload.get("git", {}).get("commit") or "")
        return str(payload.get("source_tree_commit_sha") or "")
    return result.stdout.strip()


def _http_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _published_loopback_url(container: dict[str, Any], container_port: str, path: str) -> str:
    bindings = container.get("ports", {}).get(container_port)
    if not isinstance(bindings, list) or not bindings:
        raise ReleaseAuthorityError(f"expected published bindings for {container_port}")
    host_ports = {str(binding.get("HostPort") or "").strip() for binding in bindings}
    if len(host_ports) != 1:
        raise ReleaseAuthorityError(f"ambiguous published host ports for {container_port}")
    host_port = host_ports.pop()
    if not host_port.isdigit():
        raise ReleaseAuthorityError(f"invalid published host port for {container_port}")
    host_ips = {str(binding.get("HostIp") or "").strip() for binding in bindings}
    if any(host in {"0.0.0.0", "127.0.0.1"} for host in host_ips):
        host = "127.0.0.1"
    elif host_ips and all(host in {"::", "::1"} for host in host_ips):
        host = "[::1]"
    else:
        raise ReleaseAuthorityError(f"unsupported published host binding for {container_port}")
    return f"http://{host}:{host_port}/{path.lstrip('/')}"


def _container_json_file(docker: list[str], name: str, path: str) -> dict[str, Any]:
    result = _run([*docker, "exec", name, "cat", path])
    return json.loads(result.stdout)


def _container_process_alive(docker: list[str], name: str, pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    result = _run([*docker, "exec", name, "kill", "-0", str(pid)], check=False)
    return result.returncode == 0


def _validate_worker_runtime_heartbeat(payload: dict[str, Any], *, process_alive: bool) -> None:
    if payload.get("schema_version") != "ai-platform.worker-runtime-heartbeat.v1":
        raise ReleaseAuthorityError("worker runtime heartbeat schema mismatch")
    if not str(payload.get("worker_id") or "").strip():
        raise ReleaseAuthorityError("worker runtime heartbeat worker ID missing")
    if not process_alive:
        raise ReleaseAuthorityError("worker runtime heartbeat process is not alive")
    try:
        observed_at = datetime.fromisoformat(str(payload.get("observed_at") or ""))
    except ValueError as exc:
        raise ReleaseAuthorityError("worker runtime heartbeat timestamp invalid") from exc
    if observed_at.tzinfo is None:
        raise ReleaseAuthorityError("worker runtime heartbeat timestamp lacks timezone")
    age = datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age < timedelta(seconds=-5) or age > timedelta(seconds=30):
        raise ReleaseAuthorityError("worker runtime heartbeat is stale")


def collect_live_parity(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
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
    api_health = _http_json(_published_loopback_url(containers["api"], "8020/tcp", "/api/ai/health"))
    frontend_provenance = _http_json(
        _published_loopback_url(
            containers["frontend"],
            "8080/tcp",
            "/ai-platform-build-provenance.json",
        )
    )
    if frontend_provenance.get("schema_version") != "ai-platform.frontend-build-provenance.v1":
        raise ReleaseAuthorityError("frontend provenance schema mismatch")
    if frontend_provenance.get("frontend_path") != "frontend/web":
        raise ReleaseAuthorityError("frontend provenance path mismatch")
    if frontend_provenance.get("git", {}).get("dirty") is not False:
        raise ReleaseAuthorityError("frontend provenance is dirty")
    worker_heartbeat = _container_json_file(
        docker,
        "ai-platform-worker",
        "/tmp/ai-platform-worker-runtime-heartbeat.json",
    )
    _validate_worker_runtime_heartbeat(
        worker_heartbeat,
        process_alive=_container_process_alive(
            docker,
            "ai-platform-worker",
            worker_heartbeat.get("pid"),
        ),
    )
    runtime = {
        "api_commit": str(api_health.get("runtime_commit") or ""),
        "api_health_status": api_health.get("status"),
        "worker_heartbeat": worker_heartbeat,
        "worker_running": containers["worker"].get("running") is True,
        "frontend_commit": str(frontend_provenance.get("git", {}).get("commit") or ""),
    }
    runtime["worker_commit"] = str(runtime["worker_heartbeat"].get("runtime_commit") or "")
    compose_dir = (repo_root.resolve() / DEFAULT_COMPOSE_RELATIVE_PATH).parent.as_posix()
    repository = authoritative_repository(repo_root)
    return build_parity_report(
        expected_commit=normalized,
        source={"commit": normalized, "dirty": False, "path": str(repo_root.resolve())},
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir.rstrip("/"),
        expected_repository=repository,
    )


def deploy_clean_commit(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    env_file: Path,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None = None,
    expected_manual_frontend_image_id: str | None = None,
) -> dict[str, Any]:
    """Build immutable images and recreate the repo-local compose release."""
    normalized = assert_clean_commit(repo_root, commit)
    docker = _docker_base(docker_cmd)
    refs = build_image_references(normalized)
    repository = authoritative_repository(repo_root)
    common_args = [
        "--build-arg", f"AI_PLATFORM_BUILD_COMMIT={normalized}",
        "--build-arg", "AI_PLATFORM_BUILD_DIRTY=false",
        "--build-arg", f"AI_PLATFORM_BUILD_REPOSITORY={repository}",
    ]
    images: dict[str, dict[str, Any]] = {}
    dockerfiles = {"backend": "Dockerfile", "frontend": "frontend/web/Dockerfile"}
    for role, reference in refs.items():
        image = _existing_release_image(
            docker,
            reference,
            commit=normalized,
            repository=repository,
            role=role,
        )
        if image is None:
            _run(
                [*docker, "build", *common_args, "-t", reference, "-f", dockerfiles[role], "."],
                cwd=repo_root,
            )
            image = _image_record(docker, reference)
            _validate_release_image(
                image,
                commit=normalized,
                repository=repository,
                role=role,
            )
        images[role] = image

    existing = _run([*docker, "container", "inspect", "ai-platform-frontend"], check=False)
    if existing.returncode == 0:
        payload = json.loads(existing.stdout)[0]
        labels = payload.get("Config", {}).get("Labels") or {}
        compose_dir = (repo_root.resolve() / DEFAULT_COMPOSE_RELATIVE_PATH).parent.as_posix()
        expected_config = f"{compose_dir}/docker-compose.yml"
        if labels.get("ai-platform.release-owner") == "repo-local-compose":
            if (
                labels.get("com.docker.compose.project.working_dir") != compose_dir
                or labels.get("com.docker.compose.project.config_files") != expected_config
                or labels.get("com.docker.compose.project") != COMPOSE_PROJECT
                or labels.get("com.docker.compose.service") != "frontend"
                or labels.get("com.docker.compose.oneoff") != "False"
                or not str(labels.get("com.docker.compose.config-hash") or "").strip()
            ):
                raise ReleaseAuthorityError("frontend compose ownership mismatch")
        else:
            if not replace_known_manual_frontend:
                raise ReleaseAuthorityError("manual frontend container is forbidden; rerun with explicit replacement")
            observed_image = str(payload.get("Config", {}).get("Image") or "")
            observed_image_id = str(payload.get("Image") or "")
            if not expected_manual_frontend_image or not expected_manual_frontend_image_id:
                raise ReleaseAuthorityError("manual frontend replacement requires expected image and image ID")
            if observed_image != expected_manual_frontend_image or observed_image_id != expected_manual_frontend_image_id:
                raise ReleaseAuthorityError("manual frontend identity mismatch; refusing container removal")
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
            "-p",
            COMPOSE_PROJECT,
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
    deploy.add_argument("--expected-manual-frontend-image")
    deploy.add_argument("--expected-manual-frontend-image-id")

    verify = subparsers.add_parser("verify", help="Verify source/image/runtime commit parity")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--docker-cmd", default="docker")
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
                    expected_manual_frontend_image=args.expected_manual_frontend_image,
                    expected_manual_frontend_image_id=args.expected_manual_frontend_image_id,
                ),
                None,
            )
        else:
            report = collect_live_parity(
                args.repo_root,
                args.commit,
                docker_cmd=args.docker_cmd,
            )
            _write_json(report, args.output)
            return 0 if report["verified"] else 1
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ReleaseAuthorityError) as exc:
        _write_json({"verified": False, "error": str(exc), "command": args.command}, None)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Clean-commit deployment, dirty-source preservation, and runtime parity checks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import subprocess
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence
from urllib.request import urlopen


SCHEMA_VERSION = "ai-platform.release-authority.v1"
PRESERVATION_SCHEMA_VERSION = "ai-platform.release-authority-preservation.v1"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_DIRECTORY_RE = re.compile(r"^[0-9a-f]{7,40}$")
DOCKER_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_COMPOSE_RELATIVE_PATH = Path("deploy/ai-platform/docker-compose.yml")
COMPOSE_PROJECT = "ai-platform-phaseb"
WORKER_HEARTBEAT_FILENAME = "ai-platform-worker-runtime-heartbeat.json"
WORKER_TMPDIR_EXPANSION_MARKERS = frozenset("*?$`[]{}")
WORKER_TMPDIR_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
AUTHORITATIVE_REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
AUTHORITATIVE_REPOSITORY_ALIASES = {
    AUTHORITATIVE_REPOSITORY,
    "git@github.com:demonsxxxxxx/ai-platform.git",
    "ssh://git@github.com/demonsxxxxxx/ai-platform.git",
}
SECRET_PATH_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
COMPATIBILITY_IMAGE_COMMIT_LABELS = (
    "ai-platform.source-revision",
    "ai-platform.runtime-subject",
    "ai-platform.source_revision",
    "ai-platform.source_commit",
    "ai-platform.runtime_subject",
    "ai-platform.source_tree_commit",
    "ai_platform_source_revision",
    "ai_platform_source_commit",
    "ai_platform_runtime_subject",
    "ai_platform_source_tree_commit",
)


class ReleaseAuthorityError(RuntimeError):
    """Raised when a release-authority invariant is not satisfied."""


@dataclass(frozen=True)
class _ComposeSelection:
    checkout_root: Path
    relative_paths: tuple[str, ...]
    absolute_paths: tuple[Path, ...]
    working_dir: str
    config_files: str


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
    ignored = _git_paths(repo_root, "ls-files", "--others", "--ignored", "--exclude-standard")
    if ignored:
        raise ReleaseAuthorityError("ignored worktree files are forbidden for release deployment")
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


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def resolve_compose_files(
    repo_root: Path,
    compose_files: Sequence[str | Path] | None,
) -> _ComposeSelection:
    """Validate and resolve one ordered repo-relative Compose file selection."""
    supplied_root = Path(repo_root)
    try:
        absolute_root = Path(os.path.abspath(supplied_root))
        root = supplied_root.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError("release checkout path is invalid") from exc
    if (
        not root.is_dir()
        or _is_link_or_junction(supplied_root)
        or absolute_root != root
    ):
        raise ReleaseAuthorityError("release checkout path is invalid")

    values: Sequence[str | Path]
    if compose_files is None:
        values = (DEFAULT_COMPOSE_RELATIVE_PATH.as_posix(),)
    else:
        values = compose_files
    if not values:
        raise ReleaseAuthorityError("compose file selection is invalid")

    relative_paths: list[str] = []
    absolute_paths: list[Path] = []
    identities: set[str] = set()
    for index, value in enumerate(values):
        if isinstance(value, Path):
            raw = value.as_posix()
        elif isinstance(value, str):
            raw = value
        else:
            raise ReleaseAuthorityError("compose file selection is invalid")
        invalid_text = (
            not raw
            or raw != unicodedata.normalize("NFC", raw)
            or "\\" in raw
            or "," in raw
            or any(
                unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
                for character in raw
            )
        )
        pure = PurePosixPath(raw)
        windows = PureWindowsPath(raw)
        invalid_path = (
            invalid_text
            or pure.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or pure.as_posix() != raw
            or raw.endswith("/")
            or any(part in {"", ".", ".."} for part in pure.parts)
        )
        if invalid_path:
            raise ReleaseAuthorityError("compose file selection is invalid")
        if index == 0 and raw != DEFAULT_COMPOSE_RELATIVE_PATH.as_posix():
            raise ReleaseAuthorityError("canonical main compose file must be first")
        if raw in relative_paths:
            raise ReleaseAuthorityError("duplicate compose file is forbidden")

        candidate = root.joinpath(*pure.parts)
        current = root
        for part in pure.parts:
            current = current / part
            if _is_link_or_junction(current):
                raise ReleaseAuthorityError("compose file links are forbidden")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ReleaseAuthorityError("compose file must exist") from exc
        if (
            resolved != candidate
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise ReleaseAuthorityError("compose file must be a regular checkout file")
        identity = os.path.normcase(str(resolved))
        if identity in identities or any(os.path.samefile(resolved, other) for other in absolute_paths):
            raise ReleaseAuthorityError("duplicate compose file is forbidden")
        identities.add(identity)
        relative_paths.append(raw)
        absolute_paths.append(resolved)

    working_dir = absolute_paths[0].parent.as_posix()
    return _ComposeSelection(
        checkout_root=root,
        relative_paths=tuple(relative_paths),
        absolute_paths=tuple(absolute_paths),
        working_dir=working_dir,
        config_files=",".join(path.as_posix() for path in absolute_paths),
    )


def _normalized_release_root(release_root: Path) -> Path:
    supplied = Path(release_root)
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise ReleaseAuthorityError("release root must be a normalized absolute path")
    normalized = Path(os.path.abspath(supplied))
    if normalized.exists() and not normalized.is_dir():
        raise ReleaseAuthorityError("release root must be a directory")
    if _is_link_or_junction(normalized) or normalized.resolve(strict=False) != normalized:
        raise ReleaseAuthorityError("release root symlink traversal is forbidden")
    normalized.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(normalized) or normalized.resolve(strict=True) != normalized:
        raise ReleaseAuthorityError("release root symlink traversal is forbidden")
    return normalized


def _assert_standalone_checkout(checkout: Path, release_root: Path) -> None:
    if _is_link_or_junction(checkout) or checkout.resolve(strict=False).parent != release_root:
        raise ReleaseAuthorityError("versioned release checkout symlink or path traversal is forbidden")
    git_dir = checkout / ".git"
    if not checkout.is_dir() or not git_dir.is_dir() or _is_link_or_junction(git_dir):
        raise ReleaseAuthorityError("versioned release is not an isolated Git checkout")


def _fetch_and_verify_main_commit(checkout: Path, commit: str) -> None:
    authoritative_repository(checkout)
    _git(checkout, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
    commit_object = _run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=checkout,
        check=False,
    )
    if commit_object.returncode != 0:
        raise ReleaseAuthorityError("requested commit object is not available after main fetch")
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"],
        cwd=checkout,
        check=False,
    )
    if ancestor.returncode == 1:
        raise ReleaseAuthorityError("requested commit is not reachable from fetched main")
    if ancestor.returncode != 0:
        raise ReleaseAuthorityError("unable to verify requested commit against fetched main")


def materialize_main_checkout(release_root: Path, commit: str) -> Path:
    """Fetch main and create or validate one clean isolated checkout by commit."""
    normalized = _normalize_commit(commit)
    root = _normalized_release_root(release_root)
    checkout = root / normalized
    staging = root / f".{normalized}.incoming"

    if _is_link_or_junction(staging) or staging.exists():
        raise ReleaseAuthorityError("interrupted release staging residue requires operator review")
    if _is_link_or_junction(checkout):
        raise ReleaseAuthorityError("versioned release checkout symlink is forbidden")

    if checkout.exists():
        _assert_standalone_checkout(checkout, root)
        assert_clean_commit(checkout, normalized)
        _fetch_and_verify_main_commit(checkout, normalized)
        assert_clean_commit(checkout, normalized)
        return checkout

    staging.mkdir(exist_ok=False)
    _git(staging, "init")
    _git(staging, "remote", "add", "origin", AUTHORITATIVE_REPOSITORY)
    _fetch_and_verify_main_commit(staging, normalized)
    _git(staging, "checkout", "--detach", normalized)
    assert_clean_commit(staging, normalized)
    _assert_standalone_checkout(staging, root)
    if checkout.exists() or _is_link_or_junction(checkout):
        raise ReleaseAuthorityError("versioned release destination appeared during materialization")
    staging.rename(checkout)
    _assert_standalone_checkout(checkout, root)
    return checkout


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


def _compose_identity_mismatches(
    labels: dict[str, Any],
    role: str,
    *,
    expected_compose_dir: str,
    expected_config_files: str,
) -> list[str]:
    mismatches: list[str] = []
    if labels.get("ai-platform.release-owner") != "repo-local-compose":
        mismatches.append(f"{role}_container_not_repo_local_compose_owned")
    if labels.get("ai-platform.release-role") != role:
        mismatches.append(f"{role}_container_role_mismatch")
    if labels.get("com.docker.compose.project.working_dir") != expected_compose_dir:
        mismatches.append(f"{role}_compose_working_dir_mismatch")
    if str(labels.get("com.docker.compose.project.config_files") or "") != expected_config_files:
        mismatches.append(f"{role}_compose_config_mismatch")
    if labels.get("com.docker.compose.project") != COMPOSE_PROJECT:
        mismatches.append(f"{role}_compose_project_mismatch")
    if labels.get("com.docker.compose.service") != role:
        mismatches.append(f"{role}_compose_service_mismatch")
    if labels.get("com.docker.compose.oneoff") != "False":
        mismatches.append(f"{role}_compose_oneoff_mismatch")
    if not str(labels.get("com.docker.compose.config-hash") or "").strip():
        mismatches.append(f"{role}_compose_config_hash_missing")
    return mismatches


def build_parity_report(
    *,
    expected_commit: str,
    source: dict[str, Any],
    images: dict[str, dict[str, Any]],
    containers: dict[str, dict[str, Any]],
    runtime: dict[str, Any],
    expected_compose_dir: str,
    expected_repository: str,
    expected_compose_files: Sequence[str] | None = None,
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
        if any(
            label in labels and labels.get(label) != commit
            for label in COMPATIBILITY_IMAGE_COMMIT_LABELS
        ):
            mismatches.append(f"{role}_image_compatibility_commit_mismatch")

    expected_config_files = ",".join(expected_compose_files) if expected_compose_files else (
        f"{expected_compose_dir}/docker-compose.yml"
    )
    expected_image_roles = {"api": "backend", "worker": "backend", "frontend": "frontend"}
    for role, image_role in expected_image_roles.items():
        container = containers.get(role, {})
        labels = container.get("labels") if isinstance(container.get("labels"), dict) else {}
        if container.get("running") is not True:
            mismatches.append(f"{role}_container_not_running")
        mismatches.extend(
            _compose_identity_mismatches(
                labels,
                role,
                expected_compose_dir=expected_compose_dir,
                expected_config_files=expected_config_files,
            )
        )
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_container_commit_mismatch")
        if labels.get("ai-platform.source-dirty") != "false":
            mismatches.append(f"{role}_container_dirty_label_mismatch")
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
    for label in COMPATIBILITY_IMAGE_COMMIT_LABELS:
        if label in labels and labels.get(label) != commit:
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


def _inspect_optional_container(docker: list[str], name: str) -> dict[str, Any] | None:
    existing = _run([*docker, "container", "inspect", name], check=False)
    if existing.returncode != 0:
        return None
    try:
        payload = json.loads(existing.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseAuthorityError("managed container inspect metadata is invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ReleaseAuthorityError("managed container inspect metadata is invalid")
    return payload[0]


def _compose_ownership_selection(
    labels: dict[str, Any],
    target: _ComposeSelection,
) -> _ComposeSelection | None:
    working_dir = labels.get("com.docker.compose.project.working_dir")
    config_files = labels.get("com.docker.compose.project.config_files")
    if not isinstance(working_dir, str) or not isinstance(config_files, str):
        return None
    if working_dir == target.working_dir and config_files == target.config_files:
        return target

    observed_files = config_files.split(",")
    if (
        len(observed_files) != len(target.relative_paths)
        or not observed_files
        or any(not value for value in observed_files)
    ):
        return None
    observed_main = Path(observed_files[0])
    if (
        not observed_main.is_absolute()
        or observed_main.as_posix() != observed_files[0]
        or "\\" in observed_files[0]
        or any(
            unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
            for character in observed_files[0]
        )
    ):
        return None
    observed_root = observed_main
    for _ in DEFAULT_COMPOSE_RELATIVE_PATH.parts:
        observed_root = observed_root.parent
    release_root = target.checkout_root.parent
    if (
        observed_root == target.checkout_root
        or observed_root.parent != release_root
        or not FULL_COMMIT_RE.fullmatch(target.checkout_root.name)
        or not RELEASE_DIRECTORY_RE.fullmatch(observed_root.name)
    ):
        return None
    try:
        observed = resolve_compose_files(observed_root, target.relative_paths)
    except (OSError, ReleaseAuthorityError):
        return None
    if observed.working_dir != working_dir or observed.config_files != config_files:
        return None
    return observed


def _manual_frontend_container_id(inspected: dict[str, Any]) -> str:
    container_id = inspected.get("Id")
    if not isinstance(container_id, str) or not DOCKER_CONTAINER_ID_RE.fullmatch(container_id):
        raise ReleaseAuthorityError("manual frontend container ID metadata is invalid")
    return container_id


def _preflight_managed_container_ownership(
    docker: list[str],
    selection: _ComposeSelection,
    *,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None,
    expected_manual_frontend_image_id: str | None,
) -> str | None:
    manual_frontend_id: str | None = None
    compose_owner_root: Path | None = None
    for role in ("api", "worker", "frontend"):
        name = f"ai-platform-{role}"
        inspected = _inspect_optional_container(docker, name)
        if inspected is None:
            continue
        config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if labels.get("ai-platform.release-owner") == "repo-local-compose":
            owned_selection = _compose_ownership_selection(labels, selection)
            if owned_selection is None:
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            if _compose_identity_mismatches(
                labels,
                role,
                expected_compose_dir=owned_selection.working_dir,
                expected_config_files=owned_selection.config_files,
            ):
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            if compose_owner_root is not None and compose_owner_root != owned_selection.checkout_root:
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            compose_owner_root = owned_selection.checkout_root
            continue
        if role != "frontend":
            raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
        if not replace_known_manual_frontend:
            raise ReleaseAuthorityError(
                "manual frontend container is forbidden; rerun with explicit replacement"
            )
        observed_image = str(config.get("Image") or "")
        observed_image_id = str(inspected.get("Image") or "")
        if not expected_manual_frontend_image or not expected_manual_frontend_image_id:
            raise ReleaseAuthorityError(
                "manual frontend replacement requires expected image and image ID"
            )
        if (
            observed_image != expected_manual_frontend_image
            or observed_image_id != expected_manual_frontend_image_id
        ):
            raise ReleaseAuthorityError(
                "manual frontend identity mismatch; refusing container removal"
            )
        manual_frontend_id = _manual_frontend_container_id(inspected)
    return manual_frontend_id


def _container_inspect_record(
    docker: list[str],
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _docker_json(docker, "container", "inspect", name)[0]
    state = payload.get("State") or {}
    config = payload.get("Config") if isinstance(payload.get("Config"), dict) else {}
    record = {
        "name": name,
        "image_id": payload.get("Image"),
        "labels": config.get("Labels") or {},
        "running": state.get("Running") is True,
        "pid": state.get("Pid"),
        "health": (state.get("Health") or {}).get("Status") or "",
        "ports": (payload.get("NetworkSettings") or {}).get("Ports") or {},
    }
    return record, payload


def _container_record(docker: list[str], name: str) -> dict[str, Any]:
    record, _ = _container_inspect_record(docker, name)
    return record


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


def _worker_heartbeat_path(inspected: dict[str, Any]) -> str:
    invalid = "worker container TMPDIR metadata is invalid"
    config = inspected.get("Config")
    if not isinstance(config, dict):
        raise ReleaseAuthorityError(invalid)
    environment = config.get("Env")
    if not isinstance(environment, list) or any(
        not isinstance(entry, str) for entry in environment
    ):
        raise ReleaseAuthorityError(invalid)

    entries = [
        entry
        for entry in environment
        if entry == "TMPDIR" or entry.startswith("TMPDIR=")
    ]
    if not entries:
        tmpdir = "/tmp"
    elif len(entries) != 1 or not entries[0].startswith("TMPDIR="):
        raise ReleaseAuthorityError(invalid)
    else:
        tmpdir = entries[0].partition("=")[2]
        invalid_path = (
            not tmpdir
            or not PurePosixPath(tmpdir).is_absolute()
            or tmpdir.startswith("//")
            or "\\" in tmpdir
            or any(character in WORKER_TMPDIR_EXPANSION_MARKERS for character in tmpdir)
            or any(
                unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
                for character in tmpdir
            )
            or ".." in PurePosixPath(tmpdir).parts
            or posixpath.normpath(tmpdir) != tmpdir
        )
        if invalid_path:
            raise ReleaseAuthorityError(invalid)
    return str(PurePosixPath(tmpdir) / WORKER_HEARTBEAT_FILENAME)


def _worker_container_id(inspected: dict[str, Any]) -> str:
    container_id = inspected.get("Id")
    if not isinstance(container_id, str) or not DOCKER_CONTAINER_ID_RE.fullmatch(
        container_id
    ):
        raise ReleaseAuthorityError("worker container ID metadata is invalid")
    return container_id


def _read_worker_heartbeat(
    docker: list[str],
    container_id: str,
    path: str,
) -> dict[str, Any]:
    try:
        return _container_json_file(docker, container_id, path)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        raise ReleaseAuthorityError("worker runtime heartbeat read failed") from None


def _container_process_alive(docker: list[str], container_id: str, pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        result = _run(
            [
                *docker,
                "exec",
                container_id,
                "/bin/sh",
                "-c",
                'kill -0 "$1"',
                "sh",
                str(pid),
            ],
            check=False,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
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
    compose_files: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Collect live Docker and embedded provenance for the strict parity report."""
    normalized = assert_clean_commit(repo_root, commit)
    selection = resolve_compose_files(repo_root, compose_files)
    docker = _docker_base(docker_cmd)
    refs = build_image_references(normalized)
    images = {
        "backend": _image_record(docker, refs["backend"]),
        "frontend": _image_record(docker, refs["frontend"]),
    }
    worker_name = "ai-platform-worker"
    api_container = _container_record(docker, "ai-platform-api")
    worker_container, worker_inspect = _container_inspect_record(docker, worker_name)
    worker_container_id = _worker_container_id(worker_inspect)
    frontend_container = _container_record(docker, "ai-platform-frontend")
    containers = {
        "api": api_container,
        "worker": worker_container,
        "frontend": frontend_container,
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
    worker_heartbeat = _read_worker_heartbeat(
        docker,
        worker_container_id,
        _worker_heartbeat_path(worker_inspect),
    )
    _validate_worker_runtime_heartbeat(
        worker_heartbeat,
        process_alive=_container_process_alive(
            docker,
            worker_container_id,
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
    repository = authoritative_repository(repo_root)
    return build_parity_report(
        expected_commit=normalized,
        source={"commit": normalized, "dirty": False, "path": str(repo_root.resolve())},
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=selection.working_dir,
        expected_compose_files=[path.as_posix() for path in selection.absolute_paths],
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
    compose_files: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Build immutable images and recreate the repo-local compose release."""
    normalized = assert_clean_commit(repo_root, commit)
    selection = resolve_compose_files(repo_root, compose_files)
    docker = _docker_base(docker_cmd)
    repository = authoritative_repository(repo_root)
    manual_frontend_id = _preflight_managed_container_ownership(
        docker,
        selection,
        replace_known_manual_frontend=replace_known_manual_frontend,
        expected_manual_frontend_image=expected_manual_frontend_image,
        expected_manual_frontend_image_id=expected_manual_frontend_image_id,
    )
    refs = build_image_references(normalized)
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

    assert_clean_commit(repo_root, normalized)
    revalidated = resolve_compose_files(repo_root, selection.relative_paths)
    if revalidated != selection:
        raise ReleaseAuthorityError("compose file selection changed during release preflight")
    if manual_frontend_id is not None:
        current_frontend = _inspect_optional_container(docker, "ai-platform-frontend")
        if (
            current_frontend is None
            or _manual_frontend_container_id(current_frontend) != manual_frontend_id
        ):
            raise ReleaseAuthorityError("manual frontend changed before removal")
        _run([*docker, "container", "rm", "-f", manual_frontend_id])

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
    compose_file_args = [
        argument
        for path in selection.absolute_paths
        for argument in ("-f", str(path))
    ]
    _run(
        [
            *compose_command,
            "compose",
            "-p",
            COMPOSE_PROJECT,
            "--env-file",
            str(env_file.resolve()),
            *compose_file_args,
            "up",
            "-d",
            "--no-build",
        ],
        cwd=selection.absolute_paths[0].parent,
    )
    return {
        "commit": normalized,
        "images": refs,
        "compose_file": str(selection.absolute_paths[0]),
        "compose_files": [str(path) for path in selection.absolute_paths],
    }


def deploy_main_commit(
    release_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    env_file: Path,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None = None,
    expected_manual_frontend_image_id: str | None = None,
    compose_files: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Deploy and verify an exact fetched main commit from an isolated checkout."""
    normalized = _normalize_commit(commit)
    checkout = materialize_main_checkout(release_root, normalized)
    deployment = deploy_clean_commit(
        checkout,
        normalized,
        docker_cmd=docker_cmd,
        env_file=env_file,
        replace_known_manual_frontend=replace_known_manual_frontend,
        expected_manual_frontend_image=expected_manual_frontend_image,
        expected_manual_frontend_image_id=expected_manual_frontend_image_id,
        compose_files=compose_files,
    )
    parity = collect_live_parity(
        checkout,
        normalized,
        docker_cmd=docker_cmd,
        compose_files=compose_files,
    )
    if parity.get("verified") is not True:
        mismatches = parity.get("mismatches")
        detail = ", ".join(str(item) for item in mismatches) if isinstance(mismatches, list) else "unknown"
        raise ReleaseAuthorityError(f"deployed release parity failed: {detail}")
    return {
        "commit": normalized,
        "checkout": str(checkout),
        "deployment": deployment,
        "parity": parity,
    }


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
    deploy.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

    deploy_main = subparsers.add_parser(
        "deploy-main-commit",
        help="Fetch, deploy, and verify one exact main commit",
    )
    deploy_main.add_argument("--release-root", type=Path, required=True)
    deploy_main.add_argument("--commit", required=True)
    deploy_main.add_argument("--docker-cmd", default="docker")
    deploy_main.add_argument("--env-file", type=Path, required=True)
    deploy_main.add_argument("--replace-known-manual-frontend", action="store_true")
    deploy_main.add_argument("--expected-manual-frontend-image")
    deploy_main.add_argument("--expected-manual-frontend-image-id")
    deploy_main.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

    verify = subparsers.add_parser("verify", help="Verify source/image/runtime commit parity")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--docker-cmd", default="docker")
    verify.add_argument("--output", type=Path)
    verify.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

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
                    compose_files=args.compose_files,
                ),
                None,
            )
        elif args.command == "deploy-main-commit":
            _write_json(
                deploy_main_commit(
                    args.release_root,
                    args.commit,
                    docker_cmd=args.docker_cmd,
                    env_file=args.env_file,
                    replace_known_manual_frontend=args.replace_known_manual_frontend,
                    expected_manual_frontend_image=args.expected_manual_frontend_image,
                    expected_manual_frontend_image_id=args.expected_manual_frontend_image_id,
                    compose_files=args.compose_files,
                ),
                None,
            )
        else:
            report = collect_live_parity(
                args.repo_root,
                args.commit,
                docker_cmd=args.docker_cmd,
                compose_files=args.compose_files,
            )
            _write_json(report, args.output)
            return 0 if report["verified"] else 1
    except ReleaseAuthorityError as exc:
        _write_json({"verified": False, "error": str(exc), "command": args.command}, None)
        return 2
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        _write_json(
            {
                "verified": False,
                "error": "release authority command failed",
                "command": args.command,
            },
            None,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

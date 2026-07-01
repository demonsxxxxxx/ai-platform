import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from typing import Any, Sequence


PROVENANCE_FILENAME = "ai-platform-build-provenance.json"
PROVENANCE_SCHEMA_VERSION = "ai-platform.frontend-build-provenance.v1"


class DeploymentError(RuntimeError):
    """Raised when a static frontend release cannot be activated safely."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeploymentError(f"dist_build_provenance_unreadable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"dist_build_provenance_invalid_json: {path}") from exc
    if not isinstance(payload, dict):
        raise DeploymentError("dist_build_provenance_invalid_json")
    return payload


def verify_dist_provenance(dist: Path, *, expected_commit: str) -> dict[str, Any]:
    """Verify that a built frontend dist is clean and matches the expected commit."""
    dist = Path(dist)
    if not dist.is_dir():
        raise DeploymentError(f"dist_missing: {dist}")
    provenance_path = dist / PROVENANCE_FILENAME
    if not provenance_path.exists():
        raise DeploymentError("dist_build_provenance_missing")

    provenance = _load_json(provenance_path)
    if provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        raise DeploymentError("dist_build_provenance_schema_mismatch")

    git = provenance.get("git")
    if not isinstance(git, dict):
        raise DeploymentError("dist_build_git_provenance_missing")
    build_commit = git.get("commit")
    if build_commit != expected_commit:
        raise DeploymentError("dist_build_commit_mismatch")
    dirty = git.get("dirty")
    if dirty is True:
        raise DeploymentError("dist_built_from_dirty_worktree")
    if dirty is not False:
        raise DeploymentError("dist_build_dirty_state_unknown")
    return provenance


def _safe_extract_dist(package_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    try:
        with tarfile.open(package_path, "r:*") as archive:
            members = archive.getmembers()
            for member in members:
                member_name = member.name
                member_path = Path(member_name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise DeploymentError(f"release_package_unsafe_path: {member_name}")
                if member.issym() or member.islnk():
                    raise DeploymentError(f"release_package_link_forbidden: {member_name}")
                if not member.isdir() and not member.isfile():
                    raise DeploymentError(f"release_package_member_forbidden: {member_name}")
                target_path = (destination / member_name).resolve()
                if os.path.commonpath([destination_root, target_path]) != str(destination_root):
                    raise DeploymentError(f"release_package_unsafe_path: {member_name}")
            for member in members:
                target_path = destination / member.name
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise DeploymentError(f"release_package_member_unreadable: {member.name}")
                with source, target_path.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
    except (tarfile.TarError, OSError) as exc:
        raise DeploymentError(f"release_package_unreadable: {package_path}") from exc

    dist = destination / "dist"
    if not dist.is_dir():
        raise DeploymentError("release_package_dist_missing")
    return dist


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or _is_junction(path)


def _remove_path(path: Path) -> None:
    if not _path_exists(path):
        return
    if path.is_symlink():
        path.unlink()
    elif _is_junction(path):
        path.rmdir()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _same_resolved(left: Path, right: Path) -> bool:
    try:
        return _path_exists(left) and _path_exists(right) and left.resolve() == right.resolve()
    except OSError:
        return False


def _link_directory(link_path: Path, target: Path) -> str:
    _remove_path(link_path)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    try:
        link_path.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        if os.name != "nt":
            raise DeploymentError(f"active_pointer_create_failed: {link_path}") from exc
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise DeploymentError(
                f"active_pointer_create_failed: {link_path}: {result.stderr.strip()}"
            ) from exc
        return "junction"


def _backup_active_dist(frontend_root: Path, release_dist: Path, expected_commit: str) -> Path | None:
    active_dist = frontend_root / "dist"
    if not _path_exists(active_dist) or _same_resolved(active_dist, release_dist):
        return None
    backups_root = frontend_root / "backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    backup = backups_root / f"dist-backup-before-{expected_commit[:12]}-{_timestamp()}"
    if active_dist.is_symlink() or _is_junction(active_dist):
        _remove_path(active_dist)
        return None
    shutil.move(str(active_dist), str(backup))
    return backup


def _install_release_dist(staged_dist: Path, release_dist: Path) -> None:
    if release_dist.exists():
        verify_dist_provenance(
            release_dist,
            expected_commit=release_dist.parent.name,
        )
        return
    if release_dist.parent.exists():
        raise DeploymentError(f"release_directory_incomplete: {release_dist.parent}")
    releases_root = release_dist.parent.parent
    releases_root.mkdir(parents=True, exist_ok=True)
    temp_release = releases_root / f".{release_dist.parent.name}.{_timestamp()}.tmp"
    if temp_release.exists():
        shutil.rmtree(temp_release)
    temp_release.mkdir(parents=True)
    shutil.move(str(staged_dist), str(temp_release / "dist"))
    temp_release.rename(release_dist.parent)


def _run_restart_command(restart_command: Sequence[str] | None) -> dict[str, Any]:
    if not restart_command:
        raise DeploymentError("restart_command_required")
    result = subprocess.run(
        list(restart_command),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DeploymentError(f"restart_command_failed: {result.returncode}")
    return {
        "command": list(restart_command),
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def deploy_static_frontend_release(
    *,
    package_path: Path,
    frontend_root: Path,
    expected_commit: str,
    api_base: str,
    restart: bool = False,
    restart_command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Deploy a packaged static frontend dist into immutable release directories."""
    package_path = Path(package_path)
    frontend_root = Path(frontend_root)
    if not package_path.is_file():
        raise DeploymentError(f"release_package_missing: {package_path}")
    if not expected_commit:
        raise DeploymentError("expected_commit_required")

    frontend_root.mkdir(parents=True, exist_ok=True)
    staging_parent = frontend_root / ".deploy-staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="frontend-release-", dir=staging_parent))
    backup_path: Path | None = None
    restart_result: dict[str, Any] | None = None
    current_pointer_kind = ""
    dist_pointer_kind = ""
    try:
        staged_dist = _safe_extract_dist(package_path, staging_root)
        provenance = verify_dist_provenance(staged_dist, expected_commit=expected_commit)
        release_dist = frontend_root / "releases" / expected_commit / "dist"
        _install_release_dist(staged_dist, release_dist)

        backup_path = _backup_active_dist(frontend_root, release_dist, expected_commit)
        current_pointer_kind = _link_directory(frontend_root / "current", release_dist)
        dist_pointer_kind = _link_directory(frontend_root / "dist", release_dist)
        if restart:
            restart_result = _run_restart_command(restart_command)

        return {
            "status": "deployed",
            "frontend_root": str(frontend_root),
            "expected_commit": expected_commit,
            "active_release": str(release_dist),
            "api_base": api_base,
            "build_provenance": provenance,
            "backup": str(backup_path) if backup_path else None,
            "current_pointer_kind": current_pointer_kind,
            "dist_pointer_kind": dist_pointer_kind,
            "restart": restart_result
            if restart_result is not None
            else {"requested": restart, "performed": False},
        }
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _render_markdown(result: dict[str, Any]) -> str:
    restart = result["restart"]
    return (
        "# ai-platform Static Frontend Deploy\n\n"
        f"- status: `{result['status']}`\n"
        f"- expected_commit: `{result['expected_commit']}`\n"
        f"- frontend_root: `{result['frontend_root']}`\n"
        f"- active_release: `{result['active_release']}`\n"
        f"- current_pointer_kind: `{result['current_pointer_kind']}`\n"
        f"- dist_pointer_kind: `{result['dist_pointer_kind']}`\n"
        f"- backup: `{result['backup']}`\n"
        f"- api_base: `{result['api_base']}`\n"
        f"- restart_performed: `{str(bool(restart.get('exit_code') == 0)).lower()}`\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy a built ai-platform frontend dist package into a static release root."
    )
    parser.add_argument("--package-path", required=True, type=Path)
    parser.add_argument(
        "--frontend-root",
        default=Path("/home/xinlin.jiang/frontend-pr111-smoke"),
        type=Path,
    )
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8020")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument(
        "--restart-command",
        nargs="+",
        help="Command tokens to execute after activation. Required when --restart is set.",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)

    try:
        result = deploy_static_frontend_release(
            package_path=args.package_path,
            frontend_root=args.frontend_root,
            expected_commit=args.expected_commit,
            api_base=args.api_base,
            restart=args.restart,
            restart_command=args.restart_command,
        )
    except DeploymentError as exc:
        print(f"deploy_frontend_static_error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

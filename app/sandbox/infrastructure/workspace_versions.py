"""Stopped Attempt host-bind versions, private to Sandbox and never artifacts.

Callers own provider observation and generation fencing. These synchronous I/O
operations must finish (including flock/fsync) before releasing that owner.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.sandbox.domain import host_bind as bind
from app.sandbox.domain.workspace_version import (
    WorkspaceVersionError,
    authorized_workspace_skill_names,
    workspace_version_exclusion,
)

_SCHEMA = "ai-platform.workspace-version.v1"
_VERSION_DIRECTORY = ".ai-platform-workspace-version-v1"
_MANIFEST = "manifest.json"
_RESTORE_BINDING = ".ai-platform-workspace-restore-v1.json"
_SCOPE_FIELDS = ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")
_DEFAULT_LIMITS = {"max_files": 128, "max_file_bytes": 64 * 1024 * 1024,
                   "max_total_bytes": 256 * 1024 * 1024, "max_scan_nodes": 4096}


def _scope(request: Any) -> dict[str, str]:
    result = {field: getattr(request, field) for field in _SCOPE_FIELDS}
    for value in result.values():
        if not isinstance(value, str) or not value or any(char in value for char in "/\\\x00") or value in {".", ".."}:
            raise WorkspaceVersionError("workspace_scope_invalid")
    return result


def _limits(value: Mapping | None) -> dict[str, int]:
    result = dict(_DEFAULT_LIMITS)
    if value is not None:
        if not isinstance(value, Mapping) or set(value) - set(result):
            raise WorkspaceVersionError("workspace_limits_invalid")
        result.update(value)
    if any(type(item) is not int or item <= 0 for item in result.values()):
        raise WorkspaceVersionError("workspace_limits_invalid")
    return result


def _root(value: Any) -> Path:
    if value is None:
        raise WorkspaceVersionError("workspace_root_required")
    return bind._normalized_absolute(value, label="workspace root")


def _require_secure_io() -> None:
    if not bind._secure_descriptor_walk_supported() or bind.fcntl is None:
        raise WorkspaceVersionError("workspace_secure_io_unavailable")


def _open_directory(path: Path) -> int:
    """Follow no symlink in any ancestor, including the configured root."""
    descriptor = os.open(path.anchor, bind._directory_flags())
    try:
        for component in path.parts[1:]:
            child = os.open(component, bind._directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_manifest(version: Path) -> dict:
    descriptor = _open_directory(version)
    os.close(descriptor)
    manifest = json.loads(bind.read_host_bind_control_file(version, _MANIFEST, max_bytes=1024 * 1024))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _SCHEMA:
        raise WorkspaceVersionError("workspace_version_invalid")
    return manifest


def _receipt(manifest: dict) -> dict:
    return {key: manifest[key] for key in (
        "schema_version", "version_id", "scope", "owner_generation", "state", "file_count",
        "total_bytes", "source_ref", "exclusions", "source_stop_evidence",
    )}


def _check_budget(files: list, directories: list, total: int, limits: dict) -> None:
    if len(files) > limits["max_files"] or len(directories) > limits["max_files"] or total > limits["max_total_bytes"]:
        raise WorkspaceVersionError("workspace_quota_exceeded")


def _copy_tree(source: Path, target: Path | None, *, skill_names: set[str], limits: dict,
               restored: bool = False, policy_prefix: str = "") -> dict:
    files: list[dict] = []
    directories: list[str] = []
    exclusions: Counter = Counter()
    total = 0
    scanned = 0
    root_fd = _open_directory(source)
    root_node = os.fstat(root_fd)

    def walk(descriptor: int, prefix: str) -> None:
        nonlocal total, scanned
        before = bind._source_identity(os.fstat(descriptor))
        names = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                scanned += 1
                if scanned > limits["max_scan_nodes"]:
                    raise WorkspaceVersionError("workspace_quota_exceeded")
                names.append(entry.name)
        names.sort()
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            if bind.normalize_host_bind_relative_path(relative) != relative:
                raise WorkspaceVersionError("workspace_path_unsafe")
            policy_path = f"{policy_prefix}/{relative}" if policy_prefix else relative
            reason = workspace_version_exclusion(policy_path, skill_names=skill_names)
            if reason:
                exclusions[reason] += 1
                continue
            node = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not (stat.S_ISREG(node.st_mode) or stat.S_ISDIR(node.st_mode)):
                exclusions["non_regular"] += 1
                continue
            if node.st_dev != root_node.st_dev:
                raise WorkspaceVersionError("workspace_path_unsafe")
            if relative.split("/")[0] == ".claude" and len(relative.split("/")) <= 4 and not stat.S_ISDIR(node.st_mode):
                raise WorkspaceVersionError("workspace_path_unsafe")
            destination = target.joinpath(*relative.split("/")) if target is not None else None
            if stat.S_ISDIR(node.st_mode):
                child = os.open(name, bind._directory_flags(), dir_fd=descriptor)
                try:
                    bind._assert_delivery_directory(os.fstat(child), expected_uid=root_node.st_uid, expected_gid=root_node.st_gid)
                    directories.append(relative)
                    _check_budget(files, directories, total, limits)
                    if destination is not None:
                        destination.mkdir(mode=0o755 if restored else 0o700)
                    walk(child, relative)
                finally:
                    os.close(child)
            else:
                source_fd, opened = bind._open_delivery_file(root_fd, relative, expected_uid=root_node.st_uid, expected_gid=root_node.st_gid)
                try:
                    if opened.st_size > limits["max_file_bytes"]:
                        raise WorkspaceVersionError("workspace_quota_exceeded")
                    _check_budget(files + [relative], directories, total + opened.st_size, limits)
                    if destination is None:
                        digest_builder = hashlib.sha256()
                        size = 0
                        while chunk := os.read(source_fd, 1024 * 1024):
                            size += len(chunk)
                            if size > limits["max_file_bytes"]:
                                raise WorkspaceVersionError("workspace_quota_exceeded")
                            digest_builder.update(chunk)
                        if size != opened.st_size or bind._source_identity(opened) != bind._source_identity(os.fstat(source_fd)):
                            raise WorkspaceVersionError("workspace_source_changed")
                        digest = digest_builder.hexdigest()
                    else:
                        size, digest = bind._copy_stable_delivery_file(source_fd, opened, destination, max_file_bytes=limits["max_file_bytes"])
                        if restored:
                            destination.chmod(0o755 if opened.st_mode & 0o111 else 0o644)
                        elif opened.st_mode & 0o111:
                            destination.chmod(0o700)
                        if restored or opened.st_mode & 0o111:
                            copied_fd = os.open(destination, bind._file_flags())
                            try:
                                os.fsync(copied_fd)
                            finally:
                                os.close(copied_fd)
                finally:
                    os.close(source_fd)
                total += size
                files.append({"path": relative, "size": size, "digest": digest, "executable": bool(opened.st_mode & 0o111)})
                _check_budget(files, directories, total, limits)
        if before != bind._source_identity(os.fstat(descriptor)):
            raise WorkspaceVersionError("workspace_source_changed")

    try:
        bind._assert_delivery_directory(root_node, expected_uid=root_node.st_uid, expected_gid=root_node.st_gid)
        walk(root_fd, "")
    finally:
        os.close(root_fd)
    return {"files": files, "directories": directories, "file_count": len(files), "total_bytes": total, "exclusions": dict(exclusions)}


def _validate_inventory(inventory: dict, manifest: dict) -> None:
    if any(inventory[key] != manifest[key] for key in ("files", "directories", "file_count", "total_bytes")) or inventory["exclusions"]:
        raise WorkspaceVersionError("workspace_version_invalid")


def save_workspace_version(*, request: Any, workspace_lease: Any, stop_evidence: Mapping,
                           limits: Mapping | None = None, workspace_root: Any = None) -> dict:
    """Seal one idempotent version after a trusted exact-Attempt stop/404."""
    scope = _scope(request)
    if (not isinstance(stop_evidence, Mapping)
        or stop_evidence.get("state") not in {"provider_stopped", "provider_not_found"}
        or stop_evidence.get("scope") != scope
        or type(stop_evidence.get("owner_generation")) is not int
        or stop_evidence["owner_generation"] != request.owner_generation):
        raise WorkspaceVersionError("workspace_writer_unverified")
    budget = _limits(limits)
    _require_secure_io()
    temporary = None
    lock = None
    try:
        root = _root(workspace_root)
        workspace = bind.resolve_opensandbox_host_bind_source(root, request, workspace_lease, require_existing=True)
        attempt = workspace.parent
        descriptor = _open_directory(attempt)
        os.close(descriptor)
        lock = bind._open_snapshot_lock(attempt)
        version = attempt / _VERSION_DIRECTORY
        if os.path.lexists(version):
            existing = _read_manifest(version)
            if existing.get("scope") != scope or existing.get("owner_generation") != request.owner_generation:
                raise WorkspaceVersionError("workspace_scope_invalid")
            inventory = _copy_tree(version / "data", None, skill_names=set(existing["skill_names"]), limits=budget)
            _validate_inventory(inventory, existing)
            _fsync_directory(attempt)
            return _receipt(existing)
        temporary = attempt / f"{_VERSION_DIRECTORY}.tmp-{secrets.token_hex(16)}"
        temporary.mkdir(mode=0o700)
        data = temporary / "data"
        data.mkdir(mode=0o700)
        skill_names = authorized_workspace_skill_names(request)
        inventory = _copy_tree(workspace, data, skill_names=skill_names, limits=budget)
        manifest = {
            **inventory, "schema_version": _SCHEMA, "version_id": secrets.token_hex(16),
            "scope": scope, "owner_generation": request.owner_generation, "state": "saved",
            "source_ref": version.relative_to(root).as_posix(),
            "source_stop_evidence": stop_evidence["state"], "skill_names": sorted(skill_names),
            "limits": budget,
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
        if len(manifest_bytes) > 1024 * 1024:
            raise WorkspaceVersionError("workspace_quota_exceeded")
        manifest_fd = os.open(temporary / _MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            bind._write_all(manifest_fd, manifest_bytes)
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)
        bind._fsync_tree_directories(temporary)
        os.rename(temporary, version)
        temporary = None
        _fsync_directory(attempt)
        return _receipt(manifest)
    except WorkspaceVersionError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceVersionError("workspace_save_failed") from exc
    finally:
        try:
            if temporary is not None:
                shutil.rmtree(temporary)
        finally:
            if lock is not None:
                try:
                    bind.fcntl.flock(lock, bind.fcntl.LOCK_UN)
                finally:
                    os.close(lock)


def _source_request(receipt: Mapping, scope: dict) -> SimpleNamespace:
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("scope"), dict):
        raise WorkspaceVersionError("workspace_version_invalid")
    source_scope = receipt["scope"]
    if set(source_scope) != set(_SCOPE_FIELDS) or any(source_scope.get(key) != scope[key] for key in _SCOPE_FIELDS[:4]):
        raise WorkspaceVersionError("workspace_scope_invalid")
    source_request = SimpleNamespace(**source_scope)
    _scope(source_request)
    if source_scope == scope:
        raise WorkspaceVersionError("workspace_target_not_new")
    return source_request


def _source_version(root: Path, source_request: Any, receipt: Mapping) -> tuple[Path, dict]:
    version = bind._expected_attempt_root(root, source_request) / _VERSION_DIRECTORY
    if receipt.get("source_ref") != version.relative_to(root).as_posix():
        raise WorkspaceVersionError("workspace_scope_invalid")
    manifest = _read_manifest(version)
    if _receipt(manifest) != dict(receipt):
        raise WorkspaceVersionError("workspace_version_invalid")
    return version, manifest


def verify_workspace_version(*, receipt: Mapping, request: Any, workspace_root: Any) -> dict:
    _require_secure_io()
    try:
        version, manifest = _source_version(_root(workspace_root), request, receipt)
        inventory = _copy_tree(version / "data", None,
                               skill_names=set(manifest["skill_names"]), limits=_limits(manifest["limits"]))
        _validate_inventory(inventory, manifest)
        return _receipt(manifest)
    except WorkspaceVersionError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceVersionError("workspace_version_invalid") from exc


def _write_restore_binding(attempt: Path, payload: dict) -> None:
    temporary = attempt / f"{_RESTORE_BINDING}.tmp-{secrets.token_hex(16)}"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            bind._write_all(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, attempt / _RESTORE_BINDING)
        _fsync_directory(attempt)
    finally:
        temporary.unlink(missing_ok=True)


def _directory_identity(path: Path) -> list[int]:
    descriptor = _open_directory(path)
    try:
        node = os.fstat(descriptor)
        return [node.st_dev, node.st_ino]
    finally:
        os.close(descriptor)


def _skill_output_names(manifest: dict) -> set[str]:
    return {parts[2] for relative in manifest["directories"]
            if len(parts := relative.split("/")) == 4
            and parts[:2] == [".claude", "skills"] and parts[3] == "output"}


def restore_workspace_version(*, receipt: Mapping, request: Any, target_workspace_lease: Any,
                              authorized_input_paths=(), authorized_skill_names=(),
                              workspace_root: Any = None) -> dict:
    """Restore into an absent workspace before current config/input staging.

    authorized_input_paths identify current input destinations to reserve for
    restaging, never authorization to copy an old original from a saved version.
    """
    scope = _scope(request)
    source_request = _source_request(receipt, scope)
    reserved = tuple(bind.normalize_host_bind_relative_path(path) for path in authorized_input_paths)
    skills = authorized_workspace_skill_names(request).intersection(authorized_skill_names)
    _require_secure_io()
    temporary = None
    lock = None
    try:
        root = _root(workspace_root)
        target = bind.resolve_opensandbox_host_bind_source(root, request, target_workspace_lease)
        expected_source, manifest = _source_version(root, source_request, receipt)
        descriptor = _open_directory(target.parent)
        os.close(descriptor)
        lock = bind._open_snapshot_lock(target.parent)
        if os.path.lexists(target):
            raise WorkspaceVersionError("workspace_target_not_new")
        temporary = target.parent / f".workspace-restore-v1.tmp-{secrets.token_hex(16)}"
        temporary.mkdir(mode=0o755)
        inventory = _copy_tree(expected_source / "data", temporary, skill_names=set(manifest["skill_names"]), limits=_limits(manifest["limits"]), restored=True)
        _validate_inventory(inventory, manifest)
        # Validate the whole immutable source before applying current authority.
        exclusions: Counter = Counter()
        for relative in sorted(manifest["directories"], key=lambda value: value.count("/")):
            if not (temporary / relative).exists():
                continue
            reason = workspace_version_exclusion(relative, skill_names=skills)
            if reason or any(relative == path or relative.startswith(path + "/") for path in reserved):
                shutil.rmtree(temporary / relative)
                exclusions[reason or "authorized_inputs"] += 1
        for item in manifest["files"]:
            relative = item["path"]
            path = temporary / relative
            if not path.exists():
                continue
            reason = workspace_version_exclusion(relative, skill_names=skills)
            if reason or any(relative == name or relative.startswith(name + "/") for name in reserved):
                path.unlink()
                exclusions[reason or "authorized_inputs"] += 1
        pending = sorted(name for name in _skill_output_names(manifest).intersection(skills)
                         if (temporary / ".claude" / "skills" / name / "output").is_dir())
        # SkillStager replaces each Skill tree; publish task output only afterwards.
        if (temporary / ".claude").exists():
            shutil.rmtree(temporary / ".claude")
        bind._fsync_tree_directories(temporary)
        binding = {
            "restore_id": secrets.token_hex(16), "scope": scope,
            "owner_generation": request.owner_generation, "version_id": manifest["version_id"],
            "workspace_identity": _directory_identity(temporary),
            "pending_skill_names": pending, "publications": {},
            "state": "awaiting_skill_staging" if pending else "complete",
        }
        os.rename(temporary, target)
        temporary = None
        _fsync_directory(target.parent)
        _write_restore_binding(target.parent, binding)
        return {"state": "awaiting_skill_staging" if pending else "restored",
                "version_id": manifest["version_id"], "exclusions": dict(exclusions),
                "restore_id": binding["restore_id"], "skill_outputs_pending": pending}
    except WorkspaceVersionError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceVersionError("workspace_restore_failed") from exc
    finally:
        try:
            if temporary is not None:
                shutil.rmtree(temporary)
        finally:
            if lock is not None:
                try:
                    bind.fcntl.flock(lock, bind.fcntl.LOCK_UN)
                finally:
                    os.close(lock)


def _staged_skill_identity(workspace: Path, name: str) -> dict:
    root_fd = _open_directory(workspace)
    try:
        node = os.fstat(root_fd)
        descriptor, markdown = bind._open_delivery_file(
            root_fd, f".claude/skills/{name}/SKILL.md",
            expected_uid=node.st_uid, expected_gid=node.st_gid,
        )
        os.close(descriptor)
        return {"skill_identity": _directory_identity(workspace / ".claude" / "skills" / name),
                "markdown_identity": list(bind._source_identity(markdown))}
    finally:
        os.close(root_fd)


def _validate_published_output(output: Path, name: str, manifest: dict, publication: dict) -> None:
    if _directory_identity(output) != publication["directory_identity"]:
        raise WorkspaceVersionError("workspace_skill_output_conflict")
    prefix = f".claude/skills/{name}/output/"
    files = [{**item, "path": item["path"][len(prefix):]}
             for item in manifest["files"] if item["path"].startswith(prefix)]
    expected = {
        "files": files,
        "directories": [path[len(prefix):] for path in manifest["directories"] if path.startswith(prefix)],
        "file_count": len(files), "total_bytes": sum(item["size"] for item in files),
    }
    actual = _copy_tree(output, None, skill_names={name}, limits=_limits(manifest["limits"]),
                        policy_prefix=prefix.rstrip("/"))
    _validate_inventory(actual, expected)


def restore_workspace_skill_outputs(*, receipt: Mapping, restore_receipt: Mapping, request: Any,
                                    target_workspace_lease: Any, staging_evidence: Mapping,
                                    authorized_skill_names=(), workspace_root: Any = None) -> dict:
    """Publish deferred output after trusted staging, strictly before execution.

    Existing output is a conflict unless the private restore journal proves it
    was published by this operation and its identity/content are still intact.
    """
    scope = _scope(request)
    source_request = _source_request(receipt, scope)
    if (not isinstance(staging_evidence, Mapping) or staging_evidence.get("state") != "skills_staged"
        or staging_evidence.get("execution_phase") != "before_executor_start"
        or staging_evidence.get("scope") != scope
        or type(staging_evidence.get("owner_generation")) is not int
        or staging_evidence["owner_generation"] != request.owner_generation
        or not isinstance(staging_evidence.get("staged_skill_names"), list)
        or not all(isinstance(name, str) for name in staging_evidence["staged_skill_names"])):
        raise WorkspaceVersionError("workspace_staging_unverified")
    if not isinstance(restore_receipt, Mapping):
        raise WorkspaceVersionError("workspace_restore_binding_invalid")
    skills = authorized_workspace_skill_names(request).intersection(authorized_skill_names)
    _require_secure_io()
    temporary = None
    lock = None
    try:
        root = _root(workspace_root)
        target = bind.resolve_opensandbox_host_bind_source(root, request, target_workspace_lease, require_existing=True)
        version, manifest = _source_version(root, source_request, receipt)
        descriptor = _open_directory(target.parent)
        os.close(descriptor)
        lock = bind._open_snapshot_lock(target.parent)
        binding = json.loads(bind.read_host_bind_control_file(target.parent, _RESTORE_BINDING, max_bytes=64 * 1024))
        if (binding.get("scope") != scope or binding.get("owner_generation") != request.owner_generation
            or binding.get("version_id") != manifest["version_id"]
            or binding.get("restore_id") != restore_receipt.get("restore_id")
            or restore_receipt.get("version_id") != manifest["version_id"]
            or binding.get("workspace_identity") != _directory_identity(target)
            or binding.get("pending_skill_names") != restore_receipt.get("skill_outputs_pending")
            or binding.get("state") not in {"awaiting_skill_staging", "publishing_skill_outputs", "complete"}):
            raise WorkspaceVersionError("workspace_restore_binding_invalid")
        pending = set(binding["pending_skill_names"])
        if not pending.issubset(_skill_output_names(manifest)):
            raise WorkspaceVersionError("workspace_restore_binding_invalid")
        selected = sorted(pending.intersection(skills))
        if not set(selected).issubset(staging_evidence["staged_skill_names"]):
            raise WorkspaceVersionError("workspace_staging_unverified")
        publications = binding["publications"]
        if not set(publications).issubset(pending):
            raise WorkspaceVersionError("workspace_restore_binding_invalid")
        revoked_plans = set(publications) - set(selected)
        for name in revoked_plans:
            publication = publications[name]
            if publication.get("published") is not False or name not in staging_evidence["staged_skill_names"]:
                raise WorkspaceVersionError("workspace_staging_unverified")
            identity = _staged_skill_identity(target, name)
            output = target / ".claude" / "skills" / name / "output"
            if os.path.lexists(output) or any(publication[key] != value for key, value in identity.items()):
                raise WorkspaceVersionError("workspace_staging_unverified")
        if revoked_plans:
            for name in revoked_plans:
                del publications[name]
            _write_restore_binding(target.parent, binding)
        temporary = target.parent / f".workspace-skill-output-v1.tmp-{secrets.token_hex(16)}"
        temporary.mkdir(mode=0o700)
        inventory = _copy_tree(version / "data", temporary, skill_names=set(manifest["skill_names"]),
                               limits=_limits(manifest["limits"]), restored=True)
        _validate_inventory(inventory, manifest)
        bind._fsync_tree_directories(temporary)
        identities = {}
        for name in selected:
            identities[name] = _staged_skill_identity(target, name)
            output = target / ".claude" / "skills" / name / "output"
            publication = publications.get(name)
            if publication and any(publication[key] != value for key, value in identities[name].items()):
                raise WorkspaceVersionError("workspace_staging_unverified")
            if os.path.lexists(output):
                if not publication:
                    raise WorkspaceVersionError("workspace_skill_output_conflict")
                _validate_published_output(output, name, manifest, publication)
            elif binding["state"] == "complete" or (publication and publication.get("published")):
                raise WorkspaceVersionError("workspace_skill_output_conflict")
        for name in selected:
            skill = target / ".claude" / "skills" / name
            output = skill / "output"
            if _staged_skill_identity(target, name) != identities[name]:
                raise WorkspaceVersionError("workspace_staging_unverified")
            if not os.path.lexists(output):
                source = temporary / ".claude" / "skills" / name / "output"
                publications[name] = {**identities[name], "directory_identity": _directory_identity(source), "published": False}
                binding["state"] = "publishing_skill_outputs"
                _write_restore_binding(target.parent, binding)
                os.rename(source, output)
            _fsync_directory(skill)
            publications[name]["published"] = True
            _write_restore_binding(target.parent, binding)
        binding["state"] = "complete"
        _write_restore_binding(target.parent, binding)
        return {"state": "restored", "version_id": manifest["version_id"], "restore_id": binding["restore_id"],
                "restored_skill_names": selected,
                "exclusions": {"skill_configuration": len(pending - set(selected))} if pending - set(selected) else {}}
    except WorkspaceVersionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise WorkspaceVersionError("workspace_skill_output_restore_failed") from exc
    finally:
        try:
            if temporary is not None:
                shutil.rmtree(temporary)
        finally:
            if lock is not None:
                try:
                    bind.fcntl.flock(lock, bind.fcntl.LOCK_UN)
                finally:
                    os.close(lock)

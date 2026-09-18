from __future__ import annotations

import hashlib
import threading
import zipfile
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from xml.parsers import expat

from app.sandbox.api import workspace_delivery_file_allowed

_MAX_WORKSPACE_ARTIFACT_FILES = 128
_MAX_WORKSPACE_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_DOCX_ARCHIVE_ENTRIES = 2000
_MAX_DOCX_ARCHIVE_ENTRY_BYTES = 32 * 1024 * 1024
_MAX_DOCX_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024

def _parse_docx_xml(raw_xml: bytes) -> ElementTree.Element:
    parser = expat.ParserCreate()

    def reject_declaration(*_args: object) -> None:
        raise ValueError("DTD and entity declarations are not allowed")

    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_declaration
    parser.Parse(raw_xml, True)
    return ElementTree.fromstring(raw_xml)


def valid_docx_artifact(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > _MAX_DOCX_ARCHIVE_ENTRIES
                or len({entry.filename for entry in entries}) != len(entries)
            ):
                return False
            total_bytes = 0
            for entry in entries:
                if entry.file_size < 0 or entry.file_size > _MAX_DOCX_ARCHIVE_ENTRY_BYTES:
                    return False
                total_bytes += entry.file_size
                if total_bytes > _MAX_DOCX_ARCHIVE_TOTAL_BYTES:
                    return False
            expected_roots = {
                "[Content_Types].xml": "{http://schemas.openxmlformats.org/package/2006/content-types}Types",
                "_rels/.rels": "{http://schemas.openxmlformats.org/package/2006/relationships}Relationships",
            }
            member_names = {entry.filename for entry in entries}
            if "word/document.xml" not in member_names or any(
                member not in member_names for member in expected_roots
            ):
                return False
            roots: dict[str, ElementTree.Element] = {}
            for member in (*expected_roots, "word/document.xml"):
                roots[member] = _parse_docx_xml(archive.read(member))
            if any(roots[member].tag != root for member, root in expected_roots.items()):
                return False
            if roots["word/document.xml"].tag not in {
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                "{http://purl.oclc.org/ooxml/wordprocessingml/main}document",
            }:
                return False
            content_type_overrides = [
                child
                for child in roots["[Content_Types].xml"]
                if child.tag
                == "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
                and child.get("PartName") == "/word/document.xml"
            ]
            office_document_relationships = [
                child
                for child in roots["_rels/.rels"]
                if child.tag
                == "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
                and child.get("Type")
                in {
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
                    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
                }
            ]
            return (
                len(content_type_overrides) == 1
                and content_type_overrides[0].get("ContentType")
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                and len(office_document_relationships) == 1
                and bool(office_document_relationships[0].get("Id"))
                and office_document_relationships[0].get("TargetMode", "Internal")
                == "Internal"
                and office_document_relationships[0].get("Target")
                == "word/document.xml"
            )
    except Exception:
        return False


def artifact_content_type(filename: str) -> str:
    lower = filename.lower()
    explicit = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    for suffix, content_type in explicit.items():
        if lower.endswith(suffix):
            return content_type
    return "application/octet-stream"


def artifact_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".docx"):
        return "result_docx"
    if lower.endswith(".json"):
        return "result_json"
    if lower.endswith((".txt", ".md")):
        return "report_txt"
    return "runtime_file"


def artifact_label(filename: str, _kind: str) -> str:
    return filename


def collect_workspace_artifacts(
    *,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    run_id: str,
    source_executor: str,
    workspace: Path,
    response_files: Iterable[str],
    required_artifact_types: Iterable[str],
    artifact_factory: Callable[..., Any],
    storage_factory: Callable[[], Any],
    ensure_inside: Callable[[Path, Path, str], None],
    response_file_descriptors: Iterable[Mapping[str, object]] | None = None,
    allowed_skill_names: Iterable[str] = (),
    storage_scope: str = "",
    abandoned: threading.Event | None = None,
    reserve_storage: Callable[[str], str] | None = None,
) -> list[Any]:
    storage = storage_factory()
    selected_paths = list(response_files)
    if len(selected_paths) > _MAX_WORKSPACE_ARTIFACT_FILES:
        raise ValueError("workspace artifacts exceed the file count limit")
    descriptors = list(response_file_descriptors or ())
    descriptor_by_path: dict[str, Mapping[str, object]] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            raise ValueError("response file descriptor is invalid")
        raw_source_path = descriptor.get("source_path")
        if not isinstance(raw_source_path, str):
            raise ValueError("response file descriptor is invalid")
        source_path = raw_source_path.replace("\\", "/")
        if source_path in descriptor_by_path:
            raise ValueError("response file descriptor is duplicated")
        descriptor_by_path[source_path] = descriptor
    normalized_selected_paths = [
        path.replace("\\", "/") if isinstance(path, str) else path
        for path in selected_paths
    ]
    if descriptors and list(descriptor_by_path) != normalized_selected_paths:
        raise ValueError("response file descriptors do not match response files")

    candidates: list[tuple[Path, Mapping[str, object] | None]] = []
    seen_candidates: set[Path] = set()
    total_bytes = 0
    workspace_root = workspace.resolve(strict=True)
    for raw_path in selected_paths:
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ValueError("response file path is invalid")
        relative = PurePosixPath(raw_path.replace("\\", "/"))
        windows_path = PureWindowsPath(raw_path)
        if (
            relative.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in raw_path.replace("\\", "/").split("/"))
            or not workspace_delivery_file_allowed(
                relative,
                allowed_skill_names=allowed_skill_names,
            )
        ):
            raise ValueError("response file path is invalid")
        item = workspace_root
        for part in relative.parts:
            item /= part
            if item.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
        try:
            resolved = item.resolve(strict=True)
            resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("response file is unavailable") from exc
        if not resolved.is_file():
            raise ValueError("response file is unavailable")
        ensure_inside(
            workspace,
            resolved,
            "workspace artifact must stay inside run workspace",
        )
        if resolved in seen_candidates:
            raise ValueError("response file path is duplicated")
        size_bytes = resolved.stat().st_size
        if size_bytes > _MAX_WORKSPACE_ARTIFACT_FILE_BYTES:
            raise ValueError("workspace artifact exceeds the per-file byte limit")
        total_bytes += size_bytes
        if total_bytes > _MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES:
            raise ValueError("workspace artifacts exceed the total byte limit")
        seen_candidates.add(resolved)
        candidates.append((resolved, descriptor_by_path.get(relative.as_posix())))

    for path, _descriptor in candidates:
        if artifact_type(path.name) == "result_docx" and not valid_docx_artifact(path):
            raise ValueError("response DOCX file is invalid")
    if set(required_artifact_types) - {artifact_type(path.name) for path, _ in candidates}:
        return []

    artifacts: list[Any] = []
    try:
        for index, (path, descriptor) in enumerate(candidates, start=1):
            if abandoned is not None and abandoned.is_set():
                raise RuntimeError("workspace artifact collection abandoned")
            content_type = artifact_content_type(path.name)
            kind = artifact_type(path.name)
            display_name = path.name
            delivery_role: str | None = None
            delivery_description: str | None = None
            if descriptor is not None:
                raw_display_name = descriptor.get("display_name")
                if raw_display_name is not None:
                    if (
                        not isinstance(raw_display_name, str)
                        or not raw_display_name.strip()
                        or len(raw_display_name) > 255
                        or "\x00" in raw_display_name
                        or "/" in raw_display_name
                        or "\\" in raw_display_name
                        or raw_display_name in {".", ".."}
                    ):
                        raise ValueError("response file display name is invalid")
                    display_name = raw_display_name.strip()
                raw_role = descriptor.get("role")
                if raw_role is not None:
                    if raw_role not in {"primary", "supporting"}:
                        raise ValueError("response file role is invalid")
                    delivery_role = str(raw_role)
                raw_description = descriptor.get("description")
                if raw_description is not None:
                    if (
                        not isinstance(raw_description, str)
                        or len(raw_description) > 2_000
                        or "\x00" in raw_description
                    ):
                        raise ValueError("response file description is invalid")
                    delivery_description = raw_description.strip() or None
            content = path.read_bytes()
            content_digest = hashlib.sha256(content).hexdigest()
            scoped_path = f"reconciliations/{storage_scope}/" if storage_scope else ""
            storage_key = (
                f"tenants/{tenant_id}/workspaces/{workspace_id}/sessions/{session_id}/"
                f"runs/{run_id}/{scoped_path}artifacts/{index}/{content_digest}/{path.name}"
            )
            provisional_cleanup_id = reserve_storage(storage_key) if reserve_storage else None
            if abandoned is not None and abandoned.is_set():
                raise RuntimeError("workspace artifact collection abandoned")
            stored = storage.put_bytes(
                storage_key=storage_key,
                content=content,
                content_type=content_type,
            )
            artifacts.append(
                artifact_factory(
                    artifact_type=kind,
                    label=artifact_label(display_name, kind),
                    content_type=content_type,
                    storage_key=stored.storage_key,
                    size_bytes=stored.size_bytes,
                    manifest={
                        "source_executor": source_executor,
                        "workspace_output": path.relative_to(workspace_root).as_posix(),
                        "delivery_scope": "assistant_response",
                        **(
                            {"delivery_role": delivery_role}
                            if delivery_role is not None
                            else {}
                        ),
                        **(
                            {"delivery_description": delivery_description}
                            if delivery_description is not None
                            else {}
                        ),
                    },
                    provisional_cleanup_id=provisional_cleanup_id,
                )
            )
            if abandoned is not None and abandoned.is_set():
                raise RuntimeError("workspace artifact collection abandoned")
    except Exception:
        if reserve_storage is not None:
            raise
        cleanup_error: Exception | None = None
        for artifact in artifacts:
            try:
                storage.delete_object(storage_key=artifact.storage_key)
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise RuntimeError("workspace_artifact_cleanup_failed") from cleanup_error
        raise
    return artifacts

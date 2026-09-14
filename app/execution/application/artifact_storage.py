from __future__ import annotations

import hashlib
import threading
import zipfile
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from xml.parsers import expat

_MAX_WORKSPACE_ARTIFACT_FILES = 128
_MAX_WORKSPACE_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_DOCX_ARCHIVE_ENTRIES = 2000
_MAX_DOCX_ARCHIVE_ENTRY_BYTES = 32 * 1024 * 1024
_MAX_DOCX_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024

_WORKSPACE_INTERNAL_DIRS = {
    ".ai-platform",
    ".claude",
    ".claude-config",
    ".home",
    ".pins",
    ".tmp",
    "inputs",
    "logs",
    "runtime",
    "_audit",
    "_debug",
    "artifacts",
    "tasks",
}
_WORKSPACE_INTERNAL_FILES = {
    "run-state.json",
    "step-event.json",
    "step-response.json",
}

# These directories belong to the platform or contain inputs and installed
# Skills. They stay inside the run workspace but are never user artifacts.
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


def artifact_label(filename: str, kind: str) -> str:
    if kind == "result_docx":
        return "Word 文件"
    if kind == "result_json":
        return "结果 JSON"
    if kind == "report_txt":
        return "详细报告"
    return filename


def _is_user_workspace_file(path: Path, workspace: Path) -> bool:
    """Keep collection rooted in the platform workspace and exclude internals."""
    relative = path.relative_to(workspace)
    if (
        not relative.parts
        or relative.parts[0] == "review"
        or any(part in _WORKSPACE_INTERNAL_DIRS for part in relative.parts[:-1])
    ):
        return False
    if path.name in _WORKSPACE_INTERNAL_FILES:
        return False
    return relative.parts[0] != "outputs" or "delivery" in relative.parts[1:-1]


def collect_workspace_artifacts(
    *,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    run_id: str,
    source_executor: str,
    workspace: Path,
    required_artifact_types: Iterable[str],
    artifact_factory: Callable[..., Any],
    storage_factory: Callable[[], Any],
    ensure_inside: Callable[[Path, Path, str], None],
    storage_scope: str = "",
    abandoned: threading.Event | None = None,
    reserve_storage: Callable[[str], str] | None = None,
) -> list[Any]:
    storage = storage_factory()
    output_dirs: list[Path] = [workspace]
    legacy_output = workspace / "output"
    if legacy_output.is_dir():
        ensure_inside(workspace, legacy_output, "workspace output must stay inside the run workspace")
        output_dirs.append(legacy_output)
    outputs_root = workspace / "outputs"
    if outputs_root.is_dir():
        ensure_inside(workspace, outputs_root, "workspace output must stay inside the run workspace")
        for delivery_dir in sorted(outputs_root.rglob("delivery")):
            if delivery_dir.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
            if delivery_dir.is_dir():
                ensure_inside(outputs_root, delivery_dir, "workspace artifact must stay inside output directory")
                output_dirs.append(delivery_dir)
    candidates: list[Path] = []
    seen_candidates: set[Path] = set()
    total_bytes = 0
    for output_dir in output_dirs:
        for item in sorted(output_dir.rglob("*")):
            if item.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
            if not item.is_file():
                continue
            if not _is_user_workspace_file(item, workspace):
                continue
            ensure_inside(workspace, item, "workspace artifact must stay inside run workspace")
            resolved = item.resolve(strict=False)
            if resolved in seen_candidates:
                continue
            size_bytes = item.stat().st_size
            if size_bytes > _MAX_WORKSPACE_ARTIFACT_FILE_BYTES:
                raise ValueError("workspace artifact exceeds the per-file byte limit")
            total_bytes += size_bytes
            if total_bytes > _MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES:
                raise ValueError("workspace artifacts exceed the total byte limit")
            if len(candidates) >= _MAX_WORKSPACE_ARTIFACT_FILES:
                raise ValueError("workspace artifacts exceed the file count limit")
            seen_candidates.add(resolved)
            candidates.append(item)

    valid_candidates = [
        path
        for path in candidates
        if artifact_type(path.name) != "result_docx" or valid_docx_artifact(path)
    ]
    if set(required_artifact_types) - {artifact_type(path.name) for path in valid_candidates}:
        return []

    artifacts: list[Any] = []
    try:
        for index, path in enumerate(valid_candidates, start=1):
            if abandoned is not None and abandoned.is_set():
                raise RuntimeError("workspace artifact collection abandoned")
            content_type = artifact_content_type(path.name)
            kind = artifact_type(path.name)
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
                    label=artifact_label(path.name, kind),
                    content_type=content_type,
                    storage_key=stored.storage_key,
                    size_bytes=stored.size_bytes,
                    manifest={
                        "source_executor": source_executor,
                        "workspace_output": path.relative_to(workspace).as_posix(),
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

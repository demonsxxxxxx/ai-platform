"""Runtime admission for immutable Skill deliverable contracts.

This module owns the execution-time seam between a pinned Skill contract and
ordinary-user artifacts.  Its callers provide a run, workspace roots, and the
executor facts already bound to that run; this module decides whether anything
can be staged and returns only safe terminal artifacts or a fail-closed result.
"""

from __future__ import annotations

import posixpath
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from xml.etree import ElementTree

from app.capabilities import required_artifact_types_for_skill
from app.executors.base import ArtifactManifest, ExecutorResult
from app.path_safety import ensure_path_inside
from app.skills.deliverables import (
    SkillDeliverableContractError,
    deliverable_contract_from_manifest,
    deliverable_contract_upgrade_packet,
    deliverable_spec,
    process_evidence_is_valid,
    public_artifact_matches_contract,
    required_deliverable_types,
    verified_xlsx_delivery,
)
from app.storage import ObjectStorage


_MAX_WORKSPACE_ARTIFACT_FILES = 128
_MAX_WORKSPACE_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024
_REQUIRED_DOCX_MAX_ENTRY_COUNT = 128
_REQUIRED_DOCX_MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
_REQUIRED_DOCX_MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_REQUIRED_DOCX_MAX_COMPRESSION_RATIO = 100
_OPC_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_OPC_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OPC_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_WORDPROCESSINGML_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_MAIN_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


@dataclass(frozen=True)
class DeliveryRuntimeOutcome:
    """One fully evaluated delivery decision for an executor terminal."""

    artifacts: tuple[ArtifactManifest, ...]
    contract: dict[str, object] | None
    error_code: str | None = None
    error_message: str | None = None
    missing_types: tuple[str, ...] = ()
    upgrade_packet: dict[str, object] | None = None


@dataclass(frozen=True)
class _WorkspaceArtifactCandidate:
    """One validated workspace file awaiting artifact persistence."""

    index: int
    path: Path
    artifact_type: str
    content_type: str
    label: str


class _RequiredTerminalDeliverableCardinalityError(Exception):
    """Signal bounded required-deliverable cardinality failure before storage."""


_DELIVERY_ERRORS = {
    "skill_deliverable_contract_invalid": "The selected Skill delivery contract is unavailable.",
    "skill_deliverable_contract_upgrade_required": "The selected Skill package must be upgraded before file delivery.",
    "skill_deliverable_process_evidence_missing": "The required file-delivery execution evidence is unavailable.",
    "required_artifact_missing": "The file-required Skill did not produce every required artifact type.",
    "required_artifact_cardinality_invalid": "The file-required Skill must produce exactly one of each required artifact type.",
}


def _outcome(code: str, *, contract=None, **kwargs) -> DeliveryRuntimeOutcome:
    return DeliveryRuntimeOutcome(
        artifacts=(),
        contract=contract,
        error_code=code,
        error_message=_DELIVERY_ERRORS[code],
        **kwargs,
    )


def stage_adapter_delivery(
    *,
    payload: object,
    pinned_manifests: Mapping[str, Mapping[str, object]],
    workspace: Path,
    executor_payload: Mapping[str, object],
    source_executor: str,
    artifact_dirs: Callable[[Path], list[Path]],
    storage: ObjectStorage | None = None,
) -> DeliveryRuntimeOutcome:
    """Stage only terminally valid deliverables after evaluating the selected pin."""

    try:
        contract = selected_deliverable_contract(
            skill_id=str(getattr(payload, "skill_id", "") or ""),
            manifests=pinned_manifests,
        )
    except SkillDeliverableContractError:
        return _outcome("skill_deliverable_contract_invalid")
    if _uncontracted_delivery_requires_upgrade(payload, contract, workspace, artifact_dirs):
        manifest = pinned_manifests.get(str(getattr(payload, "skill_id", "") or "")) or {}
        return _outcome(
            "skill_deliverable_contract_upgrade_required",
            upgrade_packet=deliverable_contract_upgrade_packet(
                skill_id=getattr(payload, "skill_id", ""),
                version=manifest.get("content_hash") or manifest.get("version"),
            ),
        )
    if contract is not None and not _process_evidence_is_valid(payload, contract, executor_payload):
        return _outcome("skill_deliverable_process_evidence_missing", contract=contract)
    try:
        artifacts = collect_workspace_artifacts(
            payload=payload,
            workspace=workspace,
            source_executor=source_executor,
            artifact_dirs=artifact_dirs,
            deliverable_contract=contract,
            storage=storage,
        )
    except _RequiredTerminalDeliverableCardinalityError:
        return _outcome("required_artifact_cardinality_invalid", contract=contract)
    missing_types = _missing_terminal_types(contract, artifacts)
    if missing_types:
        return _outcome("required_artifact_missing", contract=contract, missing_types=missing_types)
    return DeliveryRuntimeOutcome(artifacts=tuple(artifacts), contract=contract)


def enforce_pinned_deliverable_result(
    result: ExecutorResult,
    *,
    payload: object,
    attempt_id: str,
) -> ExecutorResult:
    """Fail closed before artifact persistence when an executor bypasses admission."""

    if result.status != "succeeded":
        return result
    try:
        contract = selected_deliverable_contract(
            skill_id=str(getattr(payload, "skill_id", "") or ""),
            manifests=_payload_manifests(payload),
        )
    except SkillDeliverableContractError:
        return _failed_result(result, "skill_deliverable_contract_invalid", _DELIVERY_ERRORS["skill_deliverable_contract_invalid"])
    skill_id = str(getattr(payload, "skill_id", "") or "")
    legacy_required_types = required_artifact_types_for_skill(skill_id)
    if result.artifacts and skill_id != "general-chat" and contract is None and not legacy_required_types:
        failed = _failed_result(result, "skill_deliverable_contract_upgrade_required", _DELIVERY_ERRORS["skill_deliverable_contract_upgrade_required"])
        return replace(
            failed,
            executor_payload={
                **failed.executor_payload,
                "deliverable_contract_upgrade": deliverable_contract_upgrade_packet(
                    skill_id=getattr(payload, "skill_id", ""),
                    version=_selected_version(payload),
                ),
            },
        )
    if contract is None:
        return result
    if not _process_evidence_is_valid(payload, contract, result.executor_payload, attempt_id=attempt_id):
        return _failed_result(result, "skill_deliverable_process_evidence_missing", _DELIVERY_ERRORS["skill_deliverable_process_evidence_missing"])
    artifacts = [
        artifact for artifact in result.artifacts if public_artifact_matches_contract(contract, artifact)
    ]
    missing_types, duplicate_types = _required_terminal_type_errors(
        contract, (artifact.artifact_type for artifact in artifacts)
    )
    if duplicate_types:
        return _failed_result(
            result,
            "required_artifact_cardinality_invalid",
            _DELIVERY_ERRORS["required_artifact_cardinality_invalid"],
        )
    if missing_types:
        return _failed_result(result, "required_artifact_missing", _DELIVERY_ERRORS["required_artifact_missing"])
    return replace(
        result,
        artifacts=artifacts,
        result={
            **result.result,
            "message": "已生成结果文件。",
            "artifact_count": len(artifacts),
        },
    )


def persisted_required_artifact_types(payload: object, executor_payload: Mapping[str, object]) -> set[str]:
    """Return only the pin-owned types, with the legacy capability fallback."""

    try:
        contract = selected_deliverable_contract(
            skill_id=str(getattr(payload, "skill_id", "") or ""),
            manifests=_payload_manifests(payload),
        )
    except SkillDeliverableContractError:
        contract = None
    if contract is not None:
        return set(required_deliverable_types(contract))
    return set(required_artifact_types_for_skill(str(getattr(payload, "skill_id", "") or ""))) | {
        str(value)
        for value in executor_payload.get("required_artifact_types", [])
        if isinstance(value, str) and value
    }


def selected_deliverable_contract(
    *,
    skill_id: str,
    manifests: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    """Resolve one delivery contract only from the selected immutable manifest."""

    manifest = manifests.get(skill_id)
    return deliverable_contract_from_manifest(manifest) if manifest is not None else None


def required_delivery_artifact_types(
    payload: object,
    contract: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Resolve contract-owned terminal types before legacy capability requirements."""

    if contract is not None:
        return required_deliverable_types(contract)
    return required_artifact_types_for_skill(str(getattr(payload, "skill_id", "") or ""))


def legacy_artifact_type(filename: str, skill_id: str | None = None) -> str:
    """Classify a legacy artifact without weakening a pinned delivery contract."""

    return _artifact_type(filename, skill_id or "")


def collect_workspace_artifacts(
    *,
    payload: object,
    workspace: Path,
    source_executor: str,
    artifact_dirs: Callable[[Path], list[Path]],
    deliverable_contract: Mapping[str, object] | None,
    storage: ObjectStorage | None = None,
) -> list[ArtifactManifest]:
    """Collect only bounded, contract-approved files before storage writes."""

    candidates = _workspace_candidates(workspace, artifact_dirs)
    approved_candidates = _approved_workspace_artifact_candidates(
        candidates=candidates,
        workspace=workspace,
        payload=payload,
        deliverable_contract=deliverable_contract,
    )
    if deliverable_contract is not None:
        missing_types, duplicate_types = _required_terminal_type_errors(
            deliverable_contract,
            (candidate.artifact_type for candidate in approved_candidates),
        )
        if duplicate_types:
            raise _RequiredTerminalDeliverableCardinalityError
        if missing_types:
            return []
    object_storage = storage or ObjectStorage()
    artifacts: list[ArtifactManifest] = []
    for candidate in approved_candidates:
        storage_key = (
            f"tenants/{getattr(payload, 'tenant_id')}/workspaces/{getattr(payload, 'workspace_id')}/"
            f"sessions/{getattr(payload, 'session_id')}/runs/{getattr(payload, 'run_id')}/"
            f"artifacts/{candidate.index}/{candidate.path.name}"
        )
        stored = object_storage.put_bytes(
            storage_key=storage_key,
            content=candidate.path.read_bytes(),
            content_type=candidate.content_type,
        )
        artifacts.append(
            ArtifactManifest(
                artifact_type=candidate.artifact_type,
                label=candidate.label,
                content_type=candidate.content_type,
                storage_key=stored.storage_key,
                size_bytes=stored.size_bytes,
                manifest={
                    "source_executor": source_executor,
                    "workspace_output": candidate.path.relative_to(workspace).as_posix(),
                    **(
                        {"deliverable_type": candidate.artifact_type}
                        if deliverable_contract is not None
                        else {}
                    ),
                },
            )
        )
    return artifacts


def _approved_workspace_artifact_candidates(
    *,
    candidates: Iterable[Path],
    workspace: Path,
    payload: object,
    deliverable_contract: Mapping[str, object] | None,
) -> list[_WorkspaceArtifactCandidate]:
    """Classify every safe public candidate before any artifact storage write."""

    approved: list[_WorkspaceArtifactCandidate] = []
    for index, path in enumerate(candidates, start=1):
        if deliverable_contract is not None:
            spec = _contract_delivery_spec(workspace, path, deliverable_contract)
            if spec is None:
                continue
            artifact_type = str(spec["artifact_type"])
            content_type = str(spec["content_type"])
            label = str(spec["label"])
        else:
            artifact_type = _artifact_type(path.name, str(getattr(payload, "skill_id", "") or ""))
            if artifact_type in {"reviewed_docx", "translated_docx"} and not _is_usable_docx(path):
                continue
            content_type = _artifact_content_type(path.name)
            label = _artifact_label(path.name, artifact_type)
        approved.append(
            _WorkspaceArtifactCandidate(
                index=index,
                path=path,
                artifact_type=artifact_type,
                content_type=content_type,
                label=label,
            )
        )
    return approved


def _payload_manifests(payload: object) -> dict[str, Mapping[str, object]]:
    manifests = getattr(payload, "skill_manifests", [])
    if not isinstance(manifests, list):
        return {}
    return {
        str(manifest.get("skill_id") or ""): manifest
        for manifest in manifests
        if isinstance(manifest, dict) and str(manifest.get("skill_id") or "")
    }


def _selected_version(payload: object) -> object:
    manifest = _payload_manifests(payload).get(str(getattr(payload, "skill_id", "") or "")) or {}
    return manifest.get("content_hash") or manifest.get("version")


def _failed_result(result: ExecutorResult, error_code: str, message: str) -> ExecutorResult:
    return replace(
        result,
        status="failed",
        artifacts=[],
        result={**result.result, "message": message, "error_code": error_code},
    )


def _process_evidence_is_valid(
    payload: object,
    contract: Mapping[str, object],
    executor_payload: Mapping[str, object],
    *,
    attempt_id: str | None = None,
) -> bool:
    return process_evidence_is_valid(
        contract,
        skill_id=str(getattr(payload, "skill_id", "") or ""),
        binding={
            "tenant_id": getattr(payload, "tenant_id", ""),
            "workspace_id": getattr(payload, "workspace_id", ""),
            "user_id": getattr(payload, "user_id", ""),
            "session_id": getattr(payload, "session_id", ""),
            "run_id": getattr(payload, "run_id", ""),
            "attempt_id": attempt_id or getattr(payload, "attempt_id", ""),
        },
        executor_payload=executor_payload,
    )


def _uncontracted_delivery_requires_upgrade(
    payload: object,
    contract: Mapping[str, object] | None,
    workspace: Path,
    artifact_dirs: Callable[[Path], list[Path]],
) -> bool:
    return (
        contract is None
        and str(getattr(payload, "skill_id", "") or "") != "general-chat"
        and not required_delivery_artifact_types(payload, None)
        and any(item.is_file() for root in artifact_dirs(workspace) for item in root.rglob("*"))
    )


def _missing_terminal_types(
    contract: Mapping[str, object] | None,
    artifacts: list[ArtifactManifest] | tuple[ArtifactManifest, ...],
) -> tuple[str, ...]:
    return _required_terminal_type_errors(
        contract, (artifact.artifact_type for artifact in artifacts)
    )[0]


def _required_terminal_type_errors(
    contract: Mapping[str, object] | None,
    artifact_types: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return missing and duplicate required types without exposing file details."""

    if contract is None:
        return (), ()
    counts = Counter(artifact_types)
    required_types = required_deliverable_types(contract)
    return (
        tuple(sorted(artifact_type for artifact_type in required_types if counts[artifact_type] == 0)),
        tuple(sorted(artifact_type for artifact_type in required_types if counts[artifact_type] > 1)),
    )


def _workspace_candidates(workspace: Path, artifact_dirs: Callable[[Path], list[Path]]) -> list[Path]:
    candidates: list[Path] = []
    seen_candidates: set[Path] = set()
    total_bytes = 0
    for output_dir in artifact_dirs(workspace):
        for item in sorted(output_dir.rglob("*")):
            if item.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
            if not item.is_file():
                continue
            ensure_path_inside(output_dir, item, "workspace artifact must stay inside output directory")
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
    return candidates


def _contract_delivery_spec(
    workspace: Path,
    path: Path,
    contract: Mapping[str, object],
) -> dict[str, object] | None:
    if not _is_controlled_delivery_file(workspace, path):
        return None
    for raw_spec in contract["allowed_public_deliverables"]:
        if not isinstance(raw_spec, Mapping) or not path.name.lower().endswith(
            str(raw_spec["extension"]).lower()
        ):
            continue
        spec = deliverable_spec(contract, str(raw_spec["deliverable_type"]))
        if str(spec["deliverable_type"]) == "xlsx" and verified_xlsx_delivery(path, spec=spec):
            return spec
    return None


def _is_controlled_delivery_file(workspace: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return False
    return bool(
        len(relative.parts) >= 3
        and relative.parts[0] == "outputs"
        and "delivery" in relative.parts[:-1]
    )


def _artifact_content_type(filename: str) -> str:
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
    return next((value for suffix, value in explicit.items() if filename.lower().endswith(suffix)), "application/octet-stream")


def _artifact_type(filename: str, skill_id: str) -> str:
    lower = filename.lower()
    if skill_id == "qa-file-reviewer" and lower.endswith(".docx"):
        return "reviewed_docx"
    if skill_id == "baoyu-translate" and lower.endswith(".docx"):
        return "translated_docx"
    if lower.endswith(".docx"):
        return "result_docx"
    if lower.endswith(".json"):
        return "result_json"
    if lower.endswith((".txt", ".md")):
        return "report_txt"
    return "runtime_file"


def _artifact_label(filename: str, artifact_type: str) -> str:
    labels = {
        "reviewed_docx": "审核 Word",
        "translated_docx": "翻译 Word",
        "result_docx": "Word 文件",
        "result_json": "结果 JSON",
        "report_txt": "详细报告",
    }
    return labels.get(artifact_type, filename)


def _is_usable_docx(path: Path) -> bool:
    try:
        if not 0 < path.stat().st_size <= _REQUIRED_DOCX_MAX_COMPRESSED_BYTES:
            return False
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not _docx_archive_entries_are_bounded(entries):
                return False
            content_types = archive.read("[Content_Types].xml")
            relationships = archive.read("_rels/.rels")
            document = archive.read("word/document.xml")
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return False
    try:
        content_types_root = ElementTree.fromstring(content_types)
        relationships_root = ElementTree.fromstring(relationships)
        document_root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return False
    if (
        content_types_root.tag != f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Types"
        or relationships_root.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationships"
        or document_root.tag != f"{{{_WORDPROCESSINGML_NAMESPACE}}}document"
    ):
        return False
    has_document_override = any(
        item.tag == f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Override"
        and item.attrib.get("PartName") == "/word/document.xml"
        and item.attrib.get("ContentType") == _WORD_MAIN_DOCUMENT_CONTENT_TYPE
        for item in content_types_root
    )
    relationship_ids: set[str] = set()
    root_office_document_relationships = []
    for item in relationships_root:
        if item.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationship":
            return False
        relationship_id = str(item.attrib.get("Id") or "")
        if not _is_valid_opc_relationship_id(relationship_id) or relationship_id in relationship_ids:
            return False
        relationship_ids.add(relationship_id)
        if str(item.attrib.get("Type") or "") == _OPC_OFFICE_DOCUMENT_RELATIONSHIP:
            root_office_document_relationships.append(item)
    has_main_document_relationship = (
        len(root_office_document_relationships) == 1
        and str(root_office_document_relationships[0].attrib.get("TargetMode") or "").lower() != "external"
        and _resolve_root_relationship_target(
            str(root_office_document_relationships[0].attrib.get("Target") or "")
        )
        == "word/document.xml"
    )
    body = next(
        (item for item in document_root if item.tag == f"{{{_WORDPROCESSINGML_NAMESPACE}}}body"),
        None,
    )
    return has_document_override and has_main_document_relationship and body is not None and any(True for _ in body)


def _is_valid_opc_relationship_id(value: str) -> bool:
    return bool(value) and ":" not in value and _is_xml_ncname_start(value[0]) and all(
        _is_xml_ncname_char(character) for character in value[1:]
    )


def _is_xml_ncname_start(character: str) -> bool:
    codepoint = ord(character)
    return (
        character == "_"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
        or 0xC0 <= codepoint <= 0xD6
        or 0xD8 <= codepoint <= 0xF6
        or 0xF8 <= codepoint <= 0x2FF
        or 0x370 <= codepoint <= 0x37D
        or 0x37F <= codepoint <= 0x1FFF
        or 0x200C <= codepoint <= 0x200D
        or 0x2070 <= codepoint <= 0x218F
        or 0x2C00 <= codepoint <= 0x2FEF
        or 0x3001 <= codepoint <= 0xD7FF
        or 0xF900 <= codepoint <= 0xFDCF
        or 0xFDF0 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0xEFFFF
    )


def _is_xml_ncname_char(character: str) -> bool:
    codepoint = ord(character)
    return _is_xml_ncname_start(character) or character in {"-", "."} or "0" <= character <= "9" or codepoint in {
        0xB7,
        *range(0x300, 0x370),
        *range(0x203F, 0x2041),
    }


def _docx_archive_entries_are_bounded(entries: list[zipfile.ZipInfo]) -> bool:
    if not entries or len(entries) > _REQUIRED_DOCX_MAX_ENTRY_COUNT:
        return False
    compressed_total = 0
    uncompressed_total = 0
    seen_package_parts: set[str] = set()
    for entry in entries:
        filename = str(entry.filename or "")
        package_path = filename[:-1] if entry.is_dir() and filename.endswith("/") else filename
        if (
            not package_path
            or "\x00" in filename
            or "\\" in filename
            or filename.startswith("/")
            or any(part in {"", ".", ".."} for part in package_path.split("/"))
        ):
            return False
        normalized_part = package_path.casefold()
        if normalized_part in seen_package_parts:
            return False
        seen_package_parts.add(normalized_part)
        compressed_size = int(entry.compress_size)
        uncompressed_size = int(entry.file_size)
        if compressed_size < 0 or uncompressed_size < 0:
            return False
        compressed_total += compressed_size
        uncompressed_total += uncompressed_size
        if (
            compressed_total > _REQUIRED_DOCX_MAX_COMPRESSED_BYTES
            or uncompressed_total > _REQUIRED_DOCX_MAX_UNCOMPRESSED_BYTES
            or (
                compressed_size > 0
                and uncompressed_size > compressed_size * _REQUIRED_DOCX_MAX_COMPRESSION_RATIO
            )
        ):
            return False
    return True


def _resolve_root_relationship_target(target: str) -> str | None:
    if not target or "\\" in target or target.startswith("/"):
        return None
    normalized = posixpath.normpath(target)
    return None if normalized.startswith("../") or normalized in {".", ".."} else normalized

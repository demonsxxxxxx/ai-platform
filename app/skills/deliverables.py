"""Pinned Skill deliverable contracts and bounded XLSX verification.

The contract is parsed from immutable Skill package front matter, then carried
inside the package contract and the selected run pin.  Executors use this
module before staging an ordinary-user artifact; it never derives deliverable
requirements from model output or a request prompt.
"""

from __future__ import annotations

import stat
import zipfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree

from app.required_tool_contract import (
    CONTROLLED_RUNNER_EVIDENCE_SOURCE,
    PROCESS_BOUND_TRUST_BASIS,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
)


SKILL_DELIVERABLE_CONTRACT_SCHEMA_VERSION = "ai-platform.skill-deliverable-contract.v1"
DELIVERABLE_PUBLIC_TYPES_FIELD = "deliverable-public-types"
DELIVERABLE_REQUIRED_TYPES_FIELD = "deliverable-required-types"
DELIVERABLE_PROCESS_EVIDENCE_FIELD = "deliverable-process-evidence"
_FRONT_MATTER_FIELDS = (
    DELIVERABLE_PUBLIC_TYPES_FIELD,
    DELIVERABLE_REQUIRED_TYPES_FIELD,
    DELIVERABLE_PROCESS_EVIDENCE_FIELD,
)
_MAX_DELIVERABLE_NAME_LENGTH = 128
_MAX_XLSX_ARCHIVE_ENTRIES = 512
_MAX_XLSX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_XLSX_COMPRESSION_RATIO = 100
_SUPPORTED_ZIP_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_OPC_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_OPC_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OPC_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_SPREADSHEETML_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_DOCUMENT_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_SPREADSHEET_WORKSHEET_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
)
_XLSX_WORKBOOK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)

# Future public file types are added here with the same strict shape.  Skill
# packages can only select a server-owned type identifier, never a MIME type,
# file suffix, label, or storage location of their own.
_DELIVERABLE_SPECS: dict[str, dict[str, object]] = {
    "xlsx": {
        "artifact_type": "xlsx",
        "label": "Excel 文件",
        "extension": ".xlsx",
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "max_size_bytes": 64 * 1024 * 1024,
    },
}


class SkillDeliverableContractError(ValueError):
    """Raised when a pinned Skill deliverable contract is absent or invalid."""


def _normalized_type_list(value: object, *, error_code: str) -> list[str]:
    if not isinstance(value, str):
        raise SkillDeliverableContractError(error_code)
    values = [item.strip().lower() for item in value.split(",")]
    if not values or any(not item or item not in _DELIVERABLE_SPECS for item in values):
        raise SkillDeliverableContractError(error_code)
    if len(values) != len(set(values)):
        raise SkillDeliverableContractError(error_code)
    return values


def _safe_spec(deliverable_type: str) -> dict[str, object]:
    spec = _DELIVERABLE_SPECS.get(deliverable_type)
    if spec is None:
        raise SkillDeliverableContractError("skill_deliverable_type_unsupported")
    return {"deliverable_type": deliverable_type, **spec}


def parse_skill_deliverable_contract(metadata: Mapping[str, object]) -> dict[str, object] | None:
    """Parse one strict optional deliverable declaration from ``SKILL.md`` metadata."""

    present = [field for field in _FRONT_MATTER_FIELDS if field in metadata]
    if not present:
        return None
    if len(present) != len(_FRONT_MATTER_FIELDS):
        raise SkillDeliverableContractError("skill_deliverable_contract_incomplete")
    allowed_types = _normalized_type_list(
        metadata.get(DELIVERABLE_PUBLIC_TYPES_FIELD),
        error_code="skill_deliverable_public_types_invalid",
    )
    required_types = _normalized_type_list(
        metadata.get(DELIVERABLE_REQUIRED_TYPES_FIELD),
        error_code="skill_deliverable_required_types_invalid",
    )
    if any(item not in allowed_types for item in required_types):
        raise SkillDeliverableContractError("skill_deliverable_required_type_not_public")
    raw_process_evidence = metadata.get(DELIVERABLE_PROCESS_EVIDENCE_FIELD)
    if raw_process_evidence not in {"required", "not_required"}:
        raise SkillDeliverableContractError("skill_deliverable_process_evidence_invalid")
    contract = {
        "schema_version": SKILL_DELIVERABLE_CONTRACT_SCHEMA_VERSION,
        "allowed_public_deliverables": [_safe_spec(item) for item in allowed_types],
        "required_terminal_types": required_types,
        "requires_process_evidence": raw_process_evidence == "required",
    }
    return validate_skill_deliverable_contract(contract)


def validate_skill_deliverable_contract(value: object) -> dict[str, object]:
    """Validate an immutable server-owned contract without accepting overrides."""

    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "allowed_public_deliverables",
        "required_terminal_types",
        "requires_process_evidence",
    }:
        raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
    if value.get("schema_version") != SKILL_DELIVERABLE_CONTRACT_SCHEMA_VERSION:
        raise SkillDeliverableContractError("skill_deliverable_contract_schema_invalid")
    raw_allowed = value.get("allowed_public_deliverables")
    raw_required = value.get("required_terminal_types")
    if not isinstance(raw_allowed, list) or not raw_allowed or not isinstance(raw_required, list) or not raw_required:
        raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
    if not isinstance(value.get("requires_process_evidence"), bool):
        raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
    allowed: list[dict[str, object]] = []
    for raw_spec in raw_allowed:
        if not isinstance(raw_spec, dict):
            raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
        deliverable_type = str(raw_spec.get("deliverable_type") or "")
        expected = _safe_spec(deliverable_type)
        if raw_spec != expected or deliverable_type in {
            str(item["deliverable_type"]) for item in allowed
        }:
            raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
        allowed.append(expected)
    required = [str(item) for item in raw_required]
    allowed_types = {str(item["deliverable_type"]) for item in allowed}
    if (
        len(required) != len(set(required))
        or any(item not in allowed_types for item in required)
        or raw_required != required
    ):
        raise SkillDeliverableContractError("skill_deliverable_contract_invalid")
    return {
        "schema_version": SKILL_DELIVERABLE_CONTRACT_SCHEMA_VERSION,
        "allowed_public_deliverables": allowed,
        "required_terminal_types": required,
        "requires_process_evidence": bool(value["requires_process_evidence"]),
    }


def deliverable_contract_from_manifest(manifest: Mapping[str, object]) -> dict[str, object] | None:
    """Read only the contract already bound into a selected version manifest."""

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    package_contract = source.get("package_contract") if isinstance(source, dict) else None
    manifest_value = manifest.get("deliverable_contract")
    if isinstance(package_contract, dict):
        if "deliverable_contract" not in package_contract:
            if manifest_value is not None:
                raise SkillDeliverableContractError("skill_deliverable_contract_pin_mismatch")
            return None
        package_value = package_contract["deliverable_contract"]
        if package_value is None:
            if manifest_value is not None:
                raise SkillDeliverableContractError("skill_deliverable_contract_pin_mismatch")
            return None
        package_validated = validate_skill_deliverable_contract(package_value)
        if manifest_value is None:
            return package_validated
        manifest_validated = validate_skill_deliverable_contract(manifest_value)
        if manifest_validated != package_validated:
            raise SkillDeliverableContractError("skill_deliverable_contract_pin_mismatch")
        return package_validated
    if source.get("kind") == "uploaded":
        if manifest_value is not None:
            raise SkillDeliverableContractError("skill_deliverable_contract_pin_mismatch")
        return None
    if manifest_value is None:
        return None
    return validate_skill_deliverable_contract(manifest_value)


def required_deliverable_types(contract: Mapping[str, object] | None) -> tuple[str, ...]:
    """Return terminal artifact types only from a validated pinned contract."""

    if contract is None:
        return ()
    validated = validate_skill_deliverable_contract(contract)
    return tuple(str(item) for item in validated["required_terminal_types"])


def deliverable_spec(contract: Mapping[str, object], deliverable_type: str) -> dict[str, object]:
    """Resolve one allowed public type from a validated pinned contract."""

    validated = validate_skill_deliverable_contract(contract)
    for spec in validated["allowed_public_deliverables"]:
        if str(spec["deliverable_type"]) == deliverable_type:
            return dict(spec)
    raise SkillDeliverableContractError("skill_deliverable_type_not_allowed")


def deliverable_contract_upgrade_packet(*, skill_id: object, version: object) -> dict[str, object]:
    """Return the bounded admin action needed when a selected file Skill lacks a contract."""

    return {
        "schema_version": "ai-platform.skill-deliverable-upgrade-packet.v1",
        "status": "package_upgrade_required",
        "skill_id": str(skill_id or ""),
        "version": str(version or ""),
        "required_front_matter_fields": list(_FRONT_MATTER_FIELDS),
        "supported_public_deliverable_types": sorted(_DELIVERABLE_SPECS),
        "process_evidence_values": ["required", "not_required"],
    }


def process_evidence_is_valid(
    contract: Mapping[str, object],
    *,
    skill_id: str,
    binding: Mapping[str, object],
    executor_payload: Mapping[str, object],
) -> bool:
    """Require exact controlled-runner completion evidence when the pin demands it."""

    validated = validate_skill_deliverable_contract(contract)
    if not bool(validated["requires_process_evidence"]):
        return True
    if executor_payload.get("executor_mode") != "platform_controlled_runner":
        return False
    raw_evidence = executor_payload.get("capability_evidence")
    if not isinstance(raw_evidence, list):
        return False
    matching: dict[str, list[RequiredCapabilityEvidence]] = {}
    required_binding = {
        key: str(binding.get(key) or "")
        for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")
    }
    if any(not value for value in required_binding.values()):
        return False
    for raw_record in raw_evidence:
        try:
            record = RequiredCapabilityEvidence.from_payload(raw_record)
        except RequiredToolContractError:
            return False
        record_values = asdict(record)
        if any(record_values[key] != value for key, value in required_binding.items()):
            continue
        if (
            record.capability_kind != "skill"
            or record.canonical_identity != skill_id
            or record.evidence_source != CONTROLLED_RUNNER_EVIDENCE_SOURCE
            or record.trust_basis != PROCESS_BOUND_TRUST_BASIS
            or not record.tool_call_id
        ):
            continue
        matching.setdefault(record.tool_call_id, []).append(record)
    if len(matching) != 1:
        return False
    records = next(iter(matching.values()))
    phases = [(item.lifecycle_phase, item.lifecycle_status) for item in records]
    return phases == [
        ("invocation_requested", "invoking"),
        ("completed", "succeeded"),
    ]


def public_artifact_matches_contract(contract: Mapping[str, object], artifact: object) -> bool:
    """Reject executor-returned artifacts that cannot be public contract deliverables."""

    validated = validate_skill_deliverable_contract(contract)
    artifact_type = str(getattr(artifact, "artifact_type", "") or "")
    manifest = getattr(artifact, "manifest", {})
    workspace_output = manifest.get("workspace_output") if isinstance(manifest, dict) else None
    try:
        spec = deliverable_spec(validated, artifact_type)
    except SkillDeliverableContractError:
        return False
    try:
        size_bytes = int(getattr(artifact, "size_bytes", 0))
    except (TypeError, ValueError):
        return False
    return (
        str(getattr(artifact, "label", "") or "") == spec["label"]
        and str(getattr(artifact, "content_type", "") or "") == spec["content_type"]
        and 0 < size_bytes <= int(spec["max_size_bytes"])
        and str(getattr(artifact, "storage_key", "") or "").lower().endswith(str(spec["extension"]))
        and isinstance(manifest, dict)
        and manifest.get("deliverable_type") == artifact_type
        and _is_controlled_delivery_output(workspace_output)
    )


def _is_controlled_delivery_output(value: object) -> bool:
    """Accept only the relative output path written by runtime delivery admission."""

    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return (
        len(parts) >= 3
        and parts[0] == "outputs"
        and "delivery" in parts[:-1]
        and all(part not in {"", ".", ".."} for part in parts)
    )


def verified_xlsx_delivery(path: Path, *, spec: Mapping[str, object]) -> bool:
    """Return whether one controlled-delivery XLSX is bounded and structurally openable."""

    try:
        if (
            path.name != path.name.strip()
            or not _safe_delivery_name(path.name, extension=str(spec["extension"]))
            or path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
            or not 0 < path.stat().st_size <= int(spec["max_size_bytes"])
        ):
            return False
        with path.open("rb") as handle:
            if handle.read(4) != b"PK\x03\x04":
                return False
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not _xlsx_archive_entries_are_bounded(entries):
                return False
            content_types = archive.read("[Content_Types].xml")
            relationships = archive.read("_rels/.rels")
            workbook = archive.read("xl/workbook.xml")
            workbook_relationships = archive.read("xl/_rels/workbook.xml.rels")
            archive_names = {entry.filename.replace("\\", "/") for entry in entries}
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return False
    try:
        content_types_root = ElementTree.fromstring(content_types)
        relationships_root = ElementTree.fromstring(relationships)
        workbook_root = ElementTree.fromstring(workbook)
        workbook_relationships_root = ElementTree.fromstring(workbook_relationships)
    except ElementTree.ParseError:
        return False
    if (
        content_types_root.tag != f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Types"
        or relationships_root.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationships"
        or workbook_root.tag != f"{{{_SPREADSHEETML_NAMESPACE}}}workbook"
        or workbook_relationships_root.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationships"
    ):
        return False
    has_workbook_override = any(
        item.tag == f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Override"
        and item.attrib.get("PartName") == "/xl/workbook.xml"
        and item.attrib.get("ContentType") == _XLSX_WORKBOOK_CONTENT_TYPE
        for item in content_types_root
    )
    office_targets = [
        item
        for item in relationships_root
        if item.tag == f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationship"
        and item.attrib.get("Type") == _OPC_OFFICE_DOCUMENT_RELATIONSHIP
    ]
    has_workbook_relationship = (
        len(office_targets) == 1
        and str(office_targets[0].attrib.get("TargetMode") or "").lower() != "external"
        and _root_relationship_target(str(office_targets[0].attrib.get("Target") or ""))
        == "xl/workbook.xml"
    )
    sheets = next(
        (item for item in workbook_root if item.tag == f"{{{_SPREADSHEETML_NAMESPACE}}}sheets"),
        None,
    )
    if not has_workbook_override or not has_workbook_relationship or sheets is None:
        return False
    worksheet_path = _workbook_reachable_worksheet_path(
        sheets,
        workbook_relationships_root,
        archive_names,
    )
    if worksheet_path is None:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            worksheet = ElementTree.fromstring(archive.read(worksheet_path))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        return False
    return worksheet.tag == f"{{{_SPREADSHEETML_NAMESPACE}}}worksheet"


def _workbook_reachable_worksheet_path(
    sheets: ElementTree.Element,
    relationships: ElementTree.Element,
    archive_names: set[str],
) -> str | None:
    worksheet_targets = {
        str(item.attrib.get("Id") or ""): _workbook_relationship_target(
            str(item.attrib.get("Target") or "")
        )
        for item in relationships
        if item.tag == f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationship"
        and item.attrib.get("Type") == _SPREADSHEET_WORKSHEET_RELATIONSHIP
        and str(item.attrib.get("TargetMode") or "").lower() != "external"
    }
    for sheet in sheets:
        if sheet.tag != f"{{{_SPREADSHEETML_NAMESPACE}}}sheet":
            continue
        relationship_id = sheet.attrib.get(f"{{{_OFFICE_DOCUMENT_RELATIONSHIPS_NAMESPACE}}}id")
        target = worksheet_targets.get(str(relationship_id or ""))
        if target and target in archive_names:
            return target
    return None


def _safe_delivery_name(name: str, *, extension: str) -> bool:
    if (
        not name
        or len(name) > _MAX_DELIVERABLE_NAME_LENGTH
        or name != Path(name).name
        or name.startswith(".")
        or not name.lower().endswith(extension.lower())
        or any(character in "\\/:\x00" or ord(character) < 32 for character in name)
    ):
        return False
    stem = name[: -len(extension)]
    return bool(stem and not stem.endswith(".") and not stem.endswith(" "))


def _xlsx_archive_entries_are_bounded(entries: list[zipfile.ZipInfo]) -> bool:
    if not entries or len(entries) > _MAX_XLSX_ARCHIVE_ENTRIES:
        return False
    total_uncompressed = 0
    seen_names: set[str] = set()
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        normalized = name.casefold()
        if (
            not name
            or name.startswith("/")
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or normalized in seen_names
            or entry.flag_bits & 0x1
            or entry.compress_type not in _SUPPORTED_ZIP_COMPRESSION
            or entry.file_size < 0
            or entry.compress_size < 0
        ):
            return False
        if entry.is_dir():
            continue
        if entry.compress_size == 0 and entry.file_size > 0:
            return False
        if entry.compress_size and entry.file_size > entry.compress_size * _MAX_XLSX_COMPRESSION_RATIO:
            return False
        total_uncompressed += entry.file_size
        if total_uncompressed > _MAX_XLSX_UNCOMPRESSED_BYTES:
            return False
        seen_names.add(normalized)
    return True


def _root_relationship_target(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        return ""
    return normalized


def _workbook_relationship_target(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        return ""
    return f"xl/{normalized}"

"""Pure Files-owned XLSX identity, parser registration, and bounded policy.

Parser selection accepts an XLSX extension OR MIME. Public preview eligibility
requires both authoritative metadata assertions. Neither imports execution resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


XLSX_PARSER_ID = "ai-platform.xlsx.openpyxl"
XLSX_PARSER_VERSION = "1"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MAX_XLSX_FILE_BYTES = 1024 * 1024
MAX_XLSX_SHEETS = 16
MAX_XLSX_ROWS_PER_SHEET = 100
MAX_XLSX_COLUMNS_PER_SHEET = 32
MAX_XLSX_CELLS = 16_000
MAX_XLSX_CELL_CHARS = 256
MAX_XLSX_ZIP_ENTRIES = 2000
MAX_XLSX_ZIP_ENTRY_BYTES = 8 * 1024 * 1024
MAX_XLSX_ZIP_TOTAL_BYTES = 32 * 1024 * 1024
MAX_XLSX_ZIP_COMPRESSION_RATIO = 100

_SUPPORTED_XLSX_EXTENSIONS = frozenset({".xlsx"})


@dataclass(frozen=True)
class AttachmentParserSpec:
    """Immutable platform-owned parser registration; never loaded from a Skill."""

    parser_id: str
    parser_version: str
    extensions: frozenset[str]
    content_types: frozenset[str]
    max_bytes: int


XLSX_PARSER_SPEC = AttachmentParserSpec(
    parser_id=XLSX_PARSER_ID,
    parser_version=XLSX_PARSER_VERSION,
    extensions=_SUPPORTED_XLSX_EXTENSIONS,
    content_types=frozenset({XLSX_CONTENT_TYPE}),
    max_bytes=MAX_XLSX_FILE_BYTES,
)


def _normalized_extension(file_name: object) -> str:
    safe_name = PurePosixPath(str(file_name or "").replace("\\", "/")).name
    return Path(safe_name).suffix.casefold()


def normalized_xlsx_content_type(content_type: object) -> str:
    return str(content_type or "").split(";", 1)[0].strip().casefold()


def parser_spec_for_attachment(
    *,
    file_name: object,
    content_type: object = "",
) -> AttachmentParserSpec | None:
    """Resolve the platform-owned XLSX preview parser."""

    extension = _normalized_extension(file_name)
    normalized_type = normalized_xlsx_content_type(content_type)
    if (
        extension in XLSX_PARSER_SPEC.extensions
        or normalized_type in XLSX_PARSER_SPEC.content_types
    ):
        return XLSX_PARSER_SPEC
    return None


@dataclass(frozen=True)
class XlsxPreviewIdentity:
    """One stored-metadata decision for the exact XLSX preview contract."""

    file_name: str | None
    content_type: str

    @property
    def has_xlsx_content_type(self) -> bool:
        """Return whether the stored MIME type selects the XLSX presentation path."""

        return self.content_type.split(";", 1)[0].strip().casefold() == XLSX_CONTENT_TYPE

    @property
    def eligible(self) -> bool:
        """Return whether both authoritative filename and MIME select XLSX."""

        return self.file_name is not None and is_xlsx_preview_request(
            file_name=self.file_name,
            content_type=self.content_type,
        )


def is_xlsx_preview_request(*, file_name: object, content_type: object) -> bool:
    """Return whether this exact metadata is eligible for the XLSX DTO path."""

    normalized_name = PurePosixPath(str(file_name or "").replace("\\", "/")).name
    normalized_type = str(content_type or "").split(";", 1)[0].strip().casefold()
    return normalized_name.casefold().endswith(".xlsx") and normalized_type == XLSX_CONTENT_TYPE


def xlsx_preview_identity_from_metadata(
    row: Mapping[str, object],
) -> XlsxPreviewIdentity:
    """Resolve XLSX eligibility from stored file metadata, never a display label.

    ``original_name`` and ``file_name`` are preferred when present.  When both
    are present they must agree; a disagreement fails closed.  Artifact rows
    without either field use the persisted storage-key basename, which is also
    the filename consumed by the authorized preview route.
    """

    preferred_names: list[str] = []
    for field_name in ("original_name", "file_name"):
        name = _stored_metadata_basename(row.get(field_name))
        if name is not None:
            preferred_names.append(name)
    normalized_names = {name.casefold() for name in preferred_names}
    if len(normalized_names) > 1:
        file_name = None
    elif preferred_names:
        file_name = preferred_names[0]
    else:
        file_name = _stored_metadata_basename(row.get("storage_key"))
    return XlsxPreviewIdentity(
        file_name=file_name,
        content_type=str(row.get("content_type") or ""),
    )


def _stored_metadata_basename(value: object) -> str | None:
    """Return a non-empty basename from a stored filename-like metadata value."""

    normalized = PurePosixPath(str(value or "").replace("\\", "/")).name.strip()
    return normalized or None


def xlsx_preview_max_bytes(*, file_name: object, content_type: object) -> int:
    """Resolve the registered byte cap for an eligible XLSX presentation request."""

    spec = parser_spec_for_attachment(file_name=file_name, content_type=content_type)
    if spec is None or not is_xlsx_preview_request(
        file_name=file_name,
        content_type=content_type,
    ):
        raise ValueError("xlsx_preview_unsupported")
    return spec.max_bytes

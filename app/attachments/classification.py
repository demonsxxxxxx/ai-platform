"""Fail-closed byte classification for attachment capability admission.

Multipart MIME and the source filename are untrusted compatibility assertions.
Only bounded bytes choose a type.  A byte-recognized XLSX must still carry the
compatible ``.xlsx`` suffix; the suffix never selects XLSX on its own.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, TypeAlias
from zipfile import BadZipFile, ZipFile

from app.file_parser_contracts import (
    AttachmentPreprocessingError,
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    _preflight_xlsx_worksheets,
    _validate_xlsx_archive,
)
from app.validation import assert_safe_id


ATTACHMENT_CLASSIFIER_VERSION = "ai-platform.attachment-byte-classifier.v1"
MAX_ATTACHMENT_CLASSIFICATION_BYTES = MAX_XLSX_FILE_BYTES
CLASSIFICATION_CLASSIFIED = "classified"
CLASSIFICATION_REJECTED = "rejected"
ClassificationState: TypeAlias = Literal["classified", "rejected"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AttachmentBytesForClassification:
    """Bounded stored bytes and the server record that must still agree with them."""

    file_id: str
    raw_bytes: bytes
    source_filename: str
    declared_media_type: str
    expected_size_bytes: int
    expected_sha256: str

    def __post_init__(self) -> None:
        assert_safe_id(self.file_id, "file_id")
        if not isinstance(self.raw_bytes, bytes):
            raise ValueError("raw_bytes must be bytes")
        if type(self.expected_size_bytes) is not int or self.expected_size_bytes < 0:
            raise ValueError("expected_size_bytes must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class AttachmentClassification:
    """Public byte-classification decision without file IDs, hashes, or parser identities."""

    state: ClassificationState
    rejection_code: str | None = None
    media_type: str | None = None
    verified_extension: str | None = None
    classifier_version: str | None = None

    def __post_init__(self) -> None:
        classified = self.state == CLASSIFICATION_CLASSIFIED
        details = (self.media_type, self.verified_extension, self.classifier_version)
        if classified and self.rejection_code is None and all(details):
            return
        if self.state == CLASSIFICATION_REJECTED and self.rejection_code and not any(details):
            return
        raise ValueError("classification decision is invalid")


@dataclass(frozen=True, slots=True)
class _ClassifiedAttachment:
    """Private exact byte facts retained only inside the attachment package."""

    file_id: str
    media_type: str
    verified_extension: str
    size_bytes: int
    sha256: str
    classifier_version: str


def classify_attachment_bytes(source: AttachmentBytesForClassification) -> AttachmentClassification:
    """Return a safe byte-classification decision for diagnostics and tests."""

    _, decision = _classify_attachment(source)
    return decision


def _classify_attachment(
    source: AttachmentBytesForClassification,
) -> tuple[_ClassifiedAttachment | None, AttachmentClassification]:
    """Create private exact facts only for the admission package's authoritative flow."""

    raw = source.raw_bytes
    if len(raw) > MAX_ATTACHMENT_CLASSIFICATION_BYTES:
        return _rejected("attachment_classification_file_too_large")
    if len(raw) != source.expected_size_bytes:
        return _rejected("attachment_classification_stale_size")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    expected_sha256 = str(source.expected_sha256).casefold()
    if not _SHA256.fullmatch(expected_sha256) or actual_sha256 != expected_sha256:
        return _rejected("attachment_classification_stale_hash")
    if _looks_active_or_dangerous(raw):
        return _rejected("attachment_classification_active_or_dangerous")
    if not _looks_like_zip(raw):
        return _rejected("attachment_classification_type_unsupported")
    try:
        _preflight_xlsx_bytes(raw)
    except AttachmentPreprocessingError:
        return _rejected("attachment_classification_xlsx_invalid")
    if _source_extension(source.source_filename) != ".xlsx":
        return _rejected("attachment_classification_extension_incompatible")
    identity = _ClassifiedAttachment(
        source.file_id, XLSX_CONTENT_TYPE, ".xlsx", len(raw), actual_sha256, ATTACHMENT_CLASSIFIER_VERSION
    )
    return identity, AttachmentClassification(
        CLASSIFICATION_CLASSIFIED,
        media_type=identity.media_type,
        verified_extension=identity.verified_extension,
        classifier_version=identity.classifier_version,
    )


def _rejected(code: str) -> tuple[None, AttachmentClassification]:
    return None, AttachmentClassification(CLASSIFICATION_REJECTED, rejection_code=code)


def _source_extension(source_filename: object) -> str:
    name = PurePosixPath(str(source_filename or "").replace("\\", "/")).name
    return PurePosixPath(name).suffix.casefold()


def _preflight_xlsx_bytes(raw: bytes) -> None:
    """Reuse the parser's exact OOXML safeguards before selecting the XLSX profile."""

    _reject_ambiguous_zip_entries(raw)
    checked_entries = _validate_xlsx_archive(raw)
    _preflight_xlsx_worksheets(raw, content_security_checked_entries=checked_entries)


def _reject_ambiguous_zip_entries(raw: bytes) -> None:
    try:
        archive = ZipFile(io.BytesIO(raw))
    except (BadZipFile, ValueError) as exc:
        raise AttachmentPreprocessingError("xlsx_parse_failed") from exc
    seen: set[str] = set()
    try:
        for entry in archive.infolist():
            name = entry.filename.replace("\\", "/").casefold()
            path = PurePosixPath(name)
            if not name or name.startswith("/") or name in seen or any(part in {"", ".", ".."} for part in path.parts):
                raise AttachmentPreprocessingError("xlsx_parse_failed")
            seen.add(name)
    finally:
        archive.close()


def _looks_like_zip(raw: bytes) -> bool:
    return raw.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _looks_active_or_dangerous(raw: bytes) -> bool:
    if raw.startswith((b"MZ", b"\x7fELF", b"#!")):
        return True
    sample = raw[:4096]
    text: list[str] = []
    try:
        text.append(sample.decode("utf-8-sig"))
    except UnicodeDecodeError:
        pass
    for bom, encoding in ((codecs.BOM_UTF16_BE, "utf-16"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF32_LE, "utf-32")):
        if sample.startswith(bom):
            try:
                text.append(sample.decode(encoding))
            except UnicodeDecodeError:
                pass
    return any(item.lstrip().casefold().startswith(("<!doctype html", "<html", "<svg", "<?xml")) for item in text)

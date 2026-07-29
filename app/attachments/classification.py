"""Byte-backed attachment classification with fail-closed XLSX identity issuance.

The source filename is only a compatibility assertion: a byte-recognized XLSX
must have a ``.xlsx`` suffix, but that suffix never chooses the type. Multipart
MIME is deliberately retained as untrusted input and never read by the classifier.
"""

from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, TypeAlias

from app.file_parser_contracts import (
    AttachmentPreprocessingError,
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    preflight_xlsx_attachment_bytes,
)
from app.validation import assert_safe_id


ATTACHMENT_CLASSIFIER_VERSION = "ai-platform.attachment-byte-classifier.v1"
MAX_ATTACHMENT_CLASSIFICATION_BYTES = MAX_XLSX_FILE_BYTES
CLASSIFICATION_CLASSIFIED = "classified"
CLASSIFICATION_REJECTED = "rejected"
ClassificationState: TypeAlias = Literal["classified", "rejected"]
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLASSIFICATION_SEAL = object()


@dataclass(frozen=True, slots=True)
class AttachmentBytesForClassification:
    """Authorized stored bytes plus the storage identity that must still match them."""

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


@dataclass(frozen=True, slots=True, init=False)
class AttachmentByteIdentity:
    """Sealed immutable identity that only this module issues after byte validation."""

    file_id: str
    media_type: str
    verified_extension: str
    size_bytes: int
    sha256: str
    classifier_version: str
    _issuer_token: object = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        file_id: str,
        media_type: str,
        verified_extension: str,
        size_bytes: int,
        sha256: str,
        classifier_version: str,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _CLASSIFICATION_SEAL:
            raise TypeError("AttachmentByteIdentity requires a classification result")
        assert_safe_id(file_id, "file_id")
        if media_type != XLSX_CONTENT_TYPE or verified_extension != ".xlsx":
            raise ValueError("unsupported byte identity")
        if type(size_bytes) is not int or size_bytes < 0 or not _SHA256_PATTERN.fullmatch(sha256):
            raise ValueError("invalid byte identity")
        if classifier_version != ATTACHMENT_CLASSIFIER_VERSION:
            raise ValueError("unexpected classifier version")
        object.__setattr__(self, "file_id", file_id)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "verified_extension", verified_extension)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "classifier_version", classifier_version)
        object.__setattr__(self, "_issuer_token", _CLASSIFICATION_SEAL)


@dataclass(frozen=True, slots=True)
class AttachmentClassification:
    """Terminal byte-classification decision without caller-controlled type authority."""

    state: ClassificationState
    identity: AttachmentByteIdentity | None
    rejection_code: str | None

    def __post_init__(self) -> None:
        if self.state == CLASSIFICATION_CLASSIFIED:
            if self.identity is None or self.rejection_code is not None:
                raise ValueError("classified result requires exactly one identity")
        elif self.state == CLASSIFICATION_REJECTED:
            if self.identity is not None or not self.rejection_code:
                raise ValueError("rejected result cannot include an identity")
        else:
            raise ValueError("classification state is invalid")


def classify_attachment_bytes(source: AttachmentBytesForClassification) -> AttachmentClassification:
    """Issue an XLSX byte identity or return one stable fail-closed rejection."""

    raw = source.raw_bytes
    if len(raw) > MAX_ATTACHMENT_CLASSIFICATION_BYTES:
        return _rejected("attachment_classification_file_too_large")
    if len(raw) != source.expected_size_bytes:
        return _rejected("attachment_classification_stale_size")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if not _SHA256_PATTERN.fullmatch(str(source.expected_sha256).casefold()) or actual_sha256 != str(
        source.expected_sha256
    ).casefold():
        return _rejected("attachment_classification_stale_hash")
    if _looks_active_or_dangerous(raw):
        return _rejected("attachment_classification_active_or_dangerous")
    if not _looks_like_zip(raw):
        return _rejected("attachment_classification_type_unsupported")
    try:
        preflight = preflight_xlsx_attachment_bytes(raw)
    except AttachmentPreprocessingError:
        return _rejected("attachment_classification_xlsx_invalid")
    if _source_extension(source.source_filename) != ".xlsx":
        return _rejected("attachment_classification_extension_incompatible")
    identity = AttachmentByteIdentity(
        file_id=source.file_id,
        media_type=XLSX_CONTENT_TYPE,
        verified_extension=".xlsx",
        size_bytes=preflight.byte_count,
        sha256=preflight.sha256,
        classifier_version=ATTACHMENT_CLASSIFIER_VERSION,
        _seal=_CLASSIFICATION_SEAL,
    )
    return AttachmentClassification(
        state=CLASSIFICATION_CLASSIFIED,
        identity=identity,
        rejection_code=None,
    )


def is_classified_attachment_identity(value: object) -> bool:
    """Return whether this process issued the exact identity through byte classification."""

    return isinstance(value, AttachmentByteIdentity) and value._issuer_token is _CLASSIFICATION_SEAL


def _source_extension(source_filename: object) -> str:
    """Return only the untrusted suffix used to detect a type contradiction."""

    normalized = PurePosixPath(str(source_filename or "").replace("\\", "/")).name
    return PurePosixPath(normalized).suffix.casefold()


def _looks_like_zip(raw: bytes) -> bool:
    return raw.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _looks_active_or_dangerous(raw: bytes) -> bool:
    if raw.startswith((b"MZ", b"\x7fELF", b"#!")):
        return True
    sample = raw[:4096]
    text_samples: list[str] = []
    try:
        text_samples.append(sample.decode("utf-8-sig"))
    except UnicodeDecodeError:
        pass
    for bom, encoding in (
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF32_LE, "utf-32"),
    ):
        if sample.startswith(bom):
            try:
                text_samples.append(sample.decode(encoding))
            except UnicodeDecodeError:
                pass
    for text in text_samples:
        probe = text.lstrip().casefold()
        if probe.startswith(("<!doctype html", "<html", "<svg", "<?xml")):
            return True
    return False


def _rejected(code: str) -> AttachmentClassification:
    return AttachmentClassification(
        state=CLASSIFICATION_REJECTED,
        identity=None,
        rejection_code=code,
    )

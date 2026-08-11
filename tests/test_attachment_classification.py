from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.attachments.classification import (
    CLASSIFICATION_CLASSIFIED,
    AttachmentBytesForClassification,
    classify_attachment_bytes,
)


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "name"
    sheet["A2"] = "alpha"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _input(
    raw: bytes,
    *,
    source_filename: str = "report.xlsx",
    declared_media_type: str = "application/octet-stream",
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> AttachmentBytesForClassification:
    return AttachmentBytesForClassification(
        file_id="file-xlsx",
        raw_bytes=raw,
        source_filename=source_filename,
        declared_media_type=declared_media_type,
        expected_size_bytes=len(raw) if expected_size_bytes is None else expected_size_bytes,
        expected_sha256=hashlib.sha256(raw).hexdigest() if expected_sha256 is None else expected_sha256,
    )


def _rewrite_archive(raw: bytes, mutate) -> bytes:
    source = io.BytesIO(raw)
    output = io.BytesIO()
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(output, "w") as rewritten:
        for entry in archive.infolist():
            rewritten.writestr(entry, archive.read(entry.filename))
        mutate(rewritten)
    return output.getvalue()


@pytest.mark.parametrize(
    "declared_media_type",
    [
        "",
        "application/octet-stream",
        XLSX_MIME,
        f" {XLSX_MIME.upper()} ; charset=binary",
    ],
)
def test_xlsx_classification_accepts_only_unknown_or_compatible_declared_mime(
    declared_media_type,
):
    raw = _xlsx_bytes()

    result = classify_attachment_bytes(
        _input(
            raw,
            declared_media_type=declared_media_type,
            source_filename="report.xlsx",
        )
    )

    assert result.state == CLASSIFICATION_CLASSIFIED
    assert result.media_type == XLSX_MIME
    assert result.verified_extension == ".xlsx"
    assert result.classifier_version
    assert not hasattr(result, "declared_media_type")
    assert not hasattr(result, "identity")


@pytest.mark.parametrize("declared_media_type", ["application/pdf", "text/plain"])
def test_xlsx_bytes_reject_an_explicitly_incompatible_declared_mime(
    declared_media_type,
):
    result = classify_attachment_bytes(
        _input(_xlsx_bytes(), declared_media_type=declared_media_type)
    )

    assert (
        result.rejection_code
        == "attachment_classification_media_type_incompatible"
    )
    assert result.media_type is None


def test_xlsx_bytes_require_a_compatible_untrusted_source_extension():
    result = classify_attachment_bytes(_input(_xlsx_bytes(), source_filename="report.bin"))

    assert result.rejection_code == "attachment_classification_extension_incompatible"
    assert result.media_type is None


def test_non_xlsx_bytes_claiming_xlsx_name_and_mime_fail_closed():
    result = classify_attachment_bytes(
        _input(
            b"not an OOXML workbook",
            source_filename="spoofed.xlsx",
            declared_media_type=XLSX_MIME,
        )
    )

    assert result.rejection_code == "attachment_classification_type_unsupported"
    assert result.media_type is None


@pytest.mark.parametrize(
    ("expected_size_bytes", "expected_sha256", "expected_code"),
    [
        (1, None, "attachment_classification_stale_size"),
        (None, "0" * 64, "attachment_classification_stale_hash"),
    ],
)
def test_stale_storage_identity_fails_before_type_admission(
    expected_size_bytes, expected_sha256, expected_code
):
    raw = _xlsx_bytes()

    result = classify_attachment_bytes(
        _input(
            raw,
            expected_size_bytes=len(raw) if expected_size_bytes is None else expected_size_bytes,
            expected_sha256=hashlib.sha256(raw).hexdigest() if expected_sha256 is None else expected_sha256,
        )
    )

    assert result.rejection_code == expected_code


def test_storage_hash_identity_rejects_non_string_values_before_classification():
    with pytest.raises(ValueError, match="expected_sha256 must be a string"):
        AttachmentBytesForClassification(
            file_id="file-xlsx",
            raw_bytes=b"payload",
            source_filename="report.xlsx",
            declared_media_type="application/octet-stream",
            expected_size_bytes=7,
            expected_sha256=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("payload", [b"MZ\x00\x00", b"#!/bin/sh\necho unsafe", b"<html><script>x</script></html>"])
def test_active_or_dangerous_payloads_fail_closed(payload):
    result = classify_attachment_bytes(_input(payload, source_filename="report.xlsx"))

    assert result.rejection_code == "attachment_classification_active_or_dangerous"


def test_macro_encrypted_and_ambiguous_ooxml_archives_fail_closed():
    raw = _xlsx_bytes()
    macro = _rewrite_archive(raw, lambda archive: archive.writestr("xl/vbaProject.bin", b"macro"))
    with pytest.warns(UserWarning, match="Duplicate name"):
        ambiguous = _rewrite_archive(raw, lambda archive: archive.writestr("xl/workbook.xml", b"<workbook/>"))
    encrypted = bytearray(raw)
    central_directory = encrypted.find(b"PK\x01\x02")
    assert central_directory >= 0
    encrypted[central_directory + 8] |= 0x01

    for payload in (macro, ambiguous, bytes(encrypted)):
        result = classify_attachment_bytes(_input(payload))

        assert result.rejection_code == "attachment_classification_xlsx_invalid"


def test_classification_exposes_a_public_decision_not_a_nominal_trusted_identity():
    result = classify_attachment_bytes(_input(_xlsx_bytes()))

    assert result.state == CLASSIFICATION_CLASSIFIED
    assert result.rejection_code is None
    assert "file-xlsx" not in repr(result)
    assert hashlib.sha256(_xlsx_bytes()).hexdigest() not in repr(result)

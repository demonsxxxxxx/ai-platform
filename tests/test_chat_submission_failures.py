"""Owned public Chat failure projection, independent of HTTP transport."""

import re

import pytest

from app.conversations.api import (
    log_safe_submission_exception,
    new_submission_diagnostic_id,
    safe_submission_code,
    submission_error_code,
    submission_error_detail,
)


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("safe_code", "safe_code"),
        ({"code": "safe_code"}, "safe_code"),
        ({"detail_code": "required_capability_unavailable"}, "required_capability_unavailable"),
        ({"detail_code": "unregistered_code"}, "chat_submission_rejected"),
        ("private message https://private.example.test", "chat_submission_rejected"),
        ({"code": "private message"}, "chat_submission_rejected"),
        (None, "chat_submission_rejected"),
        ("a" * 65, "chat_submission_rejected"),
    ],
)
def test_submission_error_code_preserves_bounded_public_mapping(detail, expected):
    assert submission_error_code(detail) == expected
    assert safe_submission_code("UPPER_CASE", "fallback") == "fallback"


@pytest.mark.parametrize("rejected", [False, True])
def test_submission_error_detail_preserves_explicit_rejection_proof(rejected):
    diagnostic_id = new_submission_diagnostic_id()
    assert re.fullmatch(r"diag_[0-9a-f]{16}", diagnostic_id)
    detail = submission_error_detail(
        code="chat_submission_internal_error", rejected_before_persist=rejected,
        diagnostic_id=diagnostic_id,
    )
    assert detail == {
        "code": "chat_submission_internal_error",
        "diagnostic_id": diagnostic_id,
        **({"submission_disposition": "rejected_before_persist"} if rejected else {}),
    }


def test_submission_diagnostics_never_log_exception_content_or_private_paths(caplog):
    diagnostic_id = new_submission_diagnostic_id()
    try:
        raise RuntimeError("private-prompt private-token /private/runtime/path")
    except RuntimeError as exc:
        log_safe_submission_exception(phase="rejection_ledger", diagnostic_id=diagnostic_id, exc=exc)

    assert diagnostic_id in caplog.text
    assert "phase=rejection_ledger" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "private-prompt" not in caplog.text
    assert "private-token" not in caplog.text
    assert "/private/runtime/path" not in caplog.text
    assert "Traceback" not in caplog.text

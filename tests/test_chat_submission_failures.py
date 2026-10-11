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


def test_submission_rejection_keeps_safe_queue_fields_and_order():
    from app.conversations.api import submission_rejection_detail
    from app.queue_payload_validation import queue_payload_invalid_detail

    class QueueValidationError(ValueError):
        def errors(self):
            return [
                {"loc": ("input", i), "type": "value_error", "msg": f"safe field {i}",
                 "input": "token=private-input", "ctx": {"private": "/private/runtime/path"}}
                for i in range(12)
            ]

    error = QueueValidationError("token=private-exception-text")
    neutral = queue_payload_invalid_detail(error)
    detail = submission_rejection_detail(code="queue_payload_invalid", validation_error=error)
    assert detail == {**neutral, "submission_disposition": "rejected_before_persist"}
    assert len(detail["errors"]) == 8
    assert [item["loc"] for item in detail["errors"]] == [["input", i] for i in range(8)]
    assert "private" not in str(detail)


@pytest.mark.parametrize("cause", [None, RuntimeError("token=private-unknown-cause")])
def test_submission_rejection_unknown_validation_cause_never_exposes_raw_content(cause):
    from app.conversations.api import submission_rejection_detail

    assert submission_rejection_detail(code="queue_payload_invalid", validation_error=cause) == {
        "code": "queue_payload_invalid",
        "submission_disposition": "rejected_before_persist",
    }

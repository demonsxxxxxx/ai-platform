"""Owned public Chat failure projection, independent of HTTP transport."""

import re

import pytest

from app.bootstrap.submission_diagnostics import log_safe_submission_exception
from app.conversations.api import (
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


def test_submission_logger_keeps_wrapped_database_cause_without_rendering_it(caplog):
    from psycopg.errors import UniqueViolation

    original = UniqueViolation("SELECT private-prompt FROM secrets password=private-token")
    wrapper = RuntimeError("postgresql://private-user:private-password@private-host/db")
    wrapper.__cause__ = original
    log_safe_submission_exception(
        phase="admission_transaction", diagnostic_id="diag_0123456789abcdef", exc=wrapper,
    )
    assert "'relation': 'cause'" in caplog.text
    assert "UniqueViolation" in caplog.text
    assert "23505" in caplog.text
    assert "exception_chain_end=complete" in caplog.text
    assert wrapper.__cause__ is original
    for secret in ("SELECT", "private-prompt", "private-token", "postgresql://", "private-password"):
        assert secret not in caplog.text
    assert caplog.records[-1].exc_info is None


def test_submission_chain_honors_context_suppression_precedence_and_cycles(caplog):
    from app.platform.tracing import safe_exception_log_fields

    outer, cause, context = RuntimeError(), ValueError(), KeyError()
    outer.__context__ = context
    assert safe_exception_log_fields(outer)["exception_chain"][1]["relation"] == "context"
    outer.__suppress_context__ = True
    assert len(safe_exception_log_fields(outer)["exception_chain"]) == 1
    outer.__cause__ = cause
    cause.__cause__ = outer
    log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=outer)
    assert "ValueError" in caplog.text
    assert "KeyError" not in caplog.text
    assert "exception_chain_end=cycle" in caplog.text
    assert outer.__cause__ is cause and cause.__cause__ is outer


def test_submission_chain_limits_depth_and_rejects_hostile_metadata(caplog):
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("must not render")

        def __repr__(self):
            raise AssertionError("must not render")

        @property
        def sqlstate(self):
            raise AssertionError("must not read arbitrary metadata")

        @property
        def __traceback__(self):
            raise AssertionError("must use builtin descriptor")

        @property
        def __cause__(self):
            raise AssertionError("must use builtin descriptor")

    root = Hostile("private-prompt")
    cursor = root
    for _ in range(1000):
        following = RuntimeError("private-token")
        BaseException.__cause__.__set__(cursor, following)
        cursor = following
    log_safe_submission_exception(phase="private/path", diagnostic_id="private-token", exc=root)
    assert "diagnostic_id=unknown phase=unknown" in caplog.text
    assert caplog.text.count("'exception_type'") == 8
    assert "exception_chain_end=limit" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("code", ["23505", "23505\nSELECT secret", "ABCDE", "x" * 10000])
def test_submission_logger_ignores_untrusted_sqlstate(code, caplog):
    class Impostor(RuntimeError):
        sqlstate = code

    log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=Impostor())
    assert "sqlstate" not in caplog.text


def test_submission_logger_bounds_app_frames_and_omits_paths(caplog):
    namespace = {"__name__": "app.synthetic_test"}
    exec(compile("def recurse(n):\n if n: return recurse(n-1)\n raise ValueError('private-prompt')", "/private/path/file.py", "exec"), namespace)
    try:
        namespace["recurse"](150)
    except ValueError as exc:
        log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=exc)
    # Eight frames in the legacy outer summary and eight in the structural node.
    assert caplog.text.count("app.synthetic_test:") == 16
    assert "'frames_limited': True" in caplog.text
    assert "/private/path" not in caplog.text
    assert "private-prompt" not in caplog.text
    assert "test_chat_submission_failures" not in caplog.text


def test_submission_logging_failure_does_not_replace_original_exception(monkeypatch):
    from app.conversations.application import submission_failures

    original = RuntimeError("original")
    def fail_log(*_args, **_kwargs):
        raise ValueError("logger unavailable")
    monkeypatch.setattr(submission_failures.logger, "error", fail_log)
    with pytest.raises(RuntimeError) as caught:
        try:
            raise original
        except RuntimeError as exc:
            log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=exc)
            raise
    assert caught.value is original


def test_submission_logger_rejects_malicious_frame_and_type_metadata(caplog):
    class Hostile(str):
        def __str__(self):
            raise AssertionError("must not render metadata")

    class Meta(type):
        def __getattribute__(self, name):
            if name in {"__name__", "sqlstate"}:
                raise AssertionError("must not execute metadata")
            return super().__getattribute__(name)

    class Error(RuntimeError, metaclass=Meta):
        pass

    namespace = {"__name__": Hostile("app.private_token"), "Error": Error}
    exec(compile("def fail():\n raise Error('private-prompt')", "/private/path/file.py", "exec"), namespace)
    try:
        namespace["fail"]()
    except Error as exc:
        log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=exc)
    assert "exception_type=Exception" in caplog.text
    assert "frames=none" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("failure", ["write", "format"])
def test_submission_sink_failure_stderr_cannot_render_active_private_exception(failure, monkeypatch, capsys):
    import logging
    from app.conversations.application import submission_failures

    class BrokenStream:
        def write(self, _value):
            raise OSError("synthetic sink unavailable")
        def flush(self):
            pass

    class BrokenFormatter(logging.Formatter):
        def format(self, _record):
            raise ValueError("synthetic formatter unavailable")

    handler = logging.StreamHandler(BrokenStream())
    if failure == "format":
        handler.setFormatter(BrokenFormatter())
    monkeypatch.setattr(submission_failures.logger, "handlers", [handler])
    monkeypatch.setattr(submission_failures.logger, "propagate", False)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    original = RuntimeError("SYNTHETIC_SECRET_MARKER")
    with pytest.raises(RuntimeError) as caught:
        try:
            raise original
        except RuntimeError as exc:
            log_safe_submission_exception(phase="resolver", diagnostic_id="diag_0123456789abcdef", exc=exc)
            raise
    stderr = capsys.readouterr().err
    assert "--- Logging error ---" in stderr
    assert "SYNTHETIC_SECRET_MARKER" not in stderr
    assert caught.value is original
    assert original.__cause__ is None and original.__context__ is None


@pytest.mark.parametrize(
    ("message", "capability_kind", "expected"),
    [
        ("private token=secret /private/runtime/path", "mcp_tool", "mcp_tool_not_available"),
        ("mcp_tool_not_available", None, "mcp_tool_not_available"),
        ("private token=secret /private/runtime/path", "native_command", "capability_not_authorized"),
        ("private token=secret /private/runtime/path", None, "capability_not_authorized"),
    ],
)
def test_capability_authorization_projection_preserves_safe_mapping(message, capability_kind, expected):
    from types import SimpleNamespace
    from app.conversations.api import capability_authorization_error_code
    from app.platform.postgres.errors import RepositoryAuthorizationError

    denial = SimpleNamespace(capability_kind=capability_kind, private_payload="token=private") if capability_kind else None
    error = RepositoryAuthorizationError(message, denial=denial)
    code = capability_authorization_error_code(error)
    assert code == expected
    detail = submission_error_detail(code=code, rejected_before_persist=True)
    assert detail == {"code": expected, "submission_disposition": "rejected_before_persist"}
    assert "private" not in str(detail)
    assert "secret" not in str(detail)


@pytest.mark.parametrize("frame_count", [1, 8, 9])
def test_submission_frame_limit_reports_retention_truncation(frame_count):
    from app.platform.tracing import safe_exception_log_fields

    namespace = {"__name__": "app.synthetic_test"}
    exec(compile("def recurse(n):\n if n: return recurse(n-1)\n raise ValueError('private-prompt')", "/private/path/file.py", "exec"), namespace)
    try:
        namespace["recurse"](frame_count - 1)
    except ValueError as exc:
        evidence = safe_exception_log_fields(exc)
    node = evidence["exception_chain"][0]
    assert len(node["frames"]) == min(frame_count, 8)
    assert node.get("frames_limited", False) is (frame_count > 8)
    assert evidence["exception_chain_end"] == "complete"

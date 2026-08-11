import json
import logging

from app.diagnostics import log_safe_exception, log_safe_failure


def test_log_safe_exception_records_correlation_without_exception_text(caplog):
    logger = logging.getLogger("tests.safe_diagnostics")
    secret = "private-token-must-not-leak"

    with caplog.at_level(logging.ERROR, logger=logger.name):
        try:
            raise RuntimeError(secret)
        except RuntimeError as exc:
            log_safe_exception(
                logger,
                event="executor_failure",
                phase="dispatch",
                diagnostic_id="diag_fixed",
                exc=exc,
                identifiers={"run_id": "run-a", "attempt": 2},
            )

    record = caplog.records[-1]
    payload = json.loads(record.message)

    assert payload["event"] == "executor_failure"
    assert payload["phase"] == "dispatch"
    assert payload["diagnostic_id"] == "diag_fixed"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["run_id"] == "run-a"
    assert payload["attempt"] == 2
    assert secret not in record.message
    assert all(secret not in frame for frame in payload["frames"])


def test_log_safe_failure_rejects_upstream_error_prose(caplog):
    logger = logging.getLogger("tests.safe_failure")
    secret = "gateway failed with private-token"

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_safe_failure(
            logger,
            event="sdk_terminal_failure",
            phase="query",
            diagnostic_id="diag_fixed",
            error_code=secret,
            identifiers={"run_id": "run-a"},
        )

    payload = json.loads(caplog.records[-1].message)

    assert payload["error_code"] == "internal_error"
    assert payload["diagnostic_id"] == "diag_fixed"
    assert secret not in caplog.records[-1].message

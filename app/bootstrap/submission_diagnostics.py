"""Compose Conversations-owned safe submission logging with platform evidence."""

from functools import partial

from app.conversations.api import log_safe_submission_exception as _log_submission_exception
from app.platform.tracing import safe_exception_log_fields


log_safe_submission_exception = partial(
    _log_submission_exception, exception_log_fields=safe_exception_log_fields,
)

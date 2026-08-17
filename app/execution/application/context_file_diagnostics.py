from __future__ import annotations

from collections.abc import Mapping
import re

from app.context.api import CONTEXT_FILE_ERROR_CODES, CONTEXT_FILE_FAILURE_SCHEMA_VERSION


_DIAGNOSTIC_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_FILE_KIND_RE = re.compile(r"^[a-z0-9.+-]{1,32}$")
_PHASES = frozenset(
    {
        "authorization",
        "classification",
        "identity",
        "limits",
        "materialization",
        "parser",
        "staging",
        "storage",
    }
)


def validated_context_file_diagnostic(
    executor_payload: Mapping[str, object],
) -> dict[str, object] | None:
    raw = executor_payload.get("context_file_failure")
    if not isinstance(raw, dict) or raw.get("schema_version") != CONTEXT_FILE_FAILURE_SCHEMA_VERSION:
        return None
    reason_code = str(raw.get("reason_code") or "")
    phase = str(raw.get("phase") or "")
    diagnostic_id = str(raw.get("diagnostic_id") or "")
    exception_chain = raw.get("exception_chain")
    if (
        reason_code not in CONTEXT_FILE_ERROR_CODES
        or phase not in _PHASES
        or _DIAGNOSTIC_ID_RE.fullmatch(diagnostic_id) is None
        or not isinstance(exception_chain, list)
        or len(exception_chain) > 4
        or any(
            not isinstance(item, str) or len(item) > 80 or not item.isidentifier()
            for item in exception_chain
        )
    ):
        return None
    diagnostic: dict[str, object] = {
        "schema_version": CONTEXT_FILE_FAILURE_SCHEMA_VERSION,
        "diagnostic_id": diagnostic_id,
        "reason_code": reason_code,
        "phase": phase,
        "exception_chain": list(exception_chain),
    }
    file_kind = raw.get("file_kind")
    if file_kind is not None:
        normalized_kind = str(file_kind)
        if _FILE_KIND_RE.fullmatch(normalized_kind) is None:
            return None
        diagnostic["file_kind"] = normalized_kind
    attachment_index = raw.get("attachment_index")
    if attachment_index is not None:
        if (
            not isinstance(attachment_index, int)
            or isinstance(attachment_index, bool)
            or not 1 <= attachment_index <= 32
        ):
            return None
        diagnostic["attachment_index"] = attachment_index
    return diagnostic


def context_file_failure_event_payload(
    diagnostic: dict[str, object] | None,
) -> dict[str, object]:
    return {"context_file_failure": diagnostic} if diagnostic is not None else {}


def context_file_failure_event_fields(
    diagnostic: dict[str, object] | None,
    *,
    trace_id: str,
    error_code: str,
) -> dict[str, object]:
    if diagnostic is None:
        return {}
    return {
        "trace_id": trace_id,
        "severity": "error",
        "visible_to_user": False,
        "error_code": error_code,
    }


def context_file_failure_log_extra(
    diagnostic: dict[str, object],
    *,
    run_id: str,
    attempt_id: str,
    trace_id: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "trace_id": trace_id,
        **diagnostic,
    }


__all__ = [
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "validated_context_file_diagnostic",
]

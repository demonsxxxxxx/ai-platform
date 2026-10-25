"""Closed, content-free facts for private execution diagnostics."""

import re
from collections.abc import Mapping


_LABELS = {
    "component": {"claude_executor", "model_proxy", "provider_session"},
    "operation": {"messages", "count_tokens", "load", "append", "list_subkeys", "initialize"},
    "phase": {"preflight", "connect", "response", "stream", "entry_validation", "structured_entry_validation", "image_validation"},
    "failure_class": {"policy_rejected", "connect_error", "connect_timeout", "read_timeout", "write_timeout", "protocol_error", "transport_error", "http_error", "size_limit", "invalid_response"},
    "policy": {"anthropic_version", "anthropic_beta", "capacity", "request", "binding"},
    "entry_kind": {"user", "assistant", "system", "summary", "file-history-snapshot", "progress", "cost-state", "queue-operation", "other"},
    "scope": {"main", "child"},
}
_COUNTS = {
    "attempt_count", "beta_count", "entry_count", "entry_index", "observed_bytes", "limit_bytes",
    "image_count", "encoded_image_bytes", "largest_encoded_image_bytes",
    "expected_sequence", "accepted_sequence", "timeout_ms",
}
_VERSIONS = {"sdk_version", "cli_version", "python_version"}
_COMMITS = {"executor_runtime_commit", "api_runtime_commit"}


def normalize_execution_debug(value: object) -> dict[str, str | int]:
    """Never retain arbitrary labels, URLs, headers, entries, or exception text."""
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | int] = {}
    for name, allowed in _LABELS.items():
        item = value.get(name)
        if name == "entry_kind" and isinstance(item, str):
            result[name] = item if item in allowed else "other"
        elif isinstance(item, str) and item in allowed:
            result[name] = item
    for name in _COUNTS:
        item = value.get(name)
        if type(item) is int and 0 <= item <= 1_000_000_000:
            result[name] = item
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    for name in _VERSIONS:
        item = value.get(name)
        if isinstance(item, str) and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,6}(?:[a-z][0-9]{1,3})?", item):
            result[name] = item
    for name in _COMMITS:
        item = value.get(name)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{40}", item):
            result[name] = item
    return result

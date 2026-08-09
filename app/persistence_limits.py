"""Hard application bounds for values persisted in PostgreSQL."""

from __future__ import annotations

import json
from typing import Any


RUN_INPUT_MAX_BYTES = 256 * 1024
RUN_RESULT_MAX_BYTES = 256 * 1024
RUN_EVENT_PAYLOAD_MAX_BYTES = 64 * 1024
RUN_EVENT_MESSAGE_MAX_BYTES = 16 * 1024
CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES = 256 * 1024
ARTIFACT_MANIFEST_MAX_BYTES = 64 * 1024
AUDIT_PAYLOAD_MAX_BYTES = 32 * 1024
MESSAGE_CONTENT_MAX_BYTES = 256 * 1024
MESSAGE_METADATA_MAX_BYTES = 64 * 1024


class PersistenceSizeLimitError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def ensure_json_size(value: Any, *, max_bytes: int, code: str) -> None:
    if json_size_bytes(value) > max_bytes:
        raise PersistenceSizeLimitError(code)


def ensure_text_size(value: str, *, max_bytes: int, code: str) -> None:
    if len(value.encode("utf-8")) > max_bytes:
        raise PersistenceSizeLimitError(code)

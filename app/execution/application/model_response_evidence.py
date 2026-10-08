"""Private, bounded evidence from the model proxy's Anthropic text stream."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Iterator
from uuid import uuid4


_logger = logging.getLogger(__name__)
_MAX_SSE_LINE_BYTES = 64 * 1024


def observe_anthropic_text(
    body: Iterable[bytes], *, run_id: str, attempt_id: str
) -> Iterator[bytes]:
    """Forward bytes unchanged and checkpoint model text before the SDK sees it."""
    call_id = uuid4().hex
    pending = bytearray()
    data_lines: list[bytes] = []
    data_size = 0
    digest = hashlib.sha256()
    chars = events = 0
    complete = False
    observing = True
    coverage = "text_delta"

    def record() -> None:
        try:
            _logger.warning(
                "model_wire_text run_id=%s attempt_id=%s call_id=%s events=%d chars=%d sha256=%s coverage=%s",
                run_id, attempt_id, call_id, events, chars, digest.hexdigest(), coverage,
            )
        except Exception:  # Diagnostics cannot interrupt the model stream.
            pass

    def accept_event() -> None:
        nonlocal chars, events, coverage, data_size, observing
        if not observing or not data_lines:
            return
        payload = b"\n".join(data_lines)
        data_lines.clear()
        data_size = 0
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            coverage = "partial_invalid_event"
            return
        if not isinstance(event, dict) or event.get("type") != "content_block_delta":
            return
        delta = event.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return
        text = delta.get("text")
        if not isinstance(text, str):
            coverage = "partial_invalid_text"
            return
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            coverage = "partial_invalid_text"
            observing = False
            return
        digest.update(encoded)
        chars += len(text)
        events += 1
        if events == 1 or (events >= 128 and events & (events - 1) == 0):
            record()

    try:
        for chunk in body:
            if observing:
                pending.extend(chunk)
                while (newline := pending.find(b"\n")) >= 0:
                    line = bytes(pending[:newline]).removesuffix(b"\r")
                    del pending[: newline + 1]
                    if not line:
                        accept_event()
                    elif line.startswith(b"data:"):
                        data = line[5:].lstrip(b" ")
                        data_size += len(data)
                        if data_size > _MAX_SSE_LINE_BYTES:
                            observing = False
                            coverage = "partial_oversized_event"
                            break
                        data_lines.append(data)
                if len(pending) > _MAX_SSE_LINE_BYTES:
                    observing = False
                    coverage = "partial_oversized_line"
                if not observing:
                    pending.clear()
                    data_lines.clear()
            yield chunk
        if coverage == "text_delta" and (pending or data_lines):
            coverage = "partial_unfinished_event"
        complete = True
    finally:
        if not complete and coverage == "text_delta":
            coverage = "partial_stream_end"
        record()

"""Private, bounded evidence from the model proxy's Anthropic text stream."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from app.execution.domain.model_text_checkpoint import ModelTextCheckpoint


_logger = logging.getLogger(__name__)
_MAX_SSE_LINE_BYTES = 64 * 1024


def observe_anthropic_text(
    body: Iterable[bytes], *, run_id: str, attempt_id: str
) -> Iterator[bytes]:
    """Forward bytes unchanged and checkpoint model text before the SDK sees it."""
    pending = bytearray()
    data_lines: list[bytes] = []
    data_size = 0
    observing = True

    def record(checkpoint: dict[str, object]) -> None:
        _logger.warning(
            "model_wire_text run_id=%s attempt_id=%s call_ref=%s events=%d chars=%d sha256=%s final=%s complete=%s coverage=%s",
            run_id, attempt_id, checkpoint["call_ref"], checkpoint["events"],
            checkpoint["chars"], checkpoint["sha256"], checkpoint["final"],
            checkpoint["complete"], checkpoint["coverage"],
        )

    observer = ModelTextCheckpoint(run_id=run_id, attempt_id=attempt_id, record=record)

    def accept_event() -> None:
        nonlocal data_size
        if not observing or not data_lines:
            return
        payload = b"\n".join(data_lines)
        data_lines.clear()
        data_size = 0
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            observer.partial("partial_invalid_event")
            return
        observer.accept(event)

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
                            observer.partial("partial_oversized_event")
                            break
                        data_lines.append(data)
                if len(pending) > _MAX_SSE_LINE_BYTES:
                    observing = False
                    observer.partial("partial_oversized_line")
                if not observing:
                    pending.clear()
                    data_lines.clear()
            yield chunk
        if pending or data_lines:
            observer.partial("partial_unfinished_event")
    finally:
        observer.finish()

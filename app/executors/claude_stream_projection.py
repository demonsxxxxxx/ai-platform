"""Fail-closed projection of Claude SDK raw stream events."""

from collections.abc import Callable
from typing import Any


class ClaudeStreamProjector:
    """Project one SDK raw event stream into safe publishable text.

    The interface accepts raw event dictionaries and returns only text with a
    stable lexical boundary.  A trailing safety window remains the fallback for
    a block without a stable boundary.  It never imports the SDK or publishes
    callbacks; callers retain those adapter responsibilities.
    """

    def __init__(
        self,
        *,
        sanitizer: Callable[[object], object],
        trailing_chars: int = 512,
        max_pending_chars: int = 4_096,
    ) -> None:
        """Create a bounded projector using the caller's public-text sanitizer."""

        self._sanitizer = sanitizer
        self._trailing_chars = max(0, trailing_chars)
        self._max_pending_chars = max(1, max_pending_chars)
        self._active_text_index: int | None = None
        self._pending_text = ""
        self._disabled = False
        self._partial_emitted = False

    @property
    def disabled(self) -> bool:
        """Whether an unsafe or conflicting event permanently disabled output."""

        return self._disabled

    @property
    def partial_emitted(self) -> bool:
        """Whether this projector has returned any text for publication."""

        return self._partial_emitted

    def accept(self, event: object) -> tuple[str, ...]:
        """Consume one raw event and return zero or more safe text chunks.

        Any malformed event or active-text sequence conflict permanently disables
        further output.  Valid non-text activity before a text block is ignored.
        """

        if self._disabled:
            return ()
        if not isinstance(event, dict):
            self._disable()
            return ()
        event_type = event.get("type")
        if event_type == "content_block_start":
            return self._accept_start(event)
        if event_type == "content_block_stop":
            return self._accept_stop(event)
        if event_type == "content_block_delta":
            return self._accept_delta(event)
        return ()

    def close_unfinished(self) -> None:
        """Disable an open text block that never received an exact stop event."""

        if self._active_text_index is not None:
            self._disable()

    def _accept_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        if self._active_text_index is not None:
            self._disable()
            return ()
        index = event.get("index")
        content_block = event.get("content_block")
        if not self._is_exact_index(index) or not isinstance(content_block, dict):
            self._disable()
            return ()
        if content_block.get("type") != "text":
            return ()
        self._active_text_index = index
        return ()

    def _accept_stop(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        if not self._is_active_index(index):
            self._disable()
            return ()
        if not self._is_safe(self._pending_text):
            self._disable()
            return ()
        chunk = self._pending_text
        self._pending_text = ""
        self._active_text_index = None
        return self._emit(chunk)

    def _accept_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        delta = event.get("delta")
        if self._active_text_index is None:
            if isinstance(delta, dict) and delta.get("type") != "text_delta":
                return ()
            self._disable()
            return ()
        if not self._is_active_index(index) or not isinstance(delta, dict):
            self._disable()
            return ()
        if delta.get("type") != "text_delta":
            self._disable()
            return ()
        text = delta.get("text")
        if (
            not isinstance(text, str)
            or not text
            or len(self._pending_text) + len(text) > self._max_pending_chars
        ):
            self._disable()
            return ()
        self._pending_text += text
        if not self._is_safe(self._pending_text):
            self._disable()
            return ()
        stable_length = self._stable_prefix_length()
        if stable_length <= 0:
            return ()
        chunk = self._pending_text[:stable_length]
        if not self._is_safe(chunk):
            self._disable()
            return ()
        self._pending_text = self._pending_text[stable_length:]
        return self._emit(chunk)

    def _stable_prefix_length(self) -> int:
        """Return a prefix that later text cannot turn into private content."""

        for index in range(len(self._pending_text) - 1, -1, -1):
            if not self._is_stable_boundary(self._pending_text[index]):
                continue
            candidate = self._pending_text[: index + 1]
            if self._is_safe_prefix(candidate):
                return len(candidate)
        return max(0, len(self._pending_text) - self._trailing_chars)

    @staticmethod
    def _is_stable_boundary(value: str) -> bool:
        return value.isspace() or value in "!?;。！？"

    def _is_safe_prefix(self, value: str) -> bool:
        if not self._is_safe(value):
            return False
        # A sensitive key may legally include whitespace before its assignment.
        # Reject a boundary whose already-published bytes would be rewritten by
        # the public sanitizer after the next fragment arrives.
        for continuation in ("= synthetic-value", ': "synthetic-value"'):
            sanitized = self._sanitizer(value + continuation)
            if not isinstance(sanitized, str) or not sanitized.startswith(value):
                return False
        return True

    def _is_safe(self, value: str) -> bool:
        sanitized = self._sanitizer(value)
        return isinstance(sanitized, str) and sanitized == value

    def _emit(self, value: str) -> tuple[str, ...]:
        if not value:
            return ()
        self._partial_emitted = True
        return (value,)

    def _disable(self) -> None:
        self._disabled = True
        self._pending_text = ""
        self._active_text_index = None

    def _is_active_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._active_text_index

    @staticmethod
    def _is_exact_index(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

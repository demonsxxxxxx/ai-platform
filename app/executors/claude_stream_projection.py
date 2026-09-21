"""Validate Claude SDK text framing before the separate public-answer gate."""

from typing import Any


class AssistantAnswerTimeline:
    """Reconcile SDK delta/full-message pairs without merging distinct turns.

    Each AssistantMessage closes its current delta source. ResultMessage is a
    terminal supplement to the last source, not a replacement for earlier text.
    A non-prefix complete message remains after an emitted delta because live
    publication cannot retract the earlier source. Content remains executor-
    private here and must pass the answer gate.
    """

    def __init__(self) -> None:
        self._messages: list[str] = []
        self._streamed = ""

    @property
    def text(self) -> str:
        return "\n\n".join(
            self._messages + ([self._streamed] if self._streamed else [])
        )

    def accept_delta(self, text: str) -> str:
        prefix = "\n\n" if self._messages and not self._streamed and text else ""
        self._streamed += text
        return prefix + text

    def accept_assistant(self, text: str | None) -> str:
        complete = text if text else self._streamed
        missing = ""
        if complete:
            if not self._streamed:
                missing = ("\n\n" if self._messages else "") + complete
            elif complete.startswith(self._streamed):
                missing = complete[len(self._streamed) :]
            else:
                # The delta may already be visible in the live callback and
                # cannot be retracted when the complete message differs.
                self._messages.append(self._streamed)
                missing = "\n\n" + complete
            self._messages.append(complete)
        self._streamed = ""
        return missing

    def accept_result(self, text: str) -> str:
        if self._streamed:
            self.accept_assistant(None)
        if not text:
            return ""
        full = self.text
        if full and text.startswith(full):
            self._messages = [text]
            return text[len(full) :]
        if self._messages and text.startswith(self._messages[-1]):
            missing = text[len(self._messages[-1]) :]
            self._messages[-1] = text
            return missing
        missing = ("\n\n" if self._messages else "") + text
        self._messages.append(text)
        return missing


class ClaudeStreamProjector:
    """Return text deltas only while the SDK is inside an exact text block.

    The projector is deliberately a framing validator, not a turn classifier.
    Safe text can therefore continue to the public-answer gate immediately;
    thinking, tool input, and other non-text blocks are observed only so their
    deltas cannot be mistaken for Assistant prose.

    A typed ``AssistantMessage`` is not a framing boundary.  The SDK can emit
    that object before the matching raw ``content_block_stop`` event, so only
    raw stream events open and close raw stream state here.
    """

    def __init__(self) -> None:
        self._active_text_index: int | None = None
        self._ignored_block_index: int | None = None
        self._ignored_block_type: str | None = None
        self._completed_block_indexes: set[int] = set()
        self._explicit_message_open = False
        self._disabled = False
        self._partial_emitted = False

    @property
    def disabled(self) -> bool:
        """Whether an unsafe or conflicting event permanently disabled output."""

        return self._disabled

    @property
    def partial_emitted(self) -> bool:
        """Whether this projector has emitted any text in its lifetime."""

        return self._partial_emitted

    def accept(self, event: object) -> tuple[str, ...]:
        """Consume one raw event and return newly framed text immediately.

        Returned fragments remain executor-private until the caller passes
        them through the public-answer sanitization gate.
        """

        if self._disabled:
            return ()
        if not isinstance(event, dict):
            self._disable()
            return ()
        event_type = event.get("type")
        if event_type == "message_start":
            return self._accept_message_start(event)
        if event_type == "message_delta":
            return self._accept_message_delta(event)
        if event_type == "message_stop":
            return self._accept_message_stop(event)
        if event_type == "content_block_start":
            return self._accept_start(event)
        if event_type == "content_block_stop":
            return self._accept_stop(event)
        if event_type == "content_block_delta":
            return self._accept_delta(event)
        return ()

    def close_unfinished(self) -> None:
        """Disable raw framing that never received its exact stop event."""

        if (
            self._active_text_index is not None
            or self._ignored_block_index is not None
            or self._explicit_message_open
        ):
            self._disable()

    def _accept_message_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        message = event.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        if (
            not isinstance(message_id, str)
            or not message_id
            or self._explicit_message_open
            or self._active_text_index is not None
            or self._ignored_block_index is not None
            or self._completed_block_indexes
        ):
            self._disable()
            return ()
        self._explicit_message_open = True
        return ()

    def _accept_message_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        delta = event.get("delta")
        if not isinstance(delta, dict):
            self._disable()
            return ()
        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
        return ()

    def _accept_message_stop(self, event: dict[str, Any]) -> tuple[str, ...]:
        del event
        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
            return ()
        self._explicit_message_open = False
        self._completed_block_indexes.clear()
        return ()

    def _accept_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
            return ()
        index = event.get("index")
        content_block = event.get("content_block")
        if (
            not self._is_exact_index(index)
            or not isinstance(content_block, dict)
            or (self._explicit_message_open and index in self._completed_block_indexes)
        ):
            self._disable()
            return ()
        content_type = content_block.get("type")
        if not isinstance(content_type, str) or not content_type:
            self._disable()
            return ()
        if content_type != "text":
            self._ignored_block_index = index
            self._ignored_block_type = content_type
            return ()
        self._active_text_index = index
        return ()

    def _accept_stop(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        if self._ignored_block_index is not None:
            if self._ignored_block_type is None or not self._is_ignored_index(index):
                self._disable()
                return ()
            self._ignored_block_index = None
            self._ignored_block_type = None
            if self._explicit_message_open:
                self._completed_block_indexes.add(index)
            return ()
        if not self._is_active_index(index):
            self._disable()
            return ()
        self._active_text_index = None
        if self._explicit_message_open:
            self._completed_block_indexes.add(index)
        return ()

    def _accept_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        index = event.get("index")
        delta = event.get("delta")
        if self._ignored_block_index is not None:
            if (
                self._ignored_block_type is None
                or not self._is_ignored_index(index)
                or not isinstance(delta, dict)
                or not isinstance(delta.get("type"), str)
                or delta.get("type") == "text_delta"
            ):
                self._disable()
            return ()
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
        if not isinstance(text, str) or not text:
            self._disable()
            return ()
        self._partial_emitted = True
        return (text,)

    def _disable(self) -> None:
        self._disabled = True
        self._active_text_index = None
        self._ignored_block_index = None
        self._ignored_block_type = None
        self._completed_block_indexes.clear()
        self._explicit_message_open = False

    def _is_active_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._active_text_index

    def _is_ignored_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._ignored_block_index

    @staticmethod
    def _is_exact_index(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

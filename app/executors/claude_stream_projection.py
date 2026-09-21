"""Validate Claude SDK text framing before the separate public-answer gate."""

from dataclasses import dataclass
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
        return "\n\n".join(self._messages + ([self._streamed] if self._streamed else []))

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
                missing = complete[len(self._streamed):]
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
            return text[len(full):]
        if self._messages and text.startswith(self._messages[-1]):
            missing = text[len(self._messages[-1]):]
            self._messages[-1] = text
            return missing
        missing = ("\n\n" if self._messages else "") + text
        self._messages.append(text)
        return missing


@dataclass(frozen=True)
class ClaudeStreamTurn:
    """One SDK assistant turn after its partial blocks are framed."""

    text: str
    message_id: str | None
    stop_reason: str | None
    parent_tool_use_id: str | None
    has_tool_use: bool

    @property
    def is_commentary(self) -> bool:
        return self.has_tool_use or self.stop_reason == "tool_use"


class ClaudeStreamProjector:
    """Frame partial SDK blocks without deciding their public destination.

    Partial text is retained until the typed ``AssistantMessage`` closes the
    SDK turn.  Its block types and stop reason then decide whether the text is
    commentary or answer; lexical content never participates in that decision.
    """

    def __init__(self) -> None:
        self._active_text_index: int | None = None
        self._ignored_block_index: int | None = None
        self._ignored_block_type: str | None = None
        self._disabled = False
        self._partial_emitted = False
        self._text_parts: list[str] = []
        self._message_id: str | None = None
        self._stop_reason: str | None = None
        self._parent_tool_use_id: str | None = None
        self._has_tool_use = False

    @property
    def disabled(self) -> bool:
        """Whether an unsafe or conflicting event permanently disabled output."""

        return self._disabled

    @property
    def partial_emitted(self) -> bool:
        """Whether this projector has received any text for the current turn."""

        return self._partial_emitted

    def accept(
        self,
        event: object,
        *,
        parent_tool_use_id: object = None,
    ) -> tuple[str, ...]:
        """Consume one raw event and retain its text for the typed turn boundary.

        The returned fragments remain executor-private.  The runner must wait
        for ``finish_turn`` before sending them to an answer or commentary
        projector.
        """

        if self._disabled:
            return ()
        if isinstance(parent_tool_use_id, str) and parent_tool_use_id:
            self._parent_tool_use_id = parent_tool_use_id
        if not isinstance(event, dict):
            self._disable()
            return ()
        event_type = event.get("type")
        if event_type == "message_start":
            return self._accept_message_start(event)
        if event_type == "message_delta":
            return self._accept_message_delta(event)
        if event_type == "content_block_start":
            return self._accept_start(event)
        if event_type == "content_block_stop":
            return self._accept_stop(event)
        if event_type == "content_block_delta":
            return self._accept_delta(event)
        return ()

    def close_unfinished(self) -> None:
        """Disable an open text block that never received an exact stop event."""

        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()

    def finish_turn(
        self,
        *,
        message_id: object = None,
        stop_reason: object = None,
        parent_tool_use_id: object = None,
        text: object = None,
        has_tool_use: bool = False,
    ) -> ClaudeStreamTurn:
        """Return the complete turn and reset framing for the next SDK turn."""

        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
        complete_text = (
            text
            if isinstance(text, str)
            else "".join(self._text_parts)
        )
        resolved_message_id = (
            message_id
            if isinstance(message_id, str) and message_id
            else self._message_id
        )
        resolved_stop_reason = (
            stop_reason
            if isinstance(stop_reason, str) and stop_reason
            else self._stop_reason
        )
        resolved_parent_tool_use_id = (
            parent_tool_use_id
            if isinstance(parent_tool_use_id, str) and parent_tool_use_id
            else self._parent_tool_use_id
        )
        turn = ClaudeStreamTurn(
            text=complete_text,
            message_id=resolved_message_id,
            stop_reason=resolved_stop_reason,
            parent_tool_use_id=resolved_parent_tool_use_id,
            has_tool_use=self._has_tool_use or has_tool_use,
        )
        self._reset()
        return turn

    def finish_message(self) -> bool:
        """Keep the legacy framing-only helper for direct projector callers."""

        was_disabled = (
            self._disabled
            or self._active_text_index is not None
            or self._ignored_block_index is not None
        )
        self.finish_turn()
        return was_disabled

    def _accept_message_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        message = event.get("message")
        if isinstance(message, dict):
            message_id = message.get("id")
            if isinstance(message_id, str) and message_id:
                self._message_id = message_id
            stop_reason = message.get("stop_reason")
            if isinstance(stop_reason, str) and stop_reason:
                self._stop_reason = stop_reason
        return ()

    def _accept_message_delta(self, event: dict[str, Any]) -> tuple[str, ...]:
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return ()
        stop_reason = delta.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason:
            self._stop_reason = stop_reason
        return ()

    def _accept_start(self, event: dict[str, Any]) -> tuple[str, ...]:
        if self._active_text_index is not None or self._ignored_block_index is not None:
            self._disable()
            return ()
        index = event.get("index")
        content_block = event.get("content_block")
        if not self._is_exact_index(index) or not isinstance(content_block, dict):
            self._disable()
            return ()
        content_type = content_block.get("type")
        if not isinstance(content_type, str) or not content_type:
            self._disable()
            return ()
        if content_type in {"tool_use", "server_tool_use"}:
            self._has_tool_use = True
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
            return ()
        if not self._is_active_index(index):
            self._disable()
            return ()
        self._active_text_index = None
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
        self._text_parts.append(text)
        return (text,)

    def _disable(self) -> None:
        self._disabled = True
        self._active_text_index = None
        self._ignored_block_index = None
        self._ignored_block_type = None

    def _reset(self) -> None:
        self._active_text_index = None
        self._ignored_block_index = None
        self._ignored_block_type = None
        self._disabled = False
        self._partial_emitted = False
        self._text_parts.clear()
        self._message_id = None
        self._stop_reason = None
        self._parent_tool_use_id = None
        self._has_tool_use = False

    def _is_active_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._active_text_index

    def _is_ignored_index(self, value: object) -> bool:
        return self._is_exact_index(value) and value == self._ignored_block_index

    @staticmethod
    def _is_exact_index(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

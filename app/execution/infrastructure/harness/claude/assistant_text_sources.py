"""Provider-message roles for completed SDK blocks, without retaining text.

The public answer gate owns sanitization; the event adapter owns public parts.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClassifiedAssistantText:
    """Metadata for a retired provider source; text remains in the event ledger."""

    has_text: bool
    role: str | None = None


class AssistantTextSourceBuffer:
    """Group complete text blocks by message, then classify its public part."""

    def __init__(self) -> None:
        self._message_key: Hashable | None = None
        self._sources: dict[Hashable, tuple[bool, str | None]] = {}
        self._meaningful_sources: set[Hashable] = set()

    def _state(self, message_key: Hashable) -> tuple[bool, str | None]:
        return self._sources.setdefault(message_key, (False, None))

    @property
    def message_key(self) -> Hashable | None:
        return self._message_key

    def begin(self, message_key: Hashable) -> None:
        if message_key is None:
            raise ValueError("assistant_text_source_invalid")
        hash(message_key)
        if self._message_key not in (None, message_key):
            raise ValueError("assistant_text_source_not_closed")
        self._state(message_key)
        self._message_key = message_key

    def mark_tool(self, message_key: Hashable) -> None:
        self.begin(message_key)
        has_text, role = self._state(message_key)
        self._sources[message_key] = (has_text, "work")

    def mark_answer(self, message_key: Hashable) -> None:
        self.begin(message_key)
        has_text, role = self._state(message_key)
        if role == "work":
            raise ValueError("assistant_text_source_role_conflict")
        self._sources[message_key] = (has_text, "answer")

    def role_for(self, message_key: Hashable) -> str | None:
        state = self._sources.get(message_key)
        return state[1] if state is not None else None

    def has_meaningful_text_for(self, message_key: Hashable) -> bool:
        return message_key in self._meaningful_sources

    @property
    def has_meaningful_answer_sources(self) -> bool:
        return any(self.role_for(key) == "answer" for key in self._meaningful_sources)

    def _observe_text(self, message_key: Hashable, text: str) -> None:
        _had_text, role = self._state(message_key)
        if text.strip() and message_key not in self._meaningful_sources:
            self._meaningful_sources.add(message_key)
        self._sources[message_key] = (True, role)

    def append_text(self, message_key: Hashable, text: str) -> str:
        """Record completed block text; source parts need no synthetic gaps."""
        if not isinstance(text, str):
            raise ValueError("assistant_text_delta_invalid")
        self.begin(message_key)
        if text:
            self._observe_text(message_key, text)
        return text

    def take(
        self, message_key: Hashable | None = None
    ) -> ClassifiedAssistantText | None:
        if self._message_key is None:
            if message_key is not None:
                raise ValueError("assistant_text_source_missing")
            return None
        if message_key is not None and self._message_key != message_key:
            raise ValueError("assistant_text_source_mismatch")
        key = self._message_key
        has_text, role = self._state(key)
        result = ClassifiedAssistantText(
            has_text=has_text,
            role=role,
        )
        self._message_key = None
        if not has_text and role != "answer":
            self._sources.pop(key, None)
        return result

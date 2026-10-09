"""Source-local metadata for already reconciled Claude text suffixes.

This router never retains text. AssistantAnswerTimeline is the sole
raw/typed reconciliation authority; the public answer gate owns its bounded
sensitive suffix, and the event adapter owns each public part's text and role.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable
from dataclasses import dataclass

_UNSET = object()
_MAX_RETAINED_SOURCES = 128


@dataclass(frozen=True, slots=True)
class ClassifiedAssistantText:
    """Metadata for a retired provider source; text remains in the event ledger."""

    message_key: Hashable
    is_tool: bool
    has_text: bool
    role: str | None = None

    @property
    def commentary_identity(self) -> str:
        """Return a stable safe identity without exposing provider IDs."""

        return "source-" + hashlib.sha256(
            repr(self.message_key).encode("utf-8")
        ).hexdigest()


class AssistantTextSourceBuffer:
    """Route Timeline suffixes by provider source without buffering their text.

    Timeline inserts exactly two line feeds before the first output from a new
    message. Remove only that synthetic prefix before sending the text to the
    per-part gate. Whitespace from the provider is left intact.
    """

    def __init__(self) -> None:
        self._message_key: Hashable | None = None
        self._previous_timeline_message: object = _UNSET
        self._sources: dict[Hashable, tuple[bool, str | None]] = {}
        self._retired_sources: dict[Hashable, None] = {}
        self._timeline_owners: dict[Hashable, Hashable] = {}
        self._answer_source_count = 0
        self._meaningful_sources: set[Hashable] = set()
        self._meaningful_answer_source_count = 0

    def _evict_source(self, message_key: Hashable) -> None:
        self._sources.pop(message_key, None)
        self._meaningful_sources.discard(message_key)
        self._retired_sources.pop(message_key, None)
        self._timeline_owners = {
            timeline_key: owner
            for timeline_key, owner in self._timeline_owners.items()
            if owner != message_key
        }

    def _ensure_capacity(
        self,
        message_key: Hashable,
        timeline_message_key: Hashable | None = None,
    ) -> None:
        needs_source = message_key not in self._sources
        needs_timeline_owner = (
            timeline_message_key is not None
            and timeline_message_key not in self._timeline_owners
        )
        source_count = len(self._sources) + int(needs_source)
        timeline_count = len(self._timeline_owners) + int(needs_timeline_owner)
        if (
            source_count <= _MAX_RETAINED_SOURCES
            and timeline_count <= _MAX_RETAINED_SOURCES
        ):
            return
        evictions: list[Hashable] = []
        for candidate in self._retired_sources:
            if candidate in {message_key, self._message_key}:
                continue
            evictions.append(candidate)
            source_count -= int(candidate in self._sources)
            timeline_count -= sum(
                owner == candidate for owner in self._timeline_owners.values()
            )
            if (
                source_count <= _MAX_RETAINED_SOURCES
                and timeline_count <= _MAX_RETAINED_SOURCES
            ):
                break
        if (
            source_count > _MAX_RETAINED_SOURCES
            or timeline_count > _MAX_RETAINED_SOURCES
        ):
            raise ValueError("assistant_text_source_limit")
        for candidate in evictions:
            self._evict_source(candidate)

    def _state(self, message_key: Hashable) -> tuple[bool, str | None]:
        if message_key not in self._sources:
            self._ensure_capacity(message_key)
            self._sources[message_key] = (False, None)
        return self._sources[message_key]

    @property
    def message_key(self) -> Hashable | None:
        return self._message_key

    @property
    def is_tool(self) -> bool:
        return bool(
            self._message_key is not None
            and self._sources.get(self._message_key, (False, None))[1] == "work"
        )

    @property
    def has_text(self) -> bool:
        return bool(
            self._message_key is not None
            and self._sources.get(self._message_key, (False, None))[0]
        )

    def begin(self, message_key: Hashable) -> None:
        if message_key is None:
            raise ValueError("assistant_text_source_invalid")
        hash(message_key)
        if self._message_key not in (None, message_key):
            raise ValueError("assistant_text_source_not_closed")
        self._state(message_key)
        self._retired_sources.pop(message_key, None)
        self._message_key = message_key

    def mark_tool(self, message_key: Hashable) -> None:
        self.begin(message_key)
        has_text, role = self._state(message_key)
        if role == "answer" and has_text:
            self._answer_source_count = max(0, self._answer_source_count - 1)
            if message_key in self._meaningful_sources:
                self._meaningful_answer_source_count -= 1
        self._sources[message_key] = (has_text, "work" if role != "work" else role)

    def mark_answer(self, message_key: Hashable) -> None:
        self.begin(message_key)
        has_text, role = self._state(message_key)
        if role == "work":
            raise ValueError("assistant_text_source_role_conflict")
        if role != "answer" and has_text:
            self._answer_source_count += 1
            if message_key in self._meaningful_sources:
                self._meaningful_answer_source_count += 1
        self._sources[message_key] = (has_text, "answer")

    def role_for(self, message_key: Hashable) -> str | None:
        state = self._sources.get(message_key)
        return state[1] if state is not None else None

    def has_text_for(self, message_key: Hashable) -> bool:
        state = self._sources.get(message_key)
        return state[0] if state is not None else False

    @property
    def has_answer_sources(self) -> bool:
        return self._answer_source_count > 0

    @property
    def has_meaningful_answer_sources(self) -> bool:
        return self._meaningful_answer_source_count > 0

    def _observe_text(self, message_key: Hashable, text: str) -> None:
        had_text, role = self._state(message_key)
        if role == "answer" and not had_text:
            self._answer_source_count += 1
        if text.strip() and message_key not in self._meaningful_sources:
            self._meaningful_sources.add(message_key)
            if role == "answer":
                self._meaningful_answer_source_count += 1
        self._sources[message_key] = (True, role)

    def owner_for_timeline(self, timeline_message_key: Hashable) -> Hashable | None:
        return self._timeline_owners.get(timeline_message_key)

    def bind_timeline(self, message_key: Hashable, timeline_message_key: Hashable) -> None:
        """Retain an empty verified answer binding for a later Result suffix."""
        if message_key is None or timeline_message_key is None:
            raise ValueError("assistant_text_source_invalid")
        owner = self._timeline_owners.get(timeline_message_key)
        if owner not in (None, message_key):
            raise ValueError("assistant_text_timeline_owner_conflict")
        self._ensure_capacity(message_key, timeline_message_key)
        self.begin(message_key)
        self._timeline_owners[timeline_message_key] = message_key

    def append_reconciled(
        self,
        message_key: Hashable,
        timeline_message_key: Hashable,
        delta: str,
    ) -> str:
        """Return the unique suffix after removing Timeline's synthetic gap."""

        if not isinstance(delta, str):
            raise ValueError("assistant_text_delta_invalid")
        if not delta:
            return ""
        if message_key is None or timeline_message_key is None:
            raise ValueError("assistant_text_source_invalid")
        hash(message_key)
        hash(timeline_message_key)
        if self._message_key not in (None, message_key):
            raise ValueError("assistant_text_source_not_closed")

        routed = delta
        if (
            self._previous_timeline_message is not _UNSET
            and self._previous_timeline_message != timeline_message_key
        ):
            if not routed.startswith("\n\n"):
                raise ValueError("assistant_text_separator_conflict")
            routed = routed[2:]

        # Validate everything before changing owner or separator state.
        if self._message_key not in (None, message_key):
            raise ValueError("assistant_text_source_not_closed")
        owner = self._timeline_owners.get(timeline_message_key)
        if owner not in (None, message_key):
            raise ValueError("assistant_text_timeline_owner_conflict")
        self._ensure_capacity(message_key, timeline_message_key)
        self.begin(message_key)
        self._previous_timeline_message = timeline_message_key
        self._timeline_owners[timeline_message_key] = message_key
        if routed:
            self._observe_text(message_key, routed)
        return routed

    def append_explicit_result(
        self,
        message_key: Hashable,
        delta: str,
    ) -> str:
        """Bind an allowed nonstreaming Result-only compatibility suffix."""

        if not isinstance(delta, str):
            raise ValueError("assistant_text_delta_invalid")
        if message_key is None:
            raise ValueError("assistant_text_source_invalid")
        hash(message_key)
        self.begin(message_key)
        if delta:
            self._observe_text(message_key, delta)
        return delta

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
            message_key=key,
            is_tool=role == "work",
            has_text=has_text,
            role=role,
        )
        self._message_key = None
        if not has_text and role != "answer":
            self._sources.pop(key, None)
            self._retired_sources.pop(key, None)
            self._timeline_owners = {
                timeline_key: owner
                for timeline_key, owner in self._timeline_owners.items()
                if owner != key
            }
        else:
            self._retired_sources[key] = None
        return result

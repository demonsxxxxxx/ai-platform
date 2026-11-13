"""Reconcile native text deltas with the SDK's completed content blocks.

Text blocks retain only length/digest metadata for completion checks. Public
sanitization and durable event identities remain with their existing owners.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class _StreamTextBlock:
    digest: object = field(default_factory=hashlib.sha256)
    text_length: int = 0
    completed: bool = False
    completion_digest: bytes | None = None
    completion_length: int = 0
    completion_published: bool = False
    stopped: bool = False

    def append(self, text: str) -> None:
        self.digest.update(text.encode("utf-8"))
        self.text_length += len(text)


class AssistantTextSourceBuffer:
    """Use native message/block identity to publish text exactly once, in order."""

    def __init__(self) -> None:
        self.message_key: tuple[str, None] | None = None
        self._active_index: int | None = None
        self._blocks: dict[str, list[_StreamTextBlock]] = {}
        self._block: _StreamTextBlock | None = None
        self._seen_frames: dict[str, str] = {}
        self._started_messages: set[str] = set()

    def accept_event(self, *, uuid: object, event: object) -> tuple[tuple[str, None], str] | None:
        """Select only main-session text; ignore tool/reasoning payloads."""
        if not isinstance(event, dict):
            raise ValueError("assistant_observation_invalid")
        if isinstance(uuid, str) and uuid:
            digest = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous = self._seen_frames.get(uuid)
            if previous is not None:
                if previous != digest:
                    raise ValueError("assistant_observation_invalid")
                return None
            self._seen_frames[uuid] = digest
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message")
            identity = message.get("id") if isinstance(message, dict) else None
            if not isinstance(identity, str) or not 0 < len(identity) <= 512:
                raise ValueError("assistant_observation_invalid")
            if self.message_key is not None or identity in self._started_messages:
                raise ValueError("assistant_observation_invalid")
            self._started_messages.add(identity)
            self.message_key = (identity, None)
            self._blocks[identity] = []
        elif kind == "content_block_start":
            index, block = event.get("index"), event.get("content_block")
            if self.message_key is None and isinstance(block, dict) and block.get("type") != "text":
                return None
            if self.message_key is None or self._active_index is not None:
                raise ValueError("assistant_observation_invalid")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0 or not isinstance(block, dict):
                raise ValueError("assistant_observation_invalid")
            self._active_index = index
            self._block = _StreamTextBlock() if block.get("type") == "text" else None
            if self._block is not None:
                self._blocks[self.message_key[0]].append(self._block)
                text = block.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("typed_text_block_invalid")
                self._block.append(text)
                if text:
                    return self.message_key, text
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict) or delta.get("type") != "text_delta":
                return None
            if self.message_key is None or event.get("index") != self._active_index or self._block is None:
                raise ValueError("assistant_observation_invalid")
            text = delta.get("text")
            if not isinstance(text, str):
                raise ValueError("typed_text_block_invalid")
            if self._block.completed and self._block.text_length + len(text) > self._block.completion_length:
                raise ValueError("assistant_text_conflict")
            self._block.append(text)
            if (
                self._block.completed
                and self._block.text_length == self._block.completion_length
                and self._block.digest.digest() != self._block.completion_digest
            ):
                raise ValueError("assistant_text_conflict")
            if text and not self._block.completion_published:
                return self.message_key, text
        elif kind == "content_block_stop":
            if event.get("index") != self._active_index or self._active_index is None:
                raise ValueError("assistant_observation_invalid")
            if self._block is not None:
                self._block.stopped = True
                if self._block.completion_digest is not None and self._block.text_length:
                    if self._block.text_length != self._block.completion_length or self._block.digest.digest() != self._block.completion_digest:
                        raise ValueError("assistant_text_conflict")
            self._active_index = self._block = None
        elif kind == "message_stop":
            if self.message_key is None or self._active_index is not None:
                raise ValueError("assistant_observation_invalid")
            self.message_key = None
        return None

    def completed_text(self, source_key: tuple[str, None], text: str) -> str:
        """Confirm a streamed block, or use a complete block when no stream exists."""
        if not isinstance(text, str):
            raise ValueError("typed_text_block_invalid")
        blocks = self._blocks.get(source_key[0])
        if blocks is None:
            return text
        block = next((block for block in blocks if not block.completed), None)
        if block is None:
            raise ValueError("assistant_text_conflict")
        if (
            block.text_length > len(text)
            or (block.stopped and block.text_length and block.text_length != len(text))
            or block.digest.digest() != hashlib.sha256(text[:block.text_length].encode("utf-8")).digest()
        ):
            raise ValueError("assistant_text_conflict")
        block.completed = True
        block.completion_digest = hashlib.sha256(text.encode("utf-8")).digest()
        block.completion_length = len(text)
        if block.text_length == 0:
            block.completion_published = True
            return text
        return ""

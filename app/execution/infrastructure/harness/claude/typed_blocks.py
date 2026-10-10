"""Attempt-local replay identity for SDK completed blocks, without raw framing."""

import hashlib
import json


class ClaudeTypedBlockObservations:
    """Suppress exact UUID replay; conflicting observations cannot publish."""

    def __init__(self):
        # Match the lifetime of the public fact ledger. Eviction would turn a
        # replay into a fresh public delta; these identities contain no bodies.
        self._recent: dict[str, tuple[str, str]] = {}
        self._current_message: str | None = None
        self._retired_messages: set[str] = set()

    def accept(self, *, message_id, uuid, blocks, stop_reason):
        if not all(
            isinstance(value, str) and 0 < len(value) <= 1024
            for value in (message_id, uuid)
        ):
            raise ValueError("assistant_observation_invalid")
        digest = hashlib.sha256()
        try:
            digest.update(
                json.dumps([message_id, stop_reason], ensure_ascii=False).encode()
            )
            for kind, identity, name, text in blocks:
                if kind == "TextBlock" and not isinstance(text, str):
                    raise ValueError("typed_text_block_invalid")
                digest.update(
                    json.dumps([kind, identity, name], ensure_ascii=False).encode()
                )
                if text is not None:
                    if not isinstance(text, str):
                        raise ValueError("typed_text_block_invalid")
                    digest.update(len(text).to_bytes(8, "big"))
                    for offset in range(0, len(text), 8192):
                        digest.update(text[offset : offset + 8192].encode())
        except (TypeError, UnicodeError) as exc:
            raise ValueError("assistant_observation_invalid") from exc
        observation = (message_id, digest.hexdigest())
        previous = self._recent.get(uuid)
        if previous is not None:
            if previous != observation:
                raise ValueError("assistant_observation_invalid")
            return False
        if message_id in self._retired_messages:
            raise ValueError("assistant_observation_invalid")
        if self._current_message not in (None, message_id):
            self._retired_messages.add(self._current_message)
        self._current_message = message_id
        self._recent[uuid] = observation
        return True

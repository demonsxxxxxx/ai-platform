"""Bounded, private checkpoints for one Anthropic model response."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable


class ModelTextCheckpoint:
    """Observe raw text deltas independently of public answer reconciliation."""

    def __init__(
        self, *, run_id: str, attempt_id: str,
        record: Callable[[dict[str, object]], None],
    ) -> None:
        self._scope = json.dumps([run_id, attempt_id], separators=(",", ":")).encode()
        self._bound = bool(run_id and attempt_id)
        self._record = record
        self._digest = hashlib.sha256()
        self.call_ref: str | None = None
        self.events = self.chars = 0
        self.coverage = "text_delta"
        self.started = self.finished = False
        self._observing = True

    def partial(self, coverage: str) -> None:
        if self.coverage == "text_delta" or (
            self.coverage == "partial_missing_call_identity" and coverage != "partial_stream_end"
        ):
            self.coverage = coverage

    def _emit(self, *, final: bool, complete: bool) -> None:
        try:
            self._record({
                "call_ref": self.call_ref,
                "events": self.events,
                "chars": self.chars,
                "sha256": self._digest.hexdigest(),
                "final": final,
                "complete": complete,
                "coverage": self.coverage,
            })
        except Exception:  # Diagnostics cannot interrupt model/SDK output.
            pass

    def accept(self, event: object) -> None:
        if self.finished or not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "message_start":
            if self.started or self.events:
                self.partial("partial_invalid_event")
                self.call_ref = None
                self._observing = False
                return
            self.started = True
            message = event.get("message")
            identity = message.get("id") if isinstance(message, dict) else None
            if isinstance(identity, str) and 0 < len(identity) <= 1024 and self._bound:
                try:
                    self.call_ref = hmac.new(
                        hashlib.sha256(self._scope).digest(), identity.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()[:32]
                except UnicodeEncodeError:
                    self.partial("partial_missing_call_identity")
            else:
                self.partial("partial_missing_call_identity")
        elif kind == "message_stop":
            self.finish(complete=True)
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict) or delta.get("type") != "text_delta":
                return
            if not self.started:
                self.partial("partial_missing_call_identity")
            text = delta.get("text")
            if not isinstance(text, str):
                self.partial("partial_invalid_text")
                return
            if not self._observing:
                return
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError:
                self.partial("partial_invalid_text")
                self._observing = False
                return
            if self.events >= 10_000_000 or self.chars + len(text) > 100_000_000:
                self.coverage = "partial_limit"
                self._observing = False
                return
            self._digest.update(encoded)
            self.events += 1
            self.chars += len(text)
            if self.events == 1 or (self.events >= 128 and self.events & (self.events - 1) == 0):
                self._emit(final=False, complete=False)

    def finish(self, *, complete: bool = False) -> None:
        if self.finished:
            return
        if not complete:
            self.partial("partial_stream_end")
        elif not self.started:
            self.partial("partial_missing_call_identity")
        self.finished = True
        self._emit(final=True, complete=complete and self.coverage == "text_delta")

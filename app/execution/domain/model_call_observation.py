"""Content-free, bounded observations of one admitted provider request."""
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from typing import Any, Callable


class ModelCallObservation:
    def __init__(self, *, operation_id: Callable[[object, object], str | None], run_id: str):
        self.call_id = "mcall_" + uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._start = monotonic()
        self._operation_id, self._run_id = operation_id, run_id
        self.stop_reason: str | None = None
        self.message_started = self.message_stopped = False
        self.text_characters = 0
        self.tools: list[str] = []
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.state = "started"
        self.http_status: int | None = None
        self.first_response_ms: int | None = None
        self.partial = False
        self.observes_message_lifecycle = False

    def mark_partial(self) -> None:
        self.partial = True

    def response(self, status: int) -> None:
        self.http_status = status
        self.first_response_ms = round((monotonic() - self._start) * 1000)

    def accept(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self.message_started = True
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        else:
            usage = event.get("usage")
        if isinstance(usage, dict):
            for name in ("input_tokens", "output_tokens"):
                value = usage.get(name)
                if type(value) is int and 0 <= value <= 2**63 - 1:
                    setattr(self, name, value)
        if kind == "message_delta":
            delta = event.get("delta")
            reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            if isinstance(reason, str) and reason in {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"}:
                self.stop_reason = reason
        elif kind == "message_stop":
            self.message_stopped = True
        elif kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                identity = self._operation_id(self._run_id, block.get("id"))
                if identity and identity not in self.tools and len(self.tools) < 128:
                    self.tools.append(identity)
                elif len(self.tools) >= 128:
                    self.partial = True
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                self.text_characters = min(100_000_000, self.text_characters + len(delta["text"]))

    def snapshot(self, *, state: str | None = None) -> dict[str, Any]:
        return {"call_id": self.call_id, "started_at": self.started_at,
                "finished_at": datetime.now(timezone.utc).isoformat() if state else None,
                "state": state or "started", "duration_ms": round((monotonic() - self._start) * 1000) if state else None,
                "first_response_ms": self.first_response_ms, "http_status": self.http_status,
                "stop_reason": self.stop_reason, "message_started": self.message_started,
                "message_stopped": self.message_stopped, "text_characters": self.text_characters,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "operation_ids": self.tools.copy(), "partial": self.partial,
                "provider_internal_retry": "not_observable", "source": "platform_model_proxy.v1"}

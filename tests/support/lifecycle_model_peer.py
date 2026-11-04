"""Scripted Anthropic-compatible streaming peer for real-CLI lifecycle checks.

The bundled Claude Code CLI is a real process: it speaks the Anthropic Messages
API and (in this harness) reaches this loopback peer instead of a model.  Each
request is answered from a scripted turn list, so the harness can produce a
normal text turn, a tool turn whose tool call never returns, and a stalled turn
that keeps the CLI waiting until the test kills it.

Recorded per request: whether the wire history already contains a tool result,
the tool-result block ids, and the user text the CLI sent.  The harness uses
these to prove that recovery continued from the persisted native prefix instead
of a rewritten history.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TEXT_KIND = "text"
TOOL_KIND = "tool"
STALL_KIND = "stall"


@dataclass
class Turn:
    """One scripted assistant response."""

    kind: str = TEXT_KIND
    chunks: tuple[str, ...] = ("synthetic answer",)
    tool_name: str = "mcp__synthetic__blocking_lookup"
    tool_input: dict[str, Any] = field(default_factory=lambda: {"query": "synthetic"})
    chunk_delay_seconds: float = 0.02

    @classmethod
    def text(cls, *chunks: str, delay: float = 0.02) -> Turn:
        return cls(kind=TEXT_KIND, chunks=chunks or ("synthetic answer",), chunk_delay_seconds=delay)

    @classmethod
    def tool(cls, name: str = "mcp__synthetic__blocking_lookup", **tool_input: Any) -> Turn:
        return cls(kind=TOOL_KIND, tool_name=name, tool_input=tool_input or {"query": "synthetic"})

    @classmethod
    def stall(cls) -> Turn:
        return cls(kind=STALL_KIND)


@dataclass
class ModelRequest:
    """What the CLI actually sent on the wire for one turn."""

    index: int
    model: str
    turn_text: str
    tool_result_ids: list[str]
    tool_result_is_error: list[bool]
    tool_result_texts: list[str]
    prior_assistant_text: str
    prior_user_texts: list[str] = field(default_factory=list)

    def as_evidence(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "model": self.model,
            "turn_text": self.turn_text,
            "tool_result_ids": self.tool_result_ids,
            "tool_result_is_error": self.tool_result_is_error,
            "tool_result_texts": self.tool_result_texts,
            "prior_assistant_text": self.prior_assistant_text,
            "prior_user_turns": self.prior_user_texts,
        }


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def summarize_request(index: int, body: dict[str, Any]) -> ModelRequest:
    """Separate the native history prefix from the text this turn contributed."""

    messages = body.get("messages") or []
    user_texts: list[str] = []
    prior_assistant, tool_ids, errors, texts = "", [], [], []
    for message in messages:
        for block in _blocks(message):
            if block.get("type") == "text":
                if message.get("role") == "assistant":
                    prior_assistant += block.get("text") or ""
                else:
                    user_texts.append(block.get("text") or "")
            elif block.get("type") == "tool_result":
                tool_ids.append(str(block.get("tool_use_id")))
                errors.append(bool(block.get("is_error")))
                content = block.get("content")
                texts.append(content if isinstance(content, str) else json.dumps(content)[:400])
    # Background-task notifications are native history, not caller input.
    caller_texts = [text for text in user_texts if not text.startswith("<system-reminder>")]
    return ModelRequest(
        index=index,
        model=str(body.get("model") or ""),
        turn_text=caller_texts[-1] if caller_texts else "",
        tool_result_ids=tool_ids,
        tool_result_is_error=errors,
        tool_result_texts=texts,
        prior_assistant_text=prior_assistant,
        prior_user_texts=caller_texts,
    )


def _message_start(model: str, index: int, *, input_tokens: int = 11) -> tuple[str, dict[str, Any]]:
    return (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": f"msg_synthetic_{index}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        },
    )


def _message_stop(stop_reason: str, *, output_tokens: int = 7) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def scripted_events(turn: Turn, model: str, index: int) -> list[tuple[str, dict[str, Any], float]]:
    """Build ``(event, payload, delay)`` triples for one scripted turn."""

    events: list[tuple[str, dict[str, Any], float]] = [(*_message_start(model, index), 0.0)]
    if turn.kind == TOOL_KIND:
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": f"toolu_synthetic_{index}",
                        "name": turn.tool_name,
                        "input": {},
                    },
                },
                0.0,
            )
        )
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(turn.tool_input),
                    },
                },
                0.0,
            )
        )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}, 0.0))
        events.extend((name, payload, 0.0) for name, payload in _message_stop("tool_use"))
        return events
    events.append(
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            0.0,
        )
    )
    for chunk in turn.chunks:
        events.append(
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}},
                turn.chunk_delay_seconds,
            )
        )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}, 0.0))
    events.extend((name, payload, 0.0) for name, payload in _message_stop("end_turn"))
    return events


@dataclass
class SyntheticModelPeer:
    """Loopback Messages-API peer driven by an ordered turn script."""

    script: list[Turn]
    default_turn: Turn = field(default_factory=lambda: Turn.text("synthetic answer", delay=0.0))
    requests: list[ModelRequest] = field(default_factory=list)
    stopped: threading.Event = field(default_factory=threading.Event)
    url: str = ""

    def turn_for(self, index: int) -> Turn:
        if index < len(self.script):
            return self.script[index]
        return self.default_turn

    def record(self, body: dict[str, Any]) -> int:
        index = len(self.requests)
        self.requests.append(summarize_request(index, body))
        return index


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        # A stopped CLI drops its sockets without a graceful close; that is the
        # condition under test, not a peer failure worth a traceback.
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, *_args: object) -> None:
        pass

    def _write_chunk(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        path = self.path.split("?", 1)[0]
        if path.endswith("/count_tokens"):
            self._json({"input_tokens": 64})
            return
        if not path.endswith("/messages"):
            self.send_error(404)
            return
        peer: SyntheticModelPeer = self.server.peer  # type: ignore[attr-defined]
        index = peer.record(body)
        turn = peer.turn_for(index)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        if turn.kind == STALL_KIND:
            # Hold the turn open until the test stops the peer; the CLI stays in
            # the requesting state, which is exactly the "no result" condition.
            peer.stopped.wait(timeout=120)
            self.close_connection = True
            return
        try:
            for event, payload, delay in scripted_events(turn, body["model"], index):
                if peer.stopped.is_set():
                    self.close_connection = True
                    return
                if delay:
                    time.sleep(delay)
                self._write_chunk(
                    f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()
                )
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()


@contextmanager
def synthetic_model_peer(script: list[Turn], *, default_turn: Turn | None = None):
    peer = SyntheticModelPeer(script=script)
    if default_turn is not None:
        peer.default_turn = default_turn
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.peer = peer  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield peer
    finally:
        peer.stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = [
    "ModelRequest",
    "STALL_KIND",
    "SyntheticModelPeer",
    "TEXT_KIND",
    "TOOL_KIND",
    "Turn",
    "scripted_events",
    "synthetic_model_peer",
]

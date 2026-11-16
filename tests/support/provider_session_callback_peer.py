"""Loopback provider-session callback peer backed by a durable entry directory.

The peer speaks the platform's private callback wire contract
(:class:`app.runtime.sandbox.contracts.ProviderSessionCallbackRequest` /
:class:`app.context.transport.provider_session_responses.ProviderSessionCallbackResponse`)
so the *production* :class:`ClaudeSessionStoreAdapter` can be handed to the real
Claude CLI as its ``session_store``.  Entries are stored as JSON lines under a
caller-owned directory, so a new process can resume from them without any
in-memory state from the run that produced them.

Only the wire fields the adapter validates are implemented; platform scope
(tenant/run/lease authorization) belongs to the real callback route and is
deliberately not re-implemented here.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class TransientCallbackFailure(RuntimeError):
    """Injected store failure so a resume attempt can fail exactly once."""


@dataclass
class DurableEntryStore:
    """Append-only per-session transcript directory shared across processes."""

    root: Path
    entries_path: Path = field(init=False)
    requests: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries_path = self.root / "entries.jsonl"

    # One JSON line per stored entry keeps the directory readable by another
    # process and by a human debugging a failed recovery.
    def _read(self) -> list[dict[str, Any]]:
        if not self.entries_path.exists():
            return []
        rows = []
        for line in self.entries_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            rows.append(record)
        return rows

    def total_entries(self) -> int:
        return len(self._read())

    def entries(
        self, session_id: str, subpath: str | None
    ) -> list[dict[str, Any]]:
        rows = [
            record["entry"]
            for record in self._read()
            if record["session_id"] == session_id and record["subpath"] == subpath
        ]
        return [json.loads(json.dumps(entry)) for entry in rows]

    def subpaths(self, session_id: str) -> list[str]:
        return sorted(
            {
                record["subpath"]
                for record in self._read()
                if record["session_id"] == session_id and record["subpath"]
            }
        )

    def append(
        self,
        *,
        session_id: str,
        subpath: str | None,
        entries: list[dict[str, Any]],
        expected_sequence: int,
    ) -> int:
        with self._lock:
            stored = self.total_entries()
            if stored + 1 != expected_sequence:
                raise TransientCallbackFailure(
                    f"provider_session_append_sequence_invalid:{expected_sequence}"
                )
            with self.entries_path.open("a", encoding="utf-8") as stream:
                for entry in entries:
                    stream.write(
                        json.dumps(
                            {"session_id": session_id, "subpath": subpath, "entry": entry},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
            return self.total_entries() + 1

    def fail_next(self, action: str) -> None:
        """Arm one injected failure for ``action`` on the next matching request."""

        with self._lock:
            self.failures[action] = self.failures.get(action, 0) + 1

    def _take_failure(self, action: str) -> bool:
        with self._lock:
            remaining = self.failures.get(action, 0)
            if not remaining:
                return False
            self.failures[action] = remaining - 1
            return True

    # -- wire operations -------------------------------------------------
    def load(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._take_failure("load"):
            raise TransientCallbackFailure("provider_session_callback_unavailable")
        entries = self.entries(request["provider_session_id"], request.get("subpath"))
        return {
            "action": "load",
            "entries": entries,
            "next_sequence": self.total_entries() + 1,
        }

    def append_request(self, request: dict[str, Any]) -> dict[str, Any]:
        entries = request["entries"]
        last_sequence = request["expected_sequence"] + len(entries) - 1
        next_sequence = self.append(
            session_id=request["provider_session_id"],
            subpath=request.get("subpath"),
            entries=entries,
            expected_sequence=request["expected_sequence"],
        )
        return {
            "action": "append",
            "entries": [],
            "accepted": True,
            "entry_count": len(entries),
            "last_sequence": last_sequence,
            "next_sequence": next_sequence,
        }

    def list_subkeys_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._take_failure("list_subkeys"):
            raise TransientCallbackFailure("provider_session_callback_unavailable")
        session_id = request["provider_session_id"]
        return {
            "action": "list_subkeys",
            "subpaths": self.subpaths(session_id),
            "next_sequence": self.total_entries() + 1,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        action = request["action"]
        if action == "load":
            return self.load(request)
        if action == "append":
            return self.append_request(request)
        if action == "list_subkeys":
            return self.list_subkeys_request(request)
        raise AssertionError(f"unexpected provider session action {action!r}")

    # -- evidence --------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        sessions = sorted({record["session_id"] for record in self._read()})
        return {
            "sessions": sessions,
            "entries": self.total_entries(),
            "actions": [request["action"] for request in self.requests],
            "subpaths": {session: self.subpaths(session) for session in sessions},
            "entry_types": {
                session: [record["entry"].get("type") for record in self._read()
                          if record["session_id"] == session]
                for session in sessions
            },
        }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, *_args: object) -> None:
        pass

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        try:
            body = self.server.entry_store.handle(request)
        except TransientCallbackFailure:
            # 503 is the platform's "callback failed" status for a store outage.
            self._respond(503, {"detail": "provider_session_callback_failed"})
            return
        self._respond(200, body)


@contextmanager
def provider_session_callback_peer(store: DurableEntryStore):
    """Serve ``store`` at the platform's provider-session callback path."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.entry_store = store  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store.url = (  # type: ignore[attr-defined]
        f"http://127.0.0.1:{server.server_port}"
        "/api/ai/runtime/callbacks/provider-session"
    )
    try:
        yield store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = [
    "DurableEntryStore",
    "TransientCallbackFailure",
    "provider_session_callback_peer",
]

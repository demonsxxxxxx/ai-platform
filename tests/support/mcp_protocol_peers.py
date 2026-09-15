"""Local protocol peers for MCP/Claude CLI checks; never contact a real model."""

import json
import queue
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

RAW_TOOL = "ProjectInfoMCPServer.get_project_info_sequences"
SDK_TOOL = "mcp__gateway__ProjectInfoMCPServer_get_project_info_sequences"
SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


@contextmanager
def local_mcp_peers():
    state = SimpleNamespace(
        tools=[{"name": RAW_TOOL, "description": "Synthetic sequence lookup", "inputSchema": SCHEMA},
               {"name": "unselected_sibling", "inputSchema": {"type": "object"}}],
        models=[], calls=[], requests=[], replies=queue.Queue(), stopped=threading.Event(),
        sse_closed=threading.Event(), is_error=False, sdk_tool=SDK_TOOL,
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *_args):
            pass

        def respond(self, value, status=200, content_type="application/json"):
            body = json.dumps(value).encode() if content_type == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/sse":
                self.respond({}, 405)
                return
            state.requests.append(("GET", dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(b"event: endpoint\ndata: /messages?session_id=synthetic\n\n")
                self.wfile.flush()
                while not state.stopped.is_set():
                    try:
                        reply = state.replies.get(timeout=0.1)
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                    else:
                        self.wfile.write(b"event: message\ndata: " + json.dumps(reply).encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                state.sse_closed.set()
                self.close_connection = True

        def do_DELETE(self):
            self.respond({})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if self.path.startswith("/v1/messages/count_tokens"):
                self.respond({"input_tokens": 100})
                return
            if self.path.startswith("/v1/messages"):
                state.models.append(request)
                used_tool = any(
                    block.get("type") == "tool_result"
                    for message in request.get("messages", [])
                    for block in message.get("content", []) if isinstance(block, dict)
                )
                content = {"type": "text", "text": "Synthetic lookup complete."} if used_tool else {
                    "type": "tool_use", "id": "toolu_synthetic", "name": state.sdk_tool, "input": {"query": "synthetic"},
                }
                stop_reason = "end_turn" if used_tool else "tool_use"
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_synthetic", "type": "message", "role": "assistant",
                        "model": request["model"], "content": [], "stop_reason": None,
                        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0},
                    }}),
                    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block":
                        {"type": "text", "text": ""} if used_tool else {**content, "input": {}}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta":
                        {"type": "text_delta", "text": content["text"]} if used_tool else
                        {"type": "input_json_delta", "partial_json": json.dumps(content["input"])}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                        "usage": {"output_tokens": 20}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                self.respond(b"".join(
                    f"event: {name}\ndata: {json.dumps(value)}\n\n".encode() for name, value in events
                ), content_type="text/event-stream")
                return
            state.requests.append((request["method"], dict(self.headers)))
            if request["method"] == "initialize":
                result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "synthetic", "version": "1"}}
            elif request["method"] == "notifications/initialized":
                self.respond({}, 202)
                return
            elif request["method"] == "tools/list":
                result = {"tools": state.tools}
            elif request["method"] == "tools/call":
                state.calls.append(request["params"])
                result = {"content": [{"type": "text", "text": "synthetic result"}],
                          "structuredContent": {"sequence": 7}, "isError": state.is_error}
            else:
                self.respond({}, 400)
                return
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            if self.path.startswith("/messages?"):
                state.replies.put(response)
                self.respond({}, 202)
            else:
                self.respond(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        state.stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()

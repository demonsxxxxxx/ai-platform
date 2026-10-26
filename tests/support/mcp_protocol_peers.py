"""Local protocol peers for MCP/Claude CLI checks; never contact a real model."""

import base64
import json
import queue
from pathlib import Path
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

RAW_TOOL = "ProjectInfoMCPServer.get_project_info_sequences"
SDK_TOOL = "mcp__gateway__ProjectInfoMCPServer_get_project_info_sequences"
SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


@contextmanager
def local_mcp_peers(*, protocol="legacy"):
    state = SimpleNamespace(
        tools=[{"name": RAW_TOOL, "description": "Synthetic sequence lookup", "inputSchema": SCHEMA},
               {"name": "unselected_sibling", "inputSchema": {"type": "object"}}],
        models=[], calls=[], requests=[], replies=queue.Queue(), reply_queues=[], stopped=threading.Event(),
        sse_closed=threading.Event(), is_error=False, sdk_tool=SDK_TOOL,
        tool_input={"query": "synthetic"}, protocol=protocol, deleted=0, search_tools=False,
        call_error=None, input_required=False, original_file=False, original_file_bytes=b"", tool_results=[],
        original_file_workspace=None, parse_output_path=None, python_executable="python",
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
            reply_queue = queue.Queue()
            state.reply_queues.append(reply_queue)
            session_id = f"synthetic-{len(state.reply_queues)}"
            try:
                self.wfile.write(
                    f"event: endpoint\ndata: /messages?session_id={session_id}\n\n".encode()
                )
                self.wfile.flush()
                while not state.stopped.is_set():
                    try:
                        reply = reply_queue.get(timeout=0.1)
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
            state.deleted += 1
            self.respond({})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))

            def reply_queue_for_path():
                if "session_id=" in self.path:
                    raw_id = self.path.split("session_id=", 1)[1].split("&", 1)[0]
                    try:
                        return state.reply_queues[int(raw_id.rsplit("-", 1)[1]) - 1]
                    except (IndexError, ValueError):
                        pass
                return state.reply_queues[0] if state.reply_queues else state.replies

            if self.path.startswith("/v1/messages/count_tokens"):
                self.respond({"input_tokens": 100})
                return
            if self.path.startswith("/v1/messages"):
                state.models.append(request)
                state.tool_results.extend(
                    block for message in request.get("messages", [])
                    for block in message.get("content", [])
                    if isinstance(block, dict) and block.get("type") == "tool_result"
                )
                used_tool = any(
                    block.get("type") == "tool_result"
                    for message in request.get("messages", [])
                    for block in message.get("content", []) if isinstance(block, dict)
                )
                if state.original_file and len(state.models) == 1:
                    content = {"type": "tool_use", "id": "toolu_original", "name": state.sdk_tool, "input": state.tool_input}
                elif state.original_file and len(state.models) == 2:
                    delivered_path = "inputs/.mcp-original/file.docx"
                    for message in request.get("messages", []):
                        for block in message.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                for item in block.get("content", []):
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        try:
                                            delivered_path = json.loads(item.get("text", "")).get("workspace_path", delivered_path)
                                        except (TypeError, ValueError):
                                            pass
                    source_path = str(Path(state.original_file_workspace or ".") / delivered_path).replace("\\", "/")
                    output_path = str(Path(state.parse_output_path or "parsed.txt")).replace("\\", "/")
                    content = {"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": "'%s' -c \"from docx import Document; d=Document(r'%s'); open(r'%s','w').write(d.paragraphs[0].text + '\\n' + d.tables[0].cell(0,0).text)\"" % (state.python_executable, source_path, output_path)}}
                elif state.original_file and len(state.models) >= 3:
                    parsed = []
                    for message in request.get("messages", []):
                        for block in message.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                parsed.append(str(block.get("content", "")))
                    content = {"type": "text", "text": " ".join(parsed)}
                else:
                    content = {"type": "text", "text": "Synthetic lookup complete."} if used_tool else {
                        "type": "tool_use", "id": "toolu_synthetic", "name": state.sdk_tool, "input": state.tool_input,
                    }
                if state.search_tools and len(state.models) == 1:
                    content = {"type": "tool_use", "id": "toolu_search", "name": "ToolSearch",
                               "input": {"query": "select:" + state.sdk_tool, "max_results": 1}}
                    used_tool = False
                elif state.search_tools and len(state.models) == 2:
                    content = {"type": "tool_use", "id": "toolu_selected", "name": state.sdk_tool,
                               "input": state.tool_input}
                    used_tool = False
                stop_reason = "end_turn" if used_tool else "tool_use"
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": f"msg_synthetic_{len(state.models)}", "type": "message", "role": "assistant",
                        "model": request["model"], "content": [], "stop_reason": None,
                        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0},
                    }}),
                    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block":
                        {"type": "text", "text": ""} if content.get("type") == "text" else {**content, "input": {}}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta":
                        {"type": "text_delta", "text": content["text"]} if content.get("type") == "text" else
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
            if request["method"] == "server/discover" and state.protocol == "modern":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
            elif request["method"] == "server/discover":
                response = {"jsonrpc": "2.0", "id": request["id"],
                            "error": {"code": -32601, "message": "Method not found"}}
                if self.path.startswith("/messages?"):
                    reply_queue_for_path().put(response)
                    self.respond({}, 202)
                else:
                    self.respond(response)
                return
            elif request["method"] == "initialize":
                result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "synthetic", "version": "1"}}
            elif request["method"] == "notifications/initialized":
                self.respond({}, 202)
                return
            elif request["method"] == "tools/list":
                result = {"tools": state.tools}
            elif request["method"] == "tools/call":
                state.calls.append(request["params"])
                if state.original_file:
                    raw = state.original_file_bytes
                    result = {"content": [
                        {"type": "text", "text": json.dumps({
                            "format": "profile-drive.original-file.v1", "source": "profile",
                            "path": "share/file.docx", "name": "file.docx", "sizeBytes": len(raw),
                            "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        })},
                        {"type": "resource", "resource": {
                            "uri": "profile-drive://original-file",
                            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "blob": base64.b64encode(raw).decode("ascii"),
                        }},
                    ], "isError": False}
                elif state.call_error is not None:
                    self.respond({"jsonrpc": "2.0", "id": request["id"], "error": state.call_error})
                    return
                else:
                    result = {"content": [{"type": "text", "text": "synthetic result"}],
                              "structuredContent": {"sequence": 7}, "isError": state.is_error}
                    if state.input_required:
                        result = {"resultType": "input_required", "requestState": "synthetic-state"}
            else:
                self.respond({}, 400)
                return
            if state.protocol == "modern" and result.get("resultType") != "input_required":
                result = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **result}
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            if self.path.startswith("/messages?"):
                reply_queue_for_path().put(response)
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

"""Sandbox-local regular-file relay used while the runsc network is disabled."""

from __future__ import annotations


# This source is passed as a fixed argv item to ``docker exec``.  Request data is
# read from HTTP and written to the scoped workspace; it is never interpolated
# into a command.  The host broker chooses every remote destination.
RELAY_SOURCE = r'''
import base64, http.server, json, os, pathlib, secrets, sys, time

ROOT = pathlib.Path(sys.argv[1])
REQ = ROOT / "requests"
RESP = ROOT / "responses"
REQ.mkdir(parents=True, exist_ok=True)
RESP.mkdir(parents=True, exist_ok=True)
os.chmod(ROOT, 0o700)
(ROOT / "relay.pid").write_text(str(os.getpid()), encoding="ascii")

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_):
        pass
    def _run(self):
        length = self.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 1048576:
            self.send_error(413); return
        body = self.rfile.read(int(length))
        request_id = secrets.token_hex(16)
        value = {
            "version": 1,
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": base64.b64encode(body).decode("ascii"),
        }
        temp = REQ / (request_id + ".tmp")
        final = REQ / (request_id + ".json")
        temp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, final)
        response_path = RESP / (request_id + ".json")
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline and not response_path.exists():
            time.sleep(.02)
        if not response_path.exists():
            self.send_error(504); return
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
            data = base64.b64decode(response["body"], validate=True)
            self.send_response(int(response["status"]))
            for key, item in response.get("headers", {}).items():
                if key.lower() in ("content-type", "cache-control"):
                    self.send_header(key, str(item))
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            response_path.unlink(missing_ok=True)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _run

server = http.server.ThreadingHTTPServer(("127.0.0.1", 18888), Handler)
server.daemon_threads = True
server.serve_forever()
'''


PROXY_SOURCE = r'''
import base64, http.client, json, sys
value = json.load(sys.stdin)
connection = http.client.HTTPConnection("127.0.0.1", int(value["port"]), timeout=float(value["timeout"]))
body = base64.b64decode(value["body"], validate=True)
headers = {k: v for k, v in value["headers"].items() if k.lower() not in {
    "connection", "content-length", "host", "open-sandbox-api-key", "open-sandbox-route-token",
    "proxy-authorization", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"
}}
connection.request(value["method"], value["path"], body=body, headers=headers)
response = connection.getresponse()
data = response.read(int(value["max_response"]) + 1)
if len(data) > int(value["max_response"]):
    raise RuntimeError("response too large")
allowed = {"content-type", "cache-control", "x-request-id"}
print(json.dumps({
    "status": response.status,
    "headers": {k.lower(): v for k, v in response.getheaders() if k.lower() in allowed},
    "body": base64.b64encode(data).decode("ascii"),
}, separators=(",", ":")))
'''


STOP_RELAY_SOURCE = r'''
import os, pathlib, signal, sys
path = pathlib.Path(sys.argv[1]) / "relay.pid"
try:
    value = path.read_text(encoding="ascii").strip()
    if not value.isdigit(): raise ValueError()
    os.kill(int(value), signal.SIGTERM)
except (FileNotFoundError, ProcessLookupError):
    pass
'''

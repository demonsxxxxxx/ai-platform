"""Sandbox-local regular-file relay used while the runsc network is disabled."""

from __future__ import annotations


# This source is passed as a fixed argv item to ``docker exec``.  Request data is
# read from HTTP and written to the scoped workspace; it is never interpolated
# into a command.  The host broker chooses every remote destination.
RELAY_SOURCE = r'''
import base64, http.server, json, os, secrets, stat, sys, threading, time

ROOT, BROKER_UID, BROKER_GID = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ROOT_FD = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
REQ_FD = os.open("requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ROOT_FD)
RESP_FD = os.open("responses", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ROOT_FD)

def require_dir(fd, uid, gid, mode):
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != uid or value.st_gid != gid or stat.S_IMODE(value.st_mode) != mode:
        raise RuntimeError("mailbox ownership protocol mismatch")

require_dir(ROOT_FD, 0, 0, 0o711)
require_dir(REQ_FD, 1000, BROKER_GID, 0o2770)
require_dir(RESP_FD, BROKER_UID, BROKER_GID, 0o755)
pid_fd = os.open("relay.pid", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640, dir_fd=REQ_FD)
try:
    os.fchmod(pid_fd, 0o640)
    os.write(pid_fd, str(os.getpid()).encode("ascii"))
    os.fsync(pid_fd)
finally:
    os.close(pid_fd)

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_):
        pass
    def _run(self):
        try:
            length = self.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 1048576:
                self.send_error(413); return
            body = self.rfile.read(int(length))
            request_id = secrets.token_hex(16)
            name = request_id + ".json"
            temporary = "." + secrets.token_hex(16) + ".tmp"
            value = {
                "version": 1,
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": base64.b64encode(body).decode("ascii"),
            }
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=REQ_FD)
            try:
                data = json.dumps(value, separators=(",", ":")).encode("utf-8")
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fchmod(descriptor, 0o640)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, name, src_dir_fd=REQ_FD, dst_dir_fd=REQ_FD)
            deadline = time.monotonic() + 65
            response_fd = None
            while time.monotonic() < deadline:
                try:
                    response_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=RESP_FD)
                    break
                except FileNotFoundError:
                    time.sleep(.02)
            if response_fd is None:
                self.send_error(504); return
            try:
                evidence = os.fstat(response_fd)
                if not stat.S_ISREG(evidence.st_mode) or evidence.st_uid != BROKER_UID or evidence.st_gid != BROKER_GID or stat.S_IMODE(evidence.st_mode) != 0o444 or evidence.st_size > 8388608:
                    raise RuntimeError("invalid broker response")
                raw = b""
                while len(raw) <= 8388608:
                    chunk = os.read(response_fd, 65536)
                    if not chunk: break
                    raw += chunk
                after = os.fstat(response_fd)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (evidence.st_dev, evidence.st_ino, evidence.st_size, evidence.st_mtime_ns, evidence.st_ctime_ns):
                    raise RuntimeError("broker response changed")
            finally:
                os.close(response_fd)
            response = json.loads(raw.decode("utf-8"))
            data = base64.b64decode(response["body"], validate=True)
            self.send_response(int(response["status"]))
            for key, item in response.get("headers", {}).items():
                if key.lower() in ("content-type", "cache-control"):
                    self.send_header(key, str(item))
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RuntimeError):
            try:
                self.send_error(502)
            except OSError:
                pass
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _run

class BoundedServer(http.server.ThreadingHTTPServer):
    slots = threading.BoundedSemaphore(16)
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close(); return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

server = BoundedServer(("127.0.0.1", 18888), Handler)
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
path = pathlib.Path(sys.argv[1]) / "requests" / "relay.pid"
try:
    value = path.read_text(encoding="ascii").strip()
    if not value.isdigit(): raise ValueError()
    os.kill(int(value), signal.SIGTERM)
except (FileNotFoundError, ProcessLookupError):
    pass
'''

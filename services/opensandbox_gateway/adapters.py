"""Production and in-memory adapters for the OpenSandbox gateway core."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict
from typing import Any, Mapping

from .gateway import (
    CALLBACK_PATHS,
    GatewayError,
    LeaseRecord,
    Request,
    Response,
    RuntimeEvidence,
)
from .relay import PROXY_SOURCE, RELAY_SOURCE, STOP_RELAY_SOURCE


class InMemoryStateStore:
    """Thread-safe ephemeral store for tests and local contract probes."""

    def __init__(self) -> None:
        self.records: dict[str, LeaseRecord] = {}
        self.denials: list[tuple[str, str]] = []
        self._lock = threading.RLock()

    def get(self, sandbox_id: str) -> LeaseRecord | None:
        with self._lock:
            return self.records.get(sandbox_id)

    def find_scope(self, scope: Mapping[str, str]) -> LeaseRecord | None:
        with self._lock:
            return next((r for r in self.records.values() if r.scope == dict(scope) and r.state != "deleted"), None)

    def save(self, record: LeaseRecord) -> None:
        with self._lock:
            self.records[record.sandbox_id] = record

    def list(self, filters: Mapping[str, str]) -> list[LeaseRecord]:
        with self._lock:
            values = []
            for record in self.records.values():
                if record.state == "deleted":
                    continue
                if all((record.workspace_host_path if key == "workspace_host_path" else record.metadata.get(key)) == value for key, value in filters.items()):
                    values.append(record)
            return values

    def record_deny(self, subject: str, code: str) -> None:
        with self._lock:
            self.denials.append((subject, code))

    def deny_count(self, subject: str) -> int:
        with self._lock:
            return sum(item == subject for item, _ in self.denials)

    def ready(self) -> bool:
        return True


class SQLiteStateStore:
    """SQLite lease store with sealed-record persistence and denial counters."""

    def __init__(self, path: str) -> None:
        self.path = path
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS leases (
                    sandbox_id TEXT PRIMARY KEY,
                    scope_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    workspace_host_path TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_scope
                    ON leases(scope_json) WHERE state != 'deleted';
                CREATE TABLE IF NOT EXISTS denials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    code TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2.0)
        db.execute("PRAGMA busy_timeout=2000")
        return db

    def get(self, sandbox_id: str) -> LeaseRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT record_json FROM leases WHERE sandbox_id = ?", (sandbox_id,)).fetchone()
        return _record_from_json(row[0]) if row else None

    def find_scope(self, scope: Mapping[str, str]) -> LeaseRecord | None:
        scope_json = _json_text(dict(scope))
        with self._lock, self._connect() as db:
            row = db.execute("SELECT record_json FROM leases WHERE scope_json = ? AND state != 'deleted'", (scope_json,)).fetchone()
        return _record_from_json(row[0]) if row else None

    def save(self, record: LeaseRecord) -> None:
        values = (
            record.sandbox_id,
            _json_text(record.scope),
            _json_text(record.metadata),
            _json_text(asdict(record)),
            record.state,
            record.workspace_host_path,
        )
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(sandbox_id) DO UPDATE SET scope_json=excluded.scope_json, metadata_json=excluded.metadata_json, record_json=excluded.record_json, state=excluded.state, workspace_host_path=excluded.workspace_host_path",
                values,
            )

    def list(self, filters: Mapping[str, str]) -> list[LeaseRecord]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT record_json FROM leases WHERE state != 'deleted' ORDER BY sandbox_id LIMIT 101").fetchall()
        records = [_record_from_json(row[0]) for row in rows]
        return [r for r in records if all((r.workspace_host_path if key == "workspace_host_path" else r.metadata.get(key)) == value for key, value in filters.items())]

    def record_deny(self, subject: str, code: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO denials(occurred_at, subject, code) VALUES (?, ?, ?)", (int(time.time()), subject, code))

    def deny_count(self, subject: str) -> int:
        with self._lock, self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM denials WHERE subject = ?", (subject,)).fetchone()[0])

    def ready(self) -> bool:
        try:
            with self._connect() as db:
                return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.Error:
            return False


class InMemoryLifecycleTransport:
    """Deterministic OpenSandbox transport used by focused contract tests."""

    def __init__(self) -> None:
        self.sandboxes: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, bytes]] = []
        self.next_id = 1
        self.redirect_path: str | None = None

    def request(self, method: str, path: str, body: bytes = b"") -> Response:
        self.requests.append((method, path, body))
        if path == self.redirect_path:
            raise GatewayError(502, "upstream_redirect_rejected")
        if path == "/health" and method == "GET":
            return Response.json(200, {"status": "ok"})
        if path == "/v1/sandboxes" and method == "POST":
            value = json.loads(body)
            sandbox_id = f"sandbox-{self.next_id}"
            self.next_id += 1
            info = {"id": sandbox_id, "status": "running", "metadata": value["metadata"]}
            self.sandboxes[sandbox_id] = info
            return Response.json(201, {"id": sandbox_id})
        if path.startswith("/v1/sandboxes/"):
            sandbox_id = path.rsplit("/", 1)[1]
            if method == "GET":
                return Response.json(200, self.sandboxes[sandbox_id]) if sandbox_id in self.sandboxes else Response.json(404, {"error": "not found"})
            if method == "DELETE":
                self.sandboxes.pop(sandbox_id, None)
                return Response(204, {"content-length": "0"}, b"")
        return Response.json(404, {"error": "not found"})


class LoopbackLifecycleTransport:
    """Bounded HTTP client fixed to the loopback OpenSandbox server."""

    def __init__(self, timeout_seconds: float, max_response_bytes: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def request(self, method: str, path: str, body: bytes = b"") -> Response:
        if not path.startswith("/") or "//" in path or ".." in path.split("/"):
            raise GatewayError(500, "invalid_upstream_path")
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=self.timeout_seconds)
        try:
            headers = {"accept": "application/json"}
            if body:
                headers.update({"content-type": "application/json", "content-length": str(len(body))})
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise GatewayError(502, "upstream_redirect_rejected")
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise GatewayError(502, "upstream_response_too_large")
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, data)
        except (OSError, http.client.HTTPException):
            raise GatewayError(502, "upstream_unavailable") from None
        finally:
            connection.close()


class InMemoryRuntimeAdapter:
    """Deterministic runtime evidence and proxy adapter for tests."""

    def __init__(self) -> None:
        self.evidence: dict[str, RuntimeEvidence] = {}
        self.proxy_responses: dict[tuple[int, str, str], Response] = {}
        self.proxied: list[tuple[str, int, Request]] = []
        self.relays: set[str] = set()

    def provision(self, record: LeaseRecord) -> None:
        self.evidence[record.sandbox_id] = RuntimeEvidence(
            sandbox_id=record.sandbox_id,
            runtime="runsc",
            network_mode="none",
            no_new_privileges=True,
            image=record.image,
            image_digest=record.image_digest,
            mounts=tuple((item["host"], item["mountPath"], item["readOnly"]) for item in record.mounts),
            labels=dict(record.metadata),
        )

    def verify(self, record: LeaseRecord) -> RuntimeEvidence:
        if record.sandbox_id not in self.evidence:
            self.provision(record)
        return self.evidence[record.sandbox_id]

    def start_relay(self, record: LeaseRecord) -> None:
        self.relays.add(record.sandbox_id)

    def stop_relay(self, record: LeaseRecord) -> None:
        self.relays.discard(record.sandbox_id)

    def proxy(self, record: LeaseRecord, port: int, request: Request) -> Response:
        self.proxied.append((record.sandbox_id, port, request))
        return self.proxy_responses.get((port, request.method.upper(), request.target.split("?", 1)[0]), Response.json(200, {"ok": True}))

    def cleanup_mailbox(self, record: LeaseRecord) -> None:
        return None


class DockerRuntimeAdapter:
    """Verify runsc/Docker state and execute only fixed in-container brokers."""

    def __init__(self, signing_key: bytes, timeout_seconds: float, dispatch_timeout_seconds: float, max_response_bytes: int, workspace_root: str) -> None:
        self.signing_key = signing_key
        self.timeout_seconds = timeout_seconds
        self.dispatch_timeout_seconds = dispatch_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.workspace_root = pathlib.Path(workspace_root).resolve()

    def verify(self, record: LeaseRecord) -> RuntimeEvidence:
        container_id = self._container_id(record.sandbox_id)
        info = self._json_command(["docker", "inspect", container_id])[0]
        host = info.get("HostConfig") or {}
        config = info.get("Config") or {}
        state = info.get("State") or {}
        labels = config.get("Labels") or {}
        security = [str(value).lower() for value in host.get("SecurityOpt") or []]
        env = {}
        for item in config.get("Env") or []:
            key, separator, value = str(item).partition("=")
            if separator:
                env[key] = value
        token = env.get("AI_PLATFORM_EXECUTOR_AUTH_TOKEN", "")
        token_hash = hmac.new(self.signing_key, token.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(token_hash, record.executor_token_hash):
            raise GatewayError(409, "executor_credential_drift")
        expected_local = {
            "AI_PLATFORM_CALLBACK_BASE_URL": "http://127.0.0.1:18888/callback",
            "SANDBOX_CALLBACK_BASE_URL": "http://127.0.0.1:18888/callback",
            "OPENAI_BASE_URL": "http://127.0.0.1:18888/model/openai",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18888/model/anthropic",
        }
        if any(env.get(key) != value for key, value in expected_local.items()) or any(key.upper().endswith("_PROXY") for key in env):
            raise GatewayError(409, "broker_environment_drift")
        mounts = tuple(
            sorted(
                (str(item.get("Source") or ""), str(item.get("Destination") or ""), not bool(item.get("RW")))
                for item in info.get("Mounts") or []
                if item.get("Type") == "bind"
            )
        )
        image_info = self._json_command(["docker", "image", "inspect", str(info.get("Image") or "")])[0]
        digests = {str(value).rsplit("@", 1)[-1] for value in image_info.get("RepoDigests") or []}
        image_digest = record.image_digest if record.image_digest in digests else ""
        return RuntimeEvidence(
            sandbox_id=record.sandbox_id,
            runtime=str(host.get("Runtime") or ""),
            network_mode=str(host.get("NetworkMode") or ""),
            no_new_privileges=any(value in ("no-new-privileges", "no-new-privileges:true") for value in security),
            image=str(config.get("Image") or ""),
            image_digest=image_digest,
            mounts=mounts,
            labels=labels,
            running=bool(state.get("Running")),
        )

    def start_relay(self, record: LeaseRecord) -> None:
        container_id = self._container_id(record.sandbox_id)
        self._command(["docker", "exec", "-d", container_id, "python3", "-c", RELAY_SOURCE, "/workspace/.opensandbox-gateway"])

    def stop_relay(self, record: LeaseRecord) -> None:
        try:
            container_id = self._container_id(record.sandbox_id)
            self._command(["docker", "exec", container_id, "python3", "-c", STOP_RELAY_SOURCE, "/workspace/.opensandbox-gateway"])
        except GatewayError:
            pass

    def proxy(self, record: LeaseRecord, port: int, request: Request) -> Response:
        container_id = self._container_id(record.sandbox_id)
        value = {
            "port": port,
            "timeout": self.timeout_seconds,
            "max_response": self.max_response_bytes,
            "method": request.method.upper(),
            "path": request.target,
            "headers": dict(request.headers),
            "body": base64.b64encode(request.body).decode("ascii"),
        }
        output = self._command(
            ["docker", "exec", "-i", container_id, "python3", "-c", PROXY_SOURCE],
            input_bytes=_json_text(value).encode(),
            timeout_seconds=self.dispatch_timeout_seconds if port == 18000 and request.target.split("?", 1)[0] == "/v1/tasks/execute" else None,
        )
        try:
            result = json.loads(output)
            body = base64.b64decode(result["body"], validate=True)
            status = int(result["status"])
            headers = result.get("headers") or {}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise GatewayError(502, "container_proxy_invalid_response") from None
        return Response(status, headers, body)

    def cleanup_mailbox(self, record: LeaseRecord) -> None:
        workspace = pathlib.Path(record.workspace_host_path).resolve()
        mailbox = (workspace / ".opensandbox-gateway").resolve()
        if self.workspace_root not in workspace.parents or mailbox.parent != workspace:
            raise GatewayError(500, "cleanup_scope_invalid")
        if mailbox.exists():
            shutil.rmtree(mailbox)

    def _container_id(self, sandbox_id: str) -> str:
        output = self._command(["docker", "ps", "-aq", "--filter", f"label=opensandbox.io/id={sandbox_id}"])
        values = [line.strip() for line in output.splitlines() if line.strip()]
        if len(values) != 1 or not all(c in "0123456789abcdef" for c in values[0].lower()):
            raise GatewayError(409, "container_identity_ambiguous")
        return values[0]

    def _json_command(self, argv: list[str]) -> Any:
        try:
            return json.loads(self._command(argv))
        except json.JSONDecodeError:
            raise GatewayError(502, "docker_evidence_invalid") from None

    def _command(self, argv: list[str], input_bytes: bytes | None = None, timeout_seconds: float | None = None) -> str:
        try:
            result = subprocess.run(
                argv,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GatewayError(502, "docker_runtime_unavailable") from None
        if result.returncode != 0 or len(result.stdout) > self.max_response_bytes:
            raise GatewayError(502, "docker_runtime_failed")
        return result.stdout.decode("utf-8")


class BrokerPolicy:
    """Exact HTTPS broker targets and their pinned IP addresses."""

    def __init__(self, value: Mapping[str, Any]) -> None:
        if set(value) != {"version", "targets"} or value.get("version") != 1 or not isinstance(value.get("targets"), dict):
            raise ValueError("invalid broker policy")
        self.targets: dict[str, tuple[str, tuple[str, ...]]] = {}
        for kind in ("callback", "openai", "anthropic"):
            item = value["targets"].get(kind)
            if not isinstance(item, dict) or set(item) != {"base_url", "expected_ips"}:
                raise ValueError("broker target is incomplete")
            parsed = urllib.parse.urlsplit(item["base_url"])
            ips = tuple(str(ipaddress.ip_address(value)) for value in item["expected_ips"])
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or not ips:
                raise ValueError("broker target must be pinned HTTPS")
            self.targets[kind] = (item["base_url"].rstrip("/"), ips)


class MailboxBroker:
    """Move bounded requests from scoped workspaces to exact pinned HTTPS targets."""

    def __init__(self, store: SQLiteStateStore, policy: BrokerPolicy, timeout_seconds: float, max_response_bytes: int) -> None:
        self.store = store
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def poll_once(self) -> int:
        """Process at most one bounded batch and return the number handled."""

        handled = 0
        for record in self.store.list({}):
            workspace = pathlib.Path(record.workspace_host_path).resolve()
            mailbox = workspace / ".opensandbox-gateway"
            request_dir = mailbox / "requests"
            response_dir = request_dir.parent / "responses"
            if not request_dir.is_dir():
                continue
            if mailbox.is_symlink() or request_dir.is_symlink() or request_dir.resolve().parent != mailbox:
                self.store.record_deny("mailbox-broker", "mailbox_scope_invalid")
                continue
            response_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if response_dir.is_symlink() or response_dir.resolve().parent != mailbox:
                self.store.record_deny("mailbox-broker", "mailbox_scope_invalid")
                continue
            for path in sorted(request_dir.glob("*.json"))[:16]:
                if path.is_symlink() or path.resolve().parent != request_dir:
                    self.store.record_deny("mailbox-broker", "mailbox_scope_invalid")
                    continue
                handled += 1
                try:
                    response = self._process(path)
                except GatewayError as exc:
                    self.store.record_deny("mailbox-broker", exc.code)
                    response = {"status": exc.status, "headers": {"content-type": "application/json"}, "body": base64.b64encode(_json_text({"error": {"code": exc.code}}).encode()).decode()}
                destination = response_dir / path.name
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=response_dir, delete=False) as output:
                    output.write(_json_text(response))
                    temp = pathlib.Path(output.name)
                os.replace(temp, destination)
                path.unlink(missing_ok=True)
        return handled

    def _process(self, path: pathlib.Path) -> dict[str, Any]:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise GatewayError(413, "broker_request_too_large")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            method = value["method"].upper()
            local = urllib.parse.urlsplit(value["path"])
            headers = value.get("headers") or {}
            body = base64.b64decode(value["body"], validate=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise GatewayError(400, "broker_request_invalid") from None
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or len(body) > 1024 * 1024 or local.scheme or local.netloc or ".." in local.path.split("/"):
            raise GatewayError(400, "broker_request_invalid")
        kind, suffix = self._route(local.path)
        base, ips = self.policy.targets[kind]
        target = urllib.parse.urlsplit(base + suffix)
        if kind == "callback" and target.path not in CALLBACK_PATHS:
            raise GatewayError(404, "broker_route_not_allowed")
        if not target.hostname:
            raise GatewayError(500, "broker_policy_invalid")
        allowed_headers = {"authorization", "content-type", "accept", "anthropic-version", "x-api-key", "user-agent"}
        outbound_headers = {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() in allowed_headers}
        connection = _PinnedHTTPSConnection(target.hostname, target.port or 443, ips, self.timeout_seconds)
        try:
            request_path = target.path + (("?" + local.query) if local.query else "")
            connection.request(method, request_path, body=body, headers=outbound_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise GatewayError(502, "broker_redirect_rejected")
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise GatewayError(502, "broker_response_too_large")
            kept = {k.lower(): v for k, v in response.getheaders() if k.lower() in {"content-type", "cache-control"}}
            return {"status": response.status, "headers": kept, "body": base64.b64encode(data).decode("ascii")}
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise GatewayError(502, "broker_upstream_unavailable") from None
        finally:
            connection.close()

    @staticmethod
    def _route(path: str) -> tuple[str, str]:
        for prefix, kind in (("/callback", "callback"), ("/model/openai", "openai"), ("/model/anthropic", "anthropic")):
            if path == prefix or path.startswith(prefix + "/"):
                return kind, path[len(prefix):]
        raise GatewayError(404, "broker_route_not_allowed")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, expected_ips: tuple[str, ...], timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self.expected_ips = expected_ips

    def connect(self) -> None:
        resolved = {item[4][0] for item in socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)}
        candidates = [value for value in self.expected_ips if value in resolved]
        if not candidates:
            raise OSError("pinned address does not match DNS")
        raw = socket.create_connection((candidates[0], self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _record_from_json(value: str) -> LeaseRecord:
    return LeaseRecord(**json.loads(value))


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

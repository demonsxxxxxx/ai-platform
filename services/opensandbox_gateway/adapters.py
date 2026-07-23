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
import re
import secrets
import socket
import sqlite3
import ssl
import stat
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict, replace
import struct
from typing import Any, Mapping

from .gateway import (
    CALLBACK_PATHS,
    DeadlineExceeded,
    GatewayError,
    LeaseRecord,
    Request,
    ReservationResult,
    Response,
    RuntimeEvidence,
    deadline_scope,
    operation_deadline,
)
from .relay import PROXY_SOURCE, RELAY_SOURCE, STOP_RELAY_SOURCE


_RUNTIME_IDENTITY_PROBE_SOURCE = r'''
import json, os, pathlib, sys
uid, gid = os.getuid(), os.getgid()
relay_active = False
try:
    value = (pathlib.Path(sys.argv[1]) / "requests" / "relay.pid").read_text(encoding="ascii").strip()
    if value.isdigit():
        os.kill(int(value), 0)
        relay_active = True
except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, OSError):
    relay_active = False
print(json.dumps({"user": f"{uid}:{gid}", "uid": str(uid), "gid": str(gid), "relay_active": relay_active}, sort_keys=True))
'''


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

    def reserve(self, record: LeaseRecord) -> ReservationResult:
        with self._lock:
            existing = self.find_scope(record.scope)
            if existing is not None:
                outcome = "resume" if existing.canonical_request_hash == record.canonical_request_hash else "conflict"
                return ReservationResult(outcome, existing, existing.reservation_owner_token)
            if any(item.workspace_host_path == record.workspace_host_path and item.state != "deleted" for item in self.records.values()):
                return ReservationResult("conflict", record, "")
            self.records[record.sandbox_id] = record
            return ReservationResult("winner", record, record.reservation_owner_token)

    def activate(self, intent_id: str, owner_token: str, record: LeaseRecord) -> None:
        with self._lock:
            current = self.records.get(intent_id)
            if current is None or current.state not in {"uncertain_create", "reconciling"} or not hmac.compare_digest(current.reservation_owner_token, owner_token):
                raise GatewayError(409, "reservation_state_drift")
            self.records.pop(intent_id)
            self.records[record.sandbox_id] = record

    def list(self, filters: Mapping[str, str]) -> list[LeaseRecord]:
        with self._lock:
            values = []
            for record in self.records.values():
                if record.state == "deleted":
                    continue
                if all(
                    (record.state if key == "state" else record.workspace_host_path if key == "workspace_host_path" else record.metadata.get(key)) == value
                    for key, value in filters.items()
                ):
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
                CREATE UNIQUE INDEX IF NOT EXISTS active_workspace
                    ON leases(workspace_host_path) WHERE state != 'deleted';
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

    def reserve(self, record: LeaseRecord) -> ReservationResult:
        with self._lock, self._connect() as db:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT record_json FROM leases WHERE scope_json = ? AND state != 'deleted'", (_json_text(record.scope),)).fetchone()
                if row:
                    db.execute("COMMIT")
                    existing = _record_from_json(row[0])
                    outcome = "resume" if existing.canonical_request_hash == record.canonical_request_hash else "conflict"
                    return ReservationResult(outcome, existing, existing.reservation_owner_token)
                occupied = db.execute("SELECT record_json FROM leases WHERE workspace_host_path = ? AND state != 'deleted'", (record.workspace_host_path,)).fetchone()
                if occupied:
                    db.execute("COMMIT")
                    return ReservationResult("conflict", _record_from_json(occupied[0]), "")
                db.execute(
                    "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.sandbox_id,
                        _json_text(record.scope),
                        _json_text(record.metadata),
                        _json_text(asdict(record)),
                        record.state,
                        record.workspace_host_path,
                    ),
                )
                db.execute("COMMIT")
                return ReservationResult("winner", record, record.reservation_owner_token)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def activate(self, intent_id: str, owner_token: str, record: LeaseRecord) -> None:
        with self._lock, self._connect() as db:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            try:
                current = db.execute("SELECT state, record_json FROM leases WHERE sandbox_id = ?", (intent_id,)).fetchone()
                current_record = _record_from_json(current[1]) if current else None
                if (
                    current_record is None
                    or current[0] not in {"uncertain_create", "reconciling"}
                    or not hmac.compare_digest(current_record.reservation_owner_token, owner_token)
                ):
                    raise GatewayError(409, "reservation_state_drift")
                changed = db.execute(
                    "UPDATE leases SET sandbox_id=?, scope_json=?, metadata_json=?, record_json=?, state=?, workspace_host_path=? WHERE sandbox_id=?",
                    (
                        record.sandbox_id,
                        _json_text(record.scope),
                        _json_text(record.metadata),
                        _json_text(asdict(record)),
                        record.state,
                        record.workspace_host_path,
                        intent_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise GatewayError(409, "reservation_state_drift")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list(self, filters: Mapping[str, str]) -> list[LeaseRecord]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT record_json FROM leases WHERE state != 'deleted' ORDER BY sandbox_id").fetchall()
        records = [_record_from_json(row[0]) for row in rows]
        return [
            r
            for r in records
            if all(
                (r.state if key == "state" else r.workspace_host_path if key == "workspace_host_path" else r.metadata.get(key)) == value
                for key, value in filters.items()
            )
        ]

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
        if path.startswith("/v1/sandboxes?") and method == "GET":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            filters = {}
            for pair in query.get("metadata", [""])[0].split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    filters[key] = value
            items = [item for item in self.sandboxes.values() if all(item.get("metadata", {}).get(key) == value for key, value in filters.items())]
            page = int(query.get("page", [1])[0])
            page_size = int(query.get("pageSize", [100])[0])
            start = (page - 1) * page_size
            page_items = items[start : start + page_size]
            return Response.json(
                200,
                {
                    "items": page_items,
                    "pagination": {
                        "page": page,
                        "pageSize": page_size,
                        "totalItems": len(items),
                        "totalPages": (len(items) + page_size - 1) // page_size,
                        "hasNextPage": start + page_size < len(items),
                    },
                },
            )
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
        deadline = operation_deadline(self.timeout_seconds)
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=deadline.remaining())
        timer = deadline.arm(lambda: _close_http_connection(connection))
        try:
            headers = {"accept": "application/json"}
            if body:
                headers.update({"content-type": "application/json", "content-length": str(len(body))})
            connection.request(method, path, body=body, headers=headers)
            if connection.sock is not None:
                deadline.bind_socket(connection.sock)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise GatewayError(502, "upstream_redirect_rejected")
            if connection.sock is not None:
                deadline.bind_socket(connection.sock)
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise GatewayError(502, "upstream_response_too_large")
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, data)
        except (DeadlineExceeded, OSError, http.client.HTTPException):
            raise GatewayError(502, "upstream_unavailable") from None
        finally:
            timer.cancel()
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
            user="1000:1000",
            uid="1000",
            gid="1000",
            image=record.image,
            image_digest=record.image_digest,
            mounts=tuple((item["host"], item["mountPath"], item["readOnly"]) for item in record.mounts),
            labels=dict(record.metadata),
            skill_mount_fingerprint=record.metadata["ai-platform.skill_mount.fingerprint"],
        )

    def verify(self, record: LeaseRecord) -> RuntimeEvidence:
        if record.sandbox_id not in self.evidence:
            self.provision(record)
        return replace(
            self.evidence[record.sandbox_id],
            relay_active=record.sandbox_id in self.relays,
        )

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
            "AI_PLATFORM_CALLBACK_BASE_URL": "http://127.0.0.1:18888",
            "SANDBOX_CALLBACK_BASE_URL": "http://127.0.0.1:18888",
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
        identity = self._json_command(
            [
                "docker",
                "exec",
                container_id,
                "python3",
                "-c",
                _RUNTIME_IDENTITY_PROBE_SOURCE,
                "/workspace/.opensandbox-gateway",
            ]
        )
        if (
            not isinstance(identity, dict)
            or set(identity) != {"user", "uid", "gid", "relay_active"}
            or not isinstance(identity["relay_active"], bool)
            or str(config.get("User") or "") != identity["user"]
            or identity["uid"] == "0"
            or identity["gid"] == "0"
        ):
            raise GatewayError(409, "executor_identity_mismatch")
        return RuntimeEvidence(
            sandbox_id=record.sandbox_id,
            runtime=str(host.get("Runtime") or ""),
            network_mode=str(host.get("NetworkMode") or ""),
            no_new_privileges=any(value in ("no-new-privileges", "no-new-privileges:true") for value in security),
            user=identity["user"],
            uid=identity["uid"],
            gid=identity["gid"],
            image=str(config.get("Image") or ""),
            image_digest=image_digest,
            mounts=mounts,
            labels=labels,
            skill_mount_fingerprint=_runtime_skill_mount_fingerprint(record, str(self.workspace_root)),
            running=bool(state.get("Running")),
            relay_active=identity["relay_active"],
        )

    def start_relay(self, record: LeaseRecord) -> None:
        if os.name != "posix" or os.geteuid() != 0:
            raise GatewayError(500, "mailbox_protocol_unavailable")
        import grp
        import pwd

        broker_uid = pwd.getpwnam("opensandbox-gateway").pw_uid
        broker_gid = grp.getgrnam("opensandbox-gateway").gr_gid
        workspace_fd = _open_workspace_dirfd(str(self.workspace_root), record.workspace_host_path)
        try:
            _prepare_mailbox(workspace_fd, record.workspace_host_path, broker_uid, broker_gid)
        except OSError:
            raise GatewayError(500, "mailbox_protocol_invalid") from None
        finally:
            os.close(workspace_fd)
        container_id = self._container_id(record.sandbox_id)
        self._command(
            [
                "docker",
                "exec",
                "-d",
                container_id,
                "python3",
                "-c",
                RELAY_SOURCE,
                "/workspace/.opensandbox-gateway",
                str(broker_uid),
                str(broker_gid),
            ]
        )

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
        if os.name != "posix":
            raise GatewayError(500, "cleanup_scope_invalid")
        workspace_fd = _open_workspace_dirfd(str(self.workspace_root), record.workspace_host_path)
        try:
            _remove_tree_at(workspace_fd, ".opensandbox-gateway")
        finally:
            os.close(workspace_fd)

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
        deadline = operation_deadline(self.timeout_seconds if timeout_seconds is None else timeout_seconds)
        try:
            result = subprocess.run(
                argv,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=deadline.remaining(),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GatewayError(502, "docker_runtime_unavailable") from None
        if result.returncode != 0 or len(result.stdout) > self.max_response_bytes:
            raise GatewayError(502, "docker_runtime_failed")
        return result.stdout.decode("utf-8")


class HelperRuntimeAdapter:
    """Narrow authenticated Unix-socket client for Docker-owned operations."""

    def __init__(
        self,
        socket_path: str,
        timeout_seconds: float,
        max_response_bytes: int,
        dispatch_timeout_seconds: float | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self.dispatch_timeout_seconds = timeout_seconds if dispatch_timeout_seconds is None else dispatch_timeout_seconds
        self.max_response_bytes = max_response_bytes

    def verify(self, record: LeaseRecord) -> RuntimeEvidence:
        value = self._call("verify", record)
        return RuntimeEvidence(
            sandbox_id=value["sandbox_id"],
            runtime=value["runtime"],
            network_mode=value["network_mode"],
            no_new_privileges=value["no_new_privileges"],
            user=value["user"],
            uid=value["uid"],
            gid=value["gid"],
            image=value["image"],
            image_digest=value["image_digest"],
            mounts=tuple(tuple(item) for item in value["mounts"]),
            labels=value["labels"],
            skill_mount_fingerprint=value["skill_mount_fingerprint"],
            running=value["running"],
            relay_active=value["relay_active"],
        )

    def start_relay(self, record: LeaseRecord) -> None:
        self._call("start_relay", record)

    def stop_relay(self, record: LeaseRecord) -> None:
        self._call("stop_relay", record)

    def cleanup_mailbox(self, record: LeaseRecord) -> None:
        self._call("cleanup_mailbox", record)

    def proxy(self, record: LeaseRecord, port: int, request: Request) -> Response:
        value = self._call(
            "proxy",
            record,
            {
                "port": port,
                "method": request.method,
                "target": request.target,
                "headers": dict(request.headers),
                "body": base64.b64encode(request.body).decode("ascii"),
            },
        )
        return Response(int(value["status"]), value.get("headers") or {}, base64.b64decode(value["body"], validate=True))

    def _call(self, operation: str, record: LeaseRecord, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _json_text({"version": 1, "operation": operation, "record": asdict(record), "arguments": dict(arguments or {})}).encode()
        if len(payload) > 2 * 1024 * 1024:
            raise GatewayError(413, "helper_request_too_large")
        dispatch = (
            operation == "proxy"
            and isinstance(arguments, Mapping)
            and arguments.get("port") == 18000
            and str(arguments.get("method") or "").upper() == "POST"
            and str(arguments.get("target") or "").split("?", 1)[0] == "/v1/tasks/execute"
        )
        deadline = operation_deadline(self.dispatch_timeout_seconds if dispatch else self.timeout_seconds)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        timer = None
        try:
            deadline.bind_socket(client)
            timer = deadline.arm(lambda: _expire_socket(client))
            client.connect(self.socket_path)
            deadline.bind_socket(client)
            client.sendall(struct.pack("!I", len(payload)) + payload)
            size = struct.unpack("!I", _recv_exact(client, 4, deadline))[0]
            if size > self.max_response_bytes:
                raise GatewayError(502, "helper_response_too_large")
            response = json.loads(_recv_exact(client, size, deadline))
        except (DeadlineExceeded, OSError, ValueError, KeyError, json.JSONDecodeError, struct.error):
            raise GatewayError(502, "runtime_helper_unavailable") from None
        finally:
            if timer is not None:
                timer.cancel()
            client.close()
        if response.get("ok") is not True or not isinstance(response.get("result"), dict):
            raise GatewayError(int(response.get("status") or 502), str(response.get("code") or "runtime_helper_failed"))
        return response["result"]


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

    REQUEST_TTL_SECONDS = 70.0
    PER_SANDBOX_PENDING_COUNT = 64
    PER_SANDBOX_PENDING_BYTES = 16 * 1024 * 1024
    GLOBAL_PENDING_COUNT = 256
    GLOBAL_PENDING_BYTES = 64 * 1024 * 1024
    EXPIRED_SWEEP_LIMIT = 16

    def __init__(self, store: SQLiteStateStore, policy: BrokerPolicy, timeout_seconds: float, max_response_bytes: int, workspace_root: str = "/data/opensandbox/workspaces") -> None:
        self.store = store
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.workspace_root = workspace_root
        self._rotation = 0

    def poll_once(self) -> int:
        """Process at most one bounded batch and return the number handled."""

        deadline = operation_deadline(self.timeout_seconds)
        with deadline_scope(deadline):
            return self._poll_until(deadline)

    def _poll_until(self, deadline) -> int:
        records = list(self.store.list({}))
        if not records:
            self._rotation = 0
            return 0
        start = self._rotation % len(records)
        records = records[start:] + records[:start]
        self._rotation = (start + 1) % len(records)
        snapshots: list[tuple[Any, int, int, list[tuple[str, int, int, int, int]]]] = []
        total_count = 0
        total_bytes = 0
        now = time.time()
        for record in records:
            try:
                deadline.remaining()
            except DeadlineExceeded:
                self.store.record_deny("mailbox-broker", "broker_deadline_exceeded")
                break
            if os.name != "posix":
                self.store.record_deny("mailbox-broker", "mailbox_scope_invalid")
                continue
            try:
                count, size, entries = self._snapshot_record(record, now)
            except FileNotFoundError:
                continue
            except Exception:
                self.store.record_deny("mailbox-broker", "mailbox_scope_invalid")
                continue
            snapshots.append((record, count, size, entries))
            total_count += count
            total_bytes += size

        handled = 0
        for record, count, size, entries in snapshots:
            if not entries:
                continue
            try:
                deadline.remaining()
                overflow = (
                    count > self.PER_SANDBOX_PENDING_COUNT
                    or size > self.PER_SANDBOX_PENDING_BYTES
                    or total_count > self.GLOBAL_PENDING_COUNT
                    or total_bytes > self.GLOBAL_PENDING_BYTES
                )
                if self._handle_entry(record, entries[0], overflow):
                    handled += 1
                    total_count = max(0, total_count - 1)
                    total_bytes = max(0, total_bytes - entries[0][3])
            except DeadlineExceeded:
                self.store.record_deny("mailbox-broker", "broker_deadline_exceeded")
                return handled
            except Exception:
                self.store.record_deny("mailbox-broker", "broker_internal_error")
                continue
        return handled

    def _snapshot_record(
        self,
        record: LeaseRecord,
        now: float,
    ) -> tuple[int, int, list[tuple[str, int, int, int, int]]]:
        descriptors = self._open_record_mailbox(record)
        workspace_fd, _mailbox_fd, request_fd, response_fd = descriptors
        try:
            _revalidate_workspace_fd(workspace_fd, record.workspace_host_path)
            self._prune_responses(response_fd)
            candidates = sorted(
                name for name in os.listdir(request_fd) if re.fullmatch(r"[0-9a-f]{32}\.json", name)
            )
            entries: list[tuple[str, int, int, int, int]] = []
            expired = 0
            known_bytes = 0
            scan_limit = self.PER_SANDBOX_PENDING_COUNT + self.EXPIRED_SWEEP_LIMIT + 1
            for name in candidates[:scan_limit]:
                try:
                    evidence = os.stat(name, dir_fd=request_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                identity = (name, evidence.st_dev, evidence.st_ino, evidence.st_size, evidence.st_mtime_ns)
                if now - evidence.st_mtime > self.REQUEST_TTL_SECONDS and expired < self.EXPIRED_SWEEP_LIMIT:
                    if self._unlink_request_if_identity(request_fd, identity):
                        expired += 1
                        self.store.record_deny("mailbox-broker", "broker_request_expired")
                    continue
                entries.append(identity)
                known_bytes += max(0, int(evidence.st_size))
            count = max(0, len(candidates) - expired)
            if len(candidates) > scan_limit:
                known_bytes = max(known_bytes, self.PER_SANDBOX_PENDING_BYTES + 1)
            return count, known_bytes, entries
        finally:
            self._close_mailbox(descriptors)

    def _handle_entry(
        self,
        record: LeaseRecord,
        entry: tuple[str, int, int, int, int],
        overflow: bool,
    ) -> bool:
        descriptors = self._open_record_mailbox(record)
        workspace_fd, _mailbox_fd, request_fd, response_fd = descriptors
        name = entry[0]
        try:
            _revalidate_workspace_fd(workspace_fd, record.workspace_host_path)
            if overflow:
                self.store.record_deny("mailbox-broker", "broker_backlog_exceeded")
                response = self._error_response(429, "broker_backlog_exceeded")
            else:
                try:
                    response = self._process(request_fd, name, entry)
                except DeadlineExceeded:
                    raise
                except GatewayError as exc:
                    self.store.record_deny("mailbox-broker", exc.code)
                    response = self._error_response(exc.status, exc.code)
                except Exception:
                    self.store.record_deny("mailbox-broker", "broker_internal_error")
                    response = self._error_response(500, "broker_internal_error")
            if not self._request_identity_matches(request_fd, entry):
                self.store.record_deny("mailbox-broker", "broker_request_changed")
                return False
            self._write_response(response_fd, name, response)
            if not self._unlink_request_if_identity(request_fd, entry):
                self._unlink_response_if_regular(response_fd, name)
                self.store.record_deny("mailbox-broker", "broker_request_changed")
                return False
            return True
        except DeadlineExceeded:
            raise
        except OSError:
            self.store.record_deny("mailbox-broker", "broker_response_write_failed")
            return False
        finally:
            self._close_mailbox(descriptors)

    def _open_record_mailbox(self, record: LeaseRecord) -> tuple[int, int, int, int]:
        workspace_fd = mailbox_fd = request_fd = response_fd = None
        try:
            workspace_fd = _open_workspace_dirfd(self.workspace_root, record.workspace_host_path)
            mailbox_fd = os.open(".opensandbox-gateway", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace_fd)
            request_fd = os.open("requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=mailbox_fd)
            response_fd = os.open("responses", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=mailbox_fd)
            _require_directory(mailbox_fd, uid=0, gid=0, mode=0o711)
            _require_directory(request_fd, uid=1000, gid=os.getgid(), mode=0o2770)
            _require_directory(response_fd, uid=os.getuid(), gid=os.getgid(), mode=0o755)
            return workspace_fd, mailbox_fd, request_fd, response_fd
        except Exception:
            self._close_mailbox((workspace_fd, mailbox_fd, request_fd, response_fd))
            raise

    @staticmethod
    def _close_mailbox(descriptors) -> None:
        for descriptor in reversed(descriptors):
            if isinstance(descriptor, int):
                os.close(descriptor)

    @staticmethod
    def _error_response(status: int, code: str) -> dict[str, Any]:
        return {
            "status": status,
            "headers": {"content-type": "application/json"},
            "body": base64.b64encode(_json_text({"error": {"code": code}}).encode()).decode(),
        }

    def _process(
        self,
        request_fd: int,
        name: str,
        expected_identity: tuple[str, int, int, int, int] | None = None,
    ) -> dict[str, Any]:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=request_fd)
            try:
                evidence = os.fstat(descriptor)
                if expected_identity is not None and (evidence.st_dev, evidence.st_ino) != expected_identity[1:3]:
                    raise GatewayError(400, "broker_request_changed")
                if (
                    not stat.S_ISREG(evidence.st_mode)
                    or evidence.st_size > 2 * 1024 * 1024
                    or evidence.st_uid != 1000
                    or evidence.st_gid != os.getgid()
                    or stat.S_IMODE(evidence.st_mode) != 0o640
                ):
                    raise GatewayError(400, "broker_request_invalid")
                raw = b""
                while len(raw) <= 2 * 1024 * 1024:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    raw += chunk
                after = os.fstat(descriptor)
                if (
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    != (evidence.st_dev, evidence.st_ino, evidence.st_size, evidence.st_mtime_ns, evidence.st_ctime_ns)
                ):
                    raise GatewayError(400, "broker_request_changed")
            finally:
                os.close(descriptor)
            value = json.loads(raw.decode("utf-8"))
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
        deadline = operation_deadline(self.timeout_seconds)
        connection = _PinnedHTTPSConnection(target.hostname, target.port or 443, ips, deadline)
        timer = deadline.arm(lambda: _close_http_connection(connection))
        try:
            request_path = target.path + (("?" + local.query) if local.query else "")
            connection.request(method, request_path, body=body, headers=outbound_headers)
            if connection.sock is not None:
                deadline.bind_socket(connection.sock)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise GatewayError(502, "broker_redirect_rejected")
            if connection.sock is not None:
                deadline.bind_socket(connection.sock)
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise GatewayError(502, "broker_response_too_large")
            kept = {k.lower(): v for k, v in response.getheaders() if k.lower() in {"content-type", "cache-control"}}
            return {"status": response.status, "headers": kept, "body": base64.b64encode(data).decode("ascii")}
        except (DeadlineExceeded, OSError, ssl.SSLError, http.client.HTTPException):
            raise GatewayError(502, "broker_upstream_unavailable") from None
        finally:
            timer.cancel()
            connection.close()

    @staticmethod
    def _request_identity_matches(
        request_fd: int,
        expected: tuple[str, int, int, int, int],
    ) -> bool:
        name, expected_dev, expected_ino, _size, _mtime = expected
        descriptor = None
        try:
            named = os.stat(name, dir_fd=request_fd, follow_symlinks=False)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=request_fd)
            opened = os.fstat(descriptor)
            return (
                stat.S_ISREG(named.st_mode)
                and stat.S_ISREG(opened.st_mode)
                and (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino) == (expected_dev, expected_ino)
            )
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @classmethod
    def _unlink_request_if_identity(
        cls,
        request_fd: int,
        expected: tuple[str, int, int, int, int],
    ) -> bool:
        if not cls._request_identity_matches(request_fd, expected):
            return False
        try:
            os.unlink(expected[0], dir_fd=request_fd)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _unlink_response_if_regular(response_fd: int, name: str) -> None:
        descriptor = None
        try:
            named = os.stat(name, dir_fd=response_fd, follow_symlinks=False)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=response_fd)
            opened = os.fstat(descriptor)
            if stat.S_ISREG(named.st_mode) and (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino):
                os.unlink(name, dir_fd=response_fd)
        except FileNotFoundError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _write_response(response_fd: int, name: str, response: Mapping[str, Any]) -> None:
        temporary = f".{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=response_fd)
        try:
            data = _json_text(response).encode("utf-8")
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o444)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, name, src_dir_fd=response_fd, dst_dir_fd=response_fd)
        except Exception:
            try:
                os.unlink(temporary, dir_fd=response_fd)
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def _prune_responses(cls, response_fd: int) -> None:
        cutoff = time.time() - 120.0
        removed = 0
        for name in sorted(os.listdir(response_fd)):
            if removed >= 16 or not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                continue
            try:
                evidence = os.stat(name, dir_fd=response_fd, follow_symlinks=False)
                if stat.S_ISREG(evidence.st_mode) and evidence.st_mtime < cutoff:
                    cls._unlink_response_if_regular(response_fd, name)
                    removed += 1
            except FileNotFoundError:
                continue

    @staticmethod
    def _route(path: str) -> tuple[str, str]:
        if path in CALLBACK_PATHS:
            return "callback", path
        for prefix, kind in (("/model/openai", "openai"), ("/model/anthropic", "anthropic")):
            if path == prefix or path.startswith(prefix + "/"):
                return kind, path[len(prefix):]
        raise GatewayError(404, "broker_route_not_allowed")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, expected_ips: tuple[str, ...], deadline) -> None:
        super().__init__(hostname, port, timeout=deadline.remaining(), context=ssl.create_default_context())
        self.expected_ips = expected_ips
        self.deadline = deadline

    def connect(self) -> None:
        raw = socket.create_connection((self.expected_ips[0], self.port), self.deadline.remaining())
        self.sock = raw
        self.deadline.bind_socket(raw)
        wrapped = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
        self.sock = wrapped
        self.deadline.bind_socket(wrapped)
        wrapped.do_handshake()


def _runtime_skill_mount_fingerprint(record: LeaseRecord, workspace_root: str) -> str:
    """Recompute the trusted Skill source identity from opened host directories."""

    required = record.metadata.get("ai-platform.skill_mount.required")
    if required == "false":
        return ""
    if required != "true" or not any(
        mount["host"] == f"{record.workspace_host_path}/.claude"
        and mount["mountPath"] == "/workspace/.claude"
        and mount["readOnly"] is True
        for mount in record.mounts
    ):
        raise GatewayError(409, "skill_mount_runtime_drift")
    workspace_fd = claude_fd = skills_fd = None
    try:
        workspace_fd = _open_workspace_dirfd(workspace_root, record.workspace_host_path)
        claude_fd = os.open(".claude", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace_fd)
        skills_fd = os.open("skills", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=claude_fd)
        _revalidate_workspace_fd(workspace_fd, record.workspace_host_path)
        nodes = (
            _require_named_directory_identity(None, record.workspace_host_path, workspace_fd),
            _require_named_directory_identity(workspace_fd, ".claude", claude_fd),
            _require_named_directory_identity(claude_fd, "skills", skills_fd),
        )
        material = "\0".join(str(value) for node in nodes for value in (node.st_dev, node.st_ino))
        return hashlib.sha256(material.encode("ascii")).hexdigest()
    except (OSError, ValueError):
        raise GatewayError(409, "skill_mount_runtime_drift") from None
    finally:
        for descriptor in (skills_fd, claude_fd, workspace_fd):
            if isinstance(descriptor, int):
                os.close(descriptor)


def _require_named_directory_identity(parent_fd: int | None, name: str, descriptor: int):
    named = (
        os.stat(name, follow_symlinks=False)
        if parent_fd is None
        else os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    )
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(named.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise OSError("Skill mount identity changed")
    return opened


def _open_workspace_dirfd(workspace_root: str, workspace_path: str) -> int:
    root = os.path.normpath(workspace_root)
    candidate = os.path.normpath(workspace_path)
    if (
        not os.path.isabs(root)
        or not os.path.isabs(candidate)
        or candidate == root
        or not candidate.startswith(root + os.sep)
        or candidate != workspace_path
    ):
        raise OSError("workspace path is not canonical")
    parts = candidate[len(root) + 1 :].split(os.sep)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise OSError("workspace path is not canonical")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _revalidate_workspace_fd(descriptor, workspace_path)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _revalidate_workspace_fd(workspace_fd: int, workspace_path: str) -> None:
    opened = os.fstat(workspace_fd)
    named = os.stat(workspace_path, follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise OSError("workspace identity changed")


def _prepare_mailbox(workspace_fd: int, workspace_path: str, broker_uid: int, broker_gid: int) -> None:
    """Create the fixed root/sandbox/broker ownership protocol below one workspace."""

    _revalidate_workspace_fd(workspace_fd, workspace_path)
    _remove_tree_at(workspace_fd, ".opensandbox-gateway")
    os.mkdir(".opensandbox-gateway", mode=0o711, dir_fd=workspace_fd)
    mailbox_fd = os.open(".opensandbox-gateway", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace_fd)
    try:
        os.fchown(mailbox_fd, 0, 0)
        os.fchmod(mailbox_fd, 0o711)
        os.mkdir("requests", mode=0o700, dir_fd=mailbox_fd)
        os.mkdir("responses", mode=0o700, dir_fd=mailbox_fd)
        request_fd = os.open("requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=mailbox_fd)
        response_fd = os.open("responses", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=mailbox_fd)
        try:
            os.fchown(request_fd, 1000, broker_gid)
            os.fchmod(request_fd, 0o2770)
            os.fchown(response_fd, broker_uid, broker_gid)
            os.fchmod(response_fd, 0o755)
            _require_directory(mailbox_fd, uid=0, gid=0, mode=0o711)
            _require_directory(request_fd, uid=1000, gid=broker_gid, mode=0o2770)
            _require_directory(response_fd, uid=broker_uid, gid=broker_gid, mode=0o755)
        finally:
            os.close(response_fd)
            os.close(request_fd)
    finally:
        os.close(mailbox_fd)
    _revalidate_workspace_fd(workspace_fd, workspace_path)


def _require_directory(descriptor: int, *, uid: int, gid: int, mode: int) -> None:
    evidence = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(evidence.st_mode)
        or evidence.st_uid != uid
        or evidence.st_gid != gid
        or stat.S_IMODE(evidence.st_mode) != mode
    ):
        raise OSError("mailbox ownership protocol mismatch")


def _remove_tree_at(parent_fd: int, name: str) -> None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except NotADirectoryError:
        os.unlink(name, dir_fd=parent_fd)
        return
    try:
        for child in os.listdir(descriptor):
            evidence = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(evidence.st_mode):
                _remove_tree_at(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_fd)


def _record_from_json(value: str) -> LeaseRecord:
    return LeaseRecord(**json.loads(value))


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _expire_socket(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        connection.close()


def _close_http_connection(connection: http.client.HTTPConnection) -> None:
    sock = connection.sock
    if sock is not None:
        _expire_socket(sock)
    connection.close()


def _recv_exact(connection: socket.socket, size: int, deadline) -> bytes:
    data = bytearray()
    while len(data) < size:
        deadline.bind_socket(connection)
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError("unexpected end of helper response")
        data.extend(chunk)
    return bytes(data)

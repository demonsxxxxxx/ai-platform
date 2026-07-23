"""Privileged narrow Docker helper for the unprivileged public gateway."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import socket
import struct
import threading
from dataclasses import asdict

from .adapters import DockerRuntimeAdapter, _recv_exact
from .gateway import EXECD_ALLOWED, EXECUTOR_ALLOWED, GatewayError, LeaseRecord, Request


def run_helper() -> None:
    """Serve fixed Docker operations to one filesystem-authorized gateway UID."""

    socket_path = os.environ.get("OPENSANDBOX_GATEWAY_HELPER_SOCKET", "/run/opensandbox-gateway/helper.sock")
    allowed_uid = int(os.environ["OPENSANDBOX_GATEWAY_ALLOWED_UID"])
    signing_key = _read_secret(os.environ["OPENSANDBOX_GATEWAY_SIGNING_KEY_FILE"]).encode()
    timeout = float(os.environ.get("OPENSANDBOX_GATEWAY_TIMEOUT_SECONDS", "5"))
    dispatch_timeout = float(os.environ.get("OPENSANDBOX_GATEWAY_DISPATCH_TIMEOUT_SECONDS", "3600"))
    adapter = DockerRuntimeAdapter(signing_key, timeout, dispatch_timeout, 8 * 1024 * 1024, "/data/opensandbox/workspaces")
    target = pathlib.Path(socket_path)
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(socket_path)
    os.chmod(socket_path, 0o660)
    listener.listen(16)
    slots = threading.BoundedSemaphore(8)
    try:
        while True:
            connection, _ = listener.accept()
            if not slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(
                target=_serve_connection,
                args=(connection, allowed_uid, signing_key, adapter, slots),
                daemon=True,
            ).start()
    finally:
        listener.close()
        target.unlink(missing_ok=True)


def _serve_connection(connection, allowed_uid, signing_key, adapter, slots) -> None:
    try:
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != allowed_uid:
            raise GatewayError(401, "helper_peer_rejected")
        size = struct.unpack("!I", _recv_exact(connection, 4))[0]
        if size > 2 * 1024 * 1024:
            raise GatewayError(413, "helper_request_too_large")
        value = json.loads(_recv_exact(connection, size))
        result = _dispatch(value, signing_key, adapter)
        response = {"ok": True, "result": result}
    except GatewayError as exc:
        response = {"ok": False, "status": exc.status, "code": exc.code}
    except Exception:
        response = {"ok": False, "status": 500, "code": "runtime_helper_failed"}
    try:
        data = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
        connection.sendall(struct.pack("!I", len(data)) + data)
    finally:
        connection.close()
        slots.release()


def _dispatch(value, signing_key, adapter):
    if not isinstance(value, dict) or set(value) != {"version", "operation", "record", "arguments"} or value["version"] != 1:
        raise GatewayError(400, "helper_request_invalid")
    try:
        record = LeaseRecord(**value["record"])
    except (TypeError, ValueError):
        raise GatewayError(400, "helper_request_invalid") from None
    unsigned = record.unsigned()
    expected = hmac.new(
        signing_key,
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(record.signature, expected) or record.state != "active":
        raise GatewayError(409, "helper_record_invalid")
    operation, arguments = value["operation"], value["arguments"]
    if operation == "verify" and arguments == {}:
        return asdict(adapter.verify(record))
    if operation == "start_relay" and arguments == {}:
        adapter.start_relay(record)
        return {}
    if operation == "stop_relay" and arguments == {}:
        adapter.stop_relay(record)
        return {}
    if operation == "cleanup_mailbox" and arguments == {}:
        adapter.cleanup_mailbox(record)
        return {}
    if operation != "proxy" or not isinstance(arguments, dict) or set(arguments) != {"port", "method", "target", "headers", "body"}:
        raise GatewayError(404, "helper_operation_not_allowed")
    port, method, target = arguments["port"], str(arguments["method"]).upper(), str(arguments["target"])
    clean_path = target.split("?", 1)[0]
    allowed = EXECD_ALLOWED if port == 44772 else EXECUTOR_ALLOWED if port == 18000 else set()
    if (method, clean_path) not in allowed or not isinstance(arguments["headers"], dict):
        raise GatewayError(404, "helper_operation_not_allowed")
    try:
        body = base64.b64decode(arguments["body"], validate=True)
    except (ValueError, TypeError):
        raise GatewayError(400, "helper_request_invalid") from None
    if len(body) > 1024 * 1024:
        raise GatewayError(413, "helper_request_too_large")
    response = adapter.proxy(record, port, Request(method, target, arguments["headers"], body))
    return {
        "status": response.status,
        "headers": dict(response.headers),
        "body": base64.b64encode(response.body).decode("ascii"),
    }


def _read_secret(path: str) -> str:
    target = pathlib.Path(path)
    if target.is_symlink() or not target.is_file() or target.stat().st_size > 4096:
        raise ValueError("invalid helper signing key file")
    value = target.read_text(encoding="utf-8").strip()
    if len(value.encode()) < 32:
        raise ValueError("invalid helper signing key")
    return value


if __name__ == "__main__":
    run_helper()

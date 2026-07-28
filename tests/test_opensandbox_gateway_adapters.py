from __future__ import annotations

import base64
import json
import ssl
import stat
import time
from types import SimpleNamespace

import pytest

import services.opensandbox_gateway.adapters as gateway_adapters
from services.opensandbox_gateway.adapters import MailboxBroker
from services.opensandbox_gateway.gateway import EXPECTED_EXECUTOR_IDENTITY, GatewayError


EXECUTOR_UID = int(EXPECTED_EXECUTOR_IDENTITY.split(":")[0])
BROKER_GID = 4321


def test_request_directory_accepts_canonical_executor_and_rejects_legacy_uid(monkeypatch) -> None:
    canonical = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o2770,
        st_uid=EXECUTOR_UID,
        st_gid=BROKER_GID,
    )
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: canonical)
    gateway_adapters._require_directory(7, uid=EXECUTOR_UID, gid=BROKER_GID, mode=0o2770)

    legacy = SimpleNamespace(**{**canonical.__dict__, "st_uid": 1000})
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: legacy)
    with pytest.raises(OSError, match="ownership protocol mismatch"):
        gateway_adapters._require_directory(7, uid=EXECUTOR_UID, gid=BROKER_GID, mode=0o2770)


def test_mailbox_processor_accepts_canonical_request_file_and_rejects_legacy_uid(monkeypatch) -> None:
    raw = json.dumps(
        {
            "version": 1,
            "method": "POST",
            "path": "/api/ai/runtime/callbacks/executor",
            "headers": {"content-type": "application/json"},
            "body": base64.b64encode(b"{}").decode("ascii"),
            "created_at_unix_seconds": time.time(),
            "timeout_seconds": 1.0,
        }
    ).encode()
    canonical = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o640,
        st_uid=EXECUTOR_UID,
        st_gid=BROKER_GID,
        st_size=len(raw),
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
        st_ctime_ns=4,
    )

    class Response:
        status = 200

        @staticmethod
        def read(_limit):
            return b"{}"

        @staticmethod
        def getheaders():
            return []

    class Connection:
        sock = None

        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            return None

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(gateway_adapters, "_PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: BROKER_GID, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)
    policy = SimpleNamespace(targets={"callback": ("https://bridge.example", ("10.56.0.211",))})
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    broker = MailboxBroker(SimpleNamespace(), policy, 1.0, 1024, upstream_tls_context=tls_context)

    chunks = iter((raw, b""))
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: canonical)
    monkeypatch.setattr(gateway_adapters.os, "read", lambda *_args: next(chunks))
    assert broker._process(6, "0" * 32 + ".json")["status"] == 200

    legacy = SimpleNamespace(**{**canonical.__dict__, "st_uid": 1000})
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: legacy)
    monkeypatch.setattr(
        gateway_adapters.os,
        "read",
        lambda *_args: (_ for _ in ()).throw(AssertionError("legacy request reached read")),
    )
    with pytest.raises(GatewayError, match="broker_request_invalid"):
        broker._process(6, "0" * 32 + ".json")

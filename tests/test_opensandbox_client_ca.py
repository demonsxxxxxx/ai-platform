from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import certifi
import httpx
import pytest
from opensandbox.config import ConnectionConfig

from app.platform.sandbox.errors import OpenSandboxCapabilityAdmissionError
from app.platform.sandbox.opensandbox_connection import build_opensandbox_connection_config


def _settings(ca_file: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        opensandbox_api_key="test-key",
        opensandbox_ca_cert_file=ca_file,
        opensandbox_domain="gateway.test:8443",
        opensandbox_protocol="https",
        opensandbox_request_timeout_seconds=30,
        opensandbox_use_server_proxy=True,
    )


def test_opensandbox_connection_adds_dedicated_ca_to_default_trust() -> None:
    config = build_opensandbox_connection_config(_settings(certifi.where()), ConnectionConfig)

    assert isinstance(config.transport, httpx.AsyncHTTPTransport)
    assert config._owns_transport is True
    asyncio.run(config.close_transport_if_owned())


def test_opensandbox_connection_keeps_sdk_default_transport_without_ca() -> None:
    config = build_opensandbox_connection_config(_settings(), ConnectionConfig)

    assert config.transport is None


def test_opensandbox_connection_uses_direct_base_url() -> None:
    settings = _settings()
    settings.opensandbox_base_url = "https://server.test:9443/"

    config = build_opensandbox_connection_config(settings, ConnectionConfig)

    assert config.protocol == "https"
    assert config.domain == "server.test:9443"


@pytest.mark.parametrize(
    "base_url",
    [
        "server.test:9443",
        "https://server.test",
        "https://user:pass@server.test:9443",
        "https://server.test:9443/api",
    ],
)
def test_opensandbox_connection_rejects_invalid_direct_base_url(base_url: str) -> None:
    settings = _settings()
    settings.opensandbox_base_url = base_url

    with pytest.raises(OpenSandboxCapabilityAdmissionError, match="base URL is invalid"):
        build_opensandbox_connection_config(settings, ConnectionConfig)


@pytest.mark.parametrize("ca_file", ["relative-ca.pem", "/missing/opensandbox-ca.pem"])
def test_opensandbox_connection_rejects_untrusted_ca_path(ca_file: str) -> None:
    with pytest.raises(OpenSandboxCapabilityAdmissionError, match="CA certificate is invalid"):
        build_opensandbox_connection_config(_settings(ca_file), ConnectionConfig)


def test_opensandbox_connection_rejects_invalid_ca_file(tmp_path: Path) -> None:
    ca_file = tmp_path / "invalid-ca.pem"
    ca_file.write_text("not a certificate\n", encoding="ascii")

    with pytest.raises(OpenSandboxCapabilityAdmissionError, match="CA certificate is invalid"):
        build_opensandbox_connection_config(_settings(str(ca_file.resolve())), ConnectionConfig)

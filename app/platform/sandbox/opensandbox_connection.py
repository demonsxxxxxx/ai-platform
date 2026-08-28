from __future__ import annotations

import ssl
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from app.platform.sandbox.errors import OpenSandboxCapabilityAdmissionError


def build_opensandbox_connection_config(settings: Any, connection_config_class: Any) -> Any:
    config = connection_config_class(
        api_key=str(getattr(settings, "opensandbox_api_key", "") or "") or None,
        domain=str(getattr(settings, "opensandbox_domain", "") or "localhost:8080"),
        protocol=str(getattr(settings, "opensandbox_protocol", "http") or "http"),
        request_timeout=timedelta(
            seconds=max(
                float(getattr(settings, "opensandbox_request_timeout_seconds", 30.0) or 30.0),
                1.0,
            )
        ),
        use_server_proxy=bool(getattr(settings, "opensandbox_use_server_proxy", False)),
    )
    ca_file = str(getattr(settings, "opensandbox_ca_cert_file", "") or "").strip()
    if not ca_file:
        return config
    ca_path = Path(ca_file)
    try:
        if not ca_path.is_absolute() or ca_path.is_symlink() or not ca_path.is_file():
            raise OSError
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ssl.SSLError):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox CA certificate is invalid") from None
    config.transport = httpx.AsyncHTTPTransport(verify=context)
    return config

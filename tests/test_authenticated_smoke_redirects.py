import io
import json
import socket
import subprocess
import sys
from email.message import Message
from pathlib import Path
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest

from tools import capacity_runtime_evidence, verify_auth_rbac_smoke
from tools.verify_alert_trace_export_runtime_acceptance import build_alert_trace_export_runtime_acceptance
from tools.verify_governance_runtime_smoke import build_governance_runtime_smoke


BASE_URL = "https://api.example.invalid"
SYNTHETIC_SECRET = "synthetic-gateway-test-value"
SAFE_ERROR = "authenticated_redirect_origin_mismatch"
REDIRECT_CODES = (301, 302, 303, 307, 308)


class ScriptedTransport(HTTPHandler, HTTPSHandler):
    """Exercise the real urllib opener/redirect flow without a socket transport."""

    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.requests = []
        self.responses = []

    def http_open(self, request):
        self.requests.append(request)
        status, location, payload = self.replies.pop(0)
        headers = Message()
        headers["Content-Type"] = "application/json"
        if location is not None:
            headers["Location"] = location
        response = addinfourl(
            io.BytesIO(json.dumps(payload).encode()), headers, request.full_url, status
        )
        response.msg = "synthetic response"
        self.responses.append(response)
        return response

    https_open = http_open


@pytest.fixture
def scripted_transport(monkeypatch):
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("redirect tests must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", forbid_network)

    def install(replies):
        transport = ScriptedTransport(replies)
        monkeypatch.setattr(
            verify_auth_rbac_smoke,
            "build_opener",
            lambda policy: build_opener(ProxyHandler({}), policy, transport),
        )
        return transport

    return install


@pytest.mark.parametrize("code", REDIRECT_CODES)
@pytest.mark.parametrize(
    "target",
    (
        "https://other.example.invalid/capture?secret=synthetic-query-value",
        "http://api.example.invalid/capture",
        "https://api.example.invalid:444/capture",
        "https://user:synthetic-url-password@api.example.invalid/capture",
        "file:///synthetic-private-file",
    ),
)
def test_auth_request_rejects_unsafe_redirect_before_forwarding_headers(scripted_transport, code, target):
    transport = scripted_transport([(code, target, {"detail": "synthetic-private-body"})])

    status, payload = verify_auth_rbac_smoke._request_json(
        f"{BASE_URL}/api/ai/auth/me",
        headers={"X-AI-Gateway-Secret": SYNTHETIC_SECRET, "X-AI-Roles": "admin"},
    )

    assert (status, payload) == (0, {"error": SAFE_ERROR})
    assert len(transport.requests) == 1
    assert transport.responses[0].closed
    assert transport.requests[0].get_header("X-ai-gateway-secret") == SYNTHETIC_SECRET


@pytest.mark.parametrize("code", REDIRECT_CODES)
@pytest.mark.parametrize("target", ("/next", "https://API.EXAMPLE.invalid:443/next"))
def test_auth_request_preserves_same_origin_redirects(scripted_transport, code, target):
    transport = scripted_transport([(code, target, {}), (200, None, {"ok": True})])

    result = verify_auth_rbac_smoke._request_json(
        f"{BASE_URL}/start",
        headers={"X-AI-Gateway-Secret": SYNTHETIC_SECRET, "X-AI-Roles": "admin"},
    )

    assert result == (200, {"ok": True})
    assert len(transport.requests) == 2
    assert transport.requests[1].get_header("X-ai-gateway-secret") == SYNTHETIC_SECRET
    assert transport.requests[1].get_header("X-ai-roles") == "admin"


def test_auth_request_rechecks_redirect_chains_against_original_origin(scripted_transport):
    transport = scripted_transport([
        (302, "/same-origin", {}),
        (307, "https://other.example.invalid/capture", {}),
    ])

    result = verify_auth_rbac_smoke._request_json(
        f"{BASE_URL}/start", headers={"X-AI-Gateway-Secret": SYNTHETIC_SECRET}
    )

    assert result == (0, {"error": SAFE_ERROR})
    assert len(transport.requests) == 2
    assert all(request.host == "api.example.invalid" for request in transport.requests)
    assert all(response.closed for response in transport.responses)


@pytest.mark.parametrize("code", REDIRECT_CODES)
@pytest.mark.parametrize("target", ("https://other.example.invalid/capture", "http://api.example.invalid/capture"))
def test_capacity_capture_rejects_redirect_with_fixed_safe_failure(monkeypatch, scripted_transport, code, target):
    monkeypatch.setenv("SYNTHETIC_GATEWAY_SECRET", SYNTHETIC_SECRET)
    transport = scripted_transport([(code, target, {"detail": "synthetic-private-body"})])

    with pytest.raises(SystemExit) as error:
        capacity_runtime_evidence.build_capacity_runtime_evidence(
            base_url=BASE_URL, user_id="synthetic-admin", tenant_id="synthetic-tenant",
            roles="admin", gateway_secret_env="SYNTHETIC_GATEWAY_SECRET",
        )

    assert str(error.value) == f"admin runtime overview request failed: {SAFE_ERROR}"
    assert len(transport.requests) == 1
    assert transport.responses[0].closed
    assert transport.requests[0].get_header("X-ai-gateway-secret") == SYNTHETIC_SECRET


def test_capacity_capture_preserves_same_origin_redirect(monkeypatch, scripted_transport):
    monkeypatch.setenv("SYNTHETIC_GATEWAY_SECRET", SYNTHETIC_SECRET)
    transport = scripted_transport([(302, "/overview", {}), (200, None, {})])

    evidence = capacity_runtime_evidence.build_capacity_runtime_evidence(
        base_url=BASE_URL, user_id="synthetic-admin", tenant_id="synthetic-tenant",
        roles="admin", gateway_secret_env="SYNTHETIC_GATEWAY_SECRET",
    )

    assert evidence["source"]["http_status"] == 200
    assert len(transport.requests) == 2
    assert transport.requests[1].get_header("X-ai-gateway-secret") == SYNTHETIC_SECRET
    assert transport.requests[1].get_header("X-ai-roles") == "admin"
    assert SYNTHETIC_SECRET not in json.dumps(evidence)


@pytest.mark.parametrize("builder,request_count", (
    (verify_auth_rbac_smoke.build_auth_rbac_smoke, 5),
    (build_alert_trace_export_runtime_acceptance, 2),
    (build_governance_runtime_smoke, 2),
))
def test_smoke_consumers_fail_closed_on_cross_origin_redirect(scripted_transport, builder, request_count):
    transport = scripted_transport([
        (302, "https://other.example.invalid/capture", {"detail": "synthetic-private-body"})
        for _ in range(request_count)
    ])

    result = builder(base_url=BASE_URL, gateway_secret=SYNTHETIC_SECRET)

    assert result["ok"] is False
    assert len(transport.requests) == request_count
    assert all(request.host == "api.example.invalid" for request in transport.requests)
    serialized = json.dumps(result)
    assert SYNTHETIC_SECRET not in serialized
    assert "other.example.invalid" not in serialized
    assert "synthetic-private-body" not in serialized


@pytest.mark.parametrize("script", (
    "verify_auth_rbac_smoke.py", "capacity_runtime_evidence.py",
    "verify_alert_trace_export_runtime_acceptance.py", "verify_governance_runtime_smoke.py",
))
def test_authenticated_smoke_cli_imports_from_outside_repository(tmp_path, script):
    path = Path(__file__).resolve().parents[1] / "tools" / script
    result = subprocess.run(
        [sys.executable, str(path), "--help"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    )
    assert "--base-url" in result.stdout

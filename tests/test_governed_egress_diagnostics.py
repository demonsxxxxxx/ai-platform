from __future__ import annotations

import json
import logging
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.runtime.sandbox import container_provider
from app.runtime.sandbox import governed_egress_diagnostics as diagnostics
from test_sandbox_container_provider import FakeDockerClient, governed_docker_settings, request, workspace


def test_admission_diagnostic_accepts_only_allowlisted_gate_values(caplog):
    sentinel = "https://token-secret.invalid/private/path?payload=raw"
    caplog.set_level(logging.DEBUG, logger=diagnostics.__name__)

    diagnostics.record_admission_failure(sentinel)  # type: ignore[arg-type]

    assert caplog.records == []


@pytest.mark.parametrize(
    ("exec_exit_code", "exec_error", "expected_stage"),
    (
        (9, None, "callback_probe_docker_exec_nonzero"),
        ([], None, "callback_probe_docker_exec_nonzero"),
        (0, RuntimeError("private-exec-sentinel"), "callback_probe_docker_exec_exception"),
    ),
)
def test_default_callback_probe_logs_only_bounded_docker_exec_stage(
    caplog,
    exec_exit_code,
    exec_error,
    expected_stage,
):
    class ProbeContainer:
        def exec_run(self, _command, **_kwargs):
            if exec_error is not None:
                raise exec_error
            return SimpleNamespace(exit_code=exec_exit_code)

    caplog.set_level(logging.DEBUG, logger=diagnostics.__name__)

    assert (
        container_provider.default_governed_callback_reachability_probe(
            ProbeContainer(),
            "http://private-callback-sentinel.invalid:8020",
            "a" * 40,
        )
        is False
    )

    assert [record.getMessage() for record in caplog.records] == [
        f"governed_egress_private_diagnostic stage={expected_stage} outcome=failed"
    ]
    assert "private-exec-sentinel" not in caplog.text
    assert "private-callback-sentinel" not in caplog.text


@pytest.mark.parametrize(
    ("http_status", "response_body", "expected_stage", "expected_result"),
    (
        (503, b"private-http-sentinel", "callback_probe_http_status", False),
        (200, b"private-payload-sentinel", "callback_probe_payload_invalid", False),
        (200, b'{"status":"ok"}', "callback_probe_payload_invalid", False),
        (
            200,
            json.dumps({"status": "ok", "runtime_commit": "b" * 40}).encode(),
            "callback_probe_runtime_commit_mismatch",
            False,
        ),
        (
            200,
            json.dumps({"status": "ok", "runtime_commit": "a" * 40}).encode(),
            "callback_probe_success",
            True,
        ),
    ),
)
def test_default_callback_probe_classifies_http_payload_commit_and_success(
    caplog,
    http_status,
    response_body,
    expected_stage,
    expected_result,
):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(http_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format, *_args):
            return None

    class LocalExecContainer:
        def exec_run(self, command, **_kwargs):
            completed = subprocess.run(command, capture_output=True, check=False, timeout=5)
            return SimpleNamespace(exit_code=completed.returncode)

    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    caplog.set_level(logging.DEBUG, logger=diagnostics.__name__)
    try:
        result = container_provider.default_governed_callback_reachability_probe(
            LocalExecContainer(),
            f"http://127.0.0.1:{server.server_port}",
            "a" * 40,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result is expected_result
    assert [record.getMessage() for record in caplog.records] == [
        "governed_egress_private_diagnostic "
        f"stage={expected_stage} outcome={'passed' if expected_result else 'failed'}"
    ]
    assert "private-http-sentinel" not in caplog.text
    assert "private-payload-sentinel" not in caplog.text
    if expected_result:
        assert all(record.levelno < logging.WARNING for record in caplog.records)
        assert "outcome=failed" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "expected_stage"),
    (
        ("preflight", "preflight_topology_admission_failed"),
        ("proof-seal", "post_create_proof_seal_failed"),
        ("callback", "callback_reachability_failed"),
    ),
)
async def test_provider_logs_private_gate_preserves_public_error_and_cleans_up(
    monkeypatch,
    caplog,
    gate,
    expected_stage,
):
    sentinel = "https://token-secret.invalid/private/path?payload=raw"
    fake = FakeDockerClient(
        list_error=RuntimeError(sentinel) if gate == "preflight" else None,
        exec_error=RuntimeError(sentinel) if gate == "callback" else None,
    )
    if gate == "proof-seal":
        fake.post_create_mutator = lambda container: container.attrs.update(Id=sentinel)
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    workspace_stat = SimpleNamespace(st_uid=10001, st_gid=10001, st_mode=0o40700)
    monkeypatch.setattr(container_provider, "_workspace_owner_stat", lambda _path: workspace_stat)
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    caplog.set_level(logging.DEBUG, logger=diagnostics.__name__)

    with pytest.raises(container_provider.GovernedEgressAdmissionError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "sandbox_egress_unavailable"
    assert str(exc_info.value).encode() == b"Governed sandbox egress is unavailable; contact an operator."
    assert f"stage={expected_stage} outcome=failed" in caplog.text
    assert sentinel not in caplog.text
    assert all(not name.startswith("ai-platform-sandbox-egress-v2-") for name in fake.networks_by_name)
    if gate == "preflight":
        assert fake.created == []
    else:
        assert fake.containers_by_name["executor-exec-run-a"].removed is True
        assert provider._leases == {}

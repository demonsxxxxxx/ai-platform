"""Content-free fault evidence from capture boundaries through the offline package."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import httpx
import pytest

from app.context.application.provider_sessions import ProviderSessionUseCases
from app.context.domain.provider_sessions import ProviderSessionContinuityError
from app.execution.events import normalize_execution_debug
from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields
from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter, ClaudeSessionStoreTransportError
from app.execution.infrastructure import model_upstream as upstream
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.application.diagnostic_export import build_admin_diagnostic_export
from app.runs.domain import diagnostics as runs_diagnostics
from app.runs.domain.diagnostics import merge_run_diagnostics, sanitize_runtime_diagnostics
from app.sandbox.api import SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, normalize_sdk_runtime_diagnostics
from app.sandbox.domain import runtime_diagnostics as sandbox_diagnostics
from tests.test_admin_run_trajectory import _row
from tests.test_model_upstream_stream import _mock, _call

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def test_debug_closed_fields_survive_both_boundaries_without_arbitrary_data():
    facts = {"component": "provider_session", "observed_bytes": 300001, "limit_bytes": 262144,
             "phase": "structured_entry_validation", "failure_class": "size_limit", "image_count": 0,
             "sdk_version": "0.2.165", "executor_runtime_commit": "a" * 40,
             "entry": {"prompt": "PRIVATE"}, "headers": "PRIVATE", "cli_version": "PRIVATE",
             "operation": "https://PRIVATE/path", "attempt_count": True}
    raw = {"schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, "error_code": "provider_session_entry_too_large", "sdk": {"debug": facts}}
    projected = sanitize_runtime_diagnostics(normalize_sdk_runtime_diagnostics(raw))
    assert projected["sdk"]["debug"] == normalize_execution_debug(facts)
    assert projected["sdk"]["debug"]["image_count"] == 0
    assert "PRIVATE" not in json.dumps(projected)
    assert sanitize_runtime_diagnostics(projected) == projected


def test_debug_projection_boundaries_revalidate_the_same_policy_at_each_trust_boundary():
    facts = {
        "component": "provider_session",
        "entry_kind": "new-future-kind",
        "attempt_count": 0,
        "http_status": 599,
        "sdk_version": "0.2.165",
        "executor_runtime_commit": "a" * 40,
        "headers": "PRIVATE",
        "url": "https://PRIVATE",
        "count": True,
    }
    expected = {
        "component": "provider_session",
        "entry_kind": "other",
        "attempt_count": 0,
        "http_status": 599,
        "sdk_version": "0.2.165",
        "executor_runtime_commit": "a" * 40,
    }

    assert normalize_execution_debug(facts) == expected
    assert runs_diagnostics._normalize_execution_debug(facts) == expected
    assert sandbox_diagnostics._normalize_execution_debug(facts) == expected
    assert normalize_execution_debug(MappingProxyType({"attempt_count": 1})) == {"attempt_count": 1}
    assert runs_diagnostics._normalize_execution_debug(MappingProxyType({"attempt_count": 1})) == {}
    assert sandbox_diagnostics._normalize_execution_debug(MappingProxyType({"attempt_count": 1})) == {}
    assert normalize_execution_debug({"attempt_count": True}) == {}
    assert runs_diagnostics._normalize_execution_debug({"attempt_count": True}) == {}
    assert sandbox_diagnostics._normalize_execution_debug({"attempt_count": True}) == {}
    assert normalize_execution_debug({"attempt_count": 1}) == {"attempt_count": 1}
    assert runs_diagnostics._normalize_execution_debug({"attempt_count": 1}) == {"attempt_count": 1}
    assert sandbox_diagnostics._normalize_execution_debug({"attempt_count": 1}) == {"attempt_count": 1}


def test_debug_projection_boundaries_match_the_execution_contract():
    from app.kernel import runtime_debug as contract

    cases = [
        {name: value}
        for name, values in contract._LABELS.items()
        for value in [*values, "PRIVATE", None, True]
    ]
    cases += [
        {name: value}
        for name in contract._COUNTS
        for value in [0, 1_000_000_000, -1, 1_000_000_001, True, "PRIVATE"]
    ]
    cases += [{"http_status": value} for value in [99, 100, 599, 600, True]]
    cases += [
        {name: value}
        for name in contract._VERSIONS
        for value in ["0.2.165", "3.13.14", "1.0.0a1", "PRIVATE", "1.2", True]
    ]
    cases += [
        {name: value}
        for name in contract._COMMITS
        for value in ["a" * 40, "A" * 40, "a" * 39, "PRIVATE", True]
    ]
    for facts in cases:
        expected = normalize_execution_debug(facts)
        raw = {"schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
               "error_code": "executor_failure", "sdk": {"debug": {**facts, "headers": "PRIVATE"}}}
        normalized = normalize_sdk_runtime_diagnostics(raw)
        assert normalized.get("sdk", {}).get("debug", {}) == expected, facts
        for payload in (raw, normalized):
            projected = sanitize_runtime_diagnostics(payload)
            assert projected.get("sdk", {}).get("debug", {}) == expected, facts
            assert sanitize_runtime_diagnostics(projected) == projected


@pytest.mark.parametrize("error,expected,phase,attempts", [
    (httpx.ConnectError("PRIVATE endpoint"), "connect_error", "connect", 2),
    (httpx.ConnectTimeout("PRIVATE endpoint"), "connect_timeout", "connect", 2),
    (httpx.ReadTimeout("PRIVATE endpoint"), "read_timeout", "response", 1),
])
def test_upstream_failure_records_phase_and_attempt_count_without_addresses(monkeypatch, error, expected, phase, attempts):
    def fail(_request):
        raise error
    _mock(monkeypatch, fail)
    with pytest.raises(upstream.ModelUpstreamError) as caught:
        _call()
    assert caught.value.debug == {"component": "model_proxy", "phase": phase, "failure_class": expected, "attempt_count": attempts}
    assert "PRIVATE" not in json.dumps(caught.value.debug)


def test_upstream_http_502_and_stream_timeout_remain_distinct(monkeypatch):
    _mock(monkeypatch, lambda request: httpx.Response(502, request=request, content=b"PRIVATE body"))
    stream = _call()
    assert stream.debug["http_status"] == 502 and stream.debug["phase"] == "response"
    stream.close()
    from tests.test_model_upstream_stream import Body
    _mock(monkeypatch, lambda request: httpx.Response(200, request=request, stream=Body(error=httpx.ReadTimeout("PRIVATE body"))))
    stream = _call()
    with pytest.raises(httpx.ReadTimeout) as caught:
        list(stream.body())
    assert caught.value.debug["failure_class"] == "read_timeout"
    assert caught.value.debug["phase"] == "stream" and caught.value.debug["attempt_count"] == 1


@pytest.mark.asyncio
async def test_session_entry_guard_keeps_exact_utf8_bytes_and_never_entry(monkeypatch):
    import app.execution.infrastructure.harness.claude.session_store as module
    monkeypatch.setattr(module, "MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES", 100)
    adapter = ClaudeSessionStoreAdapter(callback_url="https://private.invalid", callback_token="private-token", callback_token_id="private-id", run_id="run-a", attempt_id="attempt-a")
    entry = {"type": "user", "message": {"content": "私密" * 100}}
    with pytest.raises(ClaudeSessionStoreTransportError) as caught:
        await adapter.append("private-session", [entry])
    debug = caught.value.debug
    assert debug["observed_bytes"] == len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode())
    assert debug["limit_bytes"] == 100 and debug["entry_kind"] == "user" and debug["scope"] == "main"
    assert debug == adapter.diagnostic_debug
    assert "私密" not in json.dumps(debug) and "private" not in json.dumps(debug)


@pytest.mark.asyncio
async def test_structured_entry_guard_reports_after_image_split():
    cases = ProviderSessionUseCases(object(), image_fields=provider_session_image_fields)
    entry = {"type": "user", "message": {"content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "a" * 4096}},
        {"type": "text", "text": "PRIVATE" * 50000},
    ]}}
    with pytest.raises(ProviderSessionContinuityError) as caught:
        await cases.prepare_callback_media(object(), action="append", entries=[entry], subpath=None)
    facts = normalize_execution_debug(caught.value.debug)
    assert facts["phase"] == "structured_entry_validation"
    assert facts["observed_bytes"] > facts["limit_bytes"] == 262144
    assert facts["image_count"] == 1 and facts["encoded_image_bytes"] == 4096
    assert "PRIVATE" not in json.dumps(facts)


class DebugPersistence:
    def __init__(self):
        self.payload = None
        self.connections = []
        self.bound = True

    async def get_execution_debug_binding(self, conn, **kwargs):
        self.connections.append(conn)
        return {"tenant_id": "tenant-a", "lease_id": "lease-a"} if self.bound else None

    async def append_observation(self, conn, **kwargs):
        self.connections.append(conn)
        self.payload, changed = merge_run_diagnostics(self.payload, kwargs["observation"])
        return {"changed": changed}

    async def get_admin_snapshot(self, conn, **kwargs):
        self.connections.append(conn)
        return {"run": {"run_id": "run-a", "workspace_id": "workspace-a", "status": "failed", "created_at": NOW,
                        "queued_at": NOW, "started_at": NOW, "finished_at": NOW + timedelta(seconds=2),
                        "model_gateway_revision": 3, "admitted_agent_profile_revision": 4},
                "attempts": [{"attempt_id": "attempt-a", "ordinal": 1, "status": "failed"}],
                "diagnostic": {"schema_version": "ai-platform.run-diagnostics.v1", "revision": 2, "payload_json": self.payload},
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "estimated_cost_minor": None}}

    async def get_admin_investigation_events(self, conn, **kwargs):
        self.connections.append(conn)
        event = _row(2, "tool.failed", {"operation_id": "op_a", "category": "execute", "display_name": "Execute", "duration_ms": 123, "failure_category": "timeout"}, metadata={"attempt_id": "attempt-a"})
        event.update(event_id=event["id"], type=event["event_type"], payload=event["payload_json"], created_at=NOW + timedelta(seconds=1))
        return {"events": [event], "events_truncated": False, "first_public_text_at": None}

    async def get_admin_debug_skills(self, conn, **kwargs):
        self.connections.append(conn)
        return [{"skill_id": "skill-a", "skill_version": "2", "content_hash": "a" * 64, "dependency_ids": []}]


@pytest.mark.asyncio
async def test_capture_to_export_keeps_attempt_facts_timeline_usage_and_one_connection():
    persistence = DebugPersistence()
    async def runtime(conn, **kwargs):
        persistence.connections.append(conn)
        return {"leases": [{"lease_id": "lease-a", "executor_status": "failed"}]}
    service = RunDiagnosticsService(persistence=persistence, normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, exception_log_fields=lambda exc: {},
        runtime_debug_reader=runtime, query_runtime_commit="b" * 40, clock=lambda: NOW + timedelta(seconds=3))
    conn = object()
    await service.capture_execution_debug(conn, run_id="run-a", attempt_id="attempt-a", error_code="model_upstream_unavailable",
        debug={"component": "model_proxy", "phase": "connect", "failure_class": "connect_error", "attempt_count": 2, "request": "PRIVATE"})
    projection = await service.read_admin_debug(conn, tenant_id="tenant-a", run_id="run-a")
    assert all(observed is conn for observed in persistence.connections)
    assert projection["root"]["attempt_id"] == "attempt-a"
    assert projection["details"]["sdk"]["debug"]["attempt_count"] == 2
    assert projection["debug"]["events"][0]["operation_id"] == "op_a"
    assert projection["debug"]["investigation"]["total_ms"] == 2000
    assert projection["debug"]["usage"]["total_tokens"] == 15
    assert projection["debug"]["runtime"]["leases"][0]["lease_id"] == "lease-a"
    package = build_admin_diagnostic_export(diagnostics=projection, export_id="export-a", generated_at=NOW)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = json.loads(archive.read("diagnostics.json"))
        assert exported["debug"]["query_component"] == {"api_runtime_commit": "b" * 40}
        assert exported["debug"]["skill_snapshots"][0]["skill_version"] == "2"
        assert "comparison" not in exported["debug"]["investigation"]
        assert "PRIVATE" not in archive.read("diagnostics.json").decode()
    persistence.bound = False
    original = persistence.payload
    await service.capture_execution_debug(conn, run_id="foreign", attempt_id="foreign", error_code="anything", debug={"component": "model_proxy"})
    assert persistence.payload is original


def test_export_budget_reports_optional_history_omissions_and_preserves_failure():
    from tests.test_admin_diagnostic_export import _diagnostics
    source = _diagnostics()
    source["debug"] = {"events": [{"sequence": index, "summary": "safe" * 2000} for index in range(100)], "missing_evidence": ["external_trace"]}
    package = build_admin_diagnostic_export(diagnostics=source, export_id="export-a", generated_at=NOW)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert sum(item.file_size for item in archive.infolist()) <= 512 * 1024
        result = json.loads(archive.read("diagnostics.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["export_omissions"]["events"] == 100 - len(result["debug"]["events"])
    assert result["root"] == {**source["root"], "received_at": source["root"]["received_at"].isoformat()}
    assert len(source["debug"]["events"]) == 100


@pytest.mark.parametrize("mode", ["policy", "http", "stream"])
def test_model_proxy_route_persists_safe_facts_at_each_failure_boundary(monkeypatch, mode):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.execution.application.model_control_plane import _runtime_proxy_headers, RuntimeProxyResponse
    from app.execution.transport import model_management as routes
    from types import SimpleNamespace
    captured = []

    async def record(**kwargs):
        captured.append(kwargs)

    def chunks():
        error = httpx.ReadTimeout("PRIVATE stream content")
        error.debug = {"component": "model_proxy", "phase": "stream", "failure_class": "read_timeout", "attempt_count": 1}
        raise error
        yield b""

    async def proxy(**kwargs):
        if mode == "policy":
            _runtime_proxy_headers("anthropic", "v1/messages", {"anthropic-version": "2023-06-01", "anthropic-beta": "PRIVATE beta"})
        return RuntimeProxyResponse(status=502 if mode == "http" else 200, content_type="application/octet-stream",
                                    body=chunks() if mode == "stream" else (b"PRIVATE upstream body",),
                                    debug={"attempt_count": 1})

    monkeypatch.setattr(routes, "configured_model_control_plane", lambda: SimpleNamespace(proxy=proxy, record_debug=record))
    app = FastAPI()
    app.include_router(routes.build_model_management_router(principal_dependency=lambda: None, is_admin=lambda _: True))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/internal/model-proxy/anthropic/v1/messages", content=b"PRIVATE prompt", headers={
            "x-ai-platform-run-id": "run-a", "x-ai-platform-attempt-id": "attempt-a",
        })
    assert captured and captured[0]["run_id"] == "run-a" and captured[0]["attempt_id"] == "attempt-a"
    facts = captured[0]["debug"]
    if mode == "policy":
        assert response.status_code == 403
        assert facts["policy"] == "anthropic_beta" and facts["beta_count"] == 1
    elif mode == "http":
        assert response.status_code == 502 and facts["http_status"] == 502
    else:
        assert facts["failure_class"] == "read_timeout" and facts["phase"] == "stream"
    assert "PRIVATE" not in json.dumps(captured)


@pytest.mark.asyncio
async def test_successful_session_retry_clears_recovered_failure_facts():
    adapter = ClaudeSessionStoreAdapter(callback_url="https://private.invalid", callback_token="private-token", callback_token_id="private-id", run_id="run-a", attempt_id="attempt-a")
    adapter._next_sequence = 1
    calls = 0
    async def request(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise adapter._failure("provider_session_callback_timeout", action="append", phase="stream", failure_class="read_timeout")
        return {"next_sequence": 2}
    adapter._request = request
    with pytest.raises(ClaudeSessionStoreTransportError):
        await adapter.append("private-session", [{"type": "assistant"}])
    assert adapter.diagnostic_debug["failure_class"] == "read_timeout"
    await adapter.append("private-session", [{"type": "assistant"}])
    assert adapter.diagnostic_debug == {} and adapter.accepted_final_sequence == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["load", "list_subkeys"])
async def test_session_sequence_mismatch_records_expected_and_received(action):
    adapter = ClaudeSessionStoreAdapter(callback_url="https://private.invalid", callback_token="private-token", callback_token_id="private-id", run_id="run-a", attempt_id="attempt-a")
    adapter._next_sequence = 2
    async def request(payload):
        return {"next_sequence": 3, "entries": [], "subpaths": []}
    adapter._request = request
    with pytest.raises(ClaudeSessionStoreTransportError, match="sequence_conflict") as caught:
        await getattr(adapter, action)("private-session")
    assert caught.value.debug == adapter.diagnostic_debug == {
        "component": "provider_session", "operation": action, "phase": "response",
        "failure_class": "invalid_response", "expected_sequence": 2, "accepted_sequence": 3,
    }


@pytest.mark.asyncio
async def test_cached_load_clears_prior_append_failure(monkeypatch):
    import app.execution.infrastructure.harness.claude.session_store as module
    adapter = ClaudeSessionStoreAdapter(callback_url="https://private.invalid", callback_token="private-token", callback_token_id="private-id", run_id="run-a", attempt_id="attempt-a")
    async def request(payload):
        return {"next_sequence": 1, "entries": []}
    adapter._request = request
    assert await adapter.load("private-session") is None
    monkeypatch.setattr(module, "MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES", 10)
    with pytest.raises(ClaudeSessionStoreTransportError):
        await adapter.append("private-session", [{"type": "assistant"}])
    assert adapter.diagnostic_debug["failure_class"] == "size_limit"
    assert await adapter.load("private-session") is None
    assert adapter.diagnostic_debug == {}


@pytest.mark.parametrize("guard", ["count", "bytes"])
def test_provider_image_guards_keep_only_measured_facts(monkeypatch, guard):
    from app.context.domain import provider_media
    monkeypatch.setattr(provider_media, "MAX_PROVIDER_IMAGE_FIELDS", 0 if guard == "count" else 2)
    monkeypatch.setattr(provider_media, "MAX_PROVIDER_IMAGE_BYTES", 2)
    entry = {"type": "user", "message": {"content": [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "PRIVATE",
    }}]}}
    with pytest.raises(ProviderSessionContinuityError) as caught:
        provider_media.split_provider_images([entry], image_fields=provider_session_image_fields)
    facts = normalize_execution_debug(caught.value.debug)
    assert facts["phase"] == "image_validation" and facts["image_count"] == 1
    if guard == "bytes":
        assert facts["observed_bytes"] == 7 and facts["limit_bytes"] == 2
    assert "PRIVATE" not in json.dumps(facts)

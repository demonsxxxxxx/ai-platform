from __future__ import annotations

import io
import json
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.runs.application.diagnostic_export import (
    ADMIN_DIAGNOSTIC_EXPORT_FILES,
    AdminDiagnosticExportTooLarge,
    build_admin_diagnostic_export,
)


def _diagnostics() -> dict:
    return {
        "schema_version": "ai-platform.run-diagnostics.v1",
        "diagnostic_id": "rdiag_a",
        "revision": 3,
        "coverage": "partial",
        "run": {
            "run_id": "run_a",
            "session_id": "ses_a",
            "user_id": "user_a",
            "workspace_id": "workspace_a",
            "status": "failed",
            "trace_id": "trace_a",
            "created_at": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "error_code": "executor_failure",
        },
        "root": {
            "observation_id": "obs_a",
            "attempt_id": "attempt_a",
            "lease_id": None,
            "request_id": None,
            "callback_id": None,
            "kind": "failure",
            "source": "worker",
            "stage": "model_wait",
            "error_code": "provider_timeout",
            "exception_type": "TimeoutError",
            "message": "model timed out",
            "stack": None,
            "received_at": datetime(2026, 9, 20, tzinfo=timezone.utc),
        },
        "handling": [],
        "losses": [],
        "attempts": [],
        "details": {
            "schema_version": "ai-platform.sdk-runtime-diagnostics.v1",
            "sdk": {},
            "tool_lifecycles": [],
            "tool_calls": [],
            "tool_policy_denials": [],
            "executor_protocol": None,
            "observations": [],
        },
        "versions": {"run_diagnostics": "ai-platform.run-diagnostics.v1"},
        "counts": {"retained_observations": 1, "omitted_observations": 0},
    }


def test_admin_diagnostic_export_has_fixed_safe_offline_shape():
    package = build_admin_diagnostic_export(
        diagnostics=_diagnostics(),
        export_id="rdiagexp_a",
        generated_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
    )

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert tuple(sorted(archive.namelist())) == tuple(sorted(ADMIN_DIAGNOSTIC_EXPORT_FILES))
        manifest = json.loads(archive.read("manifest.json"))
        diagnostics = json.loads(archive.read("diagnostics.json"))
        readme = archive.read("README.txt").decode()

    assert manifest["export_id"] == "rdiagexp_a"
    assert manifest["diagnostic_revision"] == 3
    assert manifest["versions"]["recorded_diagnostics"]["run_diagnostics"] == (
        "ai-platform.run-diagnostics.v1"
    )
    assert diagnostics["root"]["message"] == "model timed out"
    assert "最早留存异常不等于已经证明的根因" in readme


def test_admin_diagnostic_export_fails_closed_above_uncompressed_budget():
    diagnostics = _diagnostics()
    diagnostics["details"]["sdk"] = {"errors": ["x" * (600 * 1024)]}

    with pytest.raises(AdminDiagnosticExportTooLarge, match="admin_run_diagnostic_export_too_large"):
        build_admin_diagnostic_export(
            diagnostics=diagnostics,
            export_id="rdiagexp_large",
            generated_at=datetime.now(timezone.utc),
        )


def _auth_settings():
    return type(
        "S",
        (),
        {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False},
    )()


def _headers(roles: str = "developer") -> dict[str, str]:
    return {
        "x-ai-user-id": "admin-a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": roles,
        "x-ai-gateway-secret": "test-secret",
    }


class _ExportConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql, _params=None):
        self.statements.append(" ".join(sql.split()))
        return None


def test_admin_diagnostic_export_is_admin_scoped_audited_and_no_store(monkeypatch):
    connections: list[_ExportConnection] = []
    audits: list[dict] = []

    @asynccontextmanager
    async def fake_transaction():
        connection = _ExportConnection()
        connections.append(connection)
        yield connection

    class FakeDiagnosticsService:
        async def read_admin(self, _conn, *, tenant_id, run_id):
            assert tenant_id == "default"
            assert run_id == "run_a"
            return _diagnostics()

    async def fake_append_audit_log(_conn, **kwargs):
        audits.append(kwargs)
        return "audit_a"

    monkeypatch.setattr("app.auth.get_settings", _auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runs._require_run_diagnostics_service",
        lambda _request: FakeDiagnosticsService(),
    )
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.append_audit_log",
        fake_append_audit_log,
    )
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.new_id",
        lambda prefix: f"{prefix}_a",
    )
    client = TestClient(create_app())

    forbidden = client.post(
        "/api/ai/admin/runs/run_a/diagnostic-exports",
        headers=_headers("user"),
    )
    response = client.post(
        "/api/ai/admin/runs/run_a/diagnostic-exports",
        headers=_headers(),
    )

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-diagnostic-export-id"] == "rdiagexp_a"
    assert connections[0].statements == ["set transaction isolation level repeatable read"]
    assert audits[0]["tenant_id"] == "default"
    assert audits[0]["user_id"] == "admin-a"
    assert audits[0]["payload_json"]["result"] == "response_started"
    assert audits[0]["payload_json"]["diagnostic_revision"] == 3
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == set(ADMIN_DIAGNOSTIC_EXPORT_FILES)


def test_admin_diagnostic_export_does_not_return_package_when_audit_fails(monkeypatch):
    @asynccontextmanager
    async def fake_transaction():
        yield _ExportConnection()

    class FakeDiagnosticsService:
        async def read_admin(self, _conn, *, tenant_id, run_id):
            return _diagnostics()

    async def failing_audit(*_args, **_kwargs):
        raise RuntimeError("audit_unavailable")

    monkeypatch.setattr("app.auth.get_settings", _auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.admin_runs._require_run_diagnostics_service",
        lambda _request: FakeDiagnosticsService(),
    )
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.append_audit_log",
        failing_audit,
    )
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/admin/runs/run_a/diagnostic-exports",
        headers=_headers(),
    )

    assert response.status_code == 500
    assert response.headers.get("content-type") != "application/zip"

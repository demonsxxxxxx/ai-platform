from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.platform.tracing import safe_exception_log_fields
from fastapi.testclient import TestClient

from app.main import create_app
from app.runs.api import RunCancellationPostProcessor, RunDiagnosticsService
from app.sandbox.api import normalize_sdk_runtime_diagnostics


class _RouteCancellationReceipt:
    def __init__(self, value):
        self._value = value
        self.run_id = value.get("run_id")
        self.attempt_id = value.get("attempt_id")

    def as_route_result(self):
        return dict(self._value)


class _AdminCancellationAdapter:
    def __init__(self, handler):
        self._handler = handler
        self.remove_queued_run = self._noop

    @staticmethod
    async def _noop(*_args, **_kwargs):
        return None

    async def post_cancel(self, cancellation, **kwargs):
        assert kwargs["user_id"] == "admin-a"
        assert kwargs["requested_by_role"] == "admin"
        assert kwargs["reason"] == "admin_cancel_requested"
        processor = RunCancellationPostProcessor(
            queue_cleanup=self.remove_queued_run,
            sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=self._noop),
            provider_factory=object(), transaction_factory=fake_transaction,
            admit_stream=self._noop, publish_event=self._noop, warn=lambda *_args: None,
        )
        return await processor.post_cancel(cancellation, **kwargs)

    async def request_admin_cancel(self, *, tenant_id, admin_user_id, run_id):
        value = await self._handler(
            None,
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            run_id=run_id,
        )
        return _RouteCancellationReceipt(value) if value is not None else None


def _install_admin_cancel(monkeypatch, handler):
    adapter = _AdminCancellationAdapter(handler)
    monkeypatch.setattr(
        "app.routes.admin_runs._require_run_cancellation_use_case",
        lambda _request: adapter,
    )
    return adapter


def _install_admin_monitor_metadata(monkeypatch, metadata):
    class AdminMonitorMetadataService:
        async def read_admin_monitor_metadata(self, _conn, *, tenant_id, run_ids):
            assert tenant_id == "default"
            return {
                run_id: dict(metadata[run_id])
                for run_id in run_ids
                if run_id in metadata
            }

    service = AdminMonitorMetadataService()
    monkeypatch.setattr(
        "app.routes.admin_runs._require_run_diagnostics_service",
        lambda _request: service,
    )


@pytest.fixture(autouse=True)
def _install_test_run_stream_runtime(monkeypatch):
    original_create_app = create_app

    def create_app_with_runtime():
        app = original_create_app()
        app.state.run_stream_runtime = type(
            "TestRunStreamRuntime",
            (),
            {"worker_capabilities": object()},
        )()
        return app

    monkeypatch.setattr(f"{__name__}.create_app", create_app_with_runtime)


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


class EmptyPropagationCursor:
    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


class EmptyPropagationConnection:
    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if normalized.startswith("select child.id") and "from runs child" in normalized:
            return EmptyPropagationCursor()
        if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
            return EmptyPropagationCursor()
        if normalized.startswith("select id, trace_id, status") and "from runs" in normalized:
            return EmptyPropagationCursor()
        raise AssertionError(f"unexpected fake transaction sql: {normalized}")


@asynccontextmanager
async def fake_transaction():
    yield EmptyPropagationConnection()


def headers(roles="developer"):
    return {
        "x-ai-user-id": "admin-a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": roles,
        "x-ai-gateway-secret": "test-secret",
    }


def test_admin_run_detail_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_a", headers=headers("user"))

    assert response.status_code == 403


def test_admin_run_diagnostics_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/runs/run_a/diagnostics",
        headers=headers("user"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_run_diagnostics_keeps_not_found_tenant_scoped(monkeypatch):
    class MissingDiagnosticsPersistence:
        async def get_admin_snapshot(self, _conn, *, tenant_id, run_id):
            assert tenant_id == "default"
            assert run_id == "run_missing"
            return None

        async def append_observation(self, *_args, **_kwargs):
            raise AssertionError("read route must not write diagnostics")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    app = create_app()
    app.state.run_diagnostics_service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=MissingDiagnosticsPersistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version="ai-platform.sdk-runtime-diagnostics.v1",
    )
    client = TestClient(app)

    response = client.get(
        "/api/ai/admin/runs/run_missing/diagnostics",
        headers=headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"


@pytest.mark.parametrize("include_frames", [False, True])
def test_admin_run_diagnostics_preserves_projection_failure_over_http(
    monkeypatch, include_frames,
):
    projection_failure = {
        "reason": "raw_frame_invalid", "stage": "message", "location": "raw_stream_frame",
    }
    if include_frames:
        frame = {
            "event_type": "content_block_start", "block_type": "tool_use",
            "delta_type": "other", "message_state": "open",
            "open_block_type": "none", "index_state": "other",
        }
        projection_failure.update(
            frame_shape={**frame, "guard": "block_start_state"},
            preceding_frames=[frame],
        )

    class StoredDiagnosticsPersistence:
        async def get_admin_snapshot(self, _conn, *, tenant_id, run_id):
            assert tenant_id == "default"
            assert run_id == "run_a"
            return {
                "run": {
                    "run_id": run_id, "session_id": "ses-a", "user_id": "user-a",
                    "workspace_id": "default", "status": "failed", "trace_id": None,
                    "created_at": None, "queued_at": None, "started_at": None,
                    "finished_at": None, "error_code": "claude_agent_sdk_output_validation_failed",
                },
                "result_json": {},
                "diagnostic": {
                    "diagnostic_id": "rdiag-a", "revision": 1,
                    "schema_version": "ai-platform.run-diagnostics.v1",
                    "payload_json": {
                        "coverage": "full",
                        "observations": [{
                            "observation_id": "obs-a", "attempt_id": "attempt-a",
                            "parent_observation_id": "obs_" + "1" * 32,
                            "source": "sdk", "stage": "message",
                            "error_code": "claude_agent_sdk_output_validation_failed",
                            "runtime_diagnostics": {
                                "schema_version": "ai-platform.sdk-runtime-diagnostics.v1",
                                "projection_failure": {
                                    **projection_failure, "body": "PRIVATE_FRAME_BODY",
                                },
                            },
                        }],
                    },
                },
                "attempts": [],
            }

        async def append_observation(self, *_args, **_kwargs):
            raise AssertionError("read route must not write diagnostics")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    app = create_app()
    app.state.run_diagnostics_service = RunDiagnosticsService(
        exception_log_fields=safe_exception_log_fields,
        persistence=StoredDiagnosticsPersistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version="ai-platform.sdk-runtime-diagnostics.v1",
    )
    client = TestClient(app)
    try:
        response = client.get("/api/ai/admin/runs/run_a/diagnostics", headers=headers())
    finally:
        client.close()

    assert response.status_code == 200
    details = response.json()["details"]
    assert details["projection_failure"] == projection_failure
    assert details["observations"][0]["projection_failure"] == projection_failure
    assert details["observations"][0]["parent_observation_id"] == "obs_" + "1" * 32
    assert response.json()["root"]["parent_observation_id"] == "obs_" + "1" * 32
    assert "PRIVATE_FRAME_BODY" not in response.text


def test_admin_run_list_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs", headers=headers("user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_run_list_returns_tenant_scoped_summaries(monkeypatch):
    calls = []

    async def fake_list_admin_runs(conn, *, tenant_id, user_id=None, status=None, limit=50):
        calls.append((tenant_id, user_id, status, limit))
        return [
            {
                "run_id": "run_a",
                "session_id": "ses_a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "queued",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
                "error_code": None,
                "error_message": None,
            }
        ]

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "default"
        assert run_id == "run_a"
        return 2

    async def fake_get_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "default"
        assert kwargs == {"include_user_breakdown": True}
        return {
            "tenant_id": tenant_id,
            "reason": "workers_busy",
            "depths": {"tenant_queued": 4, "tenant_processing": 1},
            "workers": {"active": 1},
            "capacity": {"available_worker_slots": 0},
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.runs.infrastructure.admin_queries_postgres.list_admin_runs", fake_list_admin_runs, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    _install_admin_monitor_metadata(
        monkeypatch,
        {
            "run_a": {
                "session_title": "审核采购合同",
                "task_summary": "请审核采购合同中的付款条款",
                "user_display_name": "王敏",
                "workspace_name": "法务工作区",
                "agent_name": "合同审阅助手",
                "skill_name": "合同审阅",
            }
        },
    )
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs?user_id=user-a&status=queued&limit=25", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 25
    assert data["runs"][0]["run_id"] == "run_a"
    assert data["runs"][0]["user_id"] == "user-a"
    assert data["runs"][0]["session_title"] == "审核采购合同"
    assert data["runs"][0]["user_display_name"] == "王敏"
    assert data["runs"][0]["workspace_name"] == "法务工作区"
    assert data["runs"][0]["agent_name"] == "合同审阅助手"
    assert data["runs"][0]["skill_name"] == "合同审阅"
    assert data["runs"][0]["queue_position"] == 2
    assert data["runs"][0]["queue_insight"]["reason"] == "workers_busy"
    assert calls == [("default", "user-a", "queued", 25)]


def test_admin_run_list_sanitizes_secret_like_error_fields(monkeypatch):
    async def fake_list_admin_runs(conn, *, tenant_id, user_id=None, status=None, limit=50):
        return [
            {
                "run_id": "run_a",
                "session_id": "ses_a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "failed",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
                "error_code": "executor_failure token=admin-list-code-token",
                "error_message": "failed token=admin-list-message-token /var/lib/ai-platform/run-a/out.log",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.runs.infrastructure.admin_queries_postgres.list_admin_runs", fake_list_admin_runs, raising=False)
    _install_admin_monitor_metadata(
        monkeypatch,
        {
            "run_a": {
                "session_title": "Review token=admin-list-title-token",
                "task_summary": "Summarize client_secret=admin-list-task-token",
                "user_display_name": "User token=admin-list-user-token",
                "workspace_name": "/var/lib/ai-platform/private-workspace",
                "agent_name": "Agent token=admin-list-agent-token",
                "skill_name": "Skill token=admin-list-skill-token",
            }
        },
    )
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs", headers=headers())

    assert response.status_code == 200
    run = response.json()["runs"][0]
    assert run["error_code"] == "executor_failure token=[redacted-secret]"
    assert run["error_message"] == ""
    assert run["session_title"] == "Review token=[redacted-secret]"
    assert run["task_summary"] == "Summarize client_secret=[redacted-secret]"
    assert run["user_display_name"] == "User token=[redacted-secret]"
    assert run["workspace_name"] is None
    assert run["agent_name"] == "Agent token=[redacted-secret]"
    assert run["skill_name"] == "Skill token=[redacted-secret]"
    assert "admin-list-code-token" not in str(run)
    assert "admin-list-message-token" not in str(run)
    assert "/var/lib/ai-platform" not in str(run)


def test_admin_run_cancel_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_a/cancel", headers=headers("user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_run_cancel_records_admin_actor(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        calls.append((tenant_id, admin_user_id, run_id))
        return {"run_id": run_id, "status": "cancel_requested"}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    _install_admin_cancel(monkeypatch, fake_request_admin_run_cancel)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_a/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run_a", "session_id": None, "status": "cancel_requested"}
    assert calls == [("default", "admin-a", "run_a")]


def test_admin_cancel_queued_run_removes_queued_payload(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancelled"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    adapter = _install_admin_cancel(monkeypatch, fake_request_admin_run_cancel)
    monkeypatch.setattr(adapter, "remove_queued_run", fake_remove_queued_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_queued/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [("remove", "default", "run_queued")]


def test_admin_run_detail_returns_explainability_contract(monkeypatch):
    async def fake_get_admin_run_detail(conn, *, tenant_id, run_id):
        return {
            "run": {
                "run_id": run_id,
                "session_id": "ses_a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "succeeded",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "created_at": None,
                "started_at": None,
                "finished_at": None,
                "input": {
                    "input": {"message": "审核文档"},
                    "intent": {"intent": "document_review", "selected_capability": "document_review"},
                },
                "result": {"message": "完成"},
            },
            "events": [
                {
                    "event_id": "evt_delta",
                    "sequence": 1,
                    "type": "message.delta",
                    "visible_to_user": True,
                    "payload": {
                        "delta": "完成",
                        "__stream_v4": {
                            "message_id": "msg-a",
                            "attempt_id": "attempt-private",
                            "authorization_epoch": 3,
                        },
                    },
                },
                {
                    "event_id": "evt_private",
                    "type": "message.delta",
                    "visible_to_user": False,
                    "payload": {"delta": "PRIVATE_OUTPUT", "__stream_v4": {"attempt_id": "secret"}},
                },
                {"event_id": "evt_a", "type": "run_succeeded", "stage": "worker", "message": "Run succeeded"},
            ],
            "artifacts": [{"artifact_id": "art_a", "label": "审核 Word", "artifact_type": "reviewed_docx"}],
            "sandbox_leases": [
                {
                    "lease_id": "lease-a",
                    "tenant_id": "default",
                    "workspace_id": "default",
                    "user_id": "user-a",
                    "session_id": "ses_a",
                    "run_id": run_id,
                    "status": "released",
                    "lease_payload": {"source": "foundation_runtime_lifecycle_probe"},
                }
            ],
            "skill_snapshots": [
                {
                    "skill_id": "qa-file-reviewer",
                    "skill_version": "hash-a",
                    "content_hash": "hash-a",
                    "source": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                    "staged": True,
                    "used": True,
                    "usage": {
                        "used_skills_source": "executor_hook",
                        "inferred_used": False,
                    },
                    "created_at": None,
                }
            ],
            "audit": [],
        }

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        raise AssertionError("succeeded admin run should not query queue position")

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        raise AssertionError("succeeded admin run should not query queue insight")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.runs.infrastructure.admin_queries_postgres.get_admin_run_detail", fake_get_admin_run_detail)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    _install_admin_monitor_metadata(
        monkeypatch,
        {
            "run_a": {
                "session_title": "审核采购合同",
                "task_summary": "检查付款条款",
                "user_display_name": "王敏",
                "workspace_name": "法务工作区",
                "agent_name": "合同审阅助手",
                "skill_name": "文档审阅",
            }
        },
    )
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_a", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["run_id"] == "run_a"
    assert data["run"]["session_title"] == "审核采购合同"
    assert data["run"]["agent_name"] == "合同审阅助手"
    assert data["run"]["workspace_name"] == "法务工作区"
    assert data["run"]["input"]["intent"]["selected_capability"] == "document_review"
    assert data["worker_execution"]["messages"][0]["text"] == "完成"
    assert data["events"][0]["payload"] == {"delta": "完成"}
    assert len(data["events"]) == 2
    assert "PRIVATE_OUTPUT" not in response.text
    assert data["artifacts"][0]["artifact_id"] == "art_a"
    assert data["sandbox_leases"][0]["lease_id"] == "lease-a"
    assert data["sandbox_leases"][0]["lease_payload"] == {"source": "foundation_runtime_lifecycle_probe"}
    assert data["skill_snapshots"][0]["skill_id"] == "qa-file-reviewer"
    assert data["skill_snapshots"][0]["used"] is True
    assert data["skill_snapshots"][0]["usage"]["used_skills_source"] == "executor_hook"


def test_admin_run_diagnostics_reads_legacy_without_mutating_detail(monkeypatch):
    from app.execution.api import public_tool_operation_id
    diagnostics = {
        "schema_version": "ai-platform.sdk-runtime-diagnostics.v1",
        "error_code": "claude_agent_sdk_tool_admission_failed",
        "sdk": {"api_error_status": 429},
        "tool_calls": [
            {
                "tool_name": "Bash",
                "invocation_id": "tool-1",
                "last_stage": "failed",
                "is_interrupt": True,
                "tool_input": {
                    "command": "cat /private/file",
                    "token": "private-token",
                },
            }
        ],
    }

    async def fake_get_admin_run_detail(conn, *, tenant_id, run_id):
        return {
            "run": {
                "run_id": run_id,
                "session_id": "ses-a",
                "user_id": "user-a",
                "status": "failed",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "created_at": None,
                "started_at": None,
                "finished_at": None,
                "input": {},
                "result": {},
            },
            "events": [],
            "steps": [],
            "artifacts": [],
            "sandbox_leases": [],
            "skill_snapshots": [],
            "audit": [],
        }

    class LegacyDiagnosticsPersistence:
        async def get_admin_monitor_metadata(self, _conn, *, tenant_id, run_ids):
            assert tenant_id == "default"
            assert run_ids == ("run_failed",)
            return {}

        async def get_admin_snapshot(self, _conn, *, tenant_id, run_id):
            assert tenant_id == "default"
            assert run_id == "run_failed"
            return {
                "run": {
                    "run_id": run_id,
                    "session_id": "ses-a",
                    "user_id": "user-a",
                    "workspace_id": "default",
                    "status": "failed",
                    "trace_id": "trace-a",
                    "created_at": None,
                    "queued_at": None,
                    "started_at": None,
                    "finished_at": None,
                    "error_code": diagnostics["error_code"],
                },
                "result_json": {"runtime_diagnostics": diagnostics},
                "diagnostic": None,
                "attempts": [],
            }

        async def append_observation(self, *_args, **_kwargs):
            raise AssertionError("read route must not write diagnostics")

    async def forbidden_get_run(*_args, **_kwargs):
        raise AssertionError("admin detail must not restore private diagnostics")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.runs.infrastructure.admin_queries_postgres.get_admin_run_detail",
        fake_get_admin_run_detail,
    )
    monkeypatch.setattr(
        "app.runs.infrastructure.postgres.get_run",
        forbidden_get_run,
    )
    app = create_app()
    app.state.run_diagnostics_service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=LegacyDiagnosticsPersistence(),
        tool_operation_id=public_tool_operation_id,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version="ai-platform.sdk-runtime-diagnostics.v1",
    )
    client = TestClient(app)

    detail_response = client.get("/api/ai/admin/runs/run_failed", headers=headers())
    diagnostics_response = client.get(
        "/api/ai/admin/runs/run_failed/diagnostics",
        headers=headers(),
    )

    assert detail_response.status_code == 200
    assert "runtime_diagnostics" not in detail_response.json()["run"]["result"]
    assert diagnostics_response.status_code == 200
    projected = diagnostics_response.json()
    assert projected["details"]["sdk"]["api_error_status"] == 429
    assert projected["details"]["observations"][0]["sdk"]["api_error_status"] == 429
    assert "api_error_status" not in str(detail_response.json())
    assert projected["coverage"] == "legacy_record"
    assert projected["root"]["error_code"] == diagnostics["error_code"]
    assert projected["details"]["tool_calls"][0]["is_interrupt"] is True
    assert "is_interrupt" not in str(detail_response.json())
    assert projected["details"]["tool_calls"][0]["operation_id"] == public_tool_operation_id("run_failed", "tool-1")
    assert "tool_input" not in str(projected)
    assert "private-token" not in str(projected)


def test_admin_run_detail_includes_live_queue_context_for_queued_run(monkeypatch):
    async def fake_get_admin_run_detail(conn, *, tenant_id, run_id):
        return {
            "run": {
                "run_id": run_id,
                "session_id": "ses-a",
                "user_id": "user-a",
                "workspace_id": "default",
                "status": "queued",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "input": {"input": {"message": "build feature"}},
                "result": {},
            },
            "events": [],
            "steps": [],
            "artifacts": [],
            "audit": [],
        }

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "default"
        assert run_id == "run_queued"
        return 3

    async def fake_get_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "default"
        assert kwargs == {"include_user_breakdown": True}
        return {
            "tenant_id": tenant_id,
            "reason": "worker_capacity_full",
            "depths": {"tenant_queued": 6, "tenant_processing": 1},
            "workers": {"active": 1},
            "capacity": {"available_worker_slots": 0},
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.runs.infrastructure.admin_queries_postgres.get_admin_run_detail", fake_get_admin_run_detail)
    monkeypatch.setattr("app.routes.admin_runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.get_queue_insight", fake_get_queue_insight, raising=False)
    _install_admin_monitor_metadata(monkeypatch, {})
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runs/run_queued", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["queue_position"] == 3
    assert data["run"]["queue_insight"]["reason"] == "worker_capacity_full"

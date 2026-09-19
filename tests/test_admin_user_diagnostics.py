from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.identity.application.admin_user_diagnostics import (
    AdminUserDiagnosticsService,
)
from app.identity.infrastructure.admin_user_diagnostics_postgres import (
    _list_user_audit,
)
from app.identity.transport.admin_users import build_admin_users_router


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_users(self, **scope):
        self.calls.append(("list", scope))
        return ([self._user()], 1)

    async def get_diagnostics(self, **scope):
        self.calls.append(("detail", scope))
        if scope["user_id"] == "missing-user":
            return None
        return {
            "user": self._user(),
            "sessions": [
                {
                    "session_id": "session-a",
                    "workspace_id": "workspace-a",
                    "agent_id": "agent-a",
                    "status": "active",
                    "purpose": "conversation",
                    "run_count": 2,
                    "failed_run_count": 1,
                }
            ],
            "runs": [
                {
                    "run_id": "run-a",
                    "session_id": "session-a",
                    "workspace_id": "workspace-a",
                    "status": "failed",
                    "agent_id": "agent-a",
                    "execution_kind": "skill",
                    "skill_id": "skill-a",
                    "error_code": "secret-error",
                    "error_message": "secret-message",
                }
            ],
            "audit": [
                {
                    "audit_id": "audit-actor",
                    "actor_user_id": "user-a",
                    "action": "run.read",
                    "target_type": "run",
                    "target_id": "run-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                    "payload_json": {"prompt": "must-not-leak"},
                },
                {
                    "audit_id": "audit-unrelated",
                    "actor_user_id": "other-user",
                    "action": "other.read",
                    "target_type": "other",
                    "target_id": "other-a",
                    "payload_json": {"prompt": "must-not-leak"},
                },
            ],
        }

    @staticmethod
    def _user():
        return {
            "user_id": "user-a",
            "display_name": "secret-name",
            "status": "active",
            "session_count": 1,
            "run_count": 2,
            "failed_run_count": 1,
        }


def _service(store: _Store) -> AdminUserDiagnosticsService:
    return AdminUserDiagnosticsService(
        store,
        sanitize_text=lambda value: str(value).replace("secret", "safe"),
    )


@pytest.mark.asyncio
async def test_admin_user_diagnostics_is_bounded_redacted_and_drops_unproven_audit():
    store = _Store()
    result = await _service(store).get_diagnostics(
        tenant_id="tenant-a",
        user_id="user-a",
        session_limit=10,
        run_limit=20,
        audit_limit=30,
    )

    assert result is not None
    assert result["user"]["display_name"] == "safe-name"
    assert result["runs"][0]["error_message"] == "safe-message"
    assert result["audit"] == [
        {
            "audit_id": "audit-actor",
            "actor_user_id": "user-a",
            "action": "run.read",
            "target_type": "run",
            "target_id": "run-a",
            "relations": ["actor", "run"],
            "run_id": "run-a",
            "session_id": None,
            "trace_id": "trace-a",
            "created_at": None,
        }
    ]
    assert "payload_json" not in str(result)
    assert store.calls == [
        (
            "detail",
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "session_limit": 10,
                "run_limit": 20,
                "audit_limit": 30,
            },
        )
    ]


def test_admin_user_routes_enforce_admin_and_tenant_scope():
    store = _Store()
    principal = SimpleNamespace(tenant_id="tenant-a", user_id="admin-a")
    authorized = {"value": True}

    async def require_principal():
        return principal

    app = FastAPI()
    app.include_router(
        build_admin_users_router(
            service=_service(store),
            principal_dependency=require_principal,
            is_admin=lambda candidate: authorized["value"] and candidate is principal,
        ),
        prefix="/api/ai",
    )

    with TestClient(app) as client:
        listed = client.get("/api/ai/admin/users?search= user &offset=2&limit=10")
        detail = client.get(
            "/api/ai/admin/users/user-a/diagnostics"
            "?session_limit=3&run_limit=4&audit_limit=5"
        )
        missing = client.get("/api/ai/admin/users/missing-user/diagnostics")
        authorized["value"] = False
        denied = client.get("/api/ai/admin/users")

    assert listed.status_code == 200
    assert listed.json()["schema_version"] == "ai-platform.admin-user-diagnostics.v1"
    assert detail.status_code == 200
    assert missing.status_code == 404
    assert missing.json() == {"detail": "user_not_found"}
    assert denied.status_code == 403
    assert store.calls[0] == (
        "list",
        {
            "tenant_id": "tenant-a",
            "search": "user",
            "offset": 2,
            "limit": 10,
        },
    )
    assert store.calls[1][1]["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_postgres_audit_projection_never_selects_payload_and_binds_tenant():
    captured = {}

    class _Cursor:
        async def fetchall(self):
            return []

    class _Connection:
        async def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params
            return _Cursor()

    rows = await _list_user_audit(
        _Connection(),
        tenant_id="tenant-a",
        user_id="user-a",
        limit=25,
    )

    assert rows == []
    assert "payload_json" not in captured["query"]
    assert captured["params"][3] == "tenant-a"
    assert captured["params"][-1] == 25

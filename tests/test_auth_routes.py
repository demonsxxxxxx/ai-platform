from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthPrincipal, is_ai_admin, require_principal, sign_principal_session, verify_principal_session
from app.main import create_app


EXPECTED_COMPANY_USER_PERMISSIONS = [
    "agent:use",
    "chat:read",
    "chat:write",
    "session:read",
    "session:write",
    "skill:read",
    "marketplace:read",
    "mcp:read",
    "avatar:upload",
    "feedback:write",
    "notification:read",
    "artifact:download",
    "file:upload",
    "file:upload:document",
]

EXPECTED_COMPANY_ADMIN_PERMISSIONS = EXPECTED_COMPANY_USER_PERMISSIONS + [
    "model:admin",
    "settings:read",
    "settings:manage",
    "settings:admin",
    "admin:status",
    "skill:write",
    "skill:delete",
    "skill:admin",
    "marketplace:publish",
    "marketplace:admin",
    "mcp:write_sse",
    "mcp:write_http",
    "mcp:write_sandbox",
    "mcp:delete",
    "mcp:admin",
    "user:read",
    "user:write",
    "user:delete",
    "user:admin",
    "role:read",
    "role:manage",
    "feedback:read",
    "feedback:admin",
    "notification:admin",
    "notification:manage",
]


def auth_settings(**overrides):
    values = {
        "ai_session_secret": "test-session-secret-with-at-least-32-bytes",
        "ai_session_max_age_seconds": 28800,
        "ai_session_cookie_name": "ai_platform_session",
        "ai_session_cookie_secure": False,
        "default_tenant_id": "default",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@asynccontextmanager
async def fake_transaction():
    yield object()


@pytest.fixture(autouse=True)
def fake_browser_auth_context(monkeypatch):
    """Keep legacy route tests focused on principal projections, not Redis I/O."""

    from app.auth_sessions import AuthOperation

    operations: dict[str, AuthOperation] = {}
    principals: dict[str, dict[str, object] | None] = {}

    async def fake_bootstrap(
        context_handle,
        _nonce,
        _settings,
        *,
        request_has_matching_context=False,
    ):
        del request_has_matching_context
        principals.setdefault(context_handle, None)
        return "created"

    async def fake_begin(context_handle, kind, _settings):
        previous = operations.get(context_handle)
        operation = AuthOperation(
            context_handle=context_handle,
            epoch=(previous.epoch if previous else 0) + 1,
            token=f"test-operation-{len(operations) + 1}",
            kind=kind,
        )
        operations[context_handle] = operation
        return operation

    async def fake_commit(operation, principal):
        if operations.get(operation.context_handle) != operation:
            return "superseded"
        principals[operation.context_handle] = dict(principal) if principal is not None else None
        return "committed"

    async def fake_principal(context_handle, _settings):
        return principals.get(context_handle)

    monkeypatch.setattr("app.routes.auth.bootstrap_auth_context", fake_bootstrap)
    monkeypatch.setattr("app.routes.auth.begin_auth_operation", fake_begin)
    monkeypatch.setattr("app.routes.auth.commit_auth_operation", fake_commit)
    monkeypatch.setattr("app.auth.principal_for_context", fake_principal)


def browser_client() -> TestClient:
    client = TestClient(create_app(), base_url="https://testserver")
    response = client.post("/api/ai/auth/bootstrap", json={"nonce": "A" * 43})
    assert response.status_code == 200
    return client


def test_ai_admin_maps_admin_and_developer_roles():
    assert is_ai_admin(AuthPrincipal("u1", "Admin", "default", roles=["admin"]))
    assert is_ai_admin(AuthPrincipal("u2", "Dev", "default", roles=["developer"]))
    assert not is_ai_admin(AuthPrincipal("u3", "User", "default", roles=["user"]))


def test_ai_admin_maps_platform_admin_aliases():
    assert is_ai_admin(AuthPrincipal("u1", "Platform Admin", "default", roles=["platform_admin"]))
    assert is_ai_admin(AuthPrincipal("u2", "Break Glass", "default", roles=["break_glass_admin"]))
    assert is_ai_admin(AuthPrincipal("u3", "Legacy Dev", "default", roles=["developer"]))
    assert not is_ai_admin(AuthPrincipal("u4", "Auditor", "default", roles=["auditor"]))
    assert not is_ai_admin(AuthPrincipal("u5", "Tenant Admin", "default", roles=["tenant_admin"]))
    assert not is_ai_admin(AuthPrincipal("u6", "Skill Developer", "default", roles=["skill_developer"]))
    assert not is_ai_admin(AuthPrincipal("u7", "Runtime Operator", "default", roles=["runtime_operator"]))


@pytest.mark.parametrize(
    ("upstream_roles", "expected_role"),
    [
        (["admin"], "admin"),
        ([" ADMIN "], "admin"),
        (["developer"], "admin"),
        (["DeVeLoPeR"], "admin"),
        (["user"], "user"),
        (["tenant_admin"], "user"),
        (["platform_admin"], "user"),
        (["unknown"], "user"),
        ([], "user"),
    ],
)
@pytest.mark.asyncio
async def test_company_login_collapses_upstream_roles_to_one_product_role(
    upstream_roles,
    expected_role,
):
    from app.principal_authority import resolve_login_principal

    async def user_info_adapter(_work_id):
        return {"roles": upstream_roles}

    principal = await resolve_login_principal(
        work_id="synthetic-work-id",
        login_name="synthetic-login",
        display_name="Synthetic User",
        user_info_adapter=user_info_adapter,
        settings=auth_settings(ai_admin_work_ids=""),
    )

    assert principal.roles == [expected_role]


def test_signed_session_roundtrip_preserves_principal(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())

    token = sign_principal_session(
        AuthPrincipal(
            user_id="W001",
            display_name="Zhang San",
            tenant_id="default",
            roles=["user"],
            permissions=["agent:use"],
            source="company-login",
        )
    )
    principal = verify_principal_session(token)

    assert len(token.split(".")) == 3
    assert principal.user_id == "W001"
    assert principal.display_name == "Zhang San"
    assert principal.roles == ["user"]
    assert principal.source == "company-login"


def test_require_principal_accepts_signed_session_bearer(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    token = sign_principal_session(AuthPrincipal("W001", "Zhang San", "default", source="company-login"))
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id, "source": principal.source}

    response = TestClient(app).get("/probe", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "W001", "source": "company-login"}


def test_login_returns_ai_role_without_mutating_context_cookie(monkeypatch):
    async def fake_login(username, password):
        assert username == "dev001"
        assert password == "pw"
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        assert work_id == "dev001"
        return {"roles": ["developer"], "permissions": ["agent:use"]}

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        assert user_id == "dev001"

    async def fake_append_audit_log(conn, **kwargs):
        assert kwargs["action"] == "auth.login"
        assert kwargs["payload_json"]["source"] == "company-login"
        assert kwargs["payload_json"]["work_id"] == "dev001"
        assert kwargs["payload_json"]["roles"] == ["admin"]
        assert "agent:use" in kwargs["payload_json"]["permissions"]
        assert "model:admin" in kwargs["payload_json"]["permissions"]
        assert kwargs["payload_json"]["is_admin"] is True
        return "aud_1"

    settings = auth_settings(ai_session_cookie_secure=True)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", fake_append_audit_log)

    client = browser_client()
    response = client.post("/api/ai/auth/login", json={"user_name": "dev001", "password": "pw"})

    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert response.json()["roles"] == ["admin"]
    assert response.json()["permissions"] == EXPECTED_COMPANY_ADMIN_PERMISSIONS
    assert response.json()["user_id"] == "dev001"
    assert "set-cookie" not in response.headers


def test_company_user_login_gets_baseline_ai_permissions(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        return {
            "workid": work_id,
            "username": None,
            "roles": ["user"],
            "permissions": [],
        }

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = browser_client()
    response = client.post("/api/ai/auth/login", json={"user_name": "user001", "password": "pw"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_admin"] is False
    assert body["roles"] == ["user"]
    assert body["permissions"] == EXPECTED_COMPANY_USER_PERMISSIONS

    me_response = client.get("/api/ai/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["permissions"] == body["permissions"]


def test_company_login_projects_principal_denial_as_existing_safe_failure(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        return {"roles": ["user"]}

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)

    response = browser_client().post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "company_login_failed"}


def _install_company_department_login_fakes(monkeypatch, user_info, *, qa_department_id="qa"):
    from app.routes import skills_marketplace

    async def fake_login(username, password):
        assert username == "user001"
        assert password == "pw"
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        assert work_id == "user001"
        return user_info

    async def noop(*args, **kwargs):
        return None

    async def fake_list_catalog(conn, *, tenant_id, include_disabled=False, rollout_key=None):
        assert tenant_id == "default"
        return [
            {
                "skill_id": "qa-skill",
                "name": "QA Skill",
                "expected_version": "qa-v1",
                "description": "QA only",
                "input_modes": ["chat"],
                "status": "active",
                "source": {},
            },
            {
                "skill_id": "rd-skill",
                "name": "RD Skill",
                "expected_version": "rd-v1",
                "description": "RD only",
                "input_modes": ["chat"],
                "status": "active",
                "source": {},
            },
        ]

    async def fake_list_distributions(conn, *, tenant_id, capability_kind=None, include_disabled=True):
        assert tenant_id == "default"
        assert capability_kind == "skill"
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-skill",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [qa_department_id],
                "allowed_roles": [],
            },
            {
                "capability_kind": "skill",
                "capability_id": "rd-skill",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["rd"],
                "allowed_roles": [],
            },
        ]

    async def fake_list_overlays(conn, *, tenant_id, user_id, skill_ids, include_content=False):
        return []

    settings = auth_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)
    monkeypatch.setattr(skills_marketplace, "transaction", fake_transaction)
    monkeypatch.setattr(skills_marketplace.repositories, "list_public_skill_catalog", fake_list_catalog)
    monkeypatch.setattr(
        skills_marketplace.repositories,
        "list_capability_distribution_rows",
        fake_list_distributions,
    )
    monkeypatch.setattr(
        skills_marketplace.repositories,
        "list_user_skill_file_overlays",
        fake_list_overlays,
    )
    return settings


def test_company_login_trusted_department_reaches_session_and_skill_projection(monkeypatch):
    settings = _install_company_department_login_fakes(
        monkeypatch,
        {"roles": ["user"], "department": " QA "},
        qa_department_id="QA",
    )
    client = browser_client()

    login_response = client.post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw"},
        headers={"X-AI-Department-ID": "rd"},
    )

    assert login_response.status_code == 200
    assert client.get("/api/ai/auth/me").json()["user_id"] == "user001"

    skills_response = client.get("/api/skills/")

    assert skills_response.status_code == 200
    assert [item["skill_name"] for item in skills_response.json()["skills"]] == ["qa-skill"]


def test_company_login_department_authorization_is_case_sensitive(monkeypatch):
    settings = _install_company_department_login_fakes(
        monkeypatch,
        {"roles": ["user"], "department": " QA "},
    )
    client = browser_client()

    login_response = client.post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw"},
    )

    assert login_response.status_code == 200
    assert client.get("/api/ai/auth/me").json()["user_id"] == "user001"

    skills_response = client.get("/api/skills/")

    assert skills_response.status_code == 200
    assert skills_response.json()["skills"] == []


@pytest.mark.parametrize(
    "alias_metadata",
    [
        {"department_id": "QA"},
        {"departmentId": "rd"},
        {"departmentName": "   "},
        {"department_name": None},
    ],
)
def test_company_login_ignores_unsupported_alias_metadata_when_top_level_department_is_valid(
    monkeypatch,
    alias_metadata,
):
    user_info = {"roles": ["user"], "department": " QA ", **alias_metadata}
    settings = _install_company_department_login_fakes(
        monkeypatch,
        user_info,
        qa_department_id="QA",
    )
    client = browser_client()

    login_response = client.post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw"},
    )

    assert login_response.status_code == 200
    assert client.get("/api/ai/auth/me").json()["user_id"] == "user001"

    skills_response = client.get("/api/skills/")

    assert skills_response.status_code == 200
    assert [item["skill_name"] for item in skills_response.json()["skills"]] == ["qa-skill"]


def test_company_login_rejects_client_department_field(monkeypatch):
    _install_company_department_login_fakes(
        monkeypatch,
        {"roles": ["user"], "department": "qa"},
    )

    response = browser_client().post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw", "department": "rd"},
    )

    assert response.status_code == 422
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "user_info",
    [
        {"roles": ["user"]},
        {"roles": ["user"], "department": "   "},
        {"roles": ["user"], "department": 42},
        {"roles": ["user"], "department": ["qa"]},
        {"roles": ["user"], "department": {"id": "qa"}},
        {"roles": ["user"], "department": "qa,rd"},
        {"roles": ["user"], "department_id": "qa"},
        {"roles": ["user"], "departmentId": "qa"},
        {"roles": ["user"], "departmentName": "qa"},
    ],
)
def test_company_login_invalid_or_ambiguous_department_fails_closed(monkeypatch, user_info):
    settings = _install_company_department_login_fakes(monkeypatch, user_info)
    client = browser_client()

    login_response = client.post(
        "/api/ai/auth/login",
        json={"user_name": "user001", "password": "pw"},
        headers={"X-AI-Department-ID": "qa"},
    )

    assert login_response.status_code == 200
    assert client.get("/api/ai/auth/me").json()["user_id"] == "user001"

    skills_response = client.get("/api/skills/")

    assert skills_response.status_code == 200
    assert skills_response.json()["skills"] == []


def test_company_login_does_not_project_large_enterprise_permissions_into_session(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        return {
            "roles": ["user"],
            "permissions": [f"TaskManagement:legacy-permission-{index}" for index in range(3000)],
        }

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = browser_client()
    response = client.post("/api/ai/auth/login", json={"user_name": "user001", "password": "pw"})

    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["user"]
    assert body["permissions"] == EXPECTED_COMPANY_USER_PERMISSIONS
    assert "set-cookie" not in response.headers

    me_response = client.get("/api/ai/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["permissions"] == body["permissions"]


def test_compat_login_projects_user_info_failure_as_existing_safe_failure(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def failing_user_info(work_id):
        raise RuntimeError("user-info unavailable")

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", failing_user_info)

    response = TestClient(create_app()).post("/api/auth/login", json={"username": "user001", "password": "pw"})

    assert response.status_code == 401
    assert response.json() == {"detail": "company_login_failed"}


def test_company_login_admin_allowlist_grants_admin_when_user_info_has_no_roles(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": [], "permissions": []}

    async def noop(*args, **kwargs):
        return None

    settings = auth_settings(ai_admin_work_ids="dev001")
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    body = me_response.json()
    assert body["roles"] == ["admin"]
    assert body["permissions"] == EXPECTED_COMPANY_ADMIN_PERMISSIONS


def test_company_login_admin_allowlist_matches_submitted_username(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "W001", "userName": username, "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": [], "permissions": []}

    async def noop(*args, **kwargs):
        return None

    settings = auth_settings(ai_admin_work_ids="synthetic-admin-login")
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post(
        "/api/auth/login",
        json={"username": "synthetic-admin-login", "password": "synthetic-password"},
    )

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    assert me_response.json()["roles"] == ["admin"]
    assert me_response.json()["permissions"] == EXPECTED_COMPANY_ADMIN_PERMISSIONS


def test_company_unsuccessful_login_status_returns_401(monkeypatch):
    async def fake_login(username, password):
        return {"userName": None, "cnName": None, "workId": None, "status": "unsuccessfully!"}

    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)

    response = TestClient(create_app()).post("/api/auth/login", json={"username": "user001", "password": "bad"})

    assert response.status_code == 401
    assert response.json()["detail"] == "company_login_failed"


def test_company_developer_login_gets_admin_ai_permissions(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": ["developer"], "permissions": ["custom:business"]}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    body = me_response.json()
    assert "custom:business" not in body["permissions"]
    assert body["roles"] == ["admin"]
    assert body["permissions"] == EXPECTED_COMPANY_ADMIN_PERMISSIONS


def test_auth_me_returns_bearer_principal(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    token = sign_principal_session(AuthPrincipal("W002", "Normal User", "default", roles=["user"], source="company-login"))

    response = TestClient(create_app()).get(
        "/api/ai/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "W002"
    assert response.json()["is_admin"] is False


def test_lambchat_auth_aliases_return_bearer_token(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": ["developer"], "permissions": ["agent:use"]}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    assert len(token.split(".")) == 3

    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    assert me_response.json()["id"] == "dev001"


def test_lambchat_oauth_providers_disable_registration():
    response = TestClient(create_app()).get("/api/auth/oauth/providers")

    assert response.status_code == 200
    assert response.json()["providers"] == []
    assert response.json()["registration_enabled"] is False

from types import SimpleNamespace

import httpx
import pytest

from app.principal_authority import (
    AI_ADMIN_PERMISSIONS,
    AI_USER_PERMISSIONS,
    CURRENT_PRINCIPAL_DENIAL_REASON,
    PrincipalAuthorityDenied,
    resolve_current_principal,
    resolve_login_principal,
)


def _settings(**overrides):
    values = {
        "default_tenant_id": "tenant-a",
        "ai_admin_work_ids": "",
        "existing_user_info_base_url": "https://company.test",
        "existing_auth_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _adapter(result):
    async def fetch(work_id):
        assert work_id == "user-a"
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch


async def _resolve(kind, result, *, settings=None, login_name="login-a"):
    if kind == "login":
        return await resolve_login_principal(
            work_id="user-a",
            login_name=login_name,
            display_name="User A",
            user_info_adapter=_adapter(result),
            settings=settings or _settings(),
        )
    return await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(result),
        settings=settings or _settings(),
    )


@pytest.mark.parametrize("optional_alias", [None, "", "   "])
@pytest.mark.asyncio
async def test_login_and_current_principal_accept_the_observed_legacy_company_record(optional_alias):
    company_record = {
        "workid": "user-a",
        "username": optional_alias,
        "roles": ["employee"],
        "department": "研发一部",
    }

    login = await _resolve("login", company_record)
    current = await _resolve("current", company_record)

    assert login.user_id == current.user_id == "user-a"
    assert login.tenant_id == current.tenant_id == "tenant-a"
    assert login.department_id == current.department_id == ""
    assert login.roles == current.roles == ["user"]
    assert login.permissions == current.permissions == list(AI_USER_PERMISSIONS)


@pytest.mark.asyncio
async def test_current_principal_uses_current_identity_roles_department_and_derived_permissions():
    principal = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(
            {
                "workId": "user-a",
                "tenantId": "tenant-a",
                "roles": ["user"],
                "department": " QA ",
                "active": True,
                "permissions": ["upstream:must-not-leak"],
            }
        ),
        settings=_settings(),
    )

    assert principal.user_id == "user-a"
    assert principal.tenant_id == "tenant-a"
    assert principal.department_id == "QA"
    assert principal.roles == ["user"]
    assert principal.permissions == list(AI_USER_PERMISSIONS)
    assert "upstream:must-not-leak" not in principal.permissions


@pytest.mark.asyncio
async def test_current_principal_recomputes_revoked_admin_role_from_the_same_authority():
    admin = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(
            {"workId": "user-a", "roles": ["developer"], "department": "qa", "active": True}
        ),
        settings=_settings(),
    )
    revoked = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(
            {"workId": "user-a", "roles": ["user"], "department": "qa", "active": True}
        ),
        settings=_settings(),
    )

    assert admin.roles == ["admin"]
    assert admin.permissions == [*AI_USER_PERMISSIONS, *AI_ADMIN_PERMISSIONS]
    assert revoked.roles == ["user"]
    assert revoked.permissions == list(AI_USER_PERMISSIONS)


@pytest.mark.parametrize(
    ("department", "expected"),
    [
        ("rd", "rd"),
        ("", ""),
        (None, ""),
        (42, ""),
        (["qa"], ""),
        ("qa,rd", ""),
    ],
)
@pytest.mark.asyncio
async def test_current_principal_uses_changed_or_removed_department(department, expected):
    principal = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(
            {"workId": "user-a", "roles": ["user"], "department": department, "active": True}
        ),
        settings=_settings(),
    )

    assert principal.department_id == expected


@pytest.mark.asyncio
async def test_current_principal_honors_current_configured_admin_identity():
    principal = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter(
            {
                "workId": "user-a",
                "userName": None,
                "roles": [],
                "department": "platform",
                "active": True,
            }
        ),
        settings=_settings(ai_admin_work_ids="USER-A"),
    )

    assert principal.roles == ["admin"]
    assert principal.permissions == [*AI_USER_PERMISSIONS, *AI_ADMIN_PERMISSIONS]


@pytest.mark.asyncio
async def test_current_principal_rejects_missing_eligibility_without_nonempty_roles():
    with pytest.raises(PrincipalAuthorityDenied, match=CURRENT_PRINCIPAL_DENIAL_REASON):
        await resolve_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            user_info_adapter=_adapter({"workId": "user-a", "roles": []}),
            settings=_settings(),
        )


@pytest.mark.parametrize(
    "eligibility",
    [
        pytest.param({"active": False}, id="active-false"),
        pytest.param({"enabled": False}, id="enabled-false"),
        pytest.param({"eligible": False}, id="eligible-false"),
        pytest.param({"active": "true"}, id="active-string"),
        pytest.param({"enabled": 1}, id="enabled-integer"),
        pytest.param({"eligible": None}, id="eligible-null"),
        pytest.param({"status": ["active"]}, id="status-non-string"),
        pytest.param({"status": "pending"}, id="status-unrecognized"),
        pytest.param({"active": True, "enabled": False}, id="mixed-boolean-signals"),
    ],
)
@pytest.mark.asyncio
async def test_current_principal_rejects_false_or_malformed_eligibility_signals(eligibility):
    with pytest.raises(PrincipalAuthorityDenied, match=CURRENT_PRINCIPAL_DENIAL_REASON):
        await resolve_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            user_info_adapter=_adapter({"workId": "user-a", "roles": ["user"], **eligibility}),
            settings=_settings(),
        )


@pytest.mark.parametrize(
    "eligibility",
    [
        pytest.param({"active": True}, id="active"),
        pytest.param({"enabled": True}, id="enabled"),
        pytest.param({"eligible": True}, id="eligible"),
        pytest.param({"status": "active"}, id="status"),
        pytest.param(
            {"active": True, "enabled": True, "eligible": True, "status": "enabled"},
            id="multiple-signals",
        ),
    ],
)
@pytest.mark.asyncio
async def test_current_principal_accepts_valid_eligibility_signals(eligibility):
    principal = await resolve_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        user_info_adapter=_adapter({"workId": "user-a", "roles": ["user"], **eligibility}),
        settings=_settings(),
    )

    assert principal.roles == ["user"]


def _http_failure():
    request = httpx.Request("GET", "https://company.test/api/userManage/user-a/info")
    response = httpx.Response(503, request=request)
    return httpx.HTTPStatusError("upstream detail", request=request, response=response)


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(TimeoutError("timeout detail"), id="timeout"),
        pytest.param(_http_failure(), id="http-failure"),
        pytest.param([{"workId": "user-a"}], id="non-object"),
        pytest.param({"roles": ["user"]}, id="missing-identity"),
        pytest.param({"workId": "user-b", "roles": ["user"]}, id="identity-mismatch"),
        pytest.param(
            {"workId": "user-a", "tenantId": "tenant-b", "roles": ["user"]},
            id="tenant-mismatch",
        ),
        pytest.param({"workId": "user-a", "roles": ["user"], "status": "disabled"}, id="disabled"),
        pytest.param({"workId": "user-a", "roles": ["user"], "eligible": False}, id="ineligible"),
        pytest.param({"workId": "user-a", "roles": {"name": "user"}}, id="malformed-roles"),
    ],
)
@pytest.mark.asyncio
async def test_current_principal_failures_collapse_to_one_stable_denial(result):
    with pytest.raises(PrincipalAuthorityDenied) as exc_info:
        await resolve_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            user_info_adapter=_adapter(result),
            settings=_settings(),
        )

    assert str(exc_info.value) == CURRENT_PRINCIPAL_DENIAL_REASON
    assert exc_info.value.reason == CURRENT_PRINCIPAL_DENIAL_REASON
    assert "upstream detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_current_principal_rejects_non_company_tenant_without_calling_adapter():
    called = False

    async def forbidden_adapter(_work_id):
        nonlocal called
        called = True
        raise AssertionError("tenant mismatch must not call company user-info")

    with pytest.raises(PrincipalAuthorityDenied, match=CURRENT_PRINCIPAL_DENIAL_REASON):
        await resolve_current_principal(
            user_id="user-a",
            tenant_id="tenant-b",
            user_info_adapter=forbidden_adapter,
            settings=_settings(),
        )

    assert called is False


@pytest.mark.parametrize("kind", ["login", "current"])
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(RuntimeError("unavailable"), id="network-failure"),
        pytest.param(_http_failure(), id="endpoint-failure"),
        pytest.param({"roles": ["user"]}, id="missing-workid"),
        pytest.param(
            {"workid": "user-a", "username": "user-b", "roles": ["user"]},
            id="conflicting-nonempty-alias",
        ),
        pytest.param(
            {"workId": "user-a", "workid": "user-b", "roles": ["user"]},
            id="conflicting-workids",
        ),
        pytest.param(
            {"workid": "user-a", "username": None, "roles": ["user"], "enabled": False},
            id="explicitly-disabled",
        ),
        pytest.param(
            {"workid": "user-a", "username": None, "roles": ["user"], "status": "locked"},
            id="explicitly-locked",
        ),
        pytest.param(
            {"workid": "user-a", "username": None, "roles": ["user"], "tenantId": "tenant-b"},
            id="tenant-mismatch",
        ),
    ],
)
@pytest.mark.asyncio
async def test_login_and_current_principal_fail_closed_for_untrusted_company_records(kind, result):
    with pytest.raises(PrincipalAuthorityDenied, match=CURRENT_PRINCIPAL_DENIAL_REASON):
        await _resolve(kind, result)


@pytest.mark.asyncio
async def test_unverified_login_name_cannot_bypass_configured_admin_identity():
    company_record = {
        "workid": "user-a",
        "username": None,
        "roles": ["user"],
        "active": True,
    }
    principal = await _resolve(
        "login",
        company_record,
        login_name="admin-login",
        settings=_settings(ai_admin_work_ids="ADMIN-LOGIN"),
    )

    assert principal.roles == ["user"]
    assert principal.permissions == list(AI_USER_PERMISSIONS)

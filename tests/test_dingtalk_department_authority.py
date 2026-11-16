from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from app.auth import AuthPrincipal, authority_checked_at_now
from app.auth_sessions import _valid_snapshot, principal_snapshot
from app.department_directory import department_parent_ids, normalize_department_directory
from app.identity.api import (
    DepartmentAuthority, department_authority_from_snapshot, resolve_department_scope,
)
from app.identity.infrastructure import dingtalk_departments as adapter
from app.principal_authority import (
    bind_department_authority, issue_refreshed_company_jwt,
    resolve_current_principal, resolve_login_principal,
)


PARENTS = {"100": None, "110": "100", "111": "110", "200": None, "210": "200"}


def test_multiple_direct_departments_include_only_their_ancestors():
    authority = resolve_department_scope(["111", "210", "111"], PARENTS)
    assert authority.direct_department_ids == ("111", "210")
    assert authority.department_ids == ("100", "110", "111", "200", "210")
    assert authority.department_authority_status == "available"
    assert resolve_department_scope(["110"], PARENTS).department_ids == ("100", "110")


@pytest.mark.parametrize("ids,parents", [
    ([], PARENTS), (["unknown"], PARENTS), (["001"], PARENTS),
    (["111", "999"], PARENTS), (["111"], {"111": "110"}),
    (["111"], {"111": "110", "110": "111"}),
    (["111"], {"111": "111"}),
])
def test_invalid_membership_or_ancestor_path_rejects_whole_scope(ids, parents):
    with pytest.raises(ValueError, match="department_authority_invalid"):
        resolve_department_scope(ids, parents)


def test_directory_names_are_display_only_and_same_names_remain_selectable():
    directory = normalize_department_directory([
        {"value": "100", "parentId": "1", "label": "公司 A", "children": [
            {"value": "110", "parentId": "100", "label": "技术", "children": []}]},
        {"value": "200", "parentId": "1", "label": "公司 B", "children": [
            {"value": "210", "parentId": "200", "label": "技术", "children": []}]},
    ])
    assert department_parent_ids(directory) == {"100": None, "110": "100", "200": None, "210": "200"}
    assert directory.departments[0].children[0].authority_id == "110"
    assert directory.departments[1].children[0].authority_id == "210"
    assert all(root.children[0].selectable for root in directory.departments)


def test_legacy_snapshot_never_infers_authority_from_scalar_department():
    assert department_authority_from_snapshot({"department_id": "110"}) == DepartmentAuthority()
    for malformed in (
        {"department_ids": ["110"]},
        {"direct_department_ids": ["110"], "department_ids": ["110"], "department_authority_status": []},
        {"direct_department_ids": ["技术"], "department_ids": ["技术"], "department_authority_status": "available"},
        {"direct_department_ids": ["110"], "department_ids": [], "department_authority_status": "available"},
        {"direct_department_ids": ["110"], "department_ids": ["110"], "department_authority_status": "unavailable"},
    ):
        with pytest.raises(ValueError):
            department_authority_from_snapshot(malformed)


def test_server_snapshot_preserves_ids_and_rejects_malformed_scope():
    scope = resolve_department_scope(["111"], PARENTS)
    principal = AuthPrincipal("CHECK001", "Check", "default", authority_source="company-user-info",
                              authority_checked_at=authority_checked_at_now(), **scope.principal_fields())
    snapshot = principal_snapshot(principal)
    assert _valid_snapshot(snapshot) == snapshot
    assert department_authority_from_snapshot(snapshot) == scope
    assert _valid_snapshot({**snapshot, "department_ids": ["部门名称"]}) is None
    unavailable = principal_snapshot(replace(principal, **DepartmentAuthority().principal_fields()))
    redis_empty = {**unavailable, "direct_department_ids": {}, "department_ids": {}}
    assert _valid_snapshot(redis_empty) == unavailable
    assert _valid_snapshot({**redis_empty, "department_ids": {"110": True}}) is None
    assert _valid_snapshot({**redis_empty, "department_authority_status": "available"}) is None


@pytest.fixture
def department_http(monkeypatch):
    adapter._membership_cache.clear()
    original = httpx.AsyncClient
    observed = []
    result = {"workid": "CHECK001", "department_ids": ["111", "210"]}
    response_status = [200]

    def handle(request):
        observed.append(request)
        return httpx.Response(response_status[0], json=result)

    def client(**kwargs):
        assert kwargs == {"timeout": 1, "trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    yield observed, result, response_status
    adapter._membership_cache.clear()


async def lookup(*, fresh=False, base_url="https://ding.test", reader=None):
    async def directory():
        return PARENTS
    return await adapter.fetch_dingtalk_department_authority(
        "CHECK001", base_url=base_url,
        timeout_seconds=1, directory_reader=reader or directory, fresh=fresh,
    )


@pytest.mark.asyncio
async def test_bootstrap_uses_one_timeout_for_membership_and_its_department_directory(monkeypatch):
    from app.bootstrap import department_authority

    adapter._membership_cache.clear()
    settings = SimpleNamespace(
        dingtalk_department_authority_base_url="https://ding.test",
        dingtalk_department_authority_timeout_seconds=15,
    )
    original = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/DingTalk/employee/CHECK001/department-ids":
            return httpx.Response(200, json={"workid": "CHECK001", "department_ids": ["110"]})
        assert request.url.path == "/api/DingTalk/departs/pure"
        return httpx.Response(200, json=[{
            "value": "110", "parentId": "1", "label": "技术", "children": [],
        }])

    def client(**kwargs):
        assert kwargs == {"timeout": 15, "trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    try:
        authority = await department_authority.resolve_department_authority(
            "CHECK001", settings=settings, fresh=True,
        )
        assert authority.department_authority_status == "available"
        assert authority.department_ids == ("110",)
        assert calls == [
            "/api/DingTalk/employee/CHECK001/department-ids", "/api/DingTalk/departs/pure",
        ]
    finally:
        adapter._membership_cache.clear()


@pytest.mark.asyncio
async def test_readonly_client_binds_identity_without_credentials_and_bounds_cache(department_http, monkeypatch):
    observed, payload, _ = department_http
    scope = await lookup()
    assert scope == resolve_department_scope(["111", "210"], PARENTS)
    assert observed[0].method == "GET"
    assert str(observed[0].url) == "https://ding.test/api/DingTalk/employee/CHECK001/department-ids"
    assert "X-Department-Authority-Key" not in observed[0].headers
    assert "Authorization" not in observed[0].headers
    assert not observed[0].url.query
    payload["department_ids"] = ["111"]
    assert await lookup() == scope
    assert len(observed) == 1
    assert (await lookup(fresh=True)).direct_department_ids == ("111",)
    now = adapter.time.monotonic()
    monkeypatch.setattr(adapter.time, "monotonic", lambda: now + 31)
    assert (await lookup()).direct_department_ids == ("111",)
    assert len(observed) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("latest_fails,earlier_fails", [(False, False), (True, False), (False, True)])
async def test_late_membership_lookup_cannot_replace_or_clear_newer_scope(
    department_http, latest_fails, earlier_fails,
):
    import asyncio

    observed, payload, status = department_http
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_directory():
        entered.set()
        await release.wait()
        if earlier_fails:
            raise RuntimeError("synthetic earlier directory outage")
        return PARENTS

    earlier = asyncio.create_task(lookup(fresh=True, reader=held_directory))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        payload["department_ids"] = ["111"]
        status[0] = 503 if latest_fails else 200
        latest = await lookup(fresh=True)
        release.set()
        assert await asyncio.wait_for(earlier, 1) == DepartmentAuthority()
        if latest_fails:
            assert latest == DepartmentAuthority()
            status[0] = 200
            assert (await lookup()).direct_department_ids == ("111",)
            assert len(observed) == 3
        else:
            assert latest.direct_department_ids == ("111",)
            assert await lookup() == latest
            assert len(observed) == 2
    finally:
        release.set()
        await asyncio.gather(earlier, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["identity", "shape", "unknown-id", "inactive", "outage", "no-base-url", "directory"])
async def test_lookup_failures_never_restore_previous_scope(department_http, failure):
    _, payload, status = department_http
    assert (await lookup()).department_authority_status == "available"
    base_url = "https://ding.test"
    reader = None
    if failure == "identity":
        payload["workid"] = "OTHER001"
    if failure == "shape":
        payload["extra"] = "not-permitted"
    if failure == "unknown-id":
        payload["department_ids"] = ["111", "999"]
    if failure == "inactive":
        status[0] = 404
    if failure == "outage":
        status[0] = 503
    if failure == "no-base-url":
        base_url = ""
    if failure == "directory":
        async def unavailable():
            raise RuntimeError("department_directory_upstream_unavailable")
        reader = unavailable
    assert await lookup(fresh=True, base_url=base_url, reader=reader) == DepartmentAuthority()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["login", "worker", "refresh"])
async def test_all_company_paths_use_department_lookup_not_ad_metadata(kind):
    settings = SimpleNamespace(default_tenant_id="default", ai_admin_work_ids="",
        company_login_jwt_secret="s" * 32, company_login_jwt_issuer="company", company_login_jwt_audience="platform")
    observed = []
    async def user_info(workid):
        assert workid == "CHECK001"
        return {"workid": workid, "username": "check-user", "department": "错误的旧部门", "role": "user"}
    async def departments(workid):
        observed.append(workid)
        return resolve_department_scope(["111", "210"], PARENTS)
    kwargs = {"user_info_adapter": user_info, "department_adapter": departments, "settings": settings}
    if kind == "login":
        principal = await resolve_login_principal(work_id="CHECK001", login_name="submitted-name", display_name="Check", **kwargs)
    elif kind == "worker":
        principal = await resolve_current_principal(user_id="CHECK001", tenant_id="default", **kwargs)
    else:
        principal, _ = await issue_refreshed_company_jwt(AuthPrincipal("CHECK001", "Check", "default"), expires_at=2_000_000_000, **kwargs)
    assert principal.department_id == "111"
    assert principal.direct_department_ids == ("111", "210")
    assert principal.department_ids == ("100", "110", "111", "200", "210")
    assert principal.roles == ["user"]
    assert observed == ["CHECK001"]


@pytest.mark.asyncio
async def test_source_outage_clears_only_department_scope():
    principal = AuthPrincipal("CHECK001", "Check", "default", department_id="旧部门", roles=["admin"],
        direct_department_ids=("111",), department_ids=("100", "110", "111"), department_authority_status="available")
    async def unavailable(_):
        raise httpx.ConnectError("synthetic outage")
    result = await bind_department_authority(principal, department_adapter=unavailable)
    assert result == replace(principal, **DepartmentAuthority().principal_fields())
    assert result.roles == ["admin"]


@pytest.mark.asyncio
async def test_authenticated_request_uses_workid_lookup_and_ignores_department_header(monkeypatch):
    from starlette.requests import Request
    from app.auth import require_principal
    from app.settings import Settings

    settings = Settings(_env_file=None, trusted_principal_secret="synthetic-gateway-key")
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    observed = []
    async def source(work_id, **kwargs):
        observed.append(work_id)
        assert kwargs["fresh"] is False
        return resolve_department_scope(["111"], PARENTS)
    monkeypatch.setattr("app.principal_authority.resolve_department_authority", source)
    request = Request({"type": "http", "headers": [
        (b"x-ai-user-id", b"CHECK001"), (b"x-ai-roles", b"user"),
        (b"x-ai-tenant-id", b"default"), (b"x-ai-department-id", b"210"),
        (b"x-ai-gateway-secret", b"synthetic-gateway-key"),
    ]})
    principal = await require_principal(request)
    assert observed == ["CHECK001"]
    assert principal.department_ids == ("100", "110", "111")
    assert "210" not in principal.department_ids
    assert principal.department_id == "111"

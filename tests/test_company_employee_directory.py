"""Company work IDs, not local login records or DingTalk user IDs, grant access."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.identity.api import CompanyEmployeeDirectoryService, CompanyEmployeeDirectoryUnavailable
from app.identity.infrastructure import dingtalk_employees as adapter
from app.identity.transport.company_employees import build_company_employees_router


def employee(work_id="E001", **overrides):
    return {
        "userId": f"ding-{work_id}", "username": f"Employee {work_id}",
        "mobile": "private-mobile", "workid": work_id, "departId": "123",
        "addDate": "2026-10-01T00:00:00", "unionid": "private-union",
        "avatar": "private-avatar", "title": None, "leader": None,
        "status": "正式员工", "email": "private@example.test", **overrides,
    }


def install_http(monkeypatch, response):
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == "https://directory.example/api/DingTalk/getUserDetail"
        calls.append(request)
        return response() if callable(response) else response

    def client(**kwargs):
        assert kwargs == {"timeout": 5, "trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    return calls


async def read_employees():
    return await adapter.fetch_dingtalk_employees(base_url="https://directory.example", timeout_seconds=5)


@pytest.mark.asyncio
async def test_directory_fetch_exact_contract_and_minimal_projection(monkeypatch):
    install_http(monkeypatch, httpx.Response(200, json=[employee(workid=" E001 ")]))
    service = CompanyEmployeeDirectoryService(read_employees, tenant_id="default")
    assert await service.list_employees(tenant_id="default") == {
        "employees": [{"work_id": "E001", "display_name": "Employee E001"}],
        "total": 1, "offset": 0, "limit": 50,
    }
    await service.validate_grants(tenant_id="default", work_ids={"E001"})
    for invalid in ("ding-E001", "123", "missing"):
        with pytest.raises(ValueError, match="model_user_access_unknown_user"):
            await service.validate_grants(tenant_id="default", work_ids={invalid})


@pytest.mark.parametrize("status", ["离职", "", None, "active", "10", "未知"])
def test_inactive_or_unknown_status_is_not_selectable(status):
    assert adapter.parse_dingtalk_employees([employee(status=status)]) == []


def test_duplicate_and_ambiguous_identities_fail_closed():
    rows = [employee(), employee(), employee("trial", status="试用期"),
            employee("", userId="blank"), employee("bad id"),
            employee("conflict"), employee("conflict", userId="other"),
            employee("name-conflict"), employee("name-conflict", username="Other"),
            employee("status-conflict"), employee("status-conflict", status="离职"),
            employee("shared-a", userId="shared"), employee("shared-b", userId="shared"),
            employee("nameless", username=None)]
    assert [item.work_id for item in adapter.parse_dingtalk_employees(rows)] == ["E001", "trial"]


@pytest.mark.parametrize("payload", [{"users": []}, [None], [{"workid": "E001"}], [employee(workid=123)], [employee(status=10)]])
def test_wrong_wire_format_is_unavailable(payload):
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        adapter.parse_dingtalk_employees(payload)


@pytest.mark.asyncio
async def test_server_pagination_beyond_100_and_literal_search(monkeypatch):
    install_http(monkeypatch, httpx.Response(200, json=[employee(f"E{i:03}") for i in range(205)]))
    service = CompanyEmployeeDirectoryService(read_employees, tenant_id="default")
    result = await service.list_employees(tenant_id="default", offset=100, limit=100)
    assert result["total"] == 205 and len(result["employees"]) == 100
    assert result["employees"][0]["work_id"] == "E100"
    assert (await service.list_employees(tenant_id="default", search="employee e204"))["total"] == 1
    for query in ("%", "_", ".*"):
        assert (await service.list_employees(tenant_id="default", search=query))["total"] == 0


@pytest.mark.asyncio
async def test_bounded_response_and_record_count(monkeypatch):
    monkeypatch.setattr(adapter, "_MAX_RESPONSE_BYTES", 10)
    install_http(monkeypatch, httpx.Response(200, content=b" " * 11))
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        await read_employees()
    monkeypatch.setattr(adapter, "_MAX_EMPLOYEES", 1)
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        adapter.parse_dingtalk_employees([employee(), employee("E002")])


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://other.example"}), httpx.Response(503), httpx.Response(200, content=b"private-invalid-json")])
async def test_upstream_error_is_safe_and_retryable(monkeypatch, response):
    responses = iter([response, httpx.Response(200, json=[employee()])])
    calls = install_http(monkeypatch, lambda: next(responses))
    with pytest.raises(CompanyEmployeeDirectoryUnavailable, match="^company_employee_directory_unavailable$"):
        await read_employees()
    assert (await read_employees())[0].work_id == "E001"
    assert len(calls) == 2


def test_admin_and_tenant_scope_ignore_client_tenant_and_hide_private_fields():
    calls = []

    async def reader():
        calls.append(True)
        return adapter.parse_dingtalk_employees([employee()])

    principal = SimpleNamespace(tenant_id="default", admin=True)
    app = FastAPI()
    app.include_router(build_company_employees_router(
        service=CompanyEmployeeDirectoryService(reader, tenant_id="default"),
        principal_dependency=lambda: principal, is_admin=lambda actor: actor.admin,
    ))
    with TestClient(app) as client:
        response = client.get("/admin/company-employees?tenant_id=foreign")
        assert response.status_code == 200
        assert set(response.json()["employees"][0]) == {"work_id", "display_name"}
        assert "private" not in json.dumps(response.json())
        for query in ("limit=101", "offset=50001", "offset=-1", "search=" + "x" * 129):
            assert client.get("/admin/company-employees?" + query).status_code == 422
        principal.admin = False
        assert client.get("/admin/company-employees").status_code == 403
        principal.admin = True
        principal.tenant_id = "foreign"
        assert client.get("/admin/company-employees?tenant_id=default").status_code == 403
        assert len(calls) == 1


def test_transport_unavailable_is_retryable_503():
    async def reader():
        raise CompanyEmployeeDirectoryUnavailable()

    app = FastAPI()
    app.include_router(build_company_employees_router(
        service=CompanyEmployeeDirectoryService(reader, tenant_id="default"),
        principal_dependency=lambda: SimpleNamespace(tenant_id="default"), is_admin=lambda _: True,
    ))
    with TestClient(app) as client:
        response = client.get("/admin/company-employees")
        assert response.status_code == 503
        assert response.json() == {"detail": "company_employee_directory_unavailable"}


@pytest.mark.asyncio
async def test_bootstrap_empty_directory_base_fails_closed_without_http(monkeypatch):
    from app.bootstrap import identity

    monkeypatch.setattr(identity, "get_settings", lambda: SimpleNamespace(
        default_tenant_id="default", dingtalk_department_authority_base_url="",
        dingtalk_department_authority_timeout_seconds=5,
    ))

    def no_http(**_kwargs):
        raise AssertionError("empty configuration must not guess a directory host")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", no_http)
    service = identity.build_company_employee_directory()
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        await service.list_employees(tenant_id="default")
    with pytest.raises(CompanyEmployeeDirectoryUnavailable):
        await service.validate_grants(tenant_id="default", work_ids={"E001"})

"""Provision verified employees without issuing credentials or login sessions."""
from __future__ import annotations

from typing import Any, Protocol

from .company_employees import CompanyEmployee, CompanyEmployeeDirectoryService


class CompanyEmployeeIdentityStore(Protocol):
    async def ensure_employee(self, conn: Any, *, tenant_id: str, work_id: str, display_name: str) -> str: ...


class CompanyEmployeeProvisioningService:
    def __init__(self, directory: CompanyEmployeeDirectoryService, store: CompanyEmployeeIdentityStore) -> None:
        self._directory = directory
        self._store = store

    async def resolve(self, *, tenant_id: str, work_ids: set[str]) -> list[CompanyEmployee]:
        return await self._directory.resolve_grants(tenant_id=tenant_id, work_ids=work_ids)

    async def provision(self, conn: Any, *, tenant_id: str, employees: list[CompanyEmployee]) -> dict[str, str]:
        return {employee.work_id: await self._store.ensure_employee(
            conn, tenant_id=tenant_id, work_id=employee.work_id, display_name=employee.display_name,
        ) for employee in sorted(employees, key=lambda item: item.work_id)}

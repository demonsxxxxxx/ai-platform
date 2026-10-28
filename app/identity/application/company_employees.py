"""Identity-owned company employee directory, independent of local login records."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class CompanyEmployeeDirectoryUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("company_employee_directory_unavailable")


@dataclass(frozen=True)
class CompanyEmployee:
    work_id: str
    display_name: str
    department_ids: tuple[str, ...] = ()


class CompanyEmployeeDirectoryService:
    def __init__(
        self, reader: Callable[[], Awaitable[list[CompanyEmployee]]], *, tenant_id: str,
    ) -> None:
        self._reader = reader
        self._tenant_id = tenant_id

    def _require_scope(self, tenant_id: str) -> None:
        if tenant_id != self._tenant_id:
            raise PermissionError("company_employee_directory_forbidden")

    async def list_employees(
        self, *, tenant_id: str, search: str = "", offset: int = 0, limit: int = 50,
        department_id: str = "",
    ) -> dict:
        self._require_scope(tenant_id)
        if (not 0 <= offset <= 50_000 or not 1 <= limit <= 100 or len(search) > 128
                or (department_id and (not department_id.isascii() or not department_id.isdecimal()
                                      or department_id.startswith("0") or len(department_id) > 10))):
            raise ValueError("company_employee_directory_query_invalid")
        employees = await self._reader()
        query = search.strip().casefold()
        matches = [employee for employee in employees
                   if (not department_id or department_id in employee.department_ids)
                   and (not query or query in employee.work_id.casefold()
                        or query in employee.display_name.casefold())]
        return {
            "employees": [{"work_id": item.work_id, "display_name": item.display_name}
                          for item in matches[offset:offset + limit]],
            "total": len(matches), "offset": offset, "limit": limit,
        }

    async def validate_grants(self, *, tenant_id: str, work_ids: set[str]) -> None:
        self._require_scope(tenant_id)
        if work_ids and not work_ids.issubset({item.work_id for item in await self._reader()}):
            raise ValueError("model_user_access_unknown_user")

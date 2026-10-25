"""Bounded adapter for the company's unpaginated DingUser directory."""

import json
import re

import httpx

from app.identity.application.company_employees import (
    CompanyEmployee, CompanyEmployeeDirectoryUnavailable,
)

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_EMPLOYEES = 50_000
_WORK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-]{0,127}\Z")


def parse_dingtalk_employees(payload: object) -> list[CompanyEmployee]:
    if not isinstance(payload, list) or len(payload) > _MAX_EMPLOYEES:
        raise CompanyEmployeeDirectoryUnavailable()
    records: dict[str, set[tuple[str, str, str | None]]] = {}
    ids: dict[str, set[str]] = {}
    for row in payload:
        if not isinstance(row, dict) or not {"userId", "username", "workid", "status"} <= row.keys():
            raise CompanyEmployeeDirectoryUnavailable()
        if any(row[key] is not None and not isinstance(row[key], str)
               for key in ("userId", "username", "workid", "status")):
            raise CompanyEmployeeDirectoryUnavailable()
        work_id = (row["workid"] or "").strip()
        user_id = (row["userId"] or "").strip()
        name = (row["username"] or "").strip()
        status = row["status"]
        ids.setdefault(user_id, set()).add(work_id)
        records.setdefault(work_id, set()).add((user_id, name, status))
    employees = []
    for work_id, identities in sorted(records.items()):
        if not _WORK_ID.fullmatch(work_id) or ".." in work_id or len(identities) != 1:
            continue
        user_id, name, status = next(iter(identities))
        if (not user_id or len(user_id) > 256 or len(ids[user_id]) != 1
                or not name or len(name) > 160 or any(ord(c) < 32 for c in name)
                or status not in {"正式员工", "试用期"}):
            continue
        employees.append(CompanyEmployee(work_id=work_id, display_name=name))
    return employees


async def fetch_dingtalk_employees(*, base_url: str, timeout_seconds: float) -> list[CompanyEmployee]:
    if not base_url:
        raise CompanyEmployeeDirectoryUnavailable()
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds, trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream("GET", f"{base_url.rstrip('/')}/api/DingTalk/getUserDetail") as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise CompanyEmployeeDirectoryUnavailable()
                    body.extend(chunk)
        return parse_dingtalk_employees(json.loads(body))
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        # Upstream bodies can contain private employee data; never expose errors or payloads.
        raise CompanyEmployeeDirectoryUnavailable() from None

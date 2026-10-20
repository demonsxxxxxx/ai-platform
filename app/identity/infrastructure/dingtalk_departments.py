"""Bounded read-only adapter for employee DingTalk memberships."""

from collections.abc import Awaitable, Callable
import json
import time
from urllib.parse import quote

import httpx

from app.identity.application.department_authority import DepartmentAuthority, resolve_department_scope

_DEPARTMENT_CACHE_TTL_SECONDS = 30.0
_MAX_DEPARTMENT_CACHE_ENTRIES = 4096
_membership_cache: dict[tuple[str, str], tuple[float, DepartmentAuthority]] = {}


async def fetch_dingtalk_department_authority(
    work_id: str, *, base_url: str, timeout_seconds: float,
    directory_reader: Callable[[], Awaitable[dict[str, str | None]]], fresh: bool = False,
) -> DepartmentAuthority:
    if not base_url:
        return DepartmentAuthority()
    key = (base_url, work_id)
    cached = _membership_cache.get(key)
    if not fresh and cached and cached[0] > time.monotonic():
        return cached[1]
    if len(_membership_cache) >= _MAX_DEPARTMENT_CACHE_ENTRIES:
        _membership_cache.clear()
    pending = (0.0, DepartmentAuthority())
    _membership_cache[key] = pending
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False, follow_redirects=False) as client:
            url = f"{base_url.rstrip('/')}/api/DingTalk/employee/{quote(work_id, safe='')}/department-ids"
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 16 * 1024:
                        raise ValueError("department_authority_invalid")
                    body.extend(chunk)
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {"workid", "department_ids"} or payload["workid"] != work_id:
            raise ValueError("department_authority_invalid")
        ids = payload["department_ids"]
        if not isinstance(ids, list):
            raise ValueError("department_authority_invalid")
        result = resolve_department_scope(ids, await directory_reader())
    except (httpx.HTTPError, ValueError, UnicodeError, RuntimeError):
        # An outage never extends the previous membership or falls back to names.
        if _membership_cache.get(key) is pending:
            _membership_cache.pop(key, None)
        return DepartmentAuthority()
    # A newer lookup owns the cache even if it denied or is still pending.
    if _membership_cache.get(key) is not pending:
        return DepartmentAuthority()
    _membership_cache[key] = (time.monotonic() + _DEPARTMENT_CACHE_TTL_SECONDS, result)
    return result

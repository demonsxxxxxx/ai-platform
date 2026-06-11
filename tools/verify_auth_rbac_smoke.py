#!/usr/bin/env python3
"""Verify basic auth/RBAC/redaction behavior for the 211 Foundation Alpha POC."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


SCHEMA_VERSION = "ai-platform.auth-rbac-smoke.v1"
ADMIN_RUNTIME_ROUTE = "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false"
FORBIDDEN_PROJECTION_TERMS = (
    "executor_private_payload",
    "runtime_private_payload",
    "private_payload",
    "raw_storage_key",
    "storage_key",
    "sandbox_workdir",
    "api_key",
    "database_url",
    "redis_url",
    "bearer ",
    "sk-",
)
REQUIRED_ADMIN_RUNTIME_SECTIONS = (
    "tenant_id",
    "queue",
    "sandbox",
    "observability",
    "capacity",
    "governance",
    "database_pool",
    "backpressure",
)


def sanitize_base_url(value: str) -> str:
    raw = str(value or "http://127.0.0.1:8020").strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return "http://127.0.0.1:8020"
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _request_json(url: str, *, headers: dict[str, str] | None = None, timeout_seconds: float = 10.0) -> tuple[int, Any]:
    request = Request(url, headers={"Accept": "application/json", **(headers or {})}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8")) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: Any = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = raw.decode("utf-8", errors="replace")[:200]
        return int(exc.code), payload
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def _principal_headers(*, user_id: str, roles: str, tenant_id: str, gateway_secret: str) -> dict[str, str]:
    headers = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Tenant-ID": tenant_id,
        "X-AI-Roles": roles,
    }
    if gateway_secret:
        headers["X-AI-Gateway-Secret"] = gateway_secret
    return headers


def _detail(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("error") or "")
    return str(payload or "")[:120]


def _contains_forbidden_projection_term(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    return any(term in text for term in FORBIDDEN_PROJECTION_TERMS)


def _admin_runtime_summary(payload: Any) -> dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    missing_sections = [section for section in REQUIRED_ADMIN_RUNTIME_SECTIONS if section not in body]
    queue = body.get("queue") if isinstance(body.get("queue"), dict) else {}
    tenant_insight = queue.get("tenant_insight") if isinstance(queue.get("tenant_insight"), dict) else {}
    capacity = tenant_insight.get("capacity") if isinstance(tenant_insight.get("capacity"), dict) else {}
    sandbox = body.get("sandbox") if isinstance(body.get("sandbox"), dict) else {}
    leases = sandbox.get("leases") if isinstance(sandbox.get("leases"), dict) else {}
    return {
        "required_sections_present": not missing_sections,
        "missing_sections": missing_sections,
        "tenant_id": str(body.get("tenant_id") or ""),
        "queue_lease_scan_limit": capacity.get("queue_lease_scan_limit"),
        "sandbox_active_leases": leases.get("active"),
        "forbidden_projection_terms_present": _contains_forbidden_projection_term(payload),
    }


def build_auth_rbac_smoke(
    *,
    base_url: str,
    gateway_secret: str,
    commit_sha: str = "unknown",
    image: str = "",
    tenant_id: str = "default",
    ordinary_user_id: str = "poc-ordinary-smoke",
    admin_user_id: str = "poc-admin-smoke",
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    safe_base_url = sanitize_base_url(base_url)
    auth_me_status, auth_me_payload = _request_json(
        f"{safe_base_url}/api/auth/me",
        timeout_seconds=timeout_seconds,
    )
    ordinary_status, ordinary_payload = _request_json(
        f"{safe_base_url}{ADMIN_RUNTIME_ROUTE}",
        headers=_principal_headers(
            user_id=ordinary_user_id,
            roles="user",
            tenant_id=tenant_id,
            gateway_secret=gateway_secret,
        ),
        timeout_seconds=timeout_seconds,
    )
    admin_status, admin_payload = _request_json(
        f"{safe_base_url}{ADMIN_RUNTIME_ROUTE}",
        headers=_principal_headers(
            user_id=admin_user_id,
            roles="admin",
            tenant_id=tenant_id,
            gateway_secret=gateway_secret,
        ),
        timeout_seconds=timeout_seconds,
    )
    admin_summary = _admin_runtime_summary(admin_payload)
    checks = {
        "unauthenticated_auth_me": {
            "route": "/api/auth/me",
            "status": auth_me_status,
            "detail": _detail(auth_me_payload),
            "expected_status": 401,
        },
        "ordinary_admin_runtime": {
            "route": ADMIN_RUNTIME_ROUTE,
            "status": ordinary_status,
            "detail": _detail(ordinary_payload),
            "expected_status": 403,
        },
        "admin_runtime": {
            "route": ADMIN_RUNTIME_ROUTE,
            "status": admin_status,
            "expected_status": 200,
            **admin_summary,
        },
    }
    ok = (
        auth_me_status == 401
        and checks["unauthenticated_auth_me"]["detail"] == "missing_authenticated_principal"
        and ordinary_status == 403
        and checks["ordinary_admin_runtime"]["detail"] == "not_ai_admin"
        and admin_status == 200
        and bool(admin_summary["required_sections_present"])
        and not bool(admin_summary["forbidden_projection_terms_present"])
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "source": {
            "base_url": safe_base_url,
            "commit_sha": commit_sha,
            "image": image,
            "tenant_id": tenant_id,
            "gateway_secret_supplied": bool(gateway_secret),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify basic auth/RBAC/redaction behavior for ai-platform.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", "http://127.0.0.1:8020"))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--ordinary-user-id", default="poc-ordinary-smoke")
    parser.add_argument("--admin-user-id", default="poc-admin-smoke")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    evidence = build_auth_rbac_smoke(
        base_url=args.base_url,
        gateway_secret=os.environ.get(args.gateway_secret_env, ""),
        commit_sha=args.commit_sha,
        image=args.image,
        tenant_id=args.tenant_id,
        ordinary_user_id=args.ordinary_user_id,
        admin_user_id=args.admin_user_id,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

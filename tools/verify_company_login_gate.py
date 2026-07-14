#!/usr/bin/env python3
"""Verify the company-login gate for the ai-platform POC.

Credentials can be read from environment variables, or the password can be
entered interactively without echoing it:

  AI_PLATFORM_LOGIN_USERNAME=<work-id>
  AI_PLATFORM_LOGIN_PASSWORD=<password>
  AI_PLATFORM_EXPECTED_WORK_ID=<work-id returned by company login, optional>

  .venv/bin/python tools/verify_company_login_gate.py \
    --username <work-id> --prompt-password --expect-user

The script prints redacted evidence for:
- LambChat-compatible login token issuance.
- /api/auth/me principal projection.
- PostgreSQL audit_logs auth.login payload.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "http://127.0.0.1:8020"
DEFAULT_POSTGRES_CONTAINER = "ai-platform-postgres"
DEFAULT_POSTGRES_USER = "ai_platform"
DEFAULT_POSTGRES_DB = "ai_platform"


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def redact_user_id(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return f"{value[:1]}***"
    return f"{value[:2]}***{value[-2:]}"


def request_json(method: str, url: str, *, payload: dict[str, Any] | None = None, token: str = "") -> HttpResult:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=20) as response:
            return HttpResult(response.status, dict(response.headers.items()), response.read())
    except error.HTTPError as exc:
        return HttpResult(exc.code, dict(exc.headers.items()), exc.read())


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def credential_value(cli_value: str | None, env_name: str) -> str:
    value = (cli_value or os.environ.get(env_name, "")).strip()
    if not value:
        raise RuntimeError(f"missing required credential: pass --{env_name.lower().replace('ai_platform_login_', '').replace('_', '-')} or set {env_name}")
    return value


def password_value(*, prompt_password: bool) -> str:
    if prompt_password:
        value = getpass.getpass("AI Platform login password: ")
        if not value:
            raise RuntimeError("missing required credential: password prompt was empty")
        return value
    return require_env("AI_PLATFORM_LOGIN_PASSWORD")


def run_psql_json(container: str, db_user: str, db_name: str, sql: str) -> list[dict[str, Any]]:
    command = [
        "sudo",
        "-n",
        "docker",
        "exec",
        container,
        "psql",
        "-U",
        db_user,
        "-d",
        db_name,
        "-t",
        "-A",
        "-F",
        "\t",
        "-c",
        sql,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "psql audit query failed")
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"unexpected psql JSON row: {raw}") from exc
    return rows


def latest_audit_payload(container: str, db_user: str, db_name: str, user_id: str, min_epoch: int) -> dict[str, Any] | None:
    escaped_user = user_id.replace("'", "''")
    sql = f"""
select json_build_object(
  'created_at', created_at,
  'user_id', user_id,
  'action', action,
  'target_type', target_type,
  'target_id', target_id,
  'payload_json', payload_json
)::text
from audit_logs
where action = 'auth.login'
  and user_id = '{escaped_user}'
  and extract(epoch from created_at) >= {int(min_epoch)}
order by created_at desc
limit 1;
"""
    rows = run_psql_json(container, db_user, db_name, sql)
    return rows[0] if rows else None


def assert_contains(values: list[str], expected: str, label: str) -> None:
    if expected not in values:
        raise RuntimeError(f"{label} missing expected value: {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify real company login, principal projection, and audit log.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--postgres-container", default=os.environ.get("AI_PLATFORM_POSTGRES_CONTAINER", DEFAULT_POSTGRES_CONTAINER))
    parser.add_argument("--postgres-user", default=os.environ.get("AI_PLATFORM_POSTGRES_USER", DEFAULT_POSTGRES_USER))
    parser.add_argument("--postgres-db", default=os.environ.get("AI_PLATFORM_POSTGRES_DB", DEFAULT_POSTGRES_DB))
    parser.add_argument("--expect-admin", action="store_true", help="Require the login principal to have admin capability.")
    parser.add_argument("--expect-user", action="store_true", help="Require the login principal to be a non-admin ordinary user.")
    parser.add_argument("--skip-audit", action="store_true", help="Skip the docker/psql audit query.")
    parser.add_argument("--username", help="Company login work-id/user name. Defaults to AI_PLATFORM_LOGIN_USERNAME.")
    parser.add_argument("--expected-work-id", help="Expected work-id returned by company login. Defaults to username or AI_PLATFORM_EXPECTED_WORK_ID.")
    parser.add_argument("--prompt-password", action="store_true", help="Prompt for the password without echoing it instead of reading AI_PLATFORM_LOGIN_PASSWORD.")
    args = parser.parse_args()
    if args.expect_admin and args.expect_user:
        raise RuntimeError("--expect-admin and --expect-user are mutually exclusive")

    username = credential_value(args.username, "AI_PLATFORM_LOGIN_USERNAME")
    password = password_value(prompt_password=args.prompt_password)
    expected_work_id = (args.expected_work_id or os.environ.get("AI_PLATFORM_EXPECTED_WORK_ID", username)).strip() or username
    base_url = args.base_url.rstrip("/")
    started_epoch = int(time.time()) - 5

    login = request_json(
        "POST",
        f"{base_url}/api/auth/login",
        payload={"username": username, "password": password},
    )
    if login.status_code != 200:
        detail = login.body.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"login failed with HTTP {login.status_code}: {detail}")
    login_payload = login.json()
    token = str(login_payload.get("access_token") or "")
    if len(token.split(".")) != 3:
        raise RuntimeError("login response did not return a signed bearer token")

    me = request_json("GET", f"{base_url}/api/auth/me", token=token)
    if me.status_code != 200:
        detail = me.body.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"/api/auth/me failed with HTTP {me.status_code}: {detail}")
    principal = me.json()
    principal_id = str(principal.get("id") or principal.get("user_id") or "")
    if principal_id != expected_work_id:
        raise RuntimeError("/api/auth/me returned a different principal id")
    roles = [str(item) for item in principal.get("roles") or []]
    permissions = [str(item) for item in principal.get("permissions") or []]
    assert_contains(permissions, "agent:use", "permissions")
    normalized_roles = {role.lower() for role in roles}
    is_admin = bool("admin:status" in permissions and normalized_roles.intersection({"admin", "developer"}))
    if args.expect_admin and not is_admin:
        raise RuntimeError("expected admin login, but admin role or admin:status permission is missing")
    if args.expect_user and is_admin:
        raise RuntimeError("expected ordinary user login, but admin capability was present")

    evidence: dict[str, Any] = {
        "login_http": login.status_code,
        "me_http": me.status_code,
        "user": redact_user_id(expected_work_id),
        "roles": roles,
        "permissions_checked": ["agent:use"] + (["admin:status"] if args.expect_admin else []),
        "is_admin": is_admin,
    }

    if not args.skip_audit:
        audit = latest_audit_payload(args.postgres_container, args.postgres_user, args.postgres_db, expected_work_id, started_epoch)
        if audit is None:
            raise RuntimeError("no matching auth.login audit record found")
        payload = audit.get("payload_json") or {}
        if payload.get("source") != "company-login":
            raise RuntimeError("audit payload source is not company-login")
        if payload.get("work_id") != expected_work_id:
            raise RuntimeError("audit payload work_id does not match login user")
        audit_permissions = [str(item) for item in payload.get("permissions") or []]
        assert_contains(audit_permissions, "agent:use", "audit permissions")
        if args.expect_admin and not payload.get("is_admin"):
            raise RuntimeError("expected admin audit payload, but is_admin is false")
        if args.expect_user and payload.get("is_admin"):
            raise RuntimeError("expected ordinary user audit payload, but is_admin is true")
        evidence["audit"] = {
            "action": audit.get("action"),
            "user": redact_user_id(str(audit.get("user_id") or "")),
            "source": payload.get("source"),
            "is_admin": payload.get("is_admin"),
            "permissions_checked": ["agent:use"] + (["admin:status"] if args.expect_admin else []),
        }

    print(json.dumps({"ok": True, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)

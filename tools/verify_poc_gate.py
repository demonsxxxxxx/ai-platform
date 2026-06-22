#!/usr/bin/env python3
"""Aggregate verification for the ai-platform + LambChat POC on 211.

The script is intentionally evidence-oriented. It does not claim completion when
the real company-login audit gate is missing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.public_context_keys import public_context_input_key_findings, safe_public_context_pack_version
from app.validation import assert_safe_id


DEFAULT_FRONTEND_URL = "http://127.0.0.1:18001"
DEFAULT_API_URL = "http://127.0.0.1:8020"
DEFAULT_FRONTEND_DIST = "/home/xinlin.jiang/lambchat-poc/frontend-dist-ai-platform"
DEFAULT_DEPLOY_ENV = "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform/.env"
DEFAULT_API_CONTAINER = "ai-platform-api"
DEFAULT_POSTGRES_CONTAINER = "ai-platform-postgres"
DEFAULT_POSTGRES_USER = "ai_platform"
DEFAULT_POSTGRES_DB = "ai_platform"
REQUIRED_UI_PERMISSIONS = {"agent:use", "artifact:download", "agent:admin", "model:admin", "settings:manage", "admin:status"}
RUNTIME_ENV_ALLOWLIST = frozenset(
    {
        "CLAUDE_AGENT_SDK_ENABLED",
        "CLAUDE_AGENT_MODEL",
        "DEFAULT_MODEL_ID",
        "MODEL_CATALOG_JSON",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "CLAUDE_AGENT_SDK_SKILLS",
        "EXISTING_AUTH_BASE_URL",
    }
)
PREVIEW_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
CONTEXT_PUBLIC_REQUIRED_COUNTS = (
    "message_count",
    "file_count",
    "artifact_count",
    "memory_record_count",
)
CONTEXT_RAW_MATERIAL_ID_KEYS = frozenset(
    {
        "message_id",
        "message_ids",
        "file_id",
        "file_ids",
        "artifact_id",
        "artifact_ids",
        "memory_record_id",
        "memory_record_ids",
        "memory_id",
        "memory_ids",
        "material_id",
        "material_ids",
        "raw_material_id",
        "raw_material_ids",
        "source_file_id",
        "source_file_ids",
        "included_message_ids",
        "included_file_ids",
        "included_artifact_ids",
        "included_memory_record_ids",
    }
)
CONTEXT_FORBIDDEN_PROJECTION_MARKERS = (
    "executor_private_payload",
    "executor_payload",
    "runtime_private_payload",
    "private_payload",
    "raw_storage_key",
    "storage_key",
    "sandbox_workdir",
    "/tmp/",
    "/home/",
    "/var/lib/ai-platform",
    "tenants/default",
)
CONTEXT_FORBIDDEN_PROJECTION_KEY_ALIASES = frozenset(
    {
        "executor_private_payload",
        "executor_payload",
        "runtime_private_payload",
        "private_payload",
        "raw_storage_key",
        "storage_key",
        "sandbox_workdir",
    }
)


@dataclass(frozen=True)
class Gate:
    name: str
    ok: bool
    evidence: dict[str, Any]


def http_get(url: str, timeout: float = 15.0) -> tuple[int, bytes]:
    req = request.Request(url, headers={"Accept": "application/json,text/html,*/*"}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def http_get_with_headers(url: str, headers: dict[str, str], timeout: float = 15.0) -> tuple[int, bytes]:
    status, body, _ = http_get_with_headers_and_response_headers(url, headers, timeout=timeout)
    return status, body


def http_get_with_headers_and_response_headers(
    url: str,
    headers: dict[str, str],
    timeout: float = 15.0,
) -> tuple[int, bytes, dict[str, str]]:
    req = request.Request(url, headers={"Accept": "*/*", **headers}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def http_json_get_with_headers(
    url: str,
    headers: dict[str, str],
    timeout: float = 15.0,
) -> tuple[int, Any]:
    status, body = http_get_with_headers(url, headers, timeout=timeout)
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("utf-8", errors="replace")[:300]


def http_json(url: str) -> tuple[int, Any]:
    status, body = http_get(url)
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("utf-8", errors="replace")[:300]


def http_json_post(url: str, payload: dict[str, Any] | None = None, timeout: float = 15.0) -> tuple[int, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status, body = response.status, response.read()
    except error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except Exception as exc:
        return 0, {"error": str(exc)}
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("utf-8", errors="replace")[:300]


def http_json_post_with_headers(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status, body = response.status, response.read()
    except error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except Exception as exc:
        return 0, {"error": str(exc)}
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("utf-8", errors="replace")[:300]


def http_multipart_file_post(
    url: str,
    *,
    field_name: str,
    filename: str,
    content: bytes,
    content_type: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    boundary = "----ai-platform-poc-gate-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    req = request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status, raw_body = response.status, response.read()
    except error.HTTPError as exc:
        status, raw_body = exc.code, exc.read()
    except Exception as exc:
        return 0, {"error": str(exc)}
    try:
        return status, json.loads(raw_body.decode("utf-8"))
    except Exception:
        return status, raw_body.decode("utf-8", errors="replace")[:300]


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_container_runtime_env(container: str) -> dict[str, str]:
    """Read only non-secret POC runtime keys from a running API container."""
    command = ["sudo", "-n", "docker", "exec", container, "env"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        return {}
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in RUNTIME_ENV_ALLOWLIST:
            values[key] = value
    return values


def runtime_env_values(env_path: str, container: str) -> dict[str, str]:
    """Prefer allowlisted live container values; use env file only as fallback."""
    file_values = read_env(env_path)
    container_values = read_container_runtime_env(container)
    if not container_values:
        return file_values
    values = dict(file_values)
    values.update(container_values)
    return values


def text_files(root: Path) -> list[Path]:
    suffixes = {".html", ".js", ".mjs", ".css", ".json", ".webmanifest"}
    return [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]


def psql_rows(container: str, db_user: str, db_name: str, sql: str) -> list[dict[str, Any]]:
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
        "-c",
        sql,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "psql query failed")
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def principal_headers(user_id: str, display_name: str = "", *, tenant_id: str = "default") -> dict[str, str]:
    return {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": display_name or user_id,
        "X-AI-Tenant-ID": tenant_id,
        "X-AI-Roles": "user",
        "X-AI-Permissions": "agent:use,chat:read,chat:write,session:read,session:write,artifact:download,file:upload,file:upload:document",
    }


def check_frontend(frontend_url: str) -> Gate:
    status, body = http_get(f"{frontend_url.rstrip('/')}/")
    text = body.decode("utf-8", errors="replace")
    ok = status == 200 and "<html" in text.lower() and "assets/" in text
    return Gate("lambchat_frontend", ok, {"url": frontend_url, "status": status, "bytes": len(body)})


def check_frontend_origin_api(frontend_url: str) -> Gate:
    api_url = f"{frontend_url.rstrip('/')}/api/ai/health"
    status, payload = http_json(api_url)
    ok = status == 200 and isinstance(payload, dict) and payload.get("status") == "ok"
    return Gate("lambchat_frontend_origin_api", ok, {"url": api_url, "status": status, "payload": payload})


def check_frontend_dist_api_boundary(frontend_dist: str) -> Gate:
    root = Path(frontend_dist)
    if not root.exists():
        return Gate(
            "lambchat_frontend_dist_api_boundary",
            False,
            {"path": frontend_dist, "exists": False, "api_reference_count": 0, "forbidden_reference_count": 0},
        )
    api_reference_count = 0
    forbidden: list[str] = []
    forbidden_pattern = re.compile(r"https?://(?:127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|[^\"'`\s/]+):18080/[^\"'`\s]*api", re.IGNORECASE)
    for path in text_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "/api/" in text:
            api_reference_count += text.count("/api/")
        if forbidden_pattern.search(text):
            forbidden.append(str(path.relative_to(root)))
    ok = api_reference_count > 0 and not forbidden
    return Gate(
        "lambchat_frontend_dist_api_boundary",
        ok,
        {
            "path": frontend_dist,
            "exists": True,
            "api_reference_count": api_reference_count,
            "forbidden_reference_count": len(forbidden),
            "forbidden_files": forbidden[:10],
        },
    )


def check_api_compat(api_url: str, *, expected_default_model_id: str = "") -> Gate:
    paths = [
        "/api/ai/health",
        "/api/auth/oauth/providers",
        "/api/auth/permissions",
        "/api/agent/models/available",
        "/api/settings/",
        "/api/projects",
        "/api/notifications/active",
        "/api/upload/config",
        "/api/tools",
        "/api/version",
    ]
    statuses: dict[str, int] = {}
    payloads: dict[str, Any] = {}
    for path in paths:
        status, payload = http_json(f"{api_url.rstrip('/')}{path}")
        statuses[path] = status
        payloads[path] = payload
    oauth = payloads.get("/api/auth/oauth/providers") if isinstance(payloads.get("/api/auth/oauth/providers"), dict) else {}
    model = payloads.get("/api/agent/models/available") if isinstance(payloads.get("/api/agent/models/available"), dict) else {}
    permissions_payload = payloads.get("/api/auth/permissions") if isinstance(payloads.get("/api/auth/permissions"), dict) else {}
    available_model_ids = _model_ids_from_catalog_payload(model.get("models"))
    default_model_id = str(model.get("default_model_id") or "")
    permission_values = {
        str(item.get("value"))
        for item in permissions_payload.get("all_permissions", [])
        if isinstance(item, dict) and item.get("value")
    }
    missing_permissions = sorted(REQUIRED_UI_PERMISSIONS - permission_values)
    ok = (
        all(status == 200 for status in statuses.values())
        and oauth.get("registration_enabled") is False
        and bool(default_model_id)
        and default_model_id in available_model_ids
        and (not expected_default_model_id or default_model_id == expected_default_model_id)
        and not missing_permissions
    )
    return Gate(
        "lambchat_api_compat",
        ok,
        {
            "statuses": statuses,
            "registration_enabled": oauth.get("registration_enabled"),
            "default_model_id": default_model_id,
            "expected_default_model_id": expected_default_model_id,
            "default_model_matches_expected": not expected_default_model_id or default_model_id == expected_default_model_id,
            "available_model_ids": available_model_ids,
            "missing_permissions": missing_permissions,
        },
    )


def _model_ids_from_catalog_payload(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    model_ids = []
    for item in value:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("value") or "")
        if model_id:
            model_ids.append(model_id)
    return sorted(set(model_ids))


def _model_ids_from_catalog_json(raw: str) -> tuple[str, list[str]]:
    if not raw.strip():
        return "absent", []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "invalid_json", []
    if not isinstance(parsed, list):
        return "invalid_shape", []
    model_ids = _model_ids_from_catalog_payload(parsed)
    if not model_ids:
        return "empty", []
    return "present", model_ids


def check_runtime_config(env_path: str, values: dict[str, str] | None = None) -> Gate:
    values = dict(values) if values is not None else read_env(env_path)
    skills = {item.strip() for item in values.get("CLAUDE_AGENT_SDK_SKILLS", "").split(",") if item.strip()}
    configured_models = {
        values.get("CLAUDE_AGENT_MODEL"),
        values.get("OPENAI_MODEL"),
        values.get("ANTHROPIC_MODEL"),
    }
    configured_models = {item for item in configured_models if item}
    configured_model_id = next(iter(configured_models)) if len(configured_models) == 1 else ""
    default_model_id = values.get("DEFAULT_MODEL_ID") or configured_model_id
    catalog_status, catalog_model_ids = _model_ids_from_catalog_json(values.get("MODEL_CATALOG_JSON", ""))
    catalog_contains_configured_model = (
        catalog_status == "absent"
        or (bool(configured_model_id) and configured_model_id in catalog_model_ids)
    )
    catalog_valid = catalog_status in {"absent", "present"} and catalog_contains_configured_model
    ok = (
        values.get("CLAUDE_AGENT_SDK_ENABLED", "").lower() == "true"
        and bool(configured_model_id)
        and default_model_id == configured_model_id
        and catalog_valid
        and {"general-chat", "qa-file-reviewer", "baoyu-translate"}.issubset(skills)
    )
    return Gate(
        "runtime_config",
        ok,
        {
            "env_path": env_path,
            "claude_agent_sdk_enabled": values.get("CLAUDE_AGENT_SDK_ENABLED"),
            "claude_agent_model": values.get("CLAUDE_AGENT_MODEL"),
            "openai_model": values.get("OPENAI_MODEL"),
            "anthropic_model": values.get("ANTHROPIC_MODEL"),
            "configured_model_id": configured_model_id,
            "default_model_id": default_model_id,
            "model_catalog_status": catalog_status,
            "available_model_ids": catalog_model_ids,
            "model_catalog_contains_configured_model": catalog_contains_configured_model,
            "skills_present": sorted(skills.intersection({"general-chat", "qa-file-reviewer", "baoyu-translate"})),
        },
    )


def check_company_auth_bridge(existing_auth_base_url: str) -> Gate:
    base_url = existing_auth_base_url.rstrip("/")
    if not base_url or not base_url.startswith(("http://", "https://")):
        return Gate(
            "company_auth_bridge",
            False,
            {
                "configured": False,
                "login_url": None,
                "error": "missing_or_invalid_existing_auth_base_url",
            },
        )
    login_url = f"{base_url}/api/Login/"
    status, payload = http_json_post(
        login_url,
        {"username": "__ai_platform_invalid_probe__", "password": "__invalid__"},
    )
    payload_status = payload.get("status") if isinstance(payload, dict) else None
    ok = status == 200 and payload_status == "unsuccessfully!"
    return Gate(
        "company_auth_bridge",
        ok,
        {
            "login_url": login_url,
            "login_probe_status": status,
            "login_probe_payload_status": payload_status,
        },
    )


def latest_successful_run(container: str, db_user: str, db_name: str, *, agent_id: str, skill_id: str) -> dict[str, Any] | None:
    sql = f"""
select json_build_object(
  'run_id', r.id,
  'tenant_id', r.tenant_id,
  'agent_id', r.agent_id,
  'skill_id', r.skill_id,
  'status', r.status,
  'user_id', r.user_id,
  'artifact_id', a.id,
  'artifact_label', a.label,
  'artifact_size_bytes', a.size_bytes,
  'artifact_content_type', a.content_type,
  'artifact_storage_key', a.storage_key
)::text
from runs r
left join artifacts a on a.run_id = r.id
where r.agent_id = '{agent_id}'
  and r.skill_id = '{skill_id}'
  and r.status = 'succeeded'
order by r.created_at desc
limit 1;
"""
    rows = psql_rows(container, db_user, db_name, sql)
    return rows[0] if rows else None


def check_db_evidence(container: str, db_user: str, db_name: str) -> list[Gate]:
    specs = [
        ("general_chat_run", "general-agent", "general-chat", False),
        ("review_artifact", "qa-word-review", "qa-file-reviewer", True),
        ("translate_artifact", "baoyu-translate", "baoyu-translate", True),
    ]
    gates: list[Gate] = []
    for name, agent_id, skill_id, require_artifact in specs:
        row = latest_successful_run(container, db_user, db_name, agent_id=agent_id, skill_id=skill_id)
        storage_key = str((row or {}).get("artifact_storage_key") or "")
        ok = bool(row) and (not require_artifact or (row.get("artifact_id") and int(row.get("artifact_size_bytes") or 0) > 0 and storage_key.startswith("tenants/")))
        gates.append(Gate(name, ok, row or {"agent_id": agent_id, "skill_id": skill_id, "found": False}))
    return gates


def _safe_run_scope(run_rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    fresh_skill_ids = {
        str(row.get("skill_id") or "")
        for row in run_rows
        if isinstance(row, dict) and row.get("fresh_smoke_run") is True and row.get("skill_id")
    }
    scopes: list[tuple[str, str, str, str]] = []
    for row in run_rows:
        if not isinstance(row, dict):
            continue
        skill_id = str(row.get("skill_id") or "")
        if row.get("fresh_smoke_run") is False:
            continue
        if skill_id in fresh_skill_ids and row.get("fresh_smoke_run") is not True:
            continue
        run_id = str(row.get("run_id") or "")
        tenant_id = str(row.get("tenant_id") or "default")
        status = str(row.get("status") or "")
        if not run_id or not skill_id:
            continue
        scopes.append(
            (
                assert_safe_id(tenant_id, "tenant_id"),
                assert_safe_id(run_id, "run_id"),
                assert_safe_id(skill_id, "skill_id"),
                status,
            )
        )
    return scopes


def check_governed_skill_runs(
    container: str,
    db_user: str,
    db_name: str,
    run_rows: list[dict[str, Any]],
) -> Gate:
    """Verify real governed-skill task runs persisted used pinned snapshots."""
    scopes = _safe_run_scope(run_rows)
    real_task_statuses = {skill_id: status for _, _, skill_id, status in scopes}
    if not scopes:
        return Gate(
            "governed_skill_runs",
            False,
            {
                "verified": False,
                "real_task_statuses": real_task_statuses,
                "run_skill_snapshots": {
                    "row_count": 0,
                    "used_count": 0,
                    "used_skill_ids": [],
                    "used_skills_source": "",
                    "pinned_snapshot_count": 0,
                    "pinned_snapshot_source": "release_decision",
                    "missing_pinned_snapshots": sorted(real_task_statuses),
                    "mismatched_pinned_snapshots": [],
                },
            },
        )
    values = ", ".join(
        f"('{tenant_id}', '{run_id}', '{skill_id}')"
        for tenant_id, run_id, skill_id, _status in scopes
    )
    sql = f"""
with target_runs(tenant_id, run_id, skill_id) as (
  values {values}
),
snapshots as (
  select
    rss.tenant_id,
    rss.run_id,
    rss.skill_id,
    rss.skill_version,
    rss.content_hash,
    rss.used,
    rss.used_skills_source
  from run_skill_snapshots rss
  join target_runs target
    on target.tenant_id = rss.tenant_id
   and target.run_id = rss.run_id
   and target.skill_id = rss.skill_id
),
pinned_runs as (
  select
    r.tenant_id,
    r.id as run_id,
    r.skill_id,
    r.input_json->'release_decision'->>'selected_version' as selected_version
  from runs r
  join target_runs target
    on target.tenant_id = r.tenant_id
   and target.run_id = r.id
   and target.skill_id = r.skill_id
  where r.input_json ? 'release_decision'
    and coalesce(r.input_json->'release_decision'->>'selected_version', '') <> ''
)
select json_build_object(
  'row_count', (select count(*) from snapshots),
  'used_count', (select count(*) from snapshots where used),
  'used_skill_ids', coalesce(
    (select json_agg(skill_id order by skill_id) from snapshots where used),
    '[]'::json
  ),
  'used_skills_sources', coalesce(
    (select json_agg(distinct used_skills_source order by used_skills_source)
       from snapshots
      where used and coalesce(used_skills_source, '') <> ''),
    '[]'::json
  ),
  'pinned_snapshot_count', (
    select count(*)
    from pinned_runs pinned
    join snapshots snapshot
      on snapshot.tenant_id = pinned.tenant_id
     and snapshot.run_id = pinned.run_id
     and snapshot.skill_id = pinned.skill_id
     and snapshot.used
     and (
       snapshot.skill_version = pinned.selected_version
       or snapshot.content_hash = pinned.selected_version
     )
  ),
  'missing_pinned_snapshots', coalesce(
    (
      select json_agg(pinned.skill_id order by pinned.skill_id)
      from pinned_runs pinned
      left join snapshots snapshot
        on snapshot.tenant_id = pinned.tenant_id
       and snapshot.run_id = pinned.run_id
       and snapshot.skill_id = pinned.skill_id
      where snapshot.skill_id is null
    ),
    '[]'::json
  ),
  'mismatched_pinned_snapshots', coalesce(
    (
      select json_agg(pinned.skill_id order by pinned.skill_id)
      from pinned_runs pinned
      join snapshots snapshot
        on snapshot.tenant_id = pinned.tenant_id
       and snapshot.run_id = pinned.run_id
       and snapshot.skill_id = pinned.skill_id
      where not (
        snapshot.skill_version = pinned.selected_version
        or snapshot.content_hash = pinned.selected_version
      )
    ),
    '[]'::json
  )
)::text;
"""
    rows = psql_rows(container, db_user, db_name, sql)
    snapshot_summary = rows[0] if rows else {}
    used_skill_ids = snapshot_summary.get("used_skill_ids")
    if not isinstance(used_skill_ids, list):
        used_skill_ids = []
    missing_pinned_snapshots = snapshot_summary.get("missing_pinned_snapshots")
    if not isinstance(missing_pinned_snapshots, list):
        missing_pinned_snapshots = []
    mismatched_pinned_snapshots = snapshot_summary.get("mismatched_pinned_snapshots")
    if not isinstance(mismatched_pinned_snapshots, list):
        mismatched_pinned_snapshots = []
    used_skills_sources = snapshot_summary.get("used_skills_sources")
    if not isinstance(used_skills_sources, list):
        used_skills_sources = []
    used_skills_source = ",".join(
        str(item).strip()
        for item in used_skills_sources
        if isinstance(item, str) and item.strip()
    )
    expected_used_skill_ids = [skill_id for _, _, skill_id, _status in scopes]
    row_count = int(snapshot_summary.get("row_count") or 0)
    used_count = int(snapshot_summary.get("used_count") or 0)
    pinned_snapshot_count = int(snapshot_summary.get("pinned_snapshot_count") or 0)
    ok = (
        all(status == "succeeded" for status in real_task_statuses.values())
        and row_count >= len(scopes)
        and used_count >= len(scopes)
        and sorted(str(item) for item in used_skill_ids) == sorted(expected_used_skill_ids)
        and pinned_snapshot_count >= len(scopes)
        and not missing_pinned_snapshots
        and not mismatched_pinned_snapshots
    )
    evidence = {
        "verified": ok,
        "real_task_statuses": real_task_statuses,
        "run_skill_snapshots": {
            "row_count": row_count,
            "used_count": used_count,
            "used_skill_ids": [
                skill_id
                for skill_id in expected_used_skill_ids
                if skill_id in {str(item) for item in used_skill_ids if isinstance(item, str)}
            ],
            "used_skills_source": used_skills_source,
            "pinned_snapshot_count": pinned_snapshot_count,
            "pinned_snapshot_source": "release_decision",
            "missing_pinned_snapshots": [
                str(item) for item in missing_pinned_snapshots if isinstance(item, str)
            ],
            "mismatched_pinned_snapshots": [
                str(item) for item in mismatched_pinned_snapshots if isinstance(item, str)
            ],
        },
    }
    return Gate("governed_skill_runs", ok, evidence)


def check_artifact_download_isolation(api_url: str, artifact_rows: list[dict[str, Any]]) -> Gate:
    denied_statuses = {401, 403, 404}
    results: list[dict[str, Any]] = []
    for row in artifact_rows:
        artifact_id = str(row.get("artifact_id") or "")
        owner_id = str(row.get("user_id") or "")
        if not artifact_id or not owner_id:
            continue
        url = f"{api_url.rstrip('/')}/api/ai/artifacts/{artifact_id}/download"
        owner_status, owner_body = http_get_with_headers(url, principal_headers(owner_id, "Artifact Owner"))
        cross_user_id = f"{owner_id}-cross-check"
        cross_status, _ = http_get_with_headers(url, principal_headers(cross_user_id, "Artifact Cross Check"))
        cross_tenant_status, _ = http_get_with_headers(
            url,
            principal_headers(owner_id, "Artifact Cross Tenant Check", tenant_id="tenant-b"),
        )
        results.append(
            {
                "artifact_id": artifact_id,
                "owner_user": owner_id,
                "owner_status": owner_status,
                "owner_bytes": len(owner_body),
                "cross_user_status": cross_status,
                "cross_tenant_status": cross_tenant_status,
            }
        )
    ok = bool(results) and all(
        item["owner_status"] == 200
        and item["owner_bytes"] > 0
        and item["cross_user_status"] in denied_statuses
        and item["cross_tenant_status"] in denied_statuses
        for item in results
    )
    return Gate("artifact_download_isolation", ok, {"checked_artifacts": len(results), "results": results})


def _header_value(headers: dict[str, str], name: str) -> str:
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected:
            return value
    return ""


def _normalized_content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def check_artifact_preview_isolation(api_url: str, artifact_rows: list[dict[str, Any]]) -> Gate:
    denied_statuses = {401, 403, 404}
    results: list[dict[str, Any]] = []
    for row in artifact_rows:
        artifact_id = str(row.get("artifact_id") or "")
        owner_id = str(row.get("user_id") or "")
        content_type = _normalized_content_type(row.get("artifact_content_type"))
        if not artifact_id or not owner_id or content_type not in PREVIEW_ALLOWED_CONTENT_TYPES:
            continue
        url = f"{api_url.rstrip('/')}/api/ai/artifacts/{artifact_id}/preview"
        owner_status, owner_body, owner_headers = http_get_with_headers_and_response_headers(
            url,
            principal_headers(owner_id, "Artifact Owner"),
        )
        cross_user_id = f"{owner_id}-cross-check"
        cross_status, _, _ = http_get_with_headers_and_response_headers(
            url,
            principal_headers(cross_user_id, "Artifact Cross Check"),
        )
        cross_tenant_status, _, _ = http_get_with_headers_and_response_headers(
            url,
            principal_headers(owner_id, "Artifact Cross Tenant Check", tenant_id="tenant-b"),
        )
        cache_control = _header_value(owner_headers, "Cache-Control")
        owner_content_type = _normalized_content_type(_header_value(owner_headers, "Content-Type"))
        content_disposition = _header_value(owner_headers, "Content-Disposition")
        x_content_type_options = _header_value(owner_headers, "X-Content-Type-Options")
        results.append(
            {
                "artifact_id": artifact_id,
                "owner_user": owner_id,
                "content_type": content_type,
                "owner_status": owner_status,
                "owner_bytes": len(owner_body),
                "owner_cache_control": cache_control,
                "owner_content_type": owner_content_type,
                "owner_content_disposition": content_disposition,
                "owner_x_content_type_options": x_content_type_options,
                "cross_user_status": cross_status,
                "cross_tenant_status": cross_tenant_status,
            }
        )
    ok = bool(results) and all(
        item["owner_status"] == 200
        and item["owner_bytes"] > 0
        and "no-store" in str(item["owner_cache_control"]).lower()
        and item["owner_content_type"] == item["content_type"]
        and str(item["owner_content_disposition"]).lower().startswith("inline")
        and str(item["owner_x_content_type_options"]).lower() == "nosniff"
        and item["cross_user_status"] in denied_statuses
        and item["cross_tenant_status"] in denied_statuses
        for item in results
    )
    return Gate("artifact_preview_isolation", ok, {"checked_artifacts": len(results), "results": results})


def _artifact_rows_from_run_evidence(run_evidence: dict[str, Any]) -> list[dict[str, str]]:
    artifacts = run_evidence.get("artifacts")
    if isinstance(artifacts, list):
        return [
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "artifact_type": str(item.get("artifact_type") or ""),
                "content_type": _normalized_content_type(item.get("content_type")),
            }
            for item in artifacts
            if isinstance(item, dict)
        ]
    artifact_ids = run_evidence.get("artifact_ids") if isinstance(run_evidence.get("artifact_ids"), list) else []
    artifact_types = run_evidence.get("artifact_types") if isinstance(run_evidence.get("artifact_types"), list) else []
    artifact_content_types = (
        run_evidence.get("artifact_content_types")
        if isinstance(run_evidence.get("artifact_content_types"), list)
        else []
    )
    rows: list[dict[str, str]] = []
    for index, artifact_id in enumerate(artifact_ids):
        rows.append(
            {
                "artifact_id": str(artifact_id or ""),
                "artifact_type": str(artifact_types[index] if index < len(artifact_types) else ""),
                "content_type": _normalized_content_type(
                    artifact_content_types[index] if index < len(artifact_content_types) else ""
                ),
            }
        )
    return rows


def _compact_key(value: object) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


CONTEXT_RAW_MATERIAL_ID_KEY_ALIASES = frozenset(_compact_key(key) for key in CONTEXT_RAW_MATERIAL_ID_KEYS)
CONTEXT_FORBIDDEN_PROJECTION_KEY_ALIAS_SET = frozenset(
    _compact_key(key) for key in CONTEXT_FORBIDDEN_PROJECTION_KEY_ALIASES
)


def _context_material_count(value: object) -> tuple[int, bool]:
    if type(value) is not int:
        return 0, False
    if value < 0:
        return 0, False
    return value, True


def _safe_non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _context_public_projection_findings(value: Any) -> tuple[bool, list[str]]:
    raw_material_id_fields_present = False
    forbidden_leaks: set[str] = set()

    def visit(item: Any) -> None:
        nonlocal raw_material_id_fields_present
        if isinstance(item, dict):
            for key, child in item.items():
                normalized_key = _compact_key(key)
                if normalized_key in CONTEXT_RAW_MATERIAL_ID_KEY_ALIASES:
                    raw_material_id_fields_present = True
                if normalized_key in CONTEXT_FORBIDDEN_PROJECTION_KEY_ALIAS_SET:
                    forbidden_leaks.add(str(key))
                visit(child)
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if isinstance(item, str):
            lowered = item.lower()
            for marker in CONTEXT_FORBIDDEN_PROJECTION_MARKERS:
                if marker in lowered:
                    forbidden_leaks.add(marker)

    visit(value)
    return raw_material_id_fields_present, sorted(forbidden_leaks)


def _context_missing_public_summary_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    referenced_materials = (
        payload.get("referenced_materials")
        if isinstance(payload.get("referenced_materials"), dict)
        else {}
    )
    used_summary = payload.get("used_context_summary")
    input_keys = (
        public_context_input_key_findings(used_summary.get("input_keys"))[0]
        if isinstance(used_summary, dict)
        else []
    )
    unsafe_input_keys = (
        public_context_input_key_findings(used_summary.get("input_keys"))[1]
        if isinstance(used_summary, dict)
        else []
    )
    if not isinstance(used_summary, dict) or _safe_non_empty_string(used_summary.get("source")) is None:
        missing.append("summary_source")
    if (
        not isinstance(used_summary, dict)
        or not isinstance(used_summary.get("input_keys"), list)
        or not input_keys
    ):
        missing.append("input_keys")
    file_count, file_count_valid = _context_material_count(referenced_materials.get("file_count"))
    if file_count_valid and file_count > 0 and "attachments" not in input_keys:
        missing.append("attachments_input_key")
    if unsafe_input_keys:
        missing.append("unsafe_input_keys")
    if not isinstance(used_summary, dict) or _safe_non_empty_string(used_summary.get("memory_policy_source")) is None:
        missing.append("memory_policy_source")
    if not isinstance(used_summary, dict) or not isinstance(used_summary.get("long_term_memory_read"), bool):
        missing.append("long_term_memory_read")
    if _safe_non_empty_string(payload.get("execution_tier")) is None:
        missing.append("execution_tier")
    if safe_public_context_pack_version(payload.get("context_pack_version")) is None:
        missing.append("context_pack_version")
    if _safe_non_empty_string(payload.get("context_pack_generated_at")) is None:
        missing.append("context_pack_generated_at")
    return sorted(missing)


def _context_snapshot_payload_summary(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    referenced_materials = (
        snapshot_payload.get("referenced_materials")
        if isinstance(snapshot_payload.get("referenced_materials"), dict)
        else {}
    )
    counts: dict[str, int] = {}
    counts_valid = True
    for key in CONTEXT_PUBLIC_REQUIRED_COUNTS:
        value, valid = _context_material_count(referenced_materials.get(key))
        if not valid:
            counts_valid = False
            counts[key] = 0
        else:
            counts[key] = value
    used_summary = (
        snapshot_payload.get("used_context_summary")
        if isinstance(snapshot_payload.get("used_context_summary"), dict)
        else {}
    )
    input_keys = used_summary.get("input_keys") if isinstance(used_summary.get("input_keys"), list) else []
    safe_input_keys, unsafe_input_keys = public_context_input_key_findings(input_keys)
    invalid_count_fields = [
        key
        for key in CONTEXT_PUBLIC_REQUIRED_COUNTS
        if not _context_material_count(referenced_materials.get(key))[1]
    ]
    return {
        "counts": counts,
        "counts_valid": counts_valid,
        "invalid_count_fields": invalid_count_fields,
        "summary_source": _safe_non_empty_string(used_summary.get("source")),
        "input_keys": safe_input_keys,
        "unsafe_input_keys": unsafe_input_keys,
        "memory_policy_source": _safe_non_empty_string(used_summary.get("memory_policy_source")),
        "long_term_memory_read": used_summary.get("long_term_memory_read")
        if isinstance(used_summary.get("long_term_memory_read"), bool)
        else None,
        "execution_tier": _safe_non_empty_string(snapshot_payload.get("execution_tier")),
        "context_pack_version": safe_public_context_pack_version(snapshot_payload.get("context_pack_version")),
        "context_pack_generated_at_present": _safe_non_empty_string(
            snapshot_payload.get("context_pack_generated_at")
        )
        is not None,
        "missing_public_summary_fields": _context_missing_public_summary_fields(snapshot_payload),
    }


def check_context_snapshot_public_projection(
    api_url: str,
    run_evidence: dict[str, Any],
    *,
    headers: dict[str, str],
) -> Gate:
    """Verify the run context snapshot endpoint exposes only safe public provenance."""
    run_id = str(run_evidence.get("run_id") or "")
    if not run_id:
        return Gate(
            "context_snapshot_public_projection",
            False,
            {"ok": False, "error": "missing_run_id", "snapshot_count": 0},
        )
    run_id = assert_safe_id(run_id, "run_id")
    status, payload = http_json_get_with_headers(
        f"{api_url.rstrip('/')}/api/ai/runs/{run_id}/context/snapshots",
        headers=headers,
        timeout=30,
    )
    snapshots = payload.get("context_snapshots") if isinstance(payload, dict) else []
    if not isinstance(snapshots, list):
        snapshots = []
    snapshot_payload_summaries = []
    for snapshot in snapshots:
        snapshot_payload = (
            snapshot.get("payload")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("payload"), dict)
            else {}
        )
        snapshot_payload_summaries.append(_context_snapshot_payload_summary(snapshot_payload))
    primary_summary = (
        snapshot_payload_summaries[0]
        if snapshot_payload_summaries
        else _context_snapshot_payload_summary({})
    )
    missing_public_summary_fields = sorted(
        {
            field
            for summary in snapshot_payload_summaries
            for field in summary["missing_public_summary_fields"]
        }
        or set(primary_summary["missing_public_summary_fields"])
    )
    invalid_count_fields = sorted(
        {
            field
            for summary in snapshot_payload_summaries
            for field in summary["invalid_count_fields"]
        }
        or set(primary_summary["invalid_count_fields"])
    )
    unsafe_input_keys = sorted(
        {
            key
            for summary in snapshot_payload_summaries
            for key in summary["unsafe_input_keys"]
        }
        or set(primary_summary["unsafe_input_keys"])
    )
    raw_material_id_fields_present, forbidden_leaks = _context_public_projection_findings(payload)
    ok = (
        status == 200
        and bool(snapshots)
        and all(summary["counts_valid"] for summary in snapshot_payload_summaries)
        and all(summary["counts"]["message_count"] >= 1 for summary in snapshot_payload_summaries)
        and all(summary["counts"]["file_count"] >= 1 for summary in snapshot_payload_summaries)
        and raw_material_id_fields_present is False
        and not forbidden_leaks
        and not missing_public_summary_fields
    )
    evidence = {
        "status": status,
        "ok": ok,
        "snapshot_count": len(snapshots),
        "referenced_material_counts": primary_summary["counts"],
        "raw_material_id_fields_present": raw_material_id_fields_present,
        "forbidden_projection_leaks": forbidden_leaks,
        "summary_source": primary_summary["summary_source"],
        "input_keys": primary_summary["input_keys"],
        "memory_policy_source": primary_summary["memory_policy_source"],
        "long_term_memory_read": primary_summary["long_term_memory_read"],
        "execution_tier": primary_summary["execution_tier"],
        "context_pack_version": primary_summary["context_pack_version"],
        "context_pack_generated_at_present": primary_summary["context_pack_generated_at_present"],
    }
    if invalid_count_fields:
        evidence["invalid_referenced_material_count_fields"] = invalid_count_fields
    if unsafe_input_keys:
        evidence["unsafe_input_keys"] = unsafe_input_keys
    if missing_public_summary_fields:
        evidence["missing_public_summary_fields"] = missing_public_summary_fields
    return Gate("context_snapshot_public_projection", ok, evidence)


def check_upload_attachment_chat(
    api_url: str,
    container: str,
    db_user: str,
    db_name: str,
    *,
    wait_attempts: int = 45,
) -> Gate:
    headers = principal_headers("upload-gate-user-a", "Upload Gate User")
    check_status, check_payload = http_json_post_with_headers(
        f"{api_url.rstrip('/')}/api/upload/check",
        {"hash": "upload-gate", "size": 18, "name": "upload-gate.txt"},
        headers=headers,
    )
    upload_status, upload_payload = http_multipart_file_post(
        f"{api_url.rstrip('/')}/api/upload/file?folder=uploads",
        field_name="file",
        filename="upload-gate.txt",
        content=b"hello upload smoke",
        content_type="text/plain",
        headers=headers,
    )
    file_id = upload_payload.get("key") if isinstance(upload_payload, dict) else None
    chat_payload: dict[str, Any] | None = None
    chat_status = 0
    if file_id:
        chat_status, chat_payload = http_json_post_with_headers(
            f"{api_url.rstrip('/')}/api/chat/stream?agent_id=general-agent",
            {
                "message": "请确认你能看到上传文件",
                "workspace_id": "default",
                "attachments": [
                    {
                        "key": file_id,
                        "name": "upload-gate.txt",
                        "type": "uploads",
                        "mimeType": "text/plain",
                        "size": 18,
                    }
                ],
            },
            headers=headers,
            timeout=30,
        )
    run_id = chat_payload.get("run_id") if isinstance(chat_payload, dict) else None
    run_evidence: dict[str, Any] = {}
    if run_id:
        run_id = assert_safe_id(str(run_id), "run_id")
        for _ in range(max(1, wait_attempts)):
            rows = psql_rows(
                container,
                db_user,
                db_name,
                f"""
select json_build_object(
  'run_id', r.id,
  'status', r.status,
  'file_ids', r.input_json->'file_ids',
  'context_snapshot', r.input_json->'context_snapshot',
  'error_code', r.error_code,
  'error_message', r.error_message,
  'executor_type', r.result_json->'executor'->>'executor_type',
  'worker_events', coalesce(
    json_agg(e.payload_json order by e.created_at)
      filter (where e.stage = 'worker' and e.message = 'Run started'),
    '[]'::json
  )
)::text
from runs r
left join run_events e on e.tenant_id = r.tenant_id and e.run_id = r.id
where r.id = '{run_id}'
group by r.id;
""",
            )
            run_evidence = rows[0] if rows else {}
            if run_evidence.get("status") in {"succeeded", "failed"}:
                break
            time.sleep(1)
    worker_started = any(
        isinstance(item, dict)
        and item.get("executor_type") == "claude-agent-worker"
        and item.get("claude_agent_sdk_enabled") is True
        and item.get("claude_agent_sdk_import") == "ok"
        for item in (run_evidence.get("worker_events") or [])
    )
    context_snapshot = run_evidence.get("context_snapshot") if isinstance(run_evidence.get("context_snapshot"), dict) else {}
    referenced_materials = (
        context_snapshot.get("referenced_materials")
        if isinstance(context_snapshot.get("referenced_materials"), dict)
        else {}
    )
    used_context_summary = (
        context_snapshot.get("used_context_summary")
        if isinstance(context_snapshot.get("used_context_summary"), dict)
        else {}
    )
    context_input_keys = used_context_summary.get("input_keys")
    if not isinstance(context_input_keys, list):
        context_input_keys = []
    attachment_context_recorded = (
        int(referenced_materials.get("file_count") or 0) > 0
        and "attachments" in {str(item) for item in context_input_keys}
    )
    error_message = str(run_evidence.get("error_message") or "")
    run_terminal_sdk_failure = (
        run_evidence.get("status") == "failed"
        and run_evidence.get("error_code") == "claude_agent_sdk_runtime_error"
        and worker_started
    )
    run_terminal_turn_limit = run_terminal_sdk_failure and "maximum number of turns" in error_message.lower()
    run_accepted_by_worker = (
        (run_evidence.get("status") == "succeeded" and worker_started)
        or (run_evidence.get("status") == "running" and worker_started)
        or (run_terminal_turn_limit and attachment_context_recorded)
    )
    ok = (
        check_status == 200
        and isinstance(check_payload, dict)
        and check_payload.get("exists") is False
        and upload_status == 200
        and isinstance(upload_payload, dict)
        and str(upload_payload.get("key") or "").startswith("file_")
        and chat_status == 200
        and run_accepted_by_worker
        and file_id in (run_evidence.get("file_ids") or [])
    )
    return Gate(
        "upload_attachment_chat",
        ok,
        {
            "upload_check_status": check_status,
            "upload_check_payload": check_payload,
            "upload_status": upload_status,
            "upload_payload": upload_payload,
            "chat_status": chat_status,
            "chat_payload": chat_payload,
            "run": run_evidence,
            "worker_started": worker_started,
            "run_terminal_sdk_failure": run_terminal_sdk_failure,
            "run_terminal_turn_limit": run_terminal_turn_limit,
            "attachment_context_recorded": attachment_context_recorded,
            "run_accepted_by_worker": run_accepted_by_worker,
        },
    )


def sample_docx_bytes() -> tuple[str, bytes] | None:
    candidates = [
        Path("/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/.venv/lib/python3.12/site-packages/docx/templates/default.docx"),
        Path("/home/xinlin.jiang/ai-platform-phaseb/tmp-smoke-translate.docx"),
        Path("/home/xinlin.jiang/ai-platform-phaseb/review-artifact-download-20260523.docx"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.name, candidate.read_bytes()
    return None


def check_word_review_attachment_chat(
    api_url: str,
    container: str,
    db_user: str,
    db_name: str,
    *,
    wait_attempts: int = 90,
) -> Gate:
    sample = sample_docx_bytes()
    if sample is None:
        return Gate("word_review_attachment_chat", False, {"sample_docx_found": False})
    filename, content = sample
    headers = principal_headers("upload-review-gate-user", "Upload Review Gate User")
    upload_status, upload_payload = http_multipart_file_post(
        f"{api_url.rstrip('/')}/api/upload/file?folder=uploads",
        field_name="file",
        filename=filename,
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )
    file_id = upload_payload.get("key") if isinstance(upload_payload, dict) else None
    chat_payload: dict[str, Any] | None = None
    chat_status = 0
    if file_id:
        chat_status, chat_payload = http_json_post_with_headers(
            f"{api_url.rstrip('/')}/api/chat/stream?agent_id=general-agent",
            {
                "message": "审核一下这个文档",
                "workspace_id": "default",
                "attachments": [
                    {
                        "key": file_id,
                        "name": filename,
                        "type": "uploads",
                        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "size": len(content),
                    }
                ],
            },
            headers=headers,
            timeout=30,
        )
    run_id = chat_payload.get("run_id") if isinstance(chat_payload, dict) else None
    run_evidence: dict[str, Any] = {}
    if run_id:
        run_id = assert_safe_id(str(run_id), "run_id")
        for _ in range(max(1, wait_attempts)):
            rows = psql_rows(
                container,
                db_user,
                db_name,
                f"""
select json_build_object(
  'run_id', r.id,
  'tenant_id', r.tenant_id,
  'agent_id', r.agent_id,
  'skill_id', r.skill_id,
  'status', r.status,
  'file_ids', r.input_json->'file_ids',
  'error_message', r.error_message,
  'artifact_count', count(a.id),
  'artifacts', coalesce(
    json_agg(
      json_build_object(
        'artifact_id', a.id,
        'artifact_type', a.artifact_type,
        'content_type', a.content_type
      )
      order by a.created_at, a.id
    ) filter (where a.id is not null),
    '[]'::json
  )
)::text
from runs r
left join artifacts a on a.run_id = r.id and a.tenant_id = r.tenant_id
where r.id = '{run_id}'
group by r.id;
""",
            )
            run_evidence = rows[0] if rows else {}
            if run_evidence.get("status") in {"succeeded", "failed"}:
                break
            time.sleep(1)
    run_artifacts = _artifact_rows_from_run_evidence(run_evidence)
    reviewed_docx_artifacts = [
        item
        for item in run_artifacts
        if item["artifact_type"] == "reviewed_docx" and item["content_type"] in PREVIEW_ALLOWED_CONTENT_TYPES
    ]
    reviewed_docx_artifact_ids = {item["artifact_id"] for item in reviewed_docx_artifacts if item["artifact_id"]}
    playback_status = 0
    playback_payload: Any = {}
    if run_id:
        playback_status, playback_payload = http_json_get_with_headers(
            f"{api_url.rstrip('/')}/api/ai/runs/{run_id}/playback",
            headers=headers,
            timeout=30,
        )
    playback_artifacts = playback_payload.get("artifacts") if isinstance(playback_payload, dict) else []
    if not isinstance(playback_artifacts, list):
        playback_artifacts = []
    playback_text = json.dumps(playback_payload, ensure_ascii=False, default=str).lower()
    playback_private_payload_leaked = any(
        marker in playback_text
        for marker in (
            "storage_key",
            "tenants/default",
            "/tmp/",
            "/home/xinlin.jiang",
            "/var/lib/ai-platform",
            "private_payload",
            "runtime_private_payload",
            "runtimeprivatepayload",
            "executor_payload",
            "executorpayload",
        )
    )
    playback_preview_url_count = sum(
        1
        for artifact in playback_artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("preview_url"), str) and artifact["preview_url"]
    )
    playback_download_url_count = sum(
        1
        for artifact in playback_artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("download_url"), str) and artifact["download_url"]
    )
    matched_preview_artifact_count = 0
    matched_download_artifact_count = 0
    for artifact in playback_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id not in reviewed_docx_artifact_ids:
            continue
        if artifact.get("download_url") == f"/api/ai/artifacts/{artifact_id}/download":
            matched_download_artifact_count += 1
        if artifact.get("preview_url") == f"/api/ai/artifacts/{artifact_id}/preview":
            matched_preview_artifact_count += 1
    playback_ok = (
        playback_status == 200
        and isinstance(playback_payload, dict)
        and playback_payload.get("contract_version") == "ai-platform.run-playback.v1"
        and bool(playback_artifacts)
        and bool(reviewed_docx_artifact_ids)
        and matched_download_artifact_count == len(reviewed_docx_artifact_ids)
        and matched_preview_artifact_count == len(reviewed_docx_artifact_ids)
        and not playback_private_payload_leaked
    )
    context_projection_gate = check_context_snapshot_public_projection(api_url, run_evidence, headers=headers)
    ok = (
        upload_status == 200
        and isinstance(upload_payload, dict)
        and str(upload_payload.get("key") or "").startswith("file_")
        and chat_status == 200
        and run_evidence.get("status") == "succeeded"
        and run_evidence.get("agent_id") == "qa-word-review"
        and run_evidence.get("skill_id") == "qa-file-reviewer"
        and file_id in (run_evidence.get("file_ids") or [])
        and bool(reviewed_docx_artifact_ids)
        and playback_ok
        and context_projection_gate.ok
    )
    return Gate(
        "word_review_attachment_chat",
        ok,
        {
            "sample_docx_found": True,
            "sample_docx_name": filename,
            "upload_status": upload_status,
            "upload_payload": upload_payload,
            "chat_status": chat_status,
            "chat_payload": chat_payload,
            "run": run_evidence,
            "playback": {
                "status": playback_status,
                "contract_version": playback_payload.get("contract_version") if isinstance(playback_payload, dict) else None,
                "artifact_count": len(playback_artifacts),
                "download_url_count": playback_download_url_count,
                "preview_url_count": playback_preview_url_count,
                "matched_download_artifact_count": matched_download_artifact_count,
                "matched_preview_artifact_count": matched_preview_artifact_count,
                "private_payload_leaked": playback_private_payload_leaked,
            },
            "context_snapshot_public_projection": context_projection_gate.evidence,
        },
    )


def check_auth_audit(container: str, db_user: str, db_name: str, allow_missing: bool) -> Gate:
    sql = """
select json_build_object(
  'count', count(*),
  'ordinary_user_count', count(*) filter (where coalesce((payload_json->>'is_admin')::boolean, false) = false),
  'admin_user_count', count(*) filter (where coalesce((payload_json->>'is_admin')::boolean, false) = true),
  'latest_user_id', (array_agg(user_id order by created_at desc))[1],
  'latest_payload', (array_agg(payload_json order by created_at desc))[1]
)::text
from audit_logs
where action = 'auth.login'
  and payload_json->>'source' = 'company-login'
  and payload_json ? 'work_id'
  and payload_json ? 'permissions'
  and payload_json ? 'is_admin';
"""
    rows = psql_rows(container, db_user, db_name, sql)
    evidence = rows[0] if rows else {"count": 0}
    raw_sql = """
select json_build_object(
  'all_auth_login_count', count(*),
  'latest_any_user_id', (array_agg(user_id order by created_at desc))[1],
  'latest_any_payload', (array_agg(payload_json order by created_at desc))[1]
)::text
from audit_logs
where action = 'auth.login';
"""
    raw_rows = psql_rows(container, db_user, db_name, raw_sql)
    if raw_rows:
        evidence.update(raw_rows[0])
    count = int(evidence.get("count") or 0)
    ordinary_user_count = int(evidence.get("ordinary_user_count") or 0)
    admin_user_count = int(evidence.get("admin_user_count") or 0)
    payload = evidence.get("latest_payload") if isinstance(evidence.get("latest_payload"), dict) else {}
    ok = (
        count > 0
        and ordinary_user_count > 0
        and admin_user_count > 0
        and payload.get("source") == "company-login"
        and bool(payload.get("work_id"))
    )
    missing_requirements = []
    if ordinary_user_count <= 0:
        missing_requirements.append("ordinary_company_login_audit")
    if admin_user_count <= 0:
        missing_requirements.append("admin_company_login_audit")
    if missing_requirements:
        evidence["missing_requirements"] = missing_requirements
    if allow_missing and not ok:
        evidence["allowed_missing_for_partial_gate"] = True
        ok = True
    return Gate("company_login_audit", ok, evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify aggregate ai-platform POC gates.")
    parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--frontend-dist", default=DEFAULT_FRONTEND_DIST)
    parser.add_argument("--env-path", default=DEFAULT_DEPLOY_ENV)
    parser.add_argument("--api-container", default=DEFAULT_API_CONTAINER)
    parser.add_argument("--postgres-container", default=DEFAULT_POSTGRES_CONTAINER)
    parser.add_argument("--postgres-user", default=DEFAULT_POSTGRES_USER)
    parser.add_argument("--postgres-db", default=DEFAULT_POSTGRES_DB)
    parser.add_argument("--allow-missing-auth-audit", action="store_true")
    parser.add_argument("--upload-run-wait-seconds", type=int, default=45)
    parser.add_argument("--word-review-run-wait-seconds", type=int, default=90)
    args = parser.parse_args()

    db_gates = check_db_evidence(args.postgres_container, args.postgres_user, args.postgres_db)
    env_values = runtime_env_values(args.env_path, args.api_container)
    runtime_config_gate = check_runtime_config(args.env_path, env_values)
    artifact_rows = [gate.evidence for gate in db_gates if gate.name in {"review_artifact", "translate_artifact"} and gate.ok]
    word_review_gate = check_word_review_attachment_chat(
        args.api_url,
        args.postgres_container,
        args.postgres_user,
        args.postgres_db,
        wait_attempts=args.word_review_run_wait_seconds,
    )
    governed_run_rows = list(artifact_rows)
    word_review_run = word_review_gate.evidence.get("run")
    if isinstance(word_review_run, dict):
        governed_run_rows.append({**word_review_run, "fresh_smoke_run": True})
    governed_skill_runs_gate = check_governed_skill_runs(
        args.postgres_container,
        args.postgres_user,
        args.postgres_db,
        governed_run_rows,
    )
    gates = [
        check_frontend(args.frontend_url),
        check_frontend_dist_api_boundary(args.frontend_dist),
        check_frontend_origin_api(args.frontend_url),
        check_api_compat(
            args.api_url,
            expected_default_model_id=str(runtime_config_gate.evidence.get("default_model_id") or ""),
        ),
        runtime_config_gate,
        check_company_auth_bridge(env_values.get("EXISTING_AUTH_BASE_URL", "")),
        *db_gates,
        governed_skill_runs_gate,
        check_artifact_download_isolation(args.api_url, artifact_rows),
        check_artifact_preview_isolation(args.api_url, artifact_rows),
        check_upload_attachment_chat(
            args.api_url,
            args.postgres_container,
            args.postgres_user,
            args.postgres_db,
            wait_attempts=args.upload_run_wait_seconds,
        ),
        word_review_gate,
        Gate(
            "context_snapshot_public_projection",
            bool(word_review_gate.evidence.get("context_snapshot_public_projection", {}).get("ok")),
            word_review_gate.evidence.get("context_snapshot_public_projection", {}),
        ),
        check_auth_audit(args.postgres_container, args.postgres_user, args.postgres_db, args.allow_missing_auth_audit),
    ]
    result = {
        "ok": all(gate.ok for gate in gates),
        "gates": [{"name": gate.name, "ok": gate.ok, "evidence": gate.evidence} for gate in gates],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

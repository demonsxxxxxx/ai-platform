#!/usr/bin/env python3
"""Verify a B1 memory/context workflow smoke against ai-platform.

The script intentionally uses only the Python standard library so it can run on
the 211 host without extra package installation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request


SCHEMA_VERSION = "ai-platform.b1-memory-context-workflow-smoke.v1"
ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"
TARGET = "211_api_memory_context_workflow"
CONTRACT_FIXTURE_TARGET = "source_contract_fixture"
DEFAULT_BASE_URL = "http://127.0.0.1:8020"
DEFAULT_RETENTION_DAYS = 30
DENIED_STATUSES = {401, 403, 404}
PUBLIC_CONTEXT_ALLOWED_MEMORY_POLICY_SOURCES = {
    "default",
    "user",
    "admin",
    "stored",
    "runs_api",
    "manual_context_snapshot",
    "not_recorded",
}
SAFE_INPUT_KEYS = {
    "attachments",
    "context",
    "file",
    "files",
    "memory",
    "message",
    "mode",
    "task",
}
PRIVATE_KEY_MARKERS = (
    "executor_private_payload",
    "executor_payload",
    "runtime_private_payload",
    "private_payload",
    "raw_storage_key",
    "storage_key",
    "sandbox_workdir",
    "callback_token",
)
PRIVATE_VALUE_MARKERS = (
    "/tmp/",
    "/home/",
    "/var/lib/ai-platform",
    "tenants/default",
)
PRIVATE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Za-z]:\\Users\\", re.IGNORECASE),
    re.compile(r"\b(?:authorization|bearer|api[_-]?key|token|password|secret)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
)
RAW_MATERIAL_ID_KEYS = {
    "message_id",
    "message_ids",
    "file_id",
    "file_ids",
    "artifact_id",
    "artifact_ids",
    "memory_record_id",
    "memory_record_ids",
    "included_message_ids",
    "included_file_ids",
    "included_artifact_ids",
    "included_memory_record_ids",
}
NON_EXPANSION_INVARIANTS = {
    "long_term_cross_session_memory_enabled": False,
    "stores_private_executor_material_as_memory": False,
    "frontend_state_is_canonical_context": False,
    "gate_closure_claimed": False,
}
REMAINING_GATE_BOUNDARIES = [
    "issue review and closure evidence",
    "runtime evidence review against merged source",
    "memory export boundary",
    "rollback boundary",
]


def sanitize_base_url(value: str) -> str:
    raw = str(value or DEFAULT_BASE_URL).strip()
    parsed = parse.urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return DEFAULT_BASE_URL
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return parse.urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("detail") or payload.get("error") or ""
    else:
        value = str(payload or "")[:120]
    return _redact_text(str(value))


def _normalized_key(value: object) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum() or ch == "_")


def _redact_text(value: str) -> str:
    redacted = str(value or "")
    replacements = [
        ("executor_private_payload", "[private-key]"),
        ("executor_payload", "[private-key]"),
        ("runtime_private_payload", "[private-key]"),
        ("private_payload", "[private-key]"),
        ("raw_storage_key", "[private-key]"),
        ("storage_key", "[private-key]"),
        ("sandbox_workdir", "[private-key]"),
        ("callback_token", "[private-key]"),
        ("/tmp/", "[private-path]/"),
        ("/home/", "[private-path]/"),
        ("/var/lib/ai-platform", "[private-path]"),
        ("tenants/default", "[tenant-path]"),
    ]
    for source, replacement in replacements:
        redacted = redacted.replace(source, replacement)
    redacted = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+", "[local-user-path]", redacted)
    return redacted


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _redact_text(str(key)): _redact_payload(item)
            for key, item in value.items()
            if _normalized_key(key) not in {_normalized_key(marker) for marker in PRIVATE_KEY_MARKERS}
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _contains_private_projection_term(value: Any) -> bool:
    def walk(item: Any, *, key_path: bool = False) -> bool:
        if isinstance(item, dict):
            for key, nested in item.items():
                key_text = str(key).lower()
                if any(marker in key_text for marker in PRIVATE_KEY_MARKERS):
                    return True
                if walk(nested):
                    return True
            return False
        if isinstance(item, list):
            return any(walk(nested) for nested in item)
        if isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in PRIVATE_VALUE_MARKERS):
                return True
            if any(marker in lowered for marker in PRIVATE_KEY_MARKERS):
                return True
            return any(pattern.search(item) is not None for pattern in PRIVATE_VALUE_PATTERNS)
        return False

    return walk(value)


def _contains_raw_material_id_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_key(key) in RAW_MATERIAL_ID_KEYS:
                return True
            if _contains_raw_material_id_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_raw_material_id_key(item) for item in value)
    return False


def _json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> tuple[int, Any]:
    data = None
    request_headers = {"Accept": "application/json", **headers}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8")) if raw else None
    except error.HTTPError as exc:
        raw = exc.read()
        try:
            body: Any = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            body = raw.decode("utf-8", errors="replace")[:300]
        return int(exc.code), body
    except Exception as exc:
        return 0, {"error": str(exc)}


def _headers(*, user_id: str, tenant_id: str, gateway_secret: str, roles: str = "user") -> dict[str, str]:
    headers = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Tenant-ID": tenant_id,
        "X-AI-Roles": roles,
    }
    if gateway_secret:
        headers["X-AI-Gateway-Secret"] = gateway_secret
    return headers


def _check(name: str, passed: bool, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"passed": bool(passed), **_redact_payload(evidence or {})}


def _public_context_summary(payload: Any) -> dict[str, Any]:
    body = _as_dict(payload)
    materials = _as_dict(body.get("referenced_materials"))
    used = _as_dict(body.get("used_context_summary"))
    counts: dict[str, int] = {}
    counts_valid = True
    for key in ("message_count", "file_count", "artifact_count", "memory_record_count"):
        value = materials.get(key)
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        counts_valid = counts_valid and valid
        counts[key] = int(value) if valid else 0
    input_keys = [str(item) for item in _as_list(used.get("input_keys"))]
    unsafe_input_keys = [key for key in input_keys if key not in SAFE_INPUT_KEYS]
    source = str(used.get("source") or "")
    memory_policy_source = str(used.get("memory_policy_source") or "")
    long_term_memory_read = used.get("long_term_memory_read")
    context_pack_version = str(body.get("context_pack_version") or "")
    context_pack_generated_at = str(body.get("context_pack_generated_at") or "")
    execution_tier = str(body.get("execution_tier") or "")
    missing = []
    if not source:
        missing.append("used_context_summary.source")
    if memory_policy_source not in PUBLIC_CONTEXT_ALLOWED_MEMORY_POLICY_SOURCES:
        missing.append("used_context_summary.memory_policy_source")
    if not isinstance(long_term_memory_read, bool):
        missing.append("used_context_summary.long_term_memory_read")
    if not execution_tier:
        missing.append("execution_tier")
    if not context_pack_version:
        missing.append("context_pack_version")
    if not context_pack_generated_at:
        missing.append("context_pack_generated_at")
    return {
        "counts": counts,
        "counts_valid": counts_valid,
        "input_keys": input_keys,
        "unsafe_input_key_count": len(unsafe_input_keys),
        "memory_policy_source": memory_policy_source,
        "long_term_memory_read": long_term_memory_read if isinstance(long_term_memory_read, bool) else None,
        "execution_tier_present": bool(execution_tier),
        "context_pack_version_present": bool(context_pack_version),
        "context_pack_generated_at_present": bool(context_pack_generated_at),
        "missing_public_fields": missing,
        "raw_material_id_fields_present": _contains_raw_material_id_key(body),
        "private_projection_terms_present": _contains_private_projection_term(body),
    }


def _context_summary_ok(summary: dict[str, Any], *, expected_memory_count: int) -> bool:
    counts = _as_dict(summary.get("counts"))
    return (
        bool(summary.get("counts_valid"))
        and counts.get("memory_record_count") == expected_memory_count
        and not summary.get("missing_public_fields")
        and not summary.get("raw_material_id_fields_present")
        and not summary.get("private_projection_terms_present")
        and summary.get("long_term_memory_read") is False
        and int(summary.get("unsafe_input_key_count") or 0) == 0
    )


def _latest_snapshot_payload(payload: Any) -> dict[str, Any]:
    snapshots = _as_list(_as_dict(payload).get("context_snapshots"))
    if not snapshots:
        return {}
    first = _as_dict(snapshots[0])
    return _as_dict(first.get("payload"))


def _event_type(event: Any) -> str:
    body = _as_dict(event)
    return str(body.get("event_type") or body.get("type") or "")


def _find_event(events: Any, event_type: str) -> dict[str, Any]:
    for event in _as_list(events):
        body = _as_dict(event)
        if _event_type(body) == event_type:
            return body
    return {}


def _run_detail_context_ref(payload: Any) -> dict[str, Any]:
    return _as_dict(_as_dict(payload).get("context_ref"))


def _live_worker_payload(
    *,
    run_detail_status: int,
    run_detail_payload: Any,
    expected_agent_id: str,
    expected_capability_id: str,
) -> dict[str, Any]:
    detail = _as_dict(run_detail_payload)
    result = _as_dict(detail.get("result"))
    executor = _as_dict(result.get("executor"))
    context_ref = _run_detail_context_ref(detail)
    context_pack_version = str(context_ref.get("context_pack_version") or "")
    context_pack_generated_at = str(context_ref.get("context_pack_generated_at") or "")
    worker_event = _find_event(detail.get("events"), "worker_started")
    status = str(detail.get("status") or "")
    return {
        "run_detail_status": run_detail_status,
        "run_status": status,
        "document_workflow": (
            detail.get("agent_id") == expected_agent_id
            and detail.get("capability_id") == expected_capability_id
            and bool(detail.get("artifacts"))
        ),
        "live_worker_run_observed": status in {"succeeded", "failed", "cancelled"},
        "worker_started_event_observed": bool(worker_event),
        "context_snapshot_id_present": bool(context_ref.get("context_snapshot_id")),
        "context_pack_schema_present": bool(context_pack_version and context_pack_generated_at),
        "context_pack_version_present": bool(context_pack_version),
        "context_pack_generated_at_present": bool(context_pack_generated_at),
        "executor_type": executor.get("executor_type"),
        "executor_schema_version_present": bool(executor.get("schema_version")),
        "artifact_count": len(_as_list(detail.get("artifacts"))),
        "step_count": len(_as_list(detail.get("steps"))),
    }


def _provenance_section(
    *,
    snapshot_summary: dict[str, Any],
    playback_context_summary: dict[str, Any],
    live_worker_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "context_snapshot_public_provenance": _context_summary_ok(snapshot_summary, expected_memory_count=1),
        "playback_public_projection": _context_summary_ok(playback_context_summary, expected_memory_count=1),
        "memory_policy_source_present": bool(snapshot_summary.get("memory_policy_source")),
        "context_pack_version_present": bool(snapshot_summary.get("context_pack_version_present")),
        "context_pack_generated_at_present": bool(snapshot_summary.get("context_pack_generated_at_present")),
        "worker_context_pack_version_present": bool(live_worker_payload.get("context_pack_version_present")),
        "worker_context_pack_generated_at_present": bool(live_worker_payload.get("context_pack_generated_at_present")),
    }


def _delete_redaction_section(
    *,
    deleted_absent: bool,
    redaction_failed: bool,
    future_snapshot_summary: dict[str, Any],
    latest_listed_snapshot_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "deleted_memory_absent_from_future_context": bool(deleted_absent),
        "redaction_scan_status": "failed" if redaction_failed else "passed",
        "private_projection_terms_present": bool(redaction_failed),
        "future_context_memory_count": _as_dict(future_snapshot_summary.get("counts")).get("memory_record_count"),
        "latest_listed_context_memory_count": _as_dict(latest_listed_snapshot_summary.get("counts")).get("memory_record_count"),
    }


def _rollback_disable_section(
    *,
    disabled_blocks_create: bool,
    disabled_blocks_list: bool,
    memory_policy_enabled: bool,
    redaction_failed: bool,
) -> dict[str, Any]:
    return {
        "memory_policy_disabled_blocks_create": bool(disabled_blocks_create),
        "memory_policy_disabled_blocks_list": bool(disabled_blocks_list),
        "memory_policy_reenabled_for_governed_scope": bool(memory_policy_enabled),
        "public_projections_hide_private_context_material": not redaction_failed,
    }


def _same_tenant_boundary_section(
    *,
    owner_context_visible: bool,
    same_tenant_cross_user_denied: bool,
    cross_tenant_context_denied: bool,
    owner_context_status: int,
    same_tenant_cross_user_status: int,
    cross_tenant_context_status: int,
) -> dict[str, Any]:
    return {
        "owner_context_visible": bool(owner_context_visible),
        "same_tenant_cross_user_denied": bool(same_tenant_cross_user_denied),
        "cross_tenant_context_denied": bool(cross_tenant_context_denied),
        "owner_context_status": owner_context_status,
        "same_tenant_cross_user_status": same_tenant_cross_user_status,
        "cross_tenant_context_status": cross_tenant_context_status,
    }


def _admin_visibility_section(
    *,
    admin_run_detail_visible: bool,
    admin_runtime_overview_visible: bool,
    ordinary_user_admin_overview_denied: bool,
    admin_projection_redacted: bool,
    admin_run_detail_status: int,
    admin_runtime_overview_status: int,
    ordinary_user_admin_overview_status: int,
) -> dict[str, Any]:
    return {
        "admin_run_detail_visible": bool(admin_run_detail_visible),
        "admin_runtime_overview_visible": bool(admin_runtime_overview_visible),
        "ordinary_user_admin_overview_denied": bool(ordinary_user_admin_overview_denied),
        "admin_projection_redacted": bool(admin_projection_redacted),
        "admin_run_detail_status": admin_run_detail_status,
        "admin_runtime_overview_status": admin_runtime_overview_status,
        "ordinary_user_admin_overview_status": ordinary_user_admin_overview_status,
    }


def _deny_path_section(
    *,
    cross_context_denied: bool,
    cross_tenant_context_denied: bool,
    disabled_blocks_create: bool,
    disabled_blocks_list: bool,
    ordinary_user_admin_overview_denied: bool,
    long_term_memory_fail_closed: bool,
    cross_context_status: int,
    cross_tenant_context_status: int,
    disabled_create_status: int,
    disabled_list_status: int,
    ordinary_user_admin_overview_status: int,
) -> dict[str, Any]:
    return {
        "cross_user_context_denied": bool(cross_context_denied),
        "cross_tenant_context_denied": bool(cross_tenant_context_denied),
        "memory_policy_disabled_blocks_create": bool(disabled_blocks_create),
        "memory_policy_disabled_blocks_list": bool(disabled_blocks_list),
        "ordinary_user_admin_overview_denied": bool(ordinary_user_admin_overview_denied),
        "long_term_memory_fail_closed": bool(long_term_memory_fail_closed),
        "cross_context_status": cross_context_status,
        "cross_tenant_context_status": cross_tenant_context_status,
        "disabled_create_status": disabled_create_status,
        "disabled_list_status": disabled_list_status,
        "ordinary_user_admin_overview_status": ordinary_user_admin_overview_status,
    }


def _policy_posture_section(
    *,
    workspace_id: str,
    policy: dict[str, Any],
    retention_days: int,
    disabled_blocks_create: bool,
    disabled_blocks_list: bool,
    deleted_absent: bool,
    redaction_failed: bool,
    long_term_memory_fail_closed: bool,
    snapshot_summary: dict[str, Any],
) -> dict[str, Any]:
    policy_retention_days = policy.get("retention_days")
    if not isinstance(policy_retention_days, int):
        policy_retention_days = retention_days
    return {
        "session_workspace_scope": (
            bool(workspace_id)
            and policy.get("workspace_id") == workspace_id
            and policy.get("long_term_memory_enabled") is False
        ),
        "retention_days": policy_retention_days,
        "retention_policy_present": isinstance(policy_retention_days, int)
        and policy_retention_days > 0,
        "opt_out_disable_policy_present": bool(disabled_blocks_create and disabled_blocks_list),
        "export_projection_only": (
            snapshot_summary.get("private_projection_terms_present") is False
            and snapshot_summary.get("raw_material_id_fields_present") is False
        ),
        "delete_redaction_posture_present": bool(deleted_absent and not redaction_failed),
        "long_term_memory_fail_closed": bool(long_term_memory_fail_closed),
    }


def _upload_probe_document(
    *,
    base_url: str,
    headers: dict[str, str],
    workspace_id: str,
    timeout_seconds: float,
    path: str,
) -> tuple[int, Any]:
    content = Path(path).read_bytes()
    boundary = f"----ai-platform-b1-{int(time.time() * 1000)}"
    filename = Path(path).name or "b1-smoke.docx"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="workspace_id"\r\n\r\n'
        f"{workspace_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **headers,
    }
    req = request.Request(
        f"{base_url}/api/ai/files",
        data=body,
        headers=request_headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8")) if raw else None
    except error.HTTPError as exc:
        raw = exc.read()
        try:
            payload: Any = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = raw.decode("utf-8", errors="replace")[:300]
        return int(exc.code), payload
    except Exception as exc:
        return 0, {"error": str(exc)}


def _poll_run_detail(
    *,
    base_url: str,
    run_id: str,
    headers: dict[str, str],
    timeout_seconds: float,
    wait_seconds: float,
) -> tuple[int, Any]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    last_status = 0
    last_payload: Any = None
    while True:
        status, payload = _json_request(
            "GET",
            f"{base_url}/api/ai/runs/{run_id}",
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        last_status, last_payload = status, payload
        run_status = str(_as_dict(payload).get("status") or "")
        if status == 200 and run_status in {"succeeded", "failed", "cancelled"}:
            return status, payload
        if time.monotonic() >= deadline:
            return last_status, last_payload
        time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))


def _contract_fixture_context_payload(*, memory_record_count: int) -> dict[str, Any]:
    return {
        "referenced_materials": {
            "message_count": 0,
            "file_count": 1,
            "artifact_count": 0,
            "memory_record_count": memory_record_count,
        },
        "used_context_summary": {
            "source": "runs_api",
            "input_keys": ["task", "memory"],
            "memory_policy_source": "user",
            "long_term_memory_read": False,
        },
        "context_pack_version": "context-pack-v1",
        "context_pack_generated_at": "2026-07-05T00:00:00Z",
        "execution_tier": "worker",
    }


def _contract_fixture_run_detail(
    *,
    agent_id: str,
    capability_id: str,
) -> dict[str, Any]:
    return {
        "run_id": "run-doc",
        "agent_id": agent_id,
        "capability_id": capability_id,
        "status": "succeeded",
        "artifacts": [{"artifact_id": "artifact-1", "kind": "document_review"}],
        "steps": [{"step_id": "worker-step-1"}],
        "events": [{"event_type": "worker_started"}],
        "context_ref": {
            "context_snapshot_id": "snapshot-1",
            "context_pack_version": "context-pack-v1",
            "context_pack_generated_at": "2026-07-05T00:00:00Z",
        },
        "result": {
            "executor": {
                "executor_type": "worker",
                "schema_version": "ai-platform.executor-result.v1",
            }
        },
    }


def build_b1_memory_context_workflow_contract_fixture(
    *,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
    tenant_id: str = "default",
    workspace_id: str = "default",
    agent_id: str = "document-review",
    capability_id: str = "document_review",
) -> dict[str, Any]:
    """Build a local source-contract fixture without contacting a live API."""
    snapshot_summary = _public_context_summary(
        _contract_fixture_context_payload(memory_record_count=1)
    )
    playback_context_summary = _public_context_summary(
        _contract_fixture_context_payload(memory_record_count=1)
    )
    future_snapshot_summary = _public_context_summary(
        _contract_fixture_context_payload(memory_record_count=0)
    )
    latest_listed_snapshot_summary = _public_context_summary(
        _contract_fixture_context_payload(memory_record_count=0)
    )
    live_worker = _live_worker_payload(
        run_detail_status=200,
        run_detail_payload=_contract_fixture_run_detail(
            agent_id=agent_id,
            capability_id=capability_id,
        ),
        expected_agent_id=agent_id,
        expected_capability_id=capability_id,
    )
    provenance = _provenance_section(
        snapshot_summary=snapshot_summary,
        playback_context_summary=playback_context_summary,
        live_worker_payload=live_worker,
    )
    delete_redaction = _delete_redaction_section(
        deleted_absent=True,
        redaction_failed=False,
        future_snapshot_summary=future_snapshot_summary,
        latest_listed_snapshot_summary=latest_listed_snapshot_summary,
    )
    rollback_disable = _rollback_disable_section(
        disabled_blocks_create=True,
        disabled_blocks_list=True,
        memory_policy_enabled=True,
        redaction_failed=False,
    )
    same_tenant_boundary = _same_tenant_boundary_section(
        owner_context_visible=True,
        same_tenant_cross_user_denied=True,
        cross_tenant_context_denied=True,
        owner_context_status=200,
        same_tenant_cross_user_status=403,
        cross_tenant_context_status=403,
    )
    admin_visibility = _admin_visibility_section(
        admin_run_detail_visible=True,
        admin_runtime_overview_visible=True,
        ordinary_user_admin_overview_denied=True,
        admin_projection_redacted=True,
        admin_run_detail_status=200,
        admin_runtime_overview_status=200,
        ordinary_user_admin_overview_status=403,
    )
    deny_path = _deny_path_section(
        cross_context_denied=True,
        cross_tenant_context_denied=True,
        disabled_blocks_create=True,
        disabled_blocks_list=True,
        ordinary_user_admin_overview_denied=True,
        long_term_memory_fail_closed=True,
        cross_context_status=403,
        cross_tenant_context_status=403,
        disabled_create_status=403,
        disabled_list_status=200,
        ordinary_user_admin_overview_status=403,
    )
    policy = {
        "workspace_id": workspace_id,
        "long_term_memory_enabled": False,
        "retention_days": DEFAULT_RETENTION_DAYS,
    }
    policy_posture = _policy_posture_section(
        workspace_id=workspace_id,
        policy=policy,
        retention_days=DEFAULT_RETENTION_DAYS,
        disabled_blocks_create=True,
        disabled_blocks_list=True,
        deleted_absent=True,
        redaction_failed=False,
        long_term_memory_fail_closed=True,
        snapshot_summary=snapshot_summary,
    )
    checks = {
        "admin_runtime_visibility": _check(
            "admin_runtime_visibility",
            True,
            admin_visibility,
        ),
        "ordinary_user_admin_visibility_denied": _check(
            "ordinary_user_admin_visibility_denied",
            True,
            {"status": 403},
        ),
        "same_tenant_context_boundary": _check(
            "same_tenant_context_boundary",
            True,
            same_tenant_boundary,
        ),
        "cross_tenant_context_denied": _check(
            "cross_tenant_context_denied",
            True,
            {"status": 403},
        ),
        "create_governed_run": _check(
            "create_governed_run",
            True,
            {
                "status": 200,
                "document_run_status": 200,
                "run_id_present": True,
                "session_id_present": True,
                "document_run_id_present": True,
                "document_session_id_present": True,
            },
        ),
        "live_worker_payload": _check("live_worker_payload", True, live_worker),
        "memory_policy_disabled_blocks_create": _check(
            "memory_policy_disabled_blocks_create",
            True,
            {"policy_status": 200, "create_status": 403},
        ),
        "memory_policy_disabled_blocks_list": _check(
            "memory_policy_disabled_blocks_list",
            True,
            {"list_status": 200, "listed_record_count": 0},
        ),
        "rollback_disable_behavior": _check(
            "rollback_disable_behavior",
            True,
            rollback_disable,
        ),
        "memory_policy_enabled_for_governed_scope": _check(
            "memory_policy_enabled_for_governed_scope",
            True,
            {
                "status": 200,
                "reenable_status": 200,
                "workspace_id": workspace_id,
                "agent_id_present": True,
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": DEFAULT_RETENTION_DAYS,
                "source": "user",
            },
        ),
        "memory_record_create_and_list": _check(
            "memory_record_create_and_list",
            True,
            {"create_status": 200, "list_status": 200, "record_created": True},
        ),
        "context_snapshot_public_provenance": _check(
            "context_snapshot_public_provenance",
            True,
            {"status": 200, "summary": snapshot_summary},
        ),
        "playback_public_projection": _check(
            "playback_public_projection",
            True,
            {"status": 200, "summary": playback_context_summary},
        ),
        "cross_user_context_denied": _check(
            "cross_user_context_denied",
            True,
            {"status": 403},
        ),
        "deleted_memory_absent_from_future_context": _check(
            "deleted_memory_absent_from_future_context",
            True,
            {
                "delete_status": 200,
                "list_after_delete_status": 200,
                "records_after_delete_count": 0,
                "snapshot_after_delete_status": 200,
                "future_summary": future_snapshot_summary,
                "list_snapshots_status": 200,
                "latest_listed_summary": latest_listed_snapshot_summary,
            },
        ),
        "long_term_memory_fail_closed": _check(
            "long_term_memory_fail_closed",
            True,
            {"long_term_memory_enabled": False},
        ),
        "no_private_projection_leakage": _check(
            "no_private_projection_leakage",
            True,
            {"redaction_scan_status": "passed"},
        ),
    }
    ok = all(item["passed"] is True for item in checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "target": CONTRACT_FIXTURE_TARGET,
        "acceptance_gap": ACCEPTANCE_GAP,
        "redaction_scan_status": "passed",
        "memory_record_count": 1,
        "does_not_run_live_target": True,
        "source": {
            "commit_sha": commit_sha,
            "runtime_subject_commit_sha": runtime_subject_commit_sha or commit_sha,
            "image": image,
            "tenant_id": tenant_id,
            "gateway_secret_supplied": False,
            "does_not_run_live_target": True,
        },
        "workflow": {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "capability_id": capability_id,
            "run_id_present": True,
            "session_id_present": True,
            "document_run_id_present": True,
            "document_session_id_present": True,
            "probe_file_bound": True,
            "memory_record_created": True,
        },
        "checks": checks,
        "live_worker_payload": live_worker,
        "provenance": provenance,
        "delete_redaction": delete_redaction,
        "rollback_disable": rollback_disable,
        "same_tenant_boundary": same_tenant_boundary,
        "admin_visibility": admin_visibility,
        "deny_path": deny_path,
        "policy_posture": policy_posture,
        "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
        "does_not_close_b1_gate": True,
        "remaining_gate_boundaries": list(REMAINING_GATE_BOUNDARIES),
    }


def build_b1_memory_context_workflow_smoke(
    *,
    base_url: str,
    gateway_secret: str,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
    tenant_id: str = "default",
    other_tenant_id: str = "",
    workspace_id: str = "default",
    agent_id: str = "document-review",
    capability_id: str = "document_review",
    user_id: str = "b1-memory-smoke-user",
    cross_user_id: str = "b1-memory-smoke-cross-user",
    operator_user_id: str = "b1-memory-smoke-operator",
    probe_file_id: str = "",
    probe_file_path: str = "",
    timeout_seconds: float = 10.0,
    wait_seconds: float = 30.0,
) -> dict[str, Any]:
    safe_base_url = sanitize_base_url(base_url)
    owner_headers = _headers(user_id=user_id, tenant_id=tenant_id, gateway_secret=gateway_secret)
    cross_headers = _headers(user_id=cross_user_id, tenant_id=tenant_id, gateway_secret=gateway_secret)
    other_tenant_headers = _headers(
        user_id=cross_user_id,
        tenant_id=other_tenant_id or f"{tenant_id}-other",
        gateway_secret=gateway_secret,
    )
    operator_headers = _headers(
        user_id=operator_user_id,
        tenant_id=tenant_id,
        gateway_secret=gateway_secret,
        roles="admin",
    )

    statuses: dict[str, int] = {}
    payloads: dict[str, Any] = {}

    file_id = str(probe_file_id or "").strip()
    if not file_id and probe_file_path:
        upload_status, upload_payload = _upload_probe_document(
            base_url=safe_base_url,
            headers=owner_headers,
            workspace_id=workspace_id,
            timeout_seconds=timeout_seconds,
            path=probe_file_path,
        )
        statuses["upload_probe_document"] = upload_status
        payloads["upload_probe_document"] = upload_payload
        file_id = str(_as_dict(upload_payload).get("file_id") or "")

    create_run_status, create_run_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/runs",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": "general-agent",
            "capability_id": "general_chat",
            "title": "b1-memory-context-smoke",
            "input": {"task": "b1-memory-context-smoke", "memory": "enabled-scope-probe"},
            "file_ids": [],
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["create_run"] = create_run_status
    payloads["create_run"] = create_run_payload
    run_id = str(_as_dict(create_run_payload).get("run_id") or "")
    session_id = str(_as_dict(create_run_payload).get("session_id") or "")

    enable_policy_status, enable_policy_payload = _json_request(
        "PUT",
        f"{safe_base_url}/api/ai/memory/policy",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "redaction_mode": "strict",
            "reason": "b1 smoke enable governed scope",
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["enable_policy"] = enable_policy_status
    policy = _as_dict(_as_dict(enable_policy_payload).get("memory_policy"))

    create_memory_status, create_memory_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/memory/records",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "record_type": "task_note",
            "content": "public memory note for B1 smoke",
            "metadata": {"source": "b1_smoke"},
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["create_memory"] = create_memory_status
    memory_record = _as_dict(_as_dict(create_memory_payload).get("memory_record"))
    memory_record_id = str(memory_record.get("memory_record_id") or "")

    list_url = (
        f"{safe_base_url}/api/ai/memory/records?"
        f"{parse.urlencode({'workspace_id': workspace_id, 'agent_id': agent_id, 'session_id': session_id})}"
    )
    list_memory_status, list_memory_payload = _json_request(
        "GET",
        list_url,
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["list_memory"] = list_memory_status
    listed_records = _as_list(_as_dict(list_memory_payload).get("memory_records"))

    document_run_status, document_run_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/runs",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "capability_id": capability_id,
            "title": "b1-memory-context-document-worker-smoke",
            "input": {"task": "b1-memory-context-document-worker-smoke", "memory": "worker-context-probe"},
            "file_ids": [file_id] if file_id else [],
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["create_document_run"] = document_run_status
    document_run_id = str(_as_dict(document_run_payload).get("run_id") or "")
    document_session_id = str(_as_dict(document_run_payload).get("session_id") or "")

    run_detail_status, run_detail_payload = _poll_run_detail(
        base_url=safe_base_url,
        run_id=document_run_id,
        headers=operator_headers,
        timeout_seconds=timeout_seconds,
        wait_seconds=wait_seconds,
    )
    statuses["document_run_detail"] = run_detail_status

    ordinary_admin_overview_status, ordinary_admin_overview_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/admin/runtime/overview",
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["ordinary_admin_overview"] = ordinary_admin_overview_status
    payloads["ordinary_admin_overview"] = ordinary_admin_overview_payload

    admin_overview_status, admin_overview_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/admin/runtime/overview",
        headers=operator_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["admin_overview"] = admin_overview_status
    payloads["admin_overview"] = admin_overview_payload

    disable_policy_status, disable_policy_payload = _json_request(
        "PUT",
        f"{safe_base_url}/api/ai/memory/policy",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "redaction_mode": "strict",
            "reason": "b1 smoke disable probe",
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["disable_policy"] = disable_policy_status
    disabled_create_status, disabled_create_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/memory/records",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "record_type": "task_note",
            "content": "must not be stored while memory is disabled",
            "metadata": {"source": "b1_smoke_disabled_probe"},
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["disabled_create"] = disabled_create_status
    payloads["disabled_create"] = disabled_create_payload
    disabled_list_url = (
        f"{safe_base_url}/api/ai/memory/records?"
        f"{parse.urlencode({'workspace_id': workspace_id, 'agent_id': agent_id, 'session_id': session_id})}"
    )
    disabled_list_status, disabled_list_payload = _json_request(
        "GET",
        disabled_list_url,
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["disabled_list"] = disabled_list_status
    disabled_list_records = _as_list(_as_dict(disabled_list_payload).get("memory_records"))

    reenable_policy_status, reenable_policy_payload = _json_request(
        "PUT",
        f"{safe_base_url}/api/ai/memory/policy",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "redaction_mode": "strict",
            "reason": "b1 smoke enable governed scope",
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["reenable_policy"] = reenable_policy_status
    reenabled_policy = _as_dict(_as_dict(reenable_policy_payload).get("memory_policy"))

    snapshot_with_memory_status, snapshot_with_memory_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/runs/{run_id}/context/snapshots",
        headers=owner_headers,
        payload={
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [memory_record_id] if memory_record_id else [],
            "redaction_summary": {"mode": "strict"},
            "payload": {
                "task": "b1-memory-context-smoke",
                "memory": "public bounded summary only",
            },
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["snapshot_with_memory"] = snapshot_with_memory_status
    snapshot_payload = _as_dict(_as_dict(snapshot_with_memory_payload).get("context_snapshot")).get("payload")
    snapshot_summary = _public_context_summary(snapshot_payload)

    playback_status, playback_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/runs/{run_id}/playback",
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["playback"] = playback_status
    playback_context_summary = _public_context_summary(_as_dict(playback_payload).get("context_ref"))

    cross_context_status, cross_context_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/runs/{run_id}/context/snapshots",
        headers=cross_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["cross_context"] = cross_context_status
    payloads["cross_context"] = cross_context_payload

    cross_tenant_context_status, cross_tenant_context_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/runs/{run_id}/context/snapshots",
        headers=other_tenant_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["cross_tenant_context"] = cross_tenant_context_status
    payloads["cross_tenant_context"] = cross_tenant_context_payload

    delete_status, delete_payload = _json_request(
        "DELETE",
        f"{safe_base_url}/api/ai/memory/records/{parse.quote(memory_record_id)}?"
        f"{parse.urlencode({'workspace_id': workspace_id, 'agent_id': agent_id, 'session_id': session_id, 'reason': 'b1 smoke delete probe'})}",
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["delete_memory"] = delete_status
    deleted_record = _as_dict(_as_dict(delete_payload).get("memory_record"))

    list_after_delete_status, list_after_delete_payload = _json_request(
        "GET",
        list_url,
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["list_after_delete"] = list_after_delete_status
    records_after_delete = _as_list(_as_dict(list_after_delete_payload).get("memory_records"))

    snapshot_after_delete_status, snapshot_after_delete_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/runs/{run_id}/context/snapshots",
        headers=owner_headers,
        payload={
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary": {"mode": "strict"},
            "payload": {
                "task": "b1-memory-context-smoke-after-delete",
                "memory": "deleted memory omitted",
            },
        },
        timeout_seconds=timeout_seconds,
    )
    statuses["snapshot_after_delete"] = snapshot_after_delete_status
    future_snapshot_payload = _as_dict(_as_dict(snapshot_after_delete_payload).get("context_snapshot")).get("payload")
    future_snapshot_summary = _public_context_summary(future_snapshot_payload)

    list_snapshots_status, list_snapshots_payload = _json_request(
        "GET",
        f"{safe_base_url}/api/ai/runs/{run_id}/context/snapshots",
        headers=owner_headers,
        timeout_seconds=timeout_seconds,
    )
    statuses["list_snapshots"] = list_snapshots_status
    latest_listed_snapshot_summary = _public_context_summary(_latest_snapshot_payload(list_snapshots_payload))

    long_term_memory_fail_closed = policy.get("long_term_memory_enabled") is False
    memory_policy_enabled = (
        enable_policy_status == 200
        and reenable_policy_status == 200
        and policy.get("memory_enabled") is True
        and reenabled_policy.get("memory_enabled") is True
        and policy.get("long_term_memory_enabled") is False
        and policy.get("workspace_id") == workspace_id
    )
    disabled_blocks_create = (
        disable_policy_status == 200
        and disabled_create_status == 403
        and _safe_detail(disabled_create_payload) == "memory_policy_disabled"
    )
    disabled_blocks_list = disabled_list_status == 200 and not disabled_list_records
    memory_create_and_list = (
        create_memory_status == 200
        and bool(memory_record_id)
        and list_memory_status == 200
        and any(_as_dict(item).get("memory_record_id") == memory_record_id for item in listed_records)
    )
    context_projection_ok = (
        snapshot_with_memory_status == 200
        and _context_summary_ok(snapshot_summary, expected_memory_count=1)
    )
    playback_projection_ok = (
        playback_status == 200
        and _as_dict(playback_payload).get("contract_version") == "ai-platform.run-playback.v1"
        and _context_summary_ok(playback_context_summary, expected_memory_count=1)
    )
    cross_context_denied = cross_context_status in DENIED_STATUSES
    cross_tenant_context_denied = cross_tenant_context_status in DENIED_STATUSES
    owner_context_visible = list_snapshots_status == 200
    ordinary_user_admin_overview_denied = ordinary_admin_overview_status in DENIED_STATUSES
    admin_runtime_overview_visible = admin_overview_status == 200
    deleted_absent = (
        delete_status == 200
        and deleted_record.get("status") == "deleted"
        and list_after_delete_status == 200
        and not any(_as_dict(item).get("memory_record_id") == memory_record_id for item in records_after_delete)
        and snapshot_after_delete_status == 200
        and _context_summary_ok(future_snapshot_summary, expected_memory_count=0)
        and list_snapshots_status == 200
        and _context_summary_ok(latest_listed_snapshot_summary, expected_memory_count=0)
    )
    redaction_failed = any(
        _contains_private_projection_term(payload)
        for payload in (
            create_run_payload,
            enable_policy_payload,
            create_memory_payload,
            list_memory_payload,
            snapshot_with_memory_payload,
            playback_payload,
            delete_payload,
            list_after_delete_payload,
            snapshot_after_delete_payload,
            list_snapshots_payload,
            run_detail_payload,
            ordinary_admin_overview_payload,
            admin_overview_payload,
            cross_tenant_context_payload,
        )
    )
    live_worker = _live_worker_payload(
        run_detail_status=run_detail_status,
        run_detail_payload=run_detail_payload,
        expected_agent_id=agent_id,
        expected_capability_id=capability_id,
    )
    admin_projection_redacted = not _contains_private_projection_term(admin_overview_payload)
    provenance = _provenance_section(
        snapshot_summary=snapshot_summary,
        playback_context_summary=playback_context_summary,
        live_worker_payload=live_worker,
    )
    delete_redaction = _delete_redaction_section(
        deleted_absent=deleted_absent,
        redaction_failed=redaction_failed,
        future_snapshot_summary=future_snapshot_summary,
        latest_listed_snapshot_summary=latest_listed_snapshot_summary,
    )
    rollback_disable = _rollback_disable_section(
        disabled_blocks_create=disabled_blocks_create,
        disabled_blocks_list=disabled_blocks_list,
        memory_policy_enabled=memory_policy_enabled,
        redaction_failed=redaction_failed,
    )
    same_tenant_boundary = _same_tenant_boundary_section(
        owner_context_visible=owner_context_visible,
        same_tenant_cross_user_denied=cross_context_denied,
        cross_tenant_context_denied=cross_tenant_context_denied,
        owner_context_status=list_snapshots_status,
        same_tenant_cross_user_status=cross_context_status,
        cross_tenant_context_status=cross_tenant_context_status,
    )
    admin_visibility = _admin_visibility_section(
        admin_run_detail_visible=run_detail_status == 200,
        admin_runtime_overview_visible=admin_runtime_overview_visible,
        ordinary_user_admin_overview_denied=ordinary_user_admin_overview_denied,
        admin_projection_redacted=admin_projection_redacted,
        admin_run_detail_status=run_detail_status,
        admin_runtime_overview_status=admin_overview_status,
        ordinary_user_admin_overview_status=ordinary_admin_overview_status,
    )
    deny_path = _deny_path_section(
        cross_context_denied=cross_context_denied,
        cross_tenant_context_denied=cross_tenant_context_denied,
        disabled_blocks_create=disabled_blocks_create,
        disabled_blocks_list=disabled_blocks_list,
        ordinary_user_admin_overview_denied=ordinary_user_admin_overview_denied,
        long_term_memory_fail_closed=long_term_memory_fail_closed,
        cross_context_status=cross_context_status,
        cross_tenant_context_status=cross_tenant_context_status,
        disabled_create_status=disabled_create_status,
        disabled_list_status=disabled_list_status,
        ordinary_user_admin_overview_status=ordinary_admin_overview_status,
    )
    policy_posture = _policy_posture_section(
        workspace_id=workspace_id,
        policy=policy,
        retention_days=DEFAULT_RETENTION_DAYS,
        disabled_blocks_create=disabled_blocks_create,
        disabled_blocks_list=disabled_blocks_list,
        deleted_absent=deleted_absent,
        redaction_failed=redaction_failed,
        long_term_memory_fail_closed=long_term_memory_fail_closed,
        snapshot_summary=snapshot_summary,
    )
    checks = {
        "create_governed_run": _check(
            "create_governed_run",
            create_run_status == 200
            and bool(run_id)
            and bool(session_id)
            and document_run_status == 200
            and bool(document_run_id)
            and bool(document_session_id),
            {
                "status": create_run_status,
                "document_run_status": document_run_status,
                "run_id_present": bool(run_id),
                "session_id_present": bool(session_id),
                "document_run_id_present": bool(document_run_id),
                "document_session_id_present": bool(document_session_id),
            },
        ),
        "live_worker_payload": _check(
            "live_worker_payload",
            all(
                live_worker.get(key) is True
                for key in (
                    "document_workflow",
                    "live_worker_run_observed",
                    "worker_started_event_observed",
                    "context_snapshot_id_present",
                    "context_pack_schema_present",
                )
            )
            and int(live_worker.get("artifact_count") or 0) >= 1,
            live_worker,
        ),
        "admin_runtime_visibility": _check(
            "admin_runtime_visibility",
            admin_runtime_overview_visible and admin_projection_redacted,
            admin_visibility,
        ),
        "ordinary_user_admin_visibility_denied": _check(
            "ordinary_user_admin_visibility_denied",
            ordinary_user_admin_overview_denied,
            {"status": ordinary_admin_overview_status},
        ),
        "same_tenant_context_boundary": _check(
            "same_tenant_context_boundary",
            owner_context_visible and cross_context_denied,
            same_tenant_boundary,
        ),
        "cross_tenant_context_denied": _check(
            "cross_tenant_context_denied",
            cross_tenant_context_denied,
            {"status": cross_tenant_context_status},
        ),
        "rollback_disable_behavior": _check(
            "rollback_disable_behavior",
            all(
                rollback_disable.get(key) is True
                for key in (
                    "memory_policy_disabled_blocks_create",
                    "memory_policy_disabled_blocks_list",
                    "memory_policy_reenabled_for_governed_scope",
                    "public_projections_hide_private_context_material",
                )
            ),
            rollback_disable,
        ),
        "memory_policy_disabled_blocks_create": _check(
            "memory_policy_disabled_blocks_create",
            disabled_blocks_create,
            {"policy_status": disable_policy_status, "create_status": disabled_create_status},
        ),
        "memory_policy_disabled_blocks_list": _check(
            "memory_policy_disabled_blocks_list",
            disabled_blocks_list,
            {"list_status": disabled_list_status, "listed_record_count": len(disabled_list_records)},
        ),
        "memory_policy_enabled_for_governed_scope": _check(
            "memory_policy_enabled_for_governed_scope",
            memory_policy_enabled,
            {
                "status": enable_policy_status,
                "reenable_status": reenable_policy_status,
                "workspace_id": policy.get("workspace_id"),
                "agent_id_present": bool(policy.get("agent_id")),
                "memory_enabled": policy.get("memory_enabled"),
                "long_term_memory_enabled": policy.get("long_term_memory_enabled"),
                "retention_days": policy_posture.get("retention_days"),
                "source": policy.get("source"),
            },
        ),
        "memory_record_create_and_list": _check(
            "memory_record_create_and_list",
            memory_create_and_list,
            {
                "create_status": create_memory_status,
                "list_status": list_memory_status,
                "record_created": bool(memory_record_id),
                "listed_record_count": len(listed_records),
            },
        ),
        "context_snapshot_public_provenance": _check(
            "context_snapshot_public_provenance",
            context_projection_ok,
            {"status": snapshot_with_memory_status, "summary": snapshot_summary},
        ),
        "playback_public_projection": _check(
            "playback_public_projection",
            playback_projection_ok,
            {"status": playback_status, "summary": playback_context_summary},
        ),
        "cross_user_context_denied": _check(
            "cross_user_context_denied",
            cross_context_denied,
            {"status": cross_context_status},
        ),
        "deleted_memory_absent_from_future_context": _check(
            "deleted_memory_absent_from_future_context",
            deleted_absent,
            {
                "delete_status": delete_status,
                "list_after_delete_status": list_after_delete_status,
                "records_after_delete_count": len(records_after_delete),
                "snapshot_after_delete_status": snapshot_after_delete_status,
                "future_summary": future_snapshot_summary,
                "list_snapshots_status": list_snapshots_status,
                "latest_listed_summary": latest_listed_snapshot_summary,
            },
        ),
        "long_term_memory_fail_closed": _check(
            "long_term_memory_fail_closed",
            long_term_memory_fail_closed,
            {"long_term_memory_enabled": policy.get("long_term_memory_enabled")},
        ),
        "no_private_projection_leakage": _check(
            "no_private_projection_leakage",
            not redaction_failed,
            {"redaction_scan_status": "failed" if redaction_failed else "passed"},
        ),
    }
    ok = all(item["passed"] is True for item in checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "target": TARGET,
        "acceptance_gap": ACCEPTANCE_GAP,
        "redaction_scan_status": "failed" if redaction_failed else "passed",
        "memory_record_count": 1 if memory_create_and_list else 0,
        "source": {
            "base_url": safe_base_url,
            "commit_sha": commit_sha,
            "runtime_subject_commit_sha": runtime_subject_commit_sha or commit_sha,
            "image": image,
            "tenant_id": tenant_id,
            "gateway_secret_supplied": bool(gateway_secret),
        },
        "workflow": {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "capability_id": capability_id,
            "run_id_present": bool(run_id),
            "session_id_present": bool(session_id),
            "document_run_id_present": bool(document_run_id),
            "document_session_id_present": bool(document_session_id),
            "probe_file_bound": bool(file_id),
            "memory_record_created": bool(memory_record_id),
        },
        "checks": checks,
        "live_worker_payload": live_worker,
        "provenance": provenance,
        "delete_redaction": delete_redaction,
        "rollback_disable": rollback_disable,
        "same_tenant_boundary": same_tenant_boundary,
        "admin_visibility": admin_visibility,
        "deny_path": deny_path,
        "policy_posture": policy_posture,
        "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
        "does_not_close_b1_gate": True,
        "remaining_gate_boundaries": list(REMAINING_GATE_BOUNDARIES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify B1 memory/context workflow behavior for ai-platform.")
    parser.add_argument(
        "--contract-fixture",
        action="store_true",
        help="Emit a local source-contract fixture without contacting a live target.",
    )
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument(
        "--runtime-subject-commit-sha",
        default=os.environ.get("AI_PLATFORM_RUNTIME_SUBJECT_COMMIT_SHA", ""),
    )
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--other-tenant-id", default="")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--agent-id", default="document-review")
    parser.add_argument("--capability-id", default="document_review")
    parser.add_argument("--user-id", default="b1-memory-smoke-user")
    parser.add_argument("--cross-user-id", default="b1-memory-smoke-cross-user")
    parser.add_argument("--operator-user-id", default="b1-memory-smoke-operator")
    parser.add_argument("--probe-file-id", default=os.environ.get("AI_PLATFORM_B1_PROBE_FILE_ID", ""))
    parser.add_argument("--probe-file-path", default=os.environ.get("AI_PLATFORM_B1_PROBE_FILE_PATH", ""))
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    args = parser.parse_args()

    if args.contract_fixture:
        evidence = build_b1_memory_context_workflow_contract_fixture(
            commit_sha=args.commit_sha,
            runtime_subject_commit_sha=args.runtime_subject_commit_sha,
            image=args.image,
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
            agent_id=args.agent_id,
            capability_id=args.capability_id,
        )
    else:
        evidence = build_b1_memory_context_workflow_smoke(
            base_url=args.base_url,
            gateway_secret=os.environ.get(args.gateway_secret_env, ""),
            commit_sha=args.commit_sha,
            runtime_subject_commit_sha=args.runtime_subject_commit_sha,
            image=args.image,
            tenant_id=args.tenant_id,
            other_tenant_id=args.other_tenant_id,
            workspace_id=args.workspace_id,
            agent_id=args.agent_id,
            capability_id=args.capability_id,
            user_id=args.user_id,
            cross_user_id=args.cross_user_id,
            operator_user_id=args.operator_user_id,
            probe_file_id=args.probe_file_id,
            probe_file_path=args.probe_file_path,
            timeout_seconds=args.timeout_seconds,
            wait_seconds=args.wait_seconds,
        )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

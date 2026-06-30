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
from typing import Any
from urllib import error, parse, request


SCHEMA_VERSION = "ai-platform.b1-memory-context-workflow-smoke.v1"
ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"
TARGET = "211_api_memory_context_workflow"
DEFAULT_BASE_URL = "http://127.0.0.1:8020"
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


def _headers(*, user_id: str, tenant_id: str, gateway_secret: str) -> dict[str, str]:
    headers = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Tenant-ID": tenant_id,
        "X-AI-Roles": "user",
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


def build_b1_memory_context_workflow_smoke(
    *,
    base_url: str,
    gateway_secret: str,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
    tenant_id: str = "default",
    workspace_id: str = "default",
    agent_id: str = "general-agent",
    user_id: str = "b1-memory-smoke-user",
    cross_user_id: str = "b1-memory-smoke-cross-user",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    safe_base_url = sanitize_base_url(base_url)
    owner_headers = _headers(user_id=user_id, tenant_id=tenant_id, gateway_secret=gateway_secret)
    cross_headers = _headers(user_id=cross_user_id, tenant_id=tenant_id, gateway_secret=gateway_secret)

    statuses: dict[str, int] = {}
    payloads: dict[str, Any] = {}

    create_run_status, create_run_payload = _json_request(
        "POST",
        f"{safe_base_url}/api/ai/runs",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
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
            "retention_days": 30,
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

    disable_policy_status, disable_policy_payload = _json_request(
        "PUT",
        f"{safe_base_url}/api/ai/memory/policy",
        headers=owner_headers,
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
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
            "retention_days": 30,
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
        )
    )
    checks = {
        "create_governed_run": _check(
            "create_governed_run",
            create_run_status == 200 and bool(run_id) and bool(session_id),
            {"status": create_run_status, "run_id_present": bool(run_id), "session_id_present": bool(session_id)},
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
            "run_id_present": bool(run_id),
            "session_id_present": bool(session_id),
            "memory_record_created": bool(memory_record_id),
        },
        "checks": checks,
        "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
        "does_not_close_b1_gate": True,
        "remaining_gate_boundaries": list(REMAINING_GATE_BOUNDARIES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify B1 memory/context workflow behavior for ai-platform.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument(
        "--runtime-subject-commit-sha",
        default=os.environ.get("AI_PLATFORM_RUNTIME_SUBJECT_COMMIT_SHA", ""),
    )
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--agent-id", default="general-agent")
    parser.add_argument("--user-id", default="b1-memory-smoke-user")
    parser.add_argument("--cross-user-id", default="b1-memory-smoke-cross-user")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    evidence = build_b1_memory_context_workflow_smoke(
        base_url=args.base_url,
        gateway_secret=os.environ.get(args.gateway_secret_env, ""),
        commit_sha=args.commit_sha,
        runtime_subject_commit_sha=args.runtime_subject_commit_sha,
        image=args.image,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        agent_id=args.agent_id,
        user_id=args.user_id,
        cross_user_id=args.cross_user_id,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

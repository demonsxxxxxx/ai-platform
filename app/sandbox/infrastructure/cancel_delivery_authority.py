"""Small, stable authority predicates for cancel-delivery persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def authority_is_current(
    *,
    run: Mapping[str, Any],
    latest_attempt_id: str,
    attempt: Mapping[str, Any],
    lease: Mapping[str, Any],
    dispatch: Mapping[str, Any] | None,
    lease_unexpired: bool,
    terminal_receipt: bool,
) -> bool:
    tenant_id = str(lease.get("tenant_id") or "")
    run_id = str(lease.get("run_id") or "")
    attempt_id = str(lease.get("attempt_id") or "")
    owner_generation = dispatch_owner_generation(dispatch)
    lease_owner_generation = _payload_owner_generation(lease.get("lease_payload_json"))
    attempt_generation = _positive_int(attempt.get("owner_generation"))
    if not all((tenant_id, run_id, attempt_id)) or dispatch is None:
        return False
    if (
        run.get("tenant_id") != tenant_id
        or run.get("id") != run_id
        or run.get("workspace_id") != lease.get("workspace_id")
        or run.get("user_id") != lease.get("user_id")
        or run.get("session_id") != lease.get("session_id")
        or run.get("status") != "running"
        or run.get("cancel_requested_at") is None
        or latest_attempt_id != attempt_id
    ):
        return False
    if (
        attempt.get("tenant_id") != tenant_id
        or attempt.get("run_id") != run_id
        or attempt.get("id") != attempt_id
        or attempt.get("status") != "cancel_requested"
        or attempt.get("owner_kind") != "queue_worker"
        or not str(attempt.get("owner_id") or "").strip()
        or attempt_generation is None
        or owner_generation is None
        or lease_owner_generation != owner_generation
        or attempt_generation != owner_generation + 1
    ):
        return False
    if (
        dispatch.get("tenant_id") != tenant_id
        or dispatch.get("run_id") != run_id
        or dispatch.get("attempt_id") != attempt_id
        or dispatch.get("lease_id") != lease.get("id")
        or dispatch.get("provider") != lease.get("provider")
        or dispatch.get("provider_task_id") != attempt_id
        or dispatch.get("status") not in {"intent", "accepted", "unknown"}
    ):
        return False
    return bool(
        lease.get("status") == "active"
        and lease.get("released_at") is None
        and lease_unexpired
        and lease.get("runtime_handle_verified_at") is not None
        and str(lease.get("runtime_container_id") or "").strip()
        and str(lease.get("runtime_container_name") or "").strip()
        and str(lease.get("runtime_executor_url") or "").strip()
        and str(lease.get("runtime_workspace_container_path") or "").strip()
        and lease.get("executor_status") in {"pending", "accepted", "running"}
        and lease.get("executor_terminal_received_at") is None
        and not terminal_receipt
    )


def claimed_row_matches_lease(
    claimed_row: Mapping[str, Any],
    lease: Mapping[str, Any],
    dispatch: Mapping[str, Any] | None,
) -> bool:
    if dispatch is None:
        return False
    return all(
        claimed_row.get(claimed_key) == lease.get(lease_key)
        for claimed_key, lease_key in (
            ("tenant_id", "tenant_id"),
            ("workspace_id", "workspace_id"),
            ("user_id", "user_id"),
            ("session_id", "session_id"),
            ("run_id", "run_id"),
            ("attempt_id", "attempt_id"),
            ("provider", "provider"),
            ("runtime_container_id", "runtime_container_id"),
            ("runtime_container_name", "runtime_container_name"),
            ("runtime_executor_url", "runtime_executor_url"),
            ("runtime_workspace_container_path", "runtime_workspace_container_path"),
        )
    ) and (
        claimed_row.get("provider_task_id") == dispatch.get("provider_task_id")
        and claimed_row.get("owner_generation") == lease.get("cancel_delivery_owner_generation")
        and claimed_row.get("owner_generation") == dispatch.get("owner_generation")
    )


def dispatch_owner_generation(dispatch: Mapping[str, Any] | None) -> int | None:
    if dispatch is None:
        return None
    return _positive_int(dispatch.get("owner_generation"))


def _payload_owner_generation(payload: object) -> int | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, Mapping):
        return None
    return _positive_int(payload.get("owner_generation"))


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capacity_baseline import build_capacity_baseline
from app.error_taxonomy import summarize_error_categories
from app.governance_readiness import build_governance_readiness
from app.observability_readiness import build_observability_readiness
from app import repositories
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text
from app.db import get_pool_status, transaction
from app.execution_boundary import REAL_SANDBOX_PROVIDERS, is_accepted_runtime_lease
from app.queue import get_queue_insight, get_queue_status
from app.runtime.sandbox.container_provider import (
    DockerPermissionDeniedError,
    DockerUnavailableError,
    create_container_provider,
)
from app.routes.sandbox_leases import lease_response
from app.routes.sandbox_runtime_cleanup import SandboxRuntimeCleanupError, cleanup_expired_sandbox_runtime_leases
from app.settings import get_settings

router = APIRouter()

_SUCCESSFUL_PROVIDER_CLEANUP_STATUSES = {"stopped", "not_found"}
_OVERVIEW_FORBIDDEN_KEYS = {"skillid"}
_DATABASE_POOL_CONFIG_KEYS = {"min_size", "max_size", "timeout_seconds", "max_waiting"}
_QUEUE_DEPTH_KEYS = {"dead_letter", "processing", "queued", "tenant_processing", "tenant_queued"}
_QUEUE_PROCESSING_STATE_KEYS = {"active", "missing_metadata", "reclaimable", "stale"}
_QUEUE_CAPACITY_KEYS = {
    "available_worker_slots",
    "max_active_worker_runs",
    "processing_saturated",
    "queue_lease_scan_limit",
    "queue_tenant_processing_limit",
    "queue_user_processing_limit",
}
_QUEUE_SAMPLE_KEYS = {"queued_sample_complete", "queued_sampled", "queued_scan_limit"}
_QUEUE_THROTTLING_KEYS = {
    "tenant_processing",
    "tenant_processing_limit",
    "tenant_processing_saturated",
    "user_processing_limit",
}
_QUEUE_USER_THROTTLING_KEYS = {"processing", "processing_saturated", "queued"}
_QUEUE_REASON_VALUES = {
    "queued_behind_existing_work",
    "processing_lease_reclaimable",
    "processing_lease_stale",
    "tenant_quota_full",
    "user_quota_full",
    "worker_available",
    "worker_capacity_full",
    "workers_busy",
}
_QUEUE_BACKPRESSURE_REASON_VALUES = _QUEUE_REASON_VALUES - {"worker_available"}


def _count_by_status(items: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = getattr(item, "status", None) if not isinstance(item, dict) else item.get("status")
        if status is None:
            continue
        status_key = str(status)
        counts[status_key] = counts.get(status_key, 0) + 1
    return counts


def _sanitize_dict(value: object) -> dict[str, object]:
    sanitized = sanitize_public_payload(value if isinstance(value, dict) else {})
    if not isinstance(sanitized, dict):
        return {}
    cleaned = _drop_overview_forbidden_keys(sanitized)
    return cleaned if isinstance(cleaned, dict) else {}


def _governance_overview_projection(settings: object) -> dict[str, object]:
    readiness = _sanitize_dict(build_governance_readiness(settings))
    domains = readiness.get("domains")
    if not isinstance(domains, dict):
        return readiness
    skill_governance = domains.get("skill_governance")
    if not isinstance(skill_governance, dict):
        return readiness
    evidence = skill_governance.get("evidence")
    if not isinstance(evidence, dict):
        return readiness
    dashboard = evidence.get("admin_skill_release_dashboard")
    if isinstance(dashboard, dict):
        evidence["admin_skill_release_dashboard"] = {
            key: value for key, value in dashboard.items() if key != "dashboard_contract"
        }
    return readiness


def _observability_readiness_overview_projection(settings: object) -> dict[str, object]:
    readiness = _sanitize_dict(build_observability_readiness(settings))
    domains = readiness.get("domains")
    if not isinstance(domains, dict):
        return readiness
    alerts = domains.get("alerts_and_exports")
    if not isinstance(alerts, dict):
        return readiness
    evidence = alerts.get("evidence")
    if not isinstance(evidence, dict):
        return readiness
    release_evidence = evidence.get("release_evidence")
    if not isinstance(release_evidence, dict):
        return readiness
    export_acceptance = release_evidence.get("export_acceptance")
    if isinstance(export_acceptance, dict):
        evidence_summary_keys = {
            "schema_version",
            "gate",
            "status",
            "export_policy",
            "evidence_root",
            "entry_count",
            "safe_entry_count",
            "blockers",
            "blocked_entry_count",
            "excluded_entry_count",
            "safe_entry_fields",
            "open_gaps",
            "does_not_export_raw_runtime_payloads",
            "does_not_close_g9",
        }
        release_evidence["export_acceptance"] = {
            key: value for key, value in export_acceptance.items() if key in evidence_summary_keys
        }
    return readiness


def _drop_overview_forbidden_keys(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
            if normalized_key in _OVERVIEW_FORBIDDEN_KEYS:
                continue
            cleaned[key] = _drop_overview_forbidden_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_overview_forbidden_keys(item) for item in value]
    return value


def _coerce_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sanitize_numeric_map(value: object, allowed_keys: set[str]) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    cleaned: dict[str, int] = {}
    for key, item in source.items():
        safe_key = sanitize_public_text(key)
        if safe_key not in allowed_keys or isinstance(item, bool) or not isinstance(item, int | float):
            continue
        cleaned[safe_key] = int(item)
    return cleaned


def _sanitize_numeric_bool_map(value: object, allowed_keys: set[str]) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    cleaned: dict[str, object] = {}
    for key, item in source.items():
        safe_key = sanitize_public_text(key)
        if safe_key not in allowed_keys:
            continue
        if isinstance(item, bool):
            cleaned[safe_key] = item
        elif isinstance(item, int | float):
            cleaned[safe_key] = int(item)
        elif item is None:
            cleaned[safe_key] = None
    return cleaned


def _sanitize_queue_workers(value: object) -> object:
    if isinstance(value, dict):
        return _sanitize_numeric_map(value, {"active"})
    if isinstance(value, list):
        workers: list[str] = []
        for item in value:
            worker_id = sanitize_public_text(item)
            if worker_id:
                workers.append(worker_id)
        return workers
    return []


def _sanitize_queue_status(value: object) -> dict[str, object]:
    status = value if isinstance(value, dict) else {}
    payload: dict[str, object] = {}
    depths = _sanitize_numeric_map(status.get("depths"), _QUEUE_DEPTH_KEYS)
    if depths:
        payload["depths"] = depths
    workers = _sanitize_queue_workers(status.get("workers"))
    if workers:
        payload["workers"] = workers
    processing_state = _sanitize_numeric_map(status.get("processing_state"), _QUEUE_PROCESSING_STATE_KEYS)
    if processing_state:
        payload["processing_state"] = processing_state
    return payload


def _sanitize_user_queue_throttling(value: object) -> dict[str, object]:
    return _sanitize_numeric_bool_map(value, _QUEUE_USER_THROTTLING_KEYS)


def _sanitize_queue_throttling(value: object) -> dict[str, object]:
    throttling = value if isinstance(value, dict) else {}
    payload = _sanitize_numeric_bool_map(throttling, _QUEUE_THROTTLING_KEYS)
    current_user = _sanitize_user_queue_throttling(throttling.get("current_user"))
    if current_user:
        payload["current_user"] = current_user
    users = throttling.get("users") if isinstance(throttling.get("users"), dict) else {}
    sanitized_users: dict[str, object] = {}
    for user_id, user_state in users.items():
        safe_user_id = sanitize_public_text(user_id)
        safe_state = _sanitize_user_queue_throttling(user_state)
        if safe_user_id and safe_state:
            sanitized_users[safe_user_id] = safe_state
    if sanitized_users:
        payload["users"] = sanitized_users
    return payload


def _sanitize_queue_insight(value: object) -> dict[str, object]:
    insight = value if isinstance(value, dict) else {}
    payload: dict[str, object] = {}
    tenant_id = sanitize_public_text(insight.get("tenant_id"))
    if tenant_id:
        payload["tenant_id"] = tenant_id
    reason = _safe_queue_reason(insight.get("reason"))
    if reason:
        payload["reason"] = reason
    depths = _sanitize_numeric_map(insight.get("depths"), _QUEUE_DEPTH_KEYS)
    if depths:
        payload["depths"] = depths
    workers = _sanitize_queue_workers(insight.get("workers"))
    if workers:
        payload["workers"] = workers
    processing_state = _sanitize_numeric_map(insight.get("processing_state"), _QUEUE_PROCESSING_STATE_KEYS)
    if processing_state:
        payload["processing_state"] = processing_state
    capacity = _sanitize_numeric_bool_map(insight.get("capacity"), _QUEUE_CAPACITY_KEYS)
    if capacity:
        payload["capacity"] = capacity
    queue_sample = _sanitize_numeric_bool_map(insight.get("queue_sample"), _QUEUE_SAMPLE_KEYS)
    if queue_sample:
        payload["queue_sample"] = queue_sample
    throttling = _sanitize_queue_throttling(insight.get("throttling"))
    if throttling:
        payload["throttling"] = throttling
    return payload


def _sanitize_observability_summary(value: object) -> dict[str, object]:
    summary = _sanitize_dict(value)
    error_types = summary.get("error_types") if isinstance(summary.get("error_types"), dict) else {}
    summary["error_types"] = {
        sanitized_key: _coerce_int(count)
        for key, count in error_types.items()
        if (sanitized_key := sanitize_public_text(key))
    }
    summary["error_categories"] = summarize_error_categories(summary["error_types"])
    latency = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), dict) else {}
    summary["latency_ms"] = {
        "avg": _coerce_int(latency["avg"]) if latency.get("avg") is not None else None,
        "max": _coerce_int(latency["max"]) if latency.get("max") is not None else None,
        "p50": _coerce_int(latency["p50"]) if latency.get("p50") is not None else None,
        "p95": _coerce_int(latency["p95"]) if latency.get("p95") is not None else None,
        "p99": _coerce_int(latency["p99"]) if latency.get("p99") is not None else None,
    }
    return summary


def _sanitize_database_pool_status(value: object) -> dict[str, object]:
    summary = _sanitize_dict(value)
    configured = summary.get("configured") if isinstance(summary.get("configured"), dict) else {}
    stats = summary.get("stats") if isinstance(summary.get("stats"), dict) else {}
    return {
        "configured": {
            key: configured[key]
            for key in _DATABASE_POOL_CONFIG_KEYS
            if isinstance(configured.get(key), int | float)
        },
        "open": bool(summary.get("open")),
        "stats": {
            str(key): item
            for key, item in stats.items()
            if isinstance(item, int | float) and not isinstance(item, bool)
        },
    }


def _sanitize_admission_summary(value: object) -> dict[str, object]:
    summary = value if isinstance(value, dict) else {}
    users = summary.get("top_users") if isinstance(summary.get("top_users"), list) else []
    top_users: list[dict[str, object]] = []
    for user in users:
        if not isinstance(user, dict) or not user.get("user_id"):
            continue
        user_id = sanitize_public_text(user.get("user_id"))
        if not user_id:
            continue
        top_users.append(
            {
                "user_id": user_id,
                "active": _coerce_int(user.get("active")),
                "saturated": bool(user.get("saturated")),
            }
        )
    return {
        "policy_active": bool(summary.get("policy_active")),
        "max_active_runs_per_user": _coerce_int(summary.get("max_active_runs_per_user")),
        "active_runs": _coerce_int(summary.get("active_runs")),
        "active_users": _coerce_int(summary.get("active_users")),
        "saturated_users": _coerce_int(summary.get("saturated_users")),
        "top_users": top_users,
    }


def _safe_queue_reason(value: object) -> str | None:
    text = sanitize_public_text(value)
    return text if text in _QUEUE_REASON_VALUES else None


def _queue_backpressure_snapshot(queue_insight: object) -> dict[str, object]:
    insight = queue_insight if isinstance(queue_insight, dict) else {}
    capacity = insight.get("capacity") if isinstance(insight.get("capacity"), dict) else {}
    throttling = insight.get("throttling") if isinstance(insight.get("throttling"), dict) else {}
    users = throttling.get("users") if isinstance(throttling.get("users"), dict) else {}
    queue_sample = insight.get("queue_sample") if isinstance(insight.get("queue_sample"), dict) else {}
    processing_state = _sanitize_numeric_map(insight.get("processing_state"), _QUEUE_PROCESSING_STATE_KEYS)
    saturated_users = sum(
        1
        for user_state in users.values()
        if isinstance(user_state, dict) and bool(user_state.get("processing_saturated"))
    )
    snapshot = {
        "reason": _safe_queue_reason(insight.get("reason")),
        "worker_capacity": {
            "max_active_worker_runs": _coerce_int(capacity.get("max_active_worker_runs")),
            "processing_saturated": bool(capacity.get("processing_saturated")),
            "available_worker_slots": (
                _coerce_int(capacity.get("available_worker_slots"))
                if capacity.get("available_worker_slots") is not None
                else None
            ),
        },
        "quota": {
            "tenant_processing_limit": _coerce_int(
                throttling.get("tenant_processing_limit", capacity.get("queue_tenant_processing_limit"))
            ),
            "tenant_processing_saturated": bool(throttling.get("tenant_processing_saturated")),
            "user_processing_limit": _coerce_int(
                throttling.get("user_processing_limit", capacity.get("queue_user_processing_limit"))
            ),
            "saturated_users": saturated_users,
        },
        "sample": {
            "queued_scan_limit": _coerce_int(queue_sample.get("queued_scan_limit")),
            "queued_sampled": _coerce_int(queue_sample.get("queued_sampled")),
            "queued_sample_complete": bool(queue_sample.get("queued_sample_complete")),
        },
    }
    if processing_state:
        snapshot["processing_state"] = processing_state
    return snapshot


def _database_pool_backpressure_snapshot(database_pool: object) -> dict[str, object]:
    pool = database_pool if isinstance(database_pool, dict) else {}
    configured = pool.get("configured") if isinstance(pool.get("configured"), dict) else {}
    stats = pool.get("stats") if isinstance(pool.get("stats"), dict) else {}
    requests_waiting = _coerce_int(stats.get("requests_waiting"))
    max_waiting = _coerce_int(configured.get("max_waiting"))
    return {
        "open": bool(pool.get("open")),
        "requests_waiting": requests_waiting,
        "max_waiting": max_waiting,
        "waiting_saturated": max_waiting > 0 and requests_waiting >= max_waiting,
    }


def _model_gateway_backpressure_snapshot(capacity: object) -> dict[str, object]:
    baseline = capacity if isinstance(capacity, dict) else {}
    limits = baseline.get("limits") if isinstance(baseline.get("limits"), dict) else {}
    model_gateway = limits.get("model_gateway") if isinstance(limits.get("model_gateway"), dict) else {}
    request_limit = model_gateway.get("request_concurrency_limit")
    configured_request_limit = model_gateway.get("configured_request_concurrency_limit")
    normalized_limit = _coerce_int(request_limit) if request_limit is not None else None
    normalized_configured_limit = (
        _coerce_int(configured_request_limit) if configured_request_limit is not None else None
    )
    if normalized_limit is not None and normalized_limit <= 0:
        normalized_limit = None
    if normalized_configured_limit is not None and normalized_configured_limit <= 0:
        normalized_configured_limit = None
    provider = sanitize_public_text(model_gateway.get("provider")) or "unknown"
    if provider not in {"new-api", "openai_compatible", "unknown"}:
        provider = "unknown"
    return {
        "provider": provider,
        "request_concurrency_limit": normalized_limit,
        "configured_request_concurrency_limit": normalized_configured_limit,
        "limit_enabled": False,
        "limit_enforced": False,
        "limit_enforcement": "not_implemented",
        "config_only": normalized_configured_limit is not None,
        "capacity_evidence": (
            "unproven_without_load_test"
            if model_gateway.get("capacity_evidence") == "unproven_without_load_test"
            else "unknown"
        ),
    }


def _backpressure_snapshot(
    *,
    admission: dict[str, object],
    queue_insight: object,
    database_pool: dict[str, object],
    capacity: dict[str, object] | None = None,
) -> dict[str, object]:
    queue = _queue_backpressure_snapshot(queue_insight)
    pool = _database_pool_backpressure_snapshot(database_pool)
    reasons: list[str] = []
    if _coerce_int(admission.get("saturated_users")) > 0:
        reasons.append("active_run_limit_saturated")
    queue_reason = queue.get("reason")
    if isinstance(queue_reason, str) and queue_reason in _QUEUE_BACKPRESSURE_REASON_VALUES:
        reasons.append(queue_reason)
    processing_state = queue.get("processing_state") if isinstance(queue.get("processing_state"), dict) else {}
    if _coerce_int(processing_state.get("reclaimable")) > 0:
        reasons.append("processing_lease_reclaimable")
    elif _coerce_int(processing_state.get("stale")) > 0:
        reasons.append("processing_lease_stale")
    worker_capacity = queue["worker_capacity"] if isinstance(queue.get("worker_capacity"), dict) else {}
    if worker_capacity.get("processing_saturated"):
        reasons.append("worker_capacity_saturated")
    quota = queue["quota"] if isinstance(queue.get("quota"), dict) else {}
    if quota.get("tenant_processing_saturated"):
        reasons.append("queue_tenant_quota_saturated")
    if _coerce_int(quota.get("saturated_users")) > 0:
        reasons.append("queue_user_quota_saturated")
    if _coerce_int(pool.get("requests_waiting")) > 0:
        reasons.append("database_pool_waiting")
    if pool.get("waiting_saturated"):
        reasons.append("database_pool_waiting_saturated")
    deduped_reasons = list(dict.fromkeys(reasons))
    snapshot = {
        "reasons": deduped_reasons,
        "queue": queue,
        "database_pool": pool,
    }
    if capacity is not None:
        snapshot["model_gateway"] = _model_gateway_backpressure_snapshot(capacity)
    return snapshot


def _provider_cleanup_failed(cleanup_results: object) -> bool:
    if not isinstance(cleanup_results, list):
        return True
    for result in cleanup_results:
        if isinstance(result, dict):
            return True
        status = getattr(result, "status", None)
        if status not in _SUCCESSFUL_PROVIDER_CLEANUP_STATUSES:
            return True
    return False


def _only_placeholder_cleanup_failures(exc: SandboxRuntimeCleanupError) -> bool:
    failures = getattr(exc, "failures", None)
    return bool(failures) and all(
        str(failure.get("message") or "") == "Unsupported sandbox provider: fake"
        for failure in failures
        if isinstance(failure, dict)
    ) and all(isinstance(failure, dict) for failure in failures)


def _accepted_runtime_containers(
    containers: list[object],
    active_leases: list[dict[str, object]],
) -> list[object]:
    accepted_run_keys = {
        (str(lease.get("tenant_id") or ""), str(lease.get("run_id") or ""))
        for lease in active_leases
        if is_accepted_runtime_lease(lease)
    }
    return [
        container
        for container in containers
        if str(getattr(container, "provider", "") or "") in REAL_SANDBOX_PROVIDERS
        and (
            str(getattr(container, "tenant_id", "") or ""),
            str(getattr(container, "run_id", "") or ""),
        )
        in accepted_run_keys
    ]


def _sandbox_overview(containers: list[object], leases: list[dict[str, object]], lease_history: list[dict[str, object]]) -> dict[str, object]:
    tenant_leases = [lease for lease in leases if lease.get("tenant_id")]
    tenant_lease_history = [lease for lease in lease_history if lease.get("tenant_id")]
    active_containers = [container for container in containers if getattr(container, "status", None) == "running"]

    return {
        "containers": {
            "total": len(containers),
            "running": len(active_containers),
            "by_status": _count_by_status(containers),
            "ephemeral_running": sum(1 for container in active_containers if getattr(container, "sandbox_mode", None) == "ephemeral"),
            "persistent_running": sum(1 for container in active_containers if getattr(container, "sandbox_mode", None) == "persistent"),
        },
        "leases": {
            "active": sum(1 for lease in tenant_leases if lease.get("status") == "active"),
            "released": sum(1 for lease in tenant_lease_history if lease.get("status") == "released"),
            "expired": sum(1 for lease in tenant_lease_history if lease.get("status") == "expired"),
            "history_included": bool(lease_history),
        },
    }


async def _list_runtime_containers_for_overview(provider: object, tenant_id: str) -> tuple[list[object], bool]:
    try:
        containers = await provider.list_runtime_containers({"tenant_id": tenant_id})
    except (DockerPermissionDeniedError, DockerUnavailableError):
        return [], True
    return containers, False


@router.get("/admin/runtime/queue")
async def admin_runtime_queue(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    return {
        "tenant_id": principal.tenant_id,
        "queue": _sanitize_queue_status(await get_queue_status()),
        "tenant_insight": _sanitize_queue_insight(
            await get_queue_insight(principal.tenant_id, include_user_breakdown=True)
        ),
    }


@router.post("/admin/runtime/multi-agent/dispatch/cleanup")
async def admin_runtime_multi_agent_dispatch_cleanup(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    async with transaction() as conn:
        expired_claims = await repositories.cleanup_expired_multi_agent_dispatch_claims(
            conn,
            tenant_id=principal.tenant_id,
            cleaned_by=principal.user_id,
            limit=100,
        )
    return {
        "tenant_id": principal.tenant_id,
        "expired_count": len(expired_claims),
        "expired_claims": expired_claims,
    }


@router.get("/admin/runtime/containers")
async def admin_runtime_containers(
    include_lease_history: bool = False,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    provider = create_container_provider()
    cleanup_orphan_containers = getattr(provider, "cleanup_orphan_containers", None)
    if cleanup_orphan_containers is not None:
        try:
            cleanup_results = await cleanup_orphan_containers({"tenant_id": principal.tenant_id}, reason="admin_runtime")
        except Exception as exc:
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed") from exc
        if _provider_cleanup_failed(cleanup_results):
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed")
    async with transaction() as conn:
        try:
            await cleanup_expired_sandbox_runtime_leases(
                conn,
                tenant_id=principal.tenant_id,
                provider_factory=create_container_provider,
            )
            await repositories.cleanup_expired_sandbox_leases(conn, tenant_id=principal.tenant_id)
        except SandboxRuntimeCleanupError as exc:
            if not _only_placeholder_cleanup_failures(exc):
                raise HTTPException(status_code=500, detail="sandbox_runtime_cleanup_failed") from exc
        leases = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status="active")
        lease_history = (
            await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status=None)
            if include_lease_history
            else []
        )
    visible_leases = [
        lease
        for lease in leases
        if lease.get("tenant_id") == principal.tenant_id and is_accepted_runtime_lease(lease)
    ]
    visible_lease_history = [
        lease
        for lease in lease_history
        if lease.get("tenant_id") == principal.tenant_id and is_accepted_runtime_lease(lease)
    ]
    containers = _accepted_runtime_containers(
        await provider.list_runtime_containers({"tenant_id": principal.tenant_id}),
        visible_leases,
    )
    active_containers = [container for container in containers if container.status == "running"]

    payload = {
        "total_active": len(active_containers),
        "ephemeral_containers": sum(1 for container in active_containers if container.sandbox_mode == "ephemeral"),
        "persistent_containers": sum(1 for container in active_containers if container.sandbox_mode == "persistent"),
        "containers": [container.model_dump() for container in containers],
        "sandbox_leases": [lease_response(lease) for lease in visible_leases],
    }
    if include_lease_history:
        payload["sandbox_lease_history"] = [lease_response(lease) for lease in visible_lease_history]
    return payload


@router.get("/admin/runtime/overview")
async def admin_runtime_overview(
    include_maintenance_cleanup: bool = True,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return an admin-only same-tenant runtime overview snapshot."""
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")

    provider = create_container_provider()
    cleanup_orphan_containers = (
        getattr(provider, "cleanup_orphan_containers", None)
        if include_maintenance_cleanup
        else None
    )
    if cleanup_orphan_containers is not None:
        try:
            cleanup_results = await cleanup_orphan_containers({"tenant_id": principal.tenant_id}, reason="admin_runtime")
        except Exception as exc:
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed") from exc
        if _provider_cleanup_failed(cleanup_results):
            raise HTTPException(status_code=500, detail="sandbox_provider_cleanup_failed")

    async with transaction() as conn:
        if include_maintenance_cleanup:
            try:
                await cleanup_expired_sandbox_runtime_leases(
                    conn,
                    tenant_id=principal.tenant_id,
                    provider_factory=create_container_provider,
                )
                await repositories.cleanup_expired_sandbox_leases(conn, tenant_id=principal.tenant_id)
            except SandboxRuntimeCleanupError as exc:
                if not _only_placeholder_cleanup_failures(exc):
                    raise HTTPException(status_code=500, detail="sandbox_runtime_cleanup_failed") from exc
        leases = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status="active")
        lease_history = await repositories.list_sandbox_leases(conn, tenant_id=principal.tenant_id, status=None)

    visible_leases = [
        lease
        for lease in leases
        if lease.get("tenant_id") == principal.tenant_id and is_accepted_runtime_lease(lease)
    ]
    visible_lease_history = [
        lease
        for lease in lease_history
        if lease.get("tenant_id") == principal.tenant_id and is_accepted_runtime_lease(lease)
    ]
    containers, container_observation_degraded = await _list_runtime_containers_for_overview(
        provider,
        principal.tenant_id,
    )
    containers = _accepted_runtime_containers(containers, visible_leases)

    async with transaction() as conn:
        run_summary = await repositories.get_admin_runtime_run_summary(conn, tenant_id=principal.tenant_id, limit=10)
        observability_summary = await repositories.get_admin_runtime_observability_summary(conn, tenant_id=principal.tenant_id)
        admission_summary = await repositories.get_admin_runtime_admission_summary(
            conn,
            tenant_id=principal.tenant_id,
            limit=int(get_settings().max_active_runs_per_user),
        )

    queue_status = await get_queue_status()
    tenant_queue_insight = await get_queue_insight(principal.tenant_id, include_user_breakdown=True)
    database_pool = _sanitize_database_pool_status(get_pool_status())
    admission = _sanitize_admission_summary(admission_summary)
    capacity = build_capacity_baseline(get_settings())

    sandbox = _sandbox_overview(containers, visible_leases, visible_lease_history)
    if container_observation_degraded:
        sandbox["list_runtime_containers_status"] = "unavailable"
        sandbox["container_observation_degraded"] = True

    return {
        "tenant_id": principal.tenant_id,
        "queue": {
            "status": _sanitize_queue_status(queue_status),
            "tenant_insight": _sanitize_queue_insight(tenant_queue_insight),
        },
        "runs": _sanitize_dict(run_summary),
        "sandbox": sandbox,
        "observability": _sanitize_observability_summary(observability_summary),
        "capacity": capacity,
        "governance": _governance_overview_projection(get_settings()),
        "observability_readiness": _observability_readiness_overview_projection(get_settings()),
        "database_pool": database_pool,
        "admission": admission,
        "backpressure": _backpressure_snapshot(
            admission=admission,
            queue_insight=tenant_queue_insight,
            database_pool=database_pool,
            capacity=capacity,
        ),
    }

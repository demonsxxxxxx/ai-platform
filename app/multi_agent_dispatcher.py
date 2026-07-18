import math
import time
from typing import Any

from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal, normalize_roles
from app.control_plane_contracts import sanitize_public_text, standard_trace_id
from app.db import transaction
from app.queue import enqueue_run
from app.repositories import RepositoryAuthorizationError, RepositoryConflictError, RepositoryNotFoundError
from app.run_control_readiness import dispatch_tick_candidate
from app.routes.runs import _raise_multi_agent_dispatch_not_available, prepare_copied_run_for_queue
from app.settings import get_settings
from app.skills.pinning import SkillVersionMaterializationError


_next_multi_agent_dispatch_at = 0.0


def _setting_int(settings: object, name: str) -> int | None:
    try:
        return int(getattr(settings, name))
    except (TypeError, ValueError):
        return None


def _setting_float(settings: object, name: str) -> float | None:
    try:
        value = float(getattr(settings, name))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _worker_admin_principal(settings: object, *, tenant_id: str) -> AuthPrincipal:
    user_id = sanitize_public_text(getattr(settings, "multi_agent_dispatch_worker_user_id", ""))
    if not user_id:
        user_id = "system:multi-agent-dispatcher"
    return AuthPrincipal(
        user_id=user_id,
        display_name=user_id,
        tenant_id=tenant_id,
        roles=["admin"],
        source="worker_multi_agent_dispatcher",
    )


def _skip_result(run_id: str, reason: object) -> dict[str, object]:
    safe_reason = sanitize_public_text(reason) or "dispatch_skipped"
    return {"run_id": run_id, "status": "skipped", "reason": safe_reason}


def _enqueue_failed_result(
    dispatch: dict[str, object],
    reason: object,
    *,
    compensated: bool,
) -> dict[str, object]:
    safe_reason = sanitize_public_text(reason) or "queue_enqueue_failed"
    return {
        "run_id": str(dispatch.get("run_id") or ""),
        "status": "enqueue_failed",
        "reason": safe_reason,
        "child_run_id": str(dispatch.get("child_run_id") or ""),
        "parent_step_id": str(dispatch.get("parent_step_id") or ""),
        "compensated": compensated,
    }


async def _dispatch_one_ready_parent(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    principal: AuthPrincipal,
    settings: object,
) -> dict[str, object]:
    run = await repositories.get_run(conn, tenant_id=tenant_id, run_id=run_id, for_update=True)
    if run is None:
        raise RepositoryNotFoundError("run_not_found")
    steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    candidate = dispatch_tick_candidate(run=run, steps=steps, principal=principal)
    claimed_step_key = str(candidate["step_key"])
    claim = await repositories.claim_multi_agent_dispatch_step(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        claimed_by=principal.user_id,
        trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
        lease_ttl_seconds=int(getattr(settings, "multi_agent_dispatch_lease_ttl_seconds", 900) or 900),
        **candidate,
    )
    copied = await repositories.create_multi_agent_dispatch_child_run(
        conn,
        tenant_id=tenant_id,
        parent_run_id=run_id,
        dispatch_id=str(claim["dispatch_id"]),
        handed_off_by=principal.user_id,
        active_run_admission_limit=int(getattr(settings, "max_active_runs_per_user")),
    )
    owner_principal = AuthPrincipal(
        user_id=str(copied["user_id"]),
        display_name=str(copied.get("user_id") or ""),
        tenant_id=tenant_id,
        department_id=str(copied.get("principal_department_id") or ""),
        roles=normalize_roles(copied.get("principal_roles") or []),
        source=str(copied.get("auth_source") or ""),
    )
    queue_payload = await prepare_copied_run_for_queue(
        conn,
        copied={**copied, "run_id": copied["child_run_id"]},
        principal=principal,
        queue_principal=owner_principal,
        source="worker_multi_agent_dispatcher",
        authorized_source_run_id=run_id,
    )
    return {
        "run_id": run_id,
        "status": "queued",
        "step_key": claimed_step_key,
        "dispatch_id": str(claim["dispatch_id"]),
        "child_run_id": str(copied["child_run_id"]),
        "parent_step_id": str(copied["parent_step_id"]),
        "queue_payload": queue_payload,
        "claim_event_id": str(claim["event_id"]),
        "claim_audit_id": str(claim["audit_id"]),
        "handoff_event_id": str(copied["event_id"]),
        "child_event_id": str(copied["child_event_id"]),
        "handoff_audit_id": str(copied["audit_id"]),
    }


async def _audit_dispatch_authorization_denial(
    *,
    tenant_id: str,
    run_id: str,
    error: RepositoryAuthorizationError,
) -> None:
    if error.denial is None:
        return
    async with transaction() as conn:
        run = await repositories.get_run(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        if run is None:
            raise RepositoryNotFoundError("run_not_found")
        owner_user_id = str(run.get("user_id") or "").strip()
        if not owner_user_id:
            raise RepositoryConflictError("run_owner_missing")
        await repositories.append_capability_authorization_denial_audit(
            conn,
            tenant_id=tenant_id,
            user_id=owner_user_id,
            error=error,
            source="worker_multi_agent_dispatcher",
        )


async def dispatch_multi_agent_ready_steps_for_worker(
    settings: object | None = None,
    *,
    now: float | None = None,
) -> list[dict[str, object]]:
    """Run one bounded worker-side multi-agent dispatch maintenance pass."""
    global _next_multi_agent_dispatch_at

    # Multi-agent dispatch is intentionally deferred.  Invoke the same
    # authoritative admission guard as public routes before reading settings,
    # listing candidates, claiming work, or touching the queue.
    try:
        _raise_multi_agent_dispatch_not_available()
    except HTTPException as exc:
        if exc.status_code == 409 and exc.detail == "multi_agent_dispatch_not_available":
            return []
        raise

    settings = settings or get_settings()
    if not bool(getattr(settings, "multi_agent_dispatch_worker_enabled", False)):
        return []
    interval_seconds = _setting_float(settings, "multi_agent_dispatch_worker_interval_seconds")
    limit = _setting_int(settings, "multi_agent_dispatch_worker_limit")
    if interval_seconds is None or limit is None or interval_seconds <= 0 or limit <= 0:
        return []
    current_time = time.monotonic() if now is None else float(now)
    if current_time < _next_multi_agent_dispatch_at:
        return []
    _next_multi_agent_dispatch_at = current_time + interval_seconds

    tenant_id = str(getattr(settings, "default_tenant_id", "default") or "default")
    bounded_limit = max(min(limit, 50), 1)
    principal = _worker_admin_principal(settings, tenant_id=tenant_id)
    async with transaction() as conn:
        candidate_run_ids = await repositories.list_multi_agent_dispatch_candidate_run_ids(
            conn,
            tenant_id=tenant_id,
            limit=bounded_limit,
        )

    results: list[dict[str, object]] = []
    for run_id in candidate_run_ids:
        run_id = str(run_id)
        try:
            async with transaction() as conn:
                dispatch = await _dispatch_one_ready_parent(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    principal=principal,
                    settings=settings,
                )
            queue_payload = dispatch.pop("queue_payload")
            try:
                queue_position = await enqueue_run(queue_payload if isinstance(queue_payload, dict) else {})
            except Exception as exc:
                compensated = False
                try:
                    async with transaction() as conn:
                        compensation = await repositories.mark_multi_agent_dispatch_enqueue_failed(
                            conn,
                            tenant_id=tenant_id,
                            parent_run_id=str(dispatch.get("run_id") or run_id),
                            parent_step_id=str(dispatch.get("parent_step_id") or ""),
                            dispatch_id=str(dispatch.get("dispatch_id") or ""),
                            child_run_id=str(dispatch.get("child_run_id") or ""),
                            reason=str(exc),
                            triggered_by=principal.user_id,
                        )
                    compensated = compensation is not None
                except Exception:
                    compensated = False
                results.append(_enqueue_failed_result(dispatch, exc, compensated=compensated))
                continue
            dispatch_result = {key: value for key, value in dispatch.items() if key != "parent_step_id"}
            results.append({**dispatch_result, "queue_position": queue_position})
        except HTTPException as exc:
            if exc.status_code in {404, 409}:
                results.append(_skip_result(run_id, exc.detail))
                continue
            raise
        except RepositoryAuthorizationError as exc:
            await _audit_dispatch_authorization_denial(
                tenant_id=tenant_id,
                run_id=run_id,
                error=exc,
            )
            results.append(_skip_result(run_id, str(exc)))
        except (RepositoryConflictError, RepositoryNotFoundError, SkillVersionMaterializationError) as exc:
            results.append(_skip_result(run_id, str(exc)))
    return results

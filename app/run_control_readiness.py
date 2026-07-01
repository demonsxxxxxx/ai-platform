from fastapi import HTTPException

from app.auth import AuthPrincipal, is_ai_admin
from app.control_plane_contracts import (
    FORBIDDEN_PUBLIC_KEY_ALIASES,
    HASH_LIKE_VALUE_PATTERN,
    sanitize_public_text,
)
from app.run_projection import (
    normalize_run_status,
    normalize_step_status,
    public_text_or_fallback,
    run_step_response,
)
from app.run_provenance import (
    contains_raw_projection_term,
    readiness_public_text,
    readiness_raw_projection_terms,
    run_playback_summary,
)
from app.validation import assert_safe_id

RUN_CONTROL_READINESS_CONTRACT_VERSION = "ai-platform.run-control-readiness.v1"
RUN_CONTROL_ACTIVE_STATUSES = {"queued", "running"}
RUN_CONTROL_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RUN_CONTROL_RETRY_PREVIEW_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}


def _control_action(*, enabled: bool, reason: str, method: str | None, href: str | None) -> dict[str, object]:
    return {"enabled": enabled, "reason": reason, "method": method, "href": href}


def _checkpoint_candidate_from_step(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> dict[str, object] | None:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    status = normalize_step_status(row.get("status"))
    if status != "succeeded" or payload.get("output") is None:
        return None
    public_step = run_step_response(row, principal=principal)
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    title = public_step.get("title")
    role = public_step.get("role")
    if not is_ai_admin(principal):
        step_key = readiness_public_text(step_key, fallback=step_id, raw_terms=raw_terms) or step_id
        title = readiness_public_text(title, fallback=step_key, raw_terms=raw_terms) or step_key
        if role is not None:
            role = readiness_public_text(role, raw_terms=raw_terms) or None
    return {
        "step_id": step_id,
        "step_key": step_key,
        "status": str(public_step["status"]),
        "title": title,
        "role": role,
        "sequence": int(public_step.get("sequence") or 0),
        "reusable": True,
        "reason": "output_available",
    }


def run_execution_input(run: dict[str, object]) -> dict[str, object]:
    source_input = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    return execution_input if isinstance(execution_input, dict) else {}


def configured_multi_agent_steps(run: dict[str, object]) -> list[dict[str, object]]:
    execution_input = run_execution_input(run)
    configured = execution_input.get("multi_agent_steps")
    if not isinstance(configured, list):
        return []
    return [dict(item) for item in configured if isinstance(item, dict) and (item.get("step_key") or item.get("stepKey"))]


def raw_depends_on(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    depends_on: list[str] = []
    for value in values:
        dependency = str(value).strip() if value is not None else ""
        if dependency and dependency not in depends_on:
            depends_on.append(dependency)
    return depends_on


def _projected_dependency(value: str, *, raw_terms: set[str], principal: AuthPrincipal) -> dict[str, str | None]:
    if is_ai_admin(principal):
        return {"step_key": public_text_or_fallback(value, value), "reason": None}
    projected = readiness_public_text(value, raw_terms=raw_terms)
    if projected:
        return {"step_key": projected, "reason": None}
    return {"step_key": None, "reason": "unsafe_dependency"}


def _multi_agent_step_public_text(
    value: object,
    *,
    fallback: object,
    raw_terms: set[str],
    principal: AuthPrincipal,
) -> str:
    if is_ai_admin(principal):
        return public_text_or_fallback(value, fallback)
    return readiness_public_text(value, fallback=fallback, raw_terms=raw_terms)


def dependency_statuses(
    depends_on: list[str],
    status_by_key: dict[str, str],
    *,
    raw_terms: set[str],
    principal: AuthPrincipal,
) -> list[dict[str, str | None]]:
    statuses: list[dict[str, str | None]] = []
    for dependency in depends_on:
        projected = _projected_dependency(dependency, raw_terms=raw_terms, principal=principal)
        if projected["reason"] == "unsafe_dependency":
            statuses.append({"step_key": None, "status": "hidden", "reason": "unsafe_dependency"})
            continue
        statuses.append({"step_key": projected["step_key"], "status": status_by_key.get(dependency, "missing")})
    return statuses


def multi_agent_blocked_reason(status: str, dependency_statuses: list[dict[str, str | None]]) -> str | None:
    if status in RUN_CONTROL_TERMINAL_STATUSES:
        return "terminal_step"
    if status == "running":
        return "already_running"
    if any(item["status"] == "hidden" for item in dependency_statuses):
        return "hidden_dependencies"
    if any(item["status"] == "missing" for item in dependency_statuses):
        return "missing_dependencies"
    if any(item["status"] != "succeeded" for item in dependency_statuses):
        return "waiting_on_dependencies"
    return None


def multi_agent_readiness_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object] | None:
    """Return public dependency readiness without opening autonomous dispatch."""
    configured_steps = configured_multi_agent_steps(run)
    execution_input = run_execution_input(run)
    execution_mode = str(execution_input.get("execution_mode") or "")
    if execution_mode != "multi_agent":
        return None
    public_execution_mode = "multi_agent"
    run_status = normalize_run_status(str(run.get("status") or ""))
    raw_terms = readiness_raw_projection_terms(run)
    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    ordered_keys = list(configured_by_key)
    for key, row in sorted(recorded_by_key.items(), key=lambda item: int(item[1].get("sequence") or 0)):
        if key not in configured_by_key:
            ordered_keys.append(key)

    status_by_key = {key: normalize_step_status(row.get("status")) for key, row in recorded_by_key.items()}
    readiness_steps: list[dict[str, object]] = []
    safe_ready_count = 0
    for index, step_key in enumerate(ordered_keys, start=1):
        configured = configured_by_key.get(step_key, {})
        row = recorded_by_key.get(step_key)
        if row is not None:
            public_step = run_step_response(row, principal=principal)
            payload = public_step.get("payload") if isinstance(public_step.get("payload"), dict) else {}
            status = str(public_step["status"])
            step_id = str(public_step["step_id"])
            sequence = int(public_step.get("sequence") or index)
            public_step_key = _multi_agent_step_public_text(
                step_key,
                fallback=step_id,
                raw_terms=raw_terms,
                principal=principal,
            ) or step_id
            title_fallback = public_step_key
            role_fallback = ""
            title = _multi_agent_step_public_text(
                public_step.get("title"),
                fallback=title_fallback,
                raw_terms=raw_terms,
                principal=principal,
            ) or public_step_key
            role = _multi_agent_step_public_text(
                public_step.get("role"),
                fallback=role_fallback,
                raw_terms=raw_terms,
                principal=principal,
            ) or None
            depends_on = raw_depends_on(
                payload.get("depends_on") or configured.get("depends_on") or configured.get("dependsOn"),
            )
            source = "recorded"
        else:
            status = "pending"
            step_id = None
            sequence = index
            public_step_key = _multi_agent_step_public_text(
                step_key,
                fallback=f"step-{index}",
                raw_terms=raw_terms,
                principal=principal,
            ) or f"step-{index}"
            title = _multi_agent_step_public_text(
                configured.get("title"),
                fallback=public_step_key,
                raw_terms=raw_terms,
                principal=principal,
            ) or public_step_key
            role = _multi_agent_step_public_text(
                configured.get("role"),
                fallback="",
                raw_terms=raw_terms,
                principal=principal,
            ) or None
            depends_on = raw_depends_on(
                configured.get("depends_on") or configured.get("dependsOn"),
            )
            source = "configured"

        dependency_state = dependency_statuses(
            depends_on,
            status_by_key,
            raw_terms=raw_terms,
            principal=principal,
        )
        projected_depends_on = [str(item["step_key"]) for item in dependency_state if item.get("step_key")]
        blocked_reason = multi_agent_blocked_reason(status, dependency_state)
        ready = status == "pending" and blocked_reason is None
        if (
            ready
            and not unsafe_dispatch_reference(step_key, raw_terms=raw_terms)
            and not any(unsafe_dispatch_reference(dependency, raw_terms=raw_terms) for dependency in depends_on)
        ):
            safe_ready_count += 1
        readiness_steps.append(
            {
                "step_key": public_step_key,
                "step_id": step_id,
                "title": title,
                "role": role,
                "sequence": sequence,
                "status": status,
                "depends_on": projected_depends_on,
                "dependency_statuses": dependency_state,
                "ready": ready,
                "blocked_reason": blocked_reason,
                "source": source,
            }
        )

    missing_dependencies = sum(
        1
        for item in readiness_steps
        for dependency in item["dependency_statuses"]
        if isinstance(dependency, dict) and dependency.get("status") == "missing"
    )
    hidden_dependencies = sum(
        1
        for item in readiness_steps
        for dependency in item["dependency_statuses"]
        if isinstance(dependency, dict) and dependency.get("status") == "hidden"
    )
    blocked = sum(
        1
        for item in readiness_steps
        if item["status"] == "pending"
        and not item["ready"]
        and item["blocked_reason"] in {"waiting_on_dependencies", "missing_dependencies", "hidden_dependencies"}
    )
    ready_count = sum(1 for item in readiness_steps if item["ready"])
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        dispatch_gate = _control_action(enabled=False, reason="run_not_dispatchable", method=None, href=None)
    elif ready_count <= 0:
        dispatch_gate = _control_action(enabled=False, reason="no_ready_steps", method=None, href=None)
    elif safe_ready_count <= 0:
        dispatch_gate = _control_action(enabled=False, reason="no_safe_ready_steps", method=None, href=None)
    elif is_ai_admin(principal):
        dispatch_gate = _control_action(
            enabled=True,
            reason="ready_steps_available",
            method="POST",
            href=f"/api/ai/runs/{run['id']}/multi-agent/dispatch/claims",
        )
    else:
        dispatch_gate = _control_action(enabled=False, reason="admin_only_dispatch", method=None, href=None)
    return {
        "enabled": True,
        "execution_mode": public_execution_mode,
        "steps": readiness_steps,
        "counts": {
            "configured": len(configured_steps),
            "recorded": len(steps),
            "completed": sum(1 for item in readiness_steps if item["status"] == "succeeded"),
            "ready": ready_count,
            "blocked": blocked,
            "missing_dependencies": missing_dependencies,
            "hidden_dependencies": hidden_dependencies,
        },
        "gates": {"dispatch": dispatch_gate},
    }


def unsafe_dispatch_reference(value: str, *, raw_terms: set[str]) -> bool:
    raw = str(value or "").strip()
    sanitized = sanitize_public_text(raw)
    if not sanitized or sanitized != raw:
        return True
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized):
        return True
    normalized_key = "".join(ch for ch in sanitized if ch.isalnum()).lower()
    if normalized_key in FORBIDDEN_PUBLIC_KEY_ALIASES:
        return True
    try:
        assert_safe_id(sanitized, "step_key")
    except ValueError:
        return True
    return contains_raw_projection_term(sanitized, raw_terms)


def dispatch_claim_sequence(
    *,
    step_key: str,
    row: dict[str, object] | None,
    configured_by_key: dict[str, dict[str, object]],
) -> int:
    if row is not None:
        sequence = int(row.get("sequence") or 0)
        if sequence > 0:
            return sequence
    for index, configured_key in enumerate(configured_by_key, start=1):
        if configured_key == step_key:
            return index
    return len(configured_by_key) + 1


def dispatch_claim_candidate(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    step_key: str,
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_status = normalize_run_status(str(run.get("status") or ""))
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run_not_dispatchable")
    configured_steps = configured_multi_agent_steps(run)
    execution_input = run_execution_input(run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        raise HTTPException(status_code=409, detail="multi_agent_not_enabled")
    raw_terms = readiness_raw_projection_terms(run)
    if unsafe_dispatch_reference(step_key, raw_terms=raw_terms):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    configured = configured_by_key.get(step_key)
    row = recorded_by_key.get(step_key)
    if configured is None and row is None:
        raise HTTPException(status_code=409, detail="step_not_found")

    payload = row.get("payload_json") if row is not None and isinstance(row.get("payload_json"), dict) else {}
    depends_on = raw_depends_on(
        payload.get("depends_on") or (configured or {}).get("depends_on") or (configured or {}).get("dependsOn")
    )
    if any(unsafe_dispatch_reference(dependency, raw_terms=raw_terms) for dependency in depends_on):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    status_by_key = {key: normalize_step_status(item.get("status")) for key, item in recorded_by_key.items()}
    dependency_state = dependency_statuses(
        depends_on,
        status_by_key,
        raw_terms=raw_terms,
        principal=principal,
    )
    status = normalize_step_status(row.get("status") if row is not None else "pending")
    blocked_reason = multi_agent_blocked_reason(status, dependency_state)
    if status != "pending" or blocked_reason is not None:
        raise HTTPException(status_code=409, detail=blocked_reason or "step_not_pending")

    sequence = dispatch_claim_sequence(step_key=step_key, row=row, configured_by_key=configured_by_key)
    title = public_text_or_fallback(
        (row or {}).get("title") or (configured or {}).get("title"),
        step_key,
    ) or step_key
    role_value = (row or {}).get("role") or (configured or {}).get("role")
    role = public_text_or_fallback(role_value) if role_value is not None else None
    return {
        "step_key": step_key,
        "step_kind": str((row or {}).get("step_kind") or "agent"),
        "title": title,
        "role": role,
        "sequence": sequence,
        "depends_on": depends_on,
    }


def dispatch_tick_candidate(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_status = normalize_run_status(str(run.get("status") or ""))
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run_not_dispatchable")
    if str(run_execution_input(run).get("execution_mode") or "") != "multi_agent":
        raise HTTPException(status_code=409, detail="multi_agent_not_enabled")

    readiness = multi_agent_readiness_snapshot(run=run, steps=steps, principal=principal)
    counts = readiness.get("counts") if isinstance(readiness, dict) else {}
    if not isinstance(counts, dict) or int(counts.get("ready") or 0) <= 0:
        raise HTTPException(status_code=409, detail="no_ready_steps")

    configured_steps = configured_multi_agent_steps(run)
    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    ordered_keys = list(configured_by_key)
    for key, row in sorted(recorded_by_key.items(), key=lambda item: int(item[1].get("sequence") or 0)):
        if key not in configured_by_key:
            ordered_keys.append(key)

    for step_key in ordered_keys:
        try:
            return {
                **dispatch_claim_candidate(
                    run=run,
                    steps=steps,
                    step_key=step_key,
                    principal=principal,
                ),
                "step_key": step_key,
            }
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail in {
                "unsafe_step_reference",
                "step_not_pending",
                "terminal_step",
                "already_running",
                "waiting_on_dependencies",
                "missing_dependencies",
                "hidden_dependencies",
            }:
                continue
            raise
    raise HTTPException(status_code=409, detail="no_safe_ready_steps")


def run_control_readiness_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    queue_insight: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return read-only readiness for platform-controlled run actions."""
    run_id = str(run["id"])
    status = normalize_run_status(str(run["status"]))
    raw_terms = readiness_raw_projection_terms(run)
    checkpoint_candidates = [
        item
        for item in (_checkpoint_candidate_from_step(row, principal, raw_terms=raw_terms) for row in steps)
        if item is not None
    ]
    cancel_requested = bool(run.get("cancel_requested_at"))
    if cancel_requested:
        cancel_reason = "cancel_already_requested"
    elif status in RUN_CONTROL_ACTIVE_STATUSES:
        cancel_reason = "cancel_available"
    elif status in RUN_CONTROL_TERMINAL_STATUSES:
        cancel_reason = "terminal_run"
    else:
        cancel_reason = "status_not_cancellable"
    cancel_enabled = cancel_reason == "cancel_available"

    if status in RUN_CONTROL_ACTIVE_STATUSES:
        resume_reason = "active_run"
    elif checkpoint_candidates:
        resume_reason = "checkpoint_outputs_available"
    else:
        resume_reason = "no_checkpoint_outputs"
    resume_enabled = resume_reason == "checkpoint_outputs_available"

    retry_enabled = status in RUN_CONTROL_RETRY_PREVIEW_STATUSES
    retry_reason = "retry_available" if retry_enabled else "status_not_retryable"
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = "run_failed" if raw_error_message and status == "failed" else ""
        run_summary["error_message"] = readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    return {
        "contract_version": RUN_CONTROL_READINESS_CONTRACT_VERSION,
        "run": run_summary,
        "actions": {
            "cancel": _control_action(
                enabled=cancel_enabled,
                reason=cancel_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/cancel",
            ),
            "resume": _control_action(
                enabled=resume_enabled,
                reason=resume_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/resume",
            ),
            "retry": _control_action(
                enabled=retry_enabled,
                reason=retry_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/retry",
            ),
        },
        "checkpoint_candidates": checkpoint_candidates,
        "queue_insight": queue_insight,
        "multi_agent": multi_agent_readiness_snapshot(run=run, steps=steps, principal=principal),
    }

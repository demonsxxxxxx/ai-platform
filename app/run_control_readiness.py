from app.auth import AuthPrincipal, is_ai_admin
from app.run_projection import (
    normalize_run_status,
    normalize_step_status,
    run_step_response,
    run_step_responses,
)
from app.run_provenance import (
    readiness_public_text,
    readiness_raw_projection_terms,
    run_playback_summary,
)

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
    public_step: dict[str, object] | None = None,
) -> dict[str, object] | None:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    status = normalize_step_status(row.get("status"))
    if status != "succeeded" or payload.get("output") is None:
        return None
    public_step = public_step or run_step_response(row, principal=principal)
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
    public_steps_by_id = {
        str(step["step_id"]): step for step in run_step_responses(steps, principal=principal)
    }
    checkpoint_candidates = [
        item
        for item in (
            _checkpoint_candidate_from_step(
                row,
                principal,
                raw_terms=raw_terms,
                public_step=public_steps_by_id.get(str(row["id"])),
            )
            for row in steps
        )
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
    return {
        "contract_version": RUN_CONTROL_READINESS_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
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
    }

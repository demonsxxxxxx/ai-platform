"""Conversation submission resolution application projection."""

from collections.abc import Awaitable, Callable
from typing import Any


_PUBLIC_RUN_STATUSES = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled"}
)


def normalize_submission_run_status(value: object) -> str | None:
    status = str(value or "").strip().lower()
    if status == "canceled":
        status = "cancelled"
    return status if status in _PUBLIC_RUN_STATUSES else None


def submission_resolution_projection(
    row: dict[str, Any],
    *,
    run_status: str | None = None,
) -> dict[str, Any]:
    """Build the read-only public submission projection."""

    outcome = row.get("outcome_json")
    return {
        "submission_id": str(row["submission_id"]),
        "state": str(row.get("state") or "accepted_pending_enqueue"),
        "submission_disposition": (
            "rejected_before_persist"
            if row.get("submission_disposition") == "rejected_before_persist"
            else None
        ),
        "rejection_code": str(row["rejection_code"])
        if row.get("rejection_code")
        else None,
        "outcome": outcome if isinstance(outcome, dict) and outcome else None,
        "run_status": run_status,
    }


async def resolve_chat_submission(
    conn: Any,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    get_submission: Callable[..., Awaitable[dict[str, Any] | None]],
    get_authorized_run: Callable[..., Awaitable[dict[str, Any] | None]],
) -> dict[str, Any] | None:
    """Resolve a submission and its linked Run within the caller's transaction."""

    row = await get_submission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
    )
    if row is None:
        return None

    run_status = None
    run_id = str(row.get("run_id") or "").strip()
    if run_id:
        run = await get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
        )
        if run is not None:
            run_status = normalize_submission_run_status(run.get("status"))
    return submission_resolution_projection(row, run_status=run_status)

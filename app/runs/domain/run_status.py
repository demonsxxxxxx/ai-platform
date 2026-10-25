"""Run status vocabulary and read-side classification owned by Runs."""

from __future__ import annotations


ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
RUN_RETRYABLE_STATUSES = frozenset(
    {"failed", "dead-letter", "dead_letter", "dead-lettered"}
)

RUN_STATUS_VALUES = frozenset(
    {
        *ACTIVE_RUN_STATUSES,
        "cancel_requested",
        *TERMINAL_RUN_STATUSES,
        "canceled",
        "dead-letter",
        "dead_letter",
        "dead-lettered",
    }
)
PUBLIC_RUN_STATUSES = frozenset(ACTIVE_RUN_STATUSES | TERMINAL_RUN_STATUSES)


def normalize_run_status(value: object) -> str:
    """Normalize the historical cancellation spelling without promoting unknown values."""

    status = str(value or "").strip().lower()
    return "cancelled" if status == "canceled" else status


def is_terminal_run_status(value: object) -> bool:
    """Classify a Run status, including the supported historical cancellation alias."""

    return normalize_run_status(value) in TERMINAL_RUN_STATUSES


def normalize_public_run_status(value: object) -> str | None:
    """Return a safe public Run status or None for unknown and private states."""

    status = normalize_run_status(value)
    return status if status in PUBLIC_RUN_STATUSES else None

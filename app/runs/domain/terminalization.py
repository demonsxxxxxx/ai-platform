"""Pure Run terminalization values and decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class RunTerminalizationProgress:
    """Describe one bounded terminalization attempt and transition ownership."""

    completed: bool
    status: str | None
    did_transition: bool = False
    needs_reconcile: bool = False
    terminalized_count: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        """Return one field with mapping-style compatibility for existing callers."""

        return getattr(self, key, default)

    def is_terminal(self, requested_status: str | None = None) -> bool:
        """Return whether the completed status is terminal and optionally requested."""

        return (
            self.completed
            and self.status in TERMINAL_RUN_STATUSES
            and (requested_status is None or self.status == requested_status)
        )

    def __bool__(self) -> bool:
        """Treat only a completed terminal transition as truthy."""

        return self.is_terminal()


def progress_for_requested_status(
    progress: RunTerminalizationProgress | None,
    *,
    requested_status: str,
) -> RunTerminalizationProgress:
    """Preserve observed facts while denying completion when another intent won."""

    if progress is None:
        return RunTerminalizationProgress(completed=False, status=None)
    if progress.is_terminal(requested_status):
        return progress
    return RunTerminalizationProgress(
        completed=False,
        status=progress.status,
        did_transition=progress.did_transition,
        needs_reconcile=progress.needs_reconcile,
        terminalized_count=progress.terminalized_count,
    )

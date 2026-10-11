"""Small, standard-logging trace primitives shared by runtime adapters."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from uuid import uuid4


def standard_trace_id(seed: str | None = None) -> str:
    if seed:
        normalized = seed.replace("run_", "", 1).replace("-", "_")
        return f"trace_{normalized}"
    return f"trace_{uuid4().hex}"


_terminal_stage_logger = logging.getLogger("app.platform.terminal_timing")


class _TerminalStageHandler(logging.StreamHandler):
    """The one owned, payload-free terminal timing sink."""

    def handleError(self, _record: logging.LogRecord) -> None:
        # StreamHandler otherwise prints a traceback on write/format failure.
        # During exception unwinding that can expose the private original error.
        pass


def configure_terminal_stage_logging() -> None:
    """Enable only bounded terminal observations at the process entrypoint."""
    _terminal_stage_logger.setLevel(logging.INFO)
    _terminal_stage_logger.propagate = False
    if not any(isinstance(handler, _TerminalStageHandler) for handler in _terminal_stage_logger.handlers):
        handler = _TerminalStageHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s run_id=%(run_id)r attempt_id=%(attempt_id)r "
            "phase=%(terminal_phase)r duration_ms=%(terminal_phase_duration_ms)d "
            "outcome=%(terminal_phase_outcome)r terminal_status=%(terminal_status)r"
        ))
        _terminal_stage_logger.addHandler(handler)


class TerminalStageTiming:
    """One monotonic terminal-stage observation, with no payload or error text.

    This observes existing barriers only; success means the measured operation
    returned, not that another process or later persistence stage has finished.
    """

    def __init__(
        self, phase: str, *, run_id: str,
        attempt_id: str, terminal_status: object = None,
    ) -> None:
        self._phase = phase
        self._run_id = run_id
        self._attempt_id = attempt_id
        self.terminal_status = terminal_status
        self._started_at = monotonic()
        self._finished = False

    def __enter__(self) -> TerminalStageTiming:
        return self

    def __exit__(self, _type, error, _traceback) -> bool:
        self.finish(error)
        return False

    def finish(self, error: BaseException | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        outcome = (
            "cancelled" if isinstance(error, asyncio.CancelledError)
            else "error" if error is not None else "ok"
        )
        status = self.terminal_status
        status = (
            {"completed": "succeeded", "canceled": "cancelled"}.get(status, status)
            if isinstance(status, str) else None
        )
        if status not in {"succeeded", "failed", "cancelled", "skipped"}:
            status = "unknown"
        try:
            _terminal_stage_logger.info(
                "run_terminal_stage",
                extra={
                    "run_id": self._run_id,
                    "attempt_id": self._attempt_id,
                    "terminal_phase": self._phase,
                    "terminal_phase_duration_ms": max(
                        int((monotonic() - self._started_at) * 1000), 0
                    ),
                    "terminal_phase_outcome": outcome,
                    "terminal_status": status,
                },
            )
        except Exception:
            # An observability failure cannot change receipt/commit semantics or
            # replace the exception (including cancellation) being propagated.
            pass

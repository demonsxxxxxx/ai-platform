"""Small, standard-logging trace primitives shared by runtime adapters."""

from __future__ import annotations

import asyncio
import logging
import re
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


class _TerminalObservationFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.msg == "run_session_store_append_summary":
            return logging.Formatter(
                "%(asctime)s %(message)s run_id=%(run_id)r attempt_id=%(attempt_id)r "
                "calls=%(append_calls)d failed_calls=%(append_failed_calls)d "
                "total_duration_ms=%(append_total_duration_ms)d max_duration_ms=%(append_max_duration_ms)d "
                "store_active_calls=%(store_active_calls)d"
            ).format(record)
        return super().format(record)


def log_session_store_append_summary(
    *, run_id: str, attempt_id: str, calls: int, failed_calls: int,
    total_ms: int, max_ms: int, active_calls: int,
) -> None:
    """One payload-free aggregate; cumulative append work can overlap model work."""
    try:
        _terminal_stage_logger.info("run_session_store_append_summary", extra={
            "run_id": run_id, "attempt_id": attempt_id,
            "append_calls": calls, "append_failed_calls": failed_calls,
            "append_total_duration_ms": total_ms, "append_max_duration_ms": max_ms,
            "store_active_calls": active_calls,
        })
    except Exception:
        pass  # Observation failure cannot replace the persistence result.


def configure_terminal_stage_logging() -> None:
    """Enable only bounded terminal observations at the process entrypoint."""
    _terminal_stage_logger.setLevel(logging.INFO)
    _terminal_stage_logger.propagate = False
    if not any(isinstance(handler, _TerminalStageHandler) for handler in _terminal_stage_logger.handlers):
        handler = _TerminalStageHandler()
        handler.setFormatter(_TerminalObservationFormatter(
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


_LOG_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,95}")
_LOG_MODULE = re.compile(r"app(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def safe_exception_log_fields(exc: BaseException) -> dict[str, object]:
    """Bounded structural evidence only; never render exception text or locals.

    Read BaseException descriptors directly so wrapper-defined properties cannot
    execute during failure handling. A displayed chain follows Python's explicit
    cause precedence and suppressed-context semantics. This is logging evidence,
    not an authority for retry or terminal state.
    """
    from psycopg import errors

    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    cursor: BaseException | None = exc
    relation = "root"
    end = "complete"
    while cursor is not None:
        if id(cursor) in seen:
            end = "cycle"
            break
        if len(chain) >= 8:
            end = "limit"
            break
        seen.add(id(cursor))
        cls = type(cursor)
        name = type.__getattribute__(cls, "__name__") if type(cls) is type else "Exception"
        node: dict[str, object] = {
            "relation": relation,
            "exception_type": name if type(name) is str and _LOG_IDENTIFIER.fullmatch(name) else "Exception",
        }
        frames: list[str] = []
        tb = BaseException.__traceback__.__get__(cursor)
        for _ in range(128):
            if tb is None:
                break
            module = tb.tb_frame.f_globals.get("__name__")
            function = tb.tb_frame.f_code.co_name
            if (
                type(module) is str and len(module) <= 192 and _LOG_MODULE.fullmatch(module)
                and _LOG_IDENTIFIER.fullmatch(function)
            ):
                frames.append(f"{module}:{tb.tb_lineno}:{function}")
            tb = tb.tb_next
        node["frames"] = frames[-8:]
        if tb is not None:
            node["frames_limited"] = True
        # Only the exact psycopg registered error class supplies SQLSTATE. Do not
        # inspect arbitrary exception attributes, driver diagnostics or strings.
        if type(cls) is type:
            code = type.__getattribute__(cls, "__dict__").get("sqlstate")
            if type(code) is str and re.fullmatch(r"[0-9A-Z]{5}", code):
                try:
                    if errors.lookup(code) is cls:
                        node["sqlstate"] = code
                except KeyError:
                    pass
        chain.append(node)
        cause = BaseException.__cause__.__get__(cursor)
        context = BaseException.__context__.__get__(cursor)
        if cause is not None:
            cursor, relation = cause, "cause"
        elif not BaseException.__suppress_context__.__get__(cursor):
            cursor, relation = context, "context"
        else:
            cursor = None
    return {"exception_chain": chain, "exception_chain_end": end}

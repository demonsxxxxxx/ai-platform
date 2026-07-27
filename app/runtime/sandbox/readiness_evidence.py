from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


ReadinessPhase = Literal["publish_wait", "health_probe"]
ContainerState = Literal["created", "running", "exited", "dead", "unknown"]
HealthOutcome = Literal["not_attempted", "healthy", "unhealthy", "timeout", "transport_error"]
_MAX_BOUNDED_INT = 2**31 - 1
_MIN_BOUNDED_INT = -(2**31)


class ExecutorReadinessEvidence(BaseModel):
    """Typed, bounded, non-sensitive evidence for one failed readiness phase."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    readiness_phase: ReadinessPhase
    container_state: ContainerState
    exit_code: int | None = Field(default=None, ge=_MIN_BOUNDED_INT, le=_MAX_BOUNDED_INT)
    oom_killed: bool | None = None
    published_port_observed: bool
    health_outcome: HealthOutcome
    elapsed_ms: int = Field(ge=0, le=_MAX_BOUNDED_INT)


def bounded_elapsed_ms(started_at: object, finished_at: object) -> int:
    """Return a finite nonnegative millisecond duration bounded to signed 32-bit max."""

    if type(started_at) not in {int, float} or type(finished_at) not in {int, float}:
        return 0
    elapsed_seconds = finished_at - started_at
    if not math.isfinite(elapsed_seconds):
        return 0
    return min(max(int(round(elapsed_seconds * 1000)), 0), _MAX_BOUNDED_INT)


def health_failure_outcome(exc: BaseException) -> Literal["timeout", "transport_error"]:
    """Classify a probe failure without retaining its text or transport details."""

    return "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "transport_error"


def normalize_docker_readiness_evidence(
    readiness_phase: ReadinessPhase,
    container_attrs: object,
    container_status: object,
    published_port_observed: bool,
    health_outcome: HealthOutcome,
    elapsed_ms: object,
) -> ExecutorReadinessEvidence:
    """Allowlist Docker state fields and discard every unrecognized or malformed value."""

    state_attrs = container_attrs.get("State") if isinstance(container_attrs, Mapping) else None
    raw_state = (
        state_attrs.get("Status")
        if isinstance(state_attrs, Mapping) and "Status" in state_attrs
        else container_status
    )
    normalized_state = raw_state.strip().lower() if isinstance(raw_state, str) else "unknown"
    container_state: ContainerState = (
        normalized_state
        if normalized_state in {"created", "running", "exited", "dead"}
        else "unknown"
    )
    terminal_state = state_attrs if isinstance(state_attrs, Mapping) and container_state in {"exited", "dead"} else {}
    raw_exit_code, raw_oom_killed = terminal_state.get("ExitCode"), terminal_state.get("OOMKilled")
    exit_code = (
        raw_exit_code
        if type(raw_exit_code) is int and _MIN_BOUNDED_INT <= raw_exit_code <= _MAX_BOUNDED_INT
        else None
    )
    oom_killed = raw_oom_killed if type(raw_oom_killed) is bool else None
    bounded_ms = min(max(elapsed_ms, 0), _MAX_BOUNDED_INT) if type(elapsed_ms) is int else 0
    return ExecutorReadinessEvidence(
        readiness_phase=readiness_phase,
        container_state=container_state,
        exit_code=exit_code,
        oom_killed=oom_killed,
        published_port_observed=published_port_observed,
        health_outcome=health_outcome,
        elapsed_ms=bounded_ms,
    )


def safe_readiness_evidence_payload(evidence: ExecutorReadinessEvidence) -> dict[str, object]:
    """Serialize only the seven contract fields allowed in private runtime persistence."""

    return {
        "readiness_phase": evidence.readiness_phase,
        "container_state": evidence.container_state,
        "exit_code": evidence.exit_code,
        "oom_killed": evidence.oom_killed,
        "published_port_observed": evidence.published_port_observed,
        "health_outcome": evidence.health_outcome,
        "elapsed_ms": evidence.elapsed_ms,
    }

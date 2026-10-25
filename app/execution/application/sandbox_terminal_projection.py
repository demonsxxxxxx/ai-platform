"""Execution-owned disposition and result projection for Sandbox terminal facts.

Provider authorization, evidence validation, and artifact I/O precede projection
in the adapter. A successful disposition permits artifact collection; failures
never do. This module neither creates executor objects nor performs I/O.
"""

from dataclasses import dataclass
from typing import Any

from app.execution.domain.public_projection import (
    claude_sdk_failure_message,
    sdk_failure_result_fields,
)


@dataclass(frozen=True)
class SandboxTerminalDisposition:
    succeeded: bool
    error_code: str | None
    message: str


def sandbox_terminal_disposition(
    runtime_status: str,
    executor_response: dict[str, Any],
    *,
    capability_error: str | None,
) -> SandboxTerminalDisposition:
    """Preserve terminal precedence, including failed capability evidence."""
    if runtime_status in {"completed", "succeeded"}:
        if capability_error is not None:
            return SandboxTerminalDisposition(
                False, capability_error,
                "Capability execution evidence was incomplete. Please retry.",
            )
        return SandboxTerminalDisposition(
            True, None, str(executor_response.get("message") or ""),
        )
    if runtime_status == "accepted":
        return SandboxTerminalDisposition(
            False, "executor_missing_structured_terminal",
            "Sandbox executor returned without an authoritative terminal result",
        )
    error_code = str(executor_response.get("error_code") or "")
    cancelled = runtime_status in {"cancelled", "canceled"}
    if not error_code and cancelled:
        error_code = "executor_cancelled"
    if not error_code:
        error_code = "executor_reported_failure"
    message = (
        "任务已取消" if cancelled
        else claude_sdk_failure_message(type("SdkFailure", (), {"error": error_code})())
    )
    return SandboxTerminalDisposition(False, error_code, message)


@dataclass(frozen=True)
class SandboxTerminalProjection:
    status: str
    result: dict[str, Any]
    executor_payload: dict[str, Any]


def project_sandbox_terminal_result(
    disposition: SandboxTerminalDisposition,
    *,
    executor_response: dict[str, Any],
    allowed_skills: list[str],
    staged_skills: list[str],
    used_skills: list[str],
    common_payload: dict[str, Any],
    diagnostic_payload: dict[str, Any],
    turn_diagnostics: dict[str, Any],
    artifact_count: int,
) -> SandboxTerminalProjection:
    """Project validated terminal facts without exposing the private carrier."""
    skill_context = {
        "sdk_used": bool(executor_response.get("sdk_used")),
        "allowed_skills": allowed_skills,
        "staged_skills": staged_skills,
        "used_skills": used_skills,
    }
    if not disposition.succeeded:
        return SandboxTerminalProjection(
            status="failed",
            result={
                "message": disposition.message,
                "error_code": disposition.error_code,
                "sdk_error": disposition.error_code,
                **sdk_failure_result_fields(turn_diagnostics),
                **skill_context,
                **diagnostic_payload,
            },
            executor_payload={
                **common_payload,
                "sdk_error": disposition.error_code,
                "sdk_turn_diagnostics": turn_diagnostics,
                **diagnostic_payload,
            },
        )
    return SandboxTerminalProjection(
        status="succeeded",
        result={
            "message": disposition.message,
            "artifact_count": artifact_count,
            **skill_context,
            "sdk_error": None,
            "sdk_turn_diagnostics": turn_diagnostics,
        },
        executor_payload={
            **common_payload, **diagnostic_payload,
            "sdk_turn_diagnostics": turn_diagnostics,
        },
    )

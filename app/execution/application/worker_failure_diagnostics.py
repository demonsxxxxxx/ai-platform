"""Private diagnostic projection for Worker execution failures."""

from __future__ import annotations

from typing import Any

from app.runs.api import sanitize_runtime_diagnostics
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    SandboxExecutorHttpFailure,
    SandboxRuntimeFailure,
    exception_chain_from_error,
    normalize_sdk_runtime_diagnostics,
)


def diagnostic_failure_result(
    error: BaseException | None = None,
    *,
    error_code: str,
    failure_source: str,
    failure_stage: str,
    reason: str | None = None,
) -> dict[str, Any]:
    losses: list[dict[str, object]] = []
    sdk: dict[str, Any] = {"errors": [reason]} if reason else {}
    if error is not None:
        sdk.update(
            exception_type=type(error).__name__,
            exception_message=str(error),
            exception_chain=exception_chain_from_error(
                error, include_nested_text=False, losses=losses
            ),
        )
        startup = getattr(error, "opensandbox_startup_evidence", None)
        if startup is not None:
            try:
                sdk["errors"] = startup.private_payload()
                failure_stage = startup.stage.value
            except (AttributeError, TypeError, ValueError):
                pass
        private = getattr(error, "private_evidence", None)
        if isinstance(private, dict):
            sdk["errors"] = dict(private)
            failure_stage = str(private.get("startup_stage") or failure_stage)
        readiness = getattr(error, "readiness_evidence", None)
        if readiness is not None:
            evidence = {
                key: getattr(readiness, key)
                for key in (
                    "readiness_phase",
                    "container_state",
                    "exit_code",
                    "oom_killed",
                    "published_port_observed",
                    "health_outcome",
                    "elapsed_ms",
                )
                if hasattr(readiness, key)
            }
            if evidence:
                sdk["errors"] = {"readiness": evidence}
                failure_stage = str(evidence.get("readiness_phase") or failure_stage)
    return {
        "runtime_diagnostics": sanitize_runtime_diagnostics(
            normalize_sdk_runtime_diagnostics(
                {
                    "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                    "error_code": error_code,
                    "failure_source": failure_source,
                    "failure_stage": failure_stage,
                    "sdk": sdk,
                    "normalization_losses": losses,
                }
            )
        )
    }


def predispatch_failure_result(
    error_code: str,
    failure_stage: str,
    *,
    error: BaseException | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return diagnostic_failure_result(
        error,
        error_code=error_code,
        failure_source="worker_pre_dispatch",
        failure_stage=failure_stage,
        reason=reason,
    )


def executor_exception_failure(
    error: Exception,
    *,
    failure_source: str = "worker_executor",
    failure_stage: str = "executor",
) -> tuple[str, str, dict[str, Any] | None]:
    if isinstance(error, SandboxExecutorHttpFailure):
        private = (
            {"runtime_diagnostics": error.runtime_diagnostics}
            if error.runtime_diagnostics is not None
            else None
        )
        return error.error_code, error.public_message, private
    if isinstance(error, SandboxRuntimeFailure):
        code = error.error_code
        if code == "native_tool_admission_failed":
            message = "Native tool sandbox admission failed"
            public_code = code
        else:
            message = "Executor failed"
            public_code = "executor_failure"
            failure_source = "sandbox_runtime"
    elif type(error).__name__ == "WorkerDirectAssistantDeltaError":
        code = public_code = "worker_direct_assistant_delta_forbidden"
        message = "Executor used an unsupported text ingress"
    else:
        code = public_code = "executor_failure"
        message = "Executor failed"
    return public_code, message, diagnostic_failure_result(
        error_code=code,
        failure_source=failure_source,
        failure_stage=failure_stage,
        error=error,
    )

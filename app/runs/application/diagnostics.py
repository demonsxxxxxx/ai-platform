"""Application service for private Run diagnostic capture and query."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    build_executor_protocol_diagnostics,
    build_failure_observation,
    sanitize_run_diagnostic_text,
    sanitize_run_diagnostic_losses,
    sanitize_runtime_diagnostics,
    split_runtime_diagnostics,
)


logger = logging.getLogger(__name__)


class RunDiagnosticsPersistence(Protocol):
    async def append_observation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def get_admin_snapshot(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None: ...

    async def get_admin_monitor_metadata(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_ids: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RunDiagnosticsService:
    persistence: RunDiagnosticsPersistence
    normalize_runtime_diagnostics: Callable[[object], dict[str, Any]]
    runtime_diagnostics_schema_version: str
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc),
        repr=False,
    )

    async def capture_failure_result(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str | None,
        source: str,
        stage: str,
        error_code: str,
        result_json: dict[str, Any] | None,
        lease_id: str | None = None,
        request_id: str | None = None,
        callback_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a private carrier and return the public terminal result."""

        private_value, public_result = split_runtime_diagnostics(result_json)
        if private_value is not None:
            await self._capture_runtime_diagnostics(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                source=source,
                stage=stage,
                error_code=error_code,
                runtime_diagnostics=private_value,
                normalize=True,
                lease_id=lease_id,
                request_id=request_id,
                callback_id=callback_id,
            )
        return public_result

    async def _capture_runtime_diagnostics(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str | None,
        source: str,
        stage: str,
        error_code: str,
        runtime_diagnostics: object,
        normalize: bool,
        runtime_diagnostics_factory: Callable[[], object] | None = None,
        lease_id: str | None = None,
        request_id: str | None = None,
        callback_id: str | None = None,
    ) -> None:
        try:
            value = (
                runtime_diagnostics_factory()
                if runtime_diagnostics_factory is not None
                else runtime_diagnostics
            )
            normalized = sanitize_runtime_diagnostics(
                self.normalize_runtime_diagnostics(value) if normalize else value
            )
            if normalized:
                observation = build_failure_observation(
                    attempt_id=attempt_id,
                    source=source,
                    stage=stage,
                    error_code=error_code,
                    runtime_diagnostics=normalized,
                    received_at=self.clock(),
                    lease_id=lease_id,
                    request_id=request_id,
                    callback_id=callback_id,
                )
                await self.persistence.append_observation(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    observation=observation,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - diagnostics cannot veto the business result.
            logger.warning(
                "Run diagnostic capture degraded",
                extra={
                    "diagnostic_source": source,
                    "diagnostic_stage": stage,
                    "diagnostic_reason": type(exc).__name__,
                },
            )

    async def capture_executor_protocol_failure(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str | None,
        lease_id: str | None,
        task_status: object,
        terminal_result: object,
        validation_errors: object,
    ) -> None:
        await self._capture_runtime_diagnostics(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            source="executor_probe",
            stage="terminal_result_validation",
            error_code="executor_protocol_invalid",
            runtime_diagnostics=None,
            runtime_diagnostics_factory=lambda: build_executor_protocol_diagnostics(
                runtime_schema_version=self.runtime_diagnostics_schema_version,
                task_status=task_status,
                terminal_result=terminal_result,
                expected_run_id=run_id,
                validation_errors=validation_errors,
            ),
            normalize=False,
            lease_id=lease_id,
        )

    async def capture_reconciliation_failure(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str | None,
        terminal_result: object,
        reconciliation_error_code: str,
        lease_id: str | None = None,
    ) -> None:
        if isinstance(terminal_result, dict):
            await self.capture_failure_result(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                source="executor_callback",
                stage="terminal_receipt",
                error_code=str(terminal_result.get("error_code") or "executor_failed"),
                result_json=terminal_result,
                lease_id=lease_id,
            )
        await self.capture_failure_result(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            source="executor_reconciler",
            stage="terminalization",
            error_code="terminal_reconciliation_failed",
            result_json={
                "runtime_diagnostics": {
                    "schema_version": self.runtime_diagnostics_schema_version,
                    "error_code": "terminal_reconciliation_failed",
                    "failure_source": "executor_reconciler",
                    "failure_stage": "terminalization",
                    "sdk": {"errors": [reconciliation_error_code]},
                }
            },
            lease_id=lease_id,
        )

    async def read_admin(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        snapshot = await self.persistence.get_admin_snapshot(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        if snapshot is None:
            return None
        return _admin_projection(
            snapshot,
            normalize_runtime_diagnostics=self.normalize_runtime_diagnostics,
        )

    async def read_admin_monitor_metadata(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_ids: list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        bounded_run_ids = tuple(
            dict.fromkeys(
                run_id.strip()
                for run_id in run_ids
                if isinstance(run_id, str) and run_id.strip()
            )
        )[:100]
        if not bounded_run_ids:
            return {}
        return await self.persistence.get_admin_monitor_metadata(
            conn,
            tenant_id=tenant_id,
            run_ids=bounded_run_ids,
        )


def _admin_projection(
    snapshot: dict[str, Any],
    *,
    normalize_runtime_diagnostics: Callable[[object], dict[str, Any]],
) -> dict[str, Any]:
    diagnostic = snapshot.get("diagnostic")
    coverage = "not_collected"
    diagnostic_id = None
    revision = 0
    payload: dict[str, Any] = {}
    if isinstance(diagnostic, dict):
        diagnostic_id = str(diagnostic.get("diagnostic_id") or "") or None
        revision = int(diagnostic.get("revision") or 0)
        if diagnostic.get("schema_version") == RUN_DIAGNOSTICS_SCHEMA_VERSION:
            raw_payload = diagnostic.get("payload_json")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            coverage = str(payload.get("coverage") or "partial")
        else:
            coverage = "unsupported_schema"
    elif _has_legacy_runtime_diagnostics(snapshot.get("result_json")):
        normalized = normalize_runtime_diagnostics(
            _legacy_runtime_diagnostics(snapshot.get("result_json"))
        )
        if normalized:
            payload = {
                "observations": [
                    build_failure_observation(
                        attempt_id=None,
                        source="legacy_run_result",
                        stage="terminal_result",
                        error_code=str(
                            normalized.get("error_code") or "executor_failure"
                        ),
                        runtime_diagnostics=normalized,
                        received_at=datetime.fromtimestamp(0, timezone.utc),
                    )
                ],
                "losses": [],
                "counts": {"retained_observations": 1, "omitted_observations": 0},
            }
            coverage = "legacy_record"

    observations = payload.get("observations")
    observations = observations if isinstance(observations, list) else []
    root, handling, details, evidence_losses = _project_observations(observations)
    losses = [
        *evidence_losses,
        *sanitize_run_diagnostic_losses(payload.get("losses")),
    ]
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
        "diagnostic_id": diagnostic_id,
        "revision": revision,
        "coverage": coverage,
        "run": _admin_run_projection(snapshot["run"]),
        "root": root,
        "handling": handling,
        "losses": losses,
        "attempts": _admin_attempt_projections(snapshot.get("attempts")),
        "details": details,
        "versions": {
            "run_diagnostics": diagnostic.get("schema_version")
            if isinstance(diagnostic, dict)
            else None,
            "runtime_diagnostics": details.get("schema_version"),
            "redaction_policy": payload.get("redaction_policy_version"),
            "budget_policy": payload.get("budget_policy_version"),
        },
        "counts": {
            "retained_observations": int(
                counts.get("retained_observations") or len(observations)
            ),
            "omitted_observations": int(counts.get("omitted_observations") or 0),
        },
    }


def _admin_run_projection(value: object) -> dict[str, Any]:
    run = dict(value) if isinstance(value, dict) else {}
    run["error_code"] = sanitize_run_diagnostic_text(
        run.get("error_code"), max_bytes=256
    )
    return run


def _admin_attempt_projections(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        attempt = dict(raw)
        attempt["terminal_reason"] = sanitize_run_diagnostic_text(
            attempt.get("terminal_reason"), max_bytes=1_024
        )
        attempt["error_code"] = sanitize_run_diagnostic_text(
            attempt.get("error_code"), max_bytes=256
        )
        projected.append(attempt)
    return projected


def _legacy_runtime_diagnostics(value: object) -> object | None:
    if not isinstance(value, dict):
        return None
    return value.get("runtime_diagnostics")


def _has_legacy_runtime_diagnostics(value: object) -> bool:
    return isinstance(value, dict) and "runtime_diagnostics" in value


def _project_observations(
    observations: list[object],
) -> tuple[
    dict[str, Any] | None, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]
]:
    root = None
    handling: list[dict[str, Any]] = []
    details: dict[str, Any] = {
        "schema_version": None,
        "sdk": {},
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "executor_protocol": None,
        "observations": [],
    }
    losses: list[dict[str, Any]] = []
    for raw in observations:
        if not isinstance(raw, dict):
            continue
        evidence = sanitize_runtime_diagnostics(raw.get("runtime_diagnostics"))
        details["observations"].append(
            {
                "observation_id": raw.get("observation_id"),
                "attempt_id": raw.get("attempt_id"),
                "lease_id": raw.get("lease_id"),
                "request_id": raw.get("request_id"),
                "callback_id": raw.get("callback_id"),
                "received_at": raw.get("received_at"),
                "source": raw.get("source"),
                "stage": raw.get("stage"),
                "error_code": raw.get("error_code"),
                "sdk": evidence.get("sdk")
                if isinstance(evidence.get("sdk"), dict)
                else {},
                "tool_lifecycles": evidence.get("tool_lifecycles")
                if isinstance(evidence.get("tool_lifecycles"), list)
                else [],
                "tool_calls": evidence.get("tool_calls")
                if isinstance(evidence.get("tool_calls"), list)
                else [],
                "tool_policy_denials": evidence.get("tool_policy_denials")
                if isinstance(evidence.get("tool_policy_denials"), list)
                else [],
                "executor_protocol": evidence.get("executor_protocol")
                if isinstance(evidence.get("executor_protocol"), dict)
                else None,
                "normalization_losses": evidence.get("normalization_losses")
                if isinstance(evidence.get("normalization_losses"), list)
                else [],
            }
        )
        failures = evidence.get("failure_observations")
        failures = failures if isinstance(failures, list) else []
        failure_projections = [
            _failure_projection(raw, item, kind="handling") for item in failures
        ]
        if root is None:
            root = (
                failure_projections.pop(0)
                if failure_projections
                else _failure_projection(raw, raw)
            )
            root["kind"] = "failure"
            details.update(
                {
                    "schema_version": evidence.get("schema_version"),
                    "sdk": evidence.get("sdk")
                    if isinstance(evidence.get("sdk"), dict)
                    else {},
                    "tool_lifecycles": evidence.get("tool_lifecycles")
                    if isinstance(evidence.get("tool_lifecycles"), list)
                    else [],
                    "tool_calls": evidence.get("tool_calls")
                    if isinstance(evidence.get("tool_calls"), list)
                    else [],
                    "tool_policy_denials": evidence.get("tool_policy_denials")
                    if isinstance(evidence.get("tool_policy_denials"), list)
                    else [],
                }
            )
        handling.extend(failure_projections)
        record_projection = _failure_projection(raw, raw, kind="handling")
        failure_identities = {
            _projection_identity(item)
            for item in [*failure_projections, root]
            if item is not None
            and item.get("observation_id") == raw.get("observation_id")
        }
        if (
            _projection_identity(record_projection) not in failure_identities
            and _projection_identity(record_projection) != _projection_identity(root)
        ):
            handling.append(record_projection)
        protocol_evidence = evidence.get("executor_protocol")
        if details["executor_protocol"] is None and isinstance(protocol_evidence, dict):
            details["executor_protocol"] = protocol_evidence
        evidence_loss = evidence.get("normalization_losses")
        if isinstance(evidence_loss, list):
            losses.extend(item for item in evidence_loss if isinstance(item, dict))
    return root, handling, details, losses


def _failure_projection(
    record: dict[str, Any],
    value: object,
    *,
    kind: str = "failure",
) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    exception = item.get("exception") if isinstance(item.get("exception"), dict) else {}
    evidence = record.get("runtime_diagnostics")
    evidence = evidence if isinstance(evidence, dict) else {}
    sdk = evidence.get("sdk") if isinstance(evidence.get("sdk"), dict) else {}
    return {
        "observation_id": record.get("observation_id"),
        "attempt_id": record.get("attempt_id"),
        "lease_id": record.get("lease_id"),
        "request_id": record.get("request_id"),
        "callback_id": record.get("callback_id"),
        "kind": kind,
        "source": item.get("failure_source") or record.get("source"),
        "stage": item.get("failure_stage") or record.get("stage"),
        "error_code": item.get("error_code") or record.get("error_code"),
        "exception_type": exception.get("type") or sdk.get("exception_type"),
        "message": exception.get("message") or sdk.get("exception_message"),
        "stack": exception.get("traceback") or sdk.get("exception_traceback"),
        "received_at": record.get("received_at"),
    }


def _projection_identity(value: dict[str, Any]) -> tuple[object, object, object]:
    return value.get("source"), value.get("stage"), value.get("error_code")

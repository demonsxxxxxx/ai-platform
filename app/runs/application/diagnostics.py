"""Application service for private Run diagnostic capture and query."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    build_failure_observation,
    sanitize_run_diagnostic_text,
    sanitize_run_diagnostic_losses,
    sanitize_runtime_diagnostics,
    split_runtime_diagnostics,
)


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
        if private_value is None:
            return public_result
        normalized = sanitize_runtime_diagnostics(
            self.normalize_runtime_diagnostics(private_value)
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
        return public_result

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
    details: dict[str, Any] = {}
    losses: list[dict[str, Any]] = []
    for record_index, raw in enumerate(observations):
        if not isinstance(raw, dict):
            continue
        evidence = sanitize_runtime_diagnostics(raw.get("runtime_diagnostics"))
        failures = evidence.get("failure_observations")
        failures = failures if isinstance(failures, list) else []
        if root is None:
            root = _failure_projection(raw, failures[0] if failures else evidence)
            details = {
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
        for item in failures[1:] if record_index == 0 else failures:
            handling.append(_failure_projection(raw, item, kind="handling"))
        if record_index > 0 or len(failures) <= 1:
            projected = _failure_projection(raw, raw, kind="handling")
            if root is None or _projection_identity(projected) != _projection_identity(
                root
            ):
                handling.append(projected)
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

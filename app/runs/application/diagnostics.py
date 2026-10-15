"""Application service for private Run diagnostic capture and query."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from app.streaming.api import V4_METADATA_KEY
from app.runs.domain.run_performance import (
    BROWSER_LATENCY_MEASUREMENT,
    BROWSER_LATENCY_MAX_MS,
    browser_latency_ms,
    build_run_investigation,
)
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

    async def append_browser_latency(
        self, conn: Any, *, tenant_id: str, run_id: str,
        measurement: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def get_admin_monitor_report(
        self, conn: Any, *, tenant_id: str, from_at: datetime, to_at: datetime,
        user_id: str | None = None, agent_id: str | None = None,
        model_id: str | None = None, model_gateway_revision: int | None = None,
        status: str | None = None, comparison_run_id: str | None = None,
        order: str = "slow", limit: int = 50,
    ) -> dict[str, Any]: ...

    async def get_admin_investigation_events(
        self, conn: Any, *, tenant_id: str, run_id: str,
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
    exception_log_fields: Callable[[BaseException], dict[str, Any]]
    tool_operation_id: Callable[[object, object], str | None] | None = None
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
        capture_phase = "factory"
        observation_id = None
        try:
            value = (
                runtime_diagnostics_factory()
                if runtime_diagnostics_factory is not None
                else runtime_diagnostics
            )
            capture_phase = "normalize"
            value = self.normalize_runtime_diagnostics(value) if normalize else value
            capture_phase = "sanitize"
            normalized = sanitize_runtime_diagnostics(value)
            if normalized:
                capture_phase = "build"
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
                observation_id = observation.get("observation_id")
                capture_phase = "persist"
                await self.persistence.append_observation(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    observation=observation,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - diagnostics cannot veto the business result.
            try:
                evidence = self.exception_log_fields(exc)
                # These values come from owner arguments, never the private carrier.
                correlations = {
                    key: value for key, value in {
                        "run_id": run_id, "attempt_id": attempt_id,
                        "observation_id": observation_id,
                    }.items()
                    if type(value) is str and re.fullmatch({
                        "run_id": r"run_[0-9a-f]{32}",
                        "attempt_id": r"rat_[0-9a-f]{64}",
                        "observation_id": r"obs_[0-9a-f]{32}",
                    }[key], value)
                }
                labels = {
                    key: value if type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "unknown"
                    for key, value in {"diagnostic_source": source, "diagnostic_stage": stage}.items()
                }
                try:
                    # Isolate default handler stderr from the active private cause.
                    raise RuntimeError("safe failure logging context") from None
                except RuntimeError:
                    logger.warning(
                        "Run diagnostic capture degraded capture_phase=%s correlations=%s "
                        "exception_chain=%s exception_chain_end=%s",
                        capture_phase, correlations, evidence["exception_chain"], evidence["exception_chain_end"],
                        extra={
                            **labels, **correlations, **evidence,
                            "capture_phase": capture_phase,
                            "diagnostic_reason": evidence["exception_chain"][0]["exception_type"],
                        },
                    )
            except Exception:
                pass  # Failure reporting cannot veto or replace the business result.

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
        reconciliation_evidence_factory: Callable[[], dict[str, Any]] | None = None,
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

        def build_reconciliation_diagnostics() -> dict[str, Any]:
            evidence = (
                reconciliation_evidence_factory()
                if reconciliation_evidence_factory is not None else {}
            )
            return {
                "schema_version": self.runtime_diagnostics_schema_version,
                "error_code": "terminal_reconciliation_failed",
                "failure_source": "executor_reconciler",
                "failure_stage": "terminalization",
                "sdk": {**evidence.get("sdk", {}), "errors": [reconciliation_error_code]},
                "normalization_losses": evidence.get("normalization_losses", []),
            }

        await self._capture_runtime_diagnostics(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            source="executor_reconciler",
            stage="terminalization",
            error_code="terminal_reconciliation_failed",
            runtime_diagnostics=None,
            runtime_diagnostics_factory=build_reconciliation_diagnostics,
            normalize=True,
            lease_id=lease_id,
        )

    async def record_browser_latency(
        self, conn: Any, *, tenant_id: str, run_id: str, duration_ms: int,
    ) -> None:
        if type(duration_ms) is not int or not 0 <= duration_ms <= BROWSER_LATENCY_MAX_MS:
            raise ValueError("invalid_browser_latency")
        await self.persistence.append_browser_latency(
            conn, tenant_id=tenant_id, run_id=run_id,
            measurement={
                "measurement": BROWSER_LATENCY_MEASUREMENT,
                "duration_ms": duration_ms, "visibility": "visible",
                "received_at": self.clock().isoformat(),
            },
        )

    def _monitor_window(
        self, hours: int, from_at: datetime | None, to_at: datetime | None,
    ) -> tuple[datetime, datetime]:
        now = self.clock()
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("invalid_monitor_window")
        if from_at is None and to_at is None:
            return now - timedelta(hours=hours), now
        if (
            from_at is None or to_at is None
            or from_at.utcoffset() is None or to_at.utcoffset() is None
            or not timedelta(0) < to_at - from_at <= timedelta(days=7)
            or to_at > now
        ):
            raise ValueError("invalid_monitor_window")
        return from_at, to_at

    async def read_monitor(
        self, conn: Any, *, tenant_id: str, hours: int = 24,
        from_at: datetime | None = None, to_at: datetime | None = None,
        user_id: str | None = None, agent_id: str | None = None,
        model_id: str | None = None, model_gateway_revision: int | None = None,
        status: str | None = None, order: str = "slow", limit: int = 50,
    ) -> dict[str, Any]:
        if order not in {"slow", "recent"} or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_monitor_query")
        start, end = self._monitor_window(hours, from_at, to_at)
        report = await self.persistence.get_admin_monitor_report(
            conn, tenant_id=tenant_id, from_at=start, to_at=end, user_id=user_id,
            agent_id=agent_id, model_id=model_id, model_gateway_revision=model_gateway_revision,
            status=status, order=order, limit=limit,
        )
        metadata = await self.read_admin_monitor_metadata(
            conn, tenant_id=tenant_id,
            run_ids=[str(row.get("run_id") or "") for row in report["runs"]],
        )
        metrics = ("total_ms", "queue_ms", "execution_ms", "server_first_text_ms", "browser_first_text_ms")
        report["runs"] = [
            {
                **row, **metadata.get(str(row.get("run_id") or ""), {}),
                "latency": {name: row.get(name) for name in metrics},
            }
            for row in report["runs"]
        ]
        report["latency"] = {
            name: report.get("latency", {}).get(name, {"sample_count": 0, "p50": None, "p95": None, "p99": None})
            for name in metrics
        }
        counts = report["counts"]
        settled = counts["succeeded"] + counts["failed"]
        report["success_rate"] = counts["succeeded"] / settled if settled else None
        return report

    async def read_admin_investigation(
        self, conn: Any, *, tenant_id: str, run_id: str, hours: int = 24,
        from_at: datetime | None = None, to_at: datetime | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        start, end = self._monitor_window(hours, from_at, to_at)
        snapshot = await self.persistence.get_admin_snapshot(conn, tenant_id=tenant_id, run_id=run_id)
        if snapshot is None:
            return None
        run = snapshot["run"]
        diagnostic = snapshot.get("diagnostic")
        payload = diagnostic.get("payload_json") if isinstance(diagnostic, dict) else None
        browser = payload.get("browser_latency") if isinstance(payload, dict) else None
        evidence = await self.persistence.get_admin_investigation_events(conn, tenant_id=tenant_id, run_id=run_id)
        events = []
        for row in evidence["events"]:
            payload = row.get("payload", row.get("payload_json"))
            metadata = payload.get(V4_METADATA_KEY) if isinstance(payload, Mapping) else None
            metadata = metadata if isinstance(metadata, Mapping) else {}
            events.append({**row, "attempt_id": metadata.get("attempt_id"), "causation_event_id": metadata.get("causation_event_id")})
        investigation = build_run_investigation(
            run, events, attempts=snapshot.get("attempts", []),
            browser_measurement=browser, first_text_at=evidence.get("first_public_text_at"),
            observed_at=self.clock(),
        )
        comparison: dict[str, Any] = {
            "window": {"from_at": start, "to_at": end}, "sample_count": 0,
            "latency": {}, "normal_run_id": None,
            "scope": "same_agent_skill_profile_model_configuration",
            "missing_reason": "model_identity_unknown",
        }
        if run.get("model_id") and run.get("model_gateway_revision") is not None:
            reference = await self.persistence.get_admin_monitor_report(
                conn, tenant_id=tenant_id, from_at=start, to_at=end,
                user_id=user_id, comparison_run_id=run_id, limit=1,
            )
            comparison.update(
                sample_count=reference["counts"]["total"], latency=reference["latency"],
                normal_run_id=reference["runs"][0]["run_id"] if reference["runs"] else None,
                missing_reason=None if reference["runs"] else "no_comparable_successful_run",
            )
        return {
            "run_id": run_id, **investigation,
            "events_truncated": evidence["events_truncated"], "comparison": comparison,
        }

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
            tool_operation_id=self.tool_operation_id,
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
    tool_operation_id: Callable[[object, object], str | None] | None = None,
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
    if not observations and browser_latency_ms(payload.get("browser_latency")) is not None:
        coverage = "not_collected"
    root, handling, details, evidence_losses = _project_observations(observations)
    if tool_operation_id is not None:
        _correlate_admin_tools(details, snapshot["run"].get("run_id"), tool_operation_id)
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
        "browser_latency": {
            "measurement": BROWSER_LATENCY_MEASUREMENT,
            "duration_ms": browser_latency_ms(payload.get("browser_latency")),
        } if browser_latency_ms(payload.get("browser_latency")) is not None else None,
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


def _correlate_admin_tools(
    details: dict[str, Any],
    run_id: object,
    operation_id: Callable[[object, object], str | None],
) -> None:
    """Enrich sanitized read projections, never persisted diagnostic evidence."""

    evidence = [
        item
        for container in (details, *details.get("observations", []))
        for key in ("tool_calls", "tool_lifecycles", "tool_policy_denials")
        for item in container.get(key, [])
    ]
    names: dict[str, set[str]] = {}
    for item in evidence:
        if call_id := item.get("invocation_id"):
            names.setdefault(call_id, set()).add(item["tool_name"])
    for item in evidence:
        call_id = item.get("invocation_id")
        if call_id and len(names[call_id]) == 1:
            if public_id := operation_id(run_id, call_id):
                item["operation_id"] = public_id


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
        "projection_failure": None,
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
                "projection_failure": evidence.get("projection_failure")
                if isinstance(evidence.get("projection_failure"), dict)
                else None,
                "normalization_losses": evidence.get("normalization_losses")
                if isinstance(evidence.get("normalization_losses"), list)
                else [],
            }
        )
        failures = evidence.get("failure_observations")
        failures = failures if isinstance(failures, list) else []
        failure_projections = [
            _failure_projection(raw, item, evidence=evidence, kind="handling")
            for item in failures
        ]
        if root is None:
            root = (
                failure_projections.pop(0)
                if failure_projections
                else _failure_projection(raw, None, evidence=evidence)
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
        record_projection = _failure_projection(
            raw, None, evidence=evidence, kind="handling"
        )
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
        projection_failure = evidence.get("projection_failure")
        if details["projection_failure"] is None and isinstance(projection_failure, dict):
            details["projection_failure"] = projection_failure
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
    evidence: dict[str, Any],
    kind: str = "failure",
) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    exception = item.get("exception") if isinstance(item.get("exception"), dict) else {}
    # Fallbacks must use the same read-time redaction as detailed evidence,
    # including when a retained record predates the current redaction policy.
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

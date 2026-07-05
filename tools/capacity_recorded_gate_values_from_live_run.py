import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import LOAD_TEST_GATES  # noqa: E402
from app.foundation_runtime_concurrency import (  # noqa: E402
    FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
    build_foundation_runtime_concurrency_readiness,
)


SCHEMA_VERSION = "ai-platform.capacity-recorded-gate-values-from-live-run.v1"
_RUNTIME_EVIDENCE_SCHEMA = "ai-platform.capacity-runtime-evidence.v1"
_SNAPSHOT_SCHEMA = "ai-platform.capacity-evidence-snapshot.v1"
_BOUNDED_PROBE_SCHEMA = "ai-platform.capacity-bounded-load-harness.v1"
_REQUIRED_EVIDENCE_FIELDS = [
    "commit_sha",
    "api_worker_image_labels",
    "frontend_commit_or_image_label",
    "runtime_profile",
    "api_worker_process_counts",
    "database_pool_settings",
    "redis_queue_settings",
    "admission_worker_queue_sandbox_model_settings",
    "peak_and_sustained_queue_depths",
    "active_worker_runs_users_and_tenants",
    "database_pool_waiting_and_saturation",
    "latency_p50_p95_p99",
    "error_taxonomy_counts",
    "dead_letter_counts",
    "cleanup_proof",
]


def _read_json(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to read JSON input: {Path(path).name}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON input must be an object: {Path(path).name}")
    return payload


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _compact_value(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            compact = _compact_value(item)
            if compact is not None:
                cleaned[str(key)] = compact
        return cleaned if cleaned else None
    if isinstance(value, list):
        cleaned_items = [compact for item in value if (compact := _compact_value(item)) is not None]
        return cleaned_items if cleaned_items else None
    return value


def _runtime_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == _SNAPSHOT_SCHEMA:
        return payload
    if payload.get("schema_version") == _RUNTIME_EVIDENCE_SCHEMA:
        snapshot = _dict(payload.get("snapshot"))
        if snapshot.get("schema_version") == _SNAPSHOT_SCHEMA:
            return snapshot
    return {}


def _foundation_runtime_evidence(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if payload.get("schema_version") == _BOUNDED_PROBE_SCHEMA:
        return {}, ["bounded_probe_input_not_allowed"]
    if payload.get("schema_version") == FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA:
        return payload, []
    evidence = _dict(payload.get("evidence"))
    if evidence.get("schema_version") == FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA:
        return evidence, []
    return {}, ["foundation_runtime_evidence_missing_or_schema_unsupported"]


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return round(ordered[index], 3)


def _duration_percentiles_from_evidence(evidence: dict[str, Any]) -> dict[str, object]:
    durations = [
        float(item)
        for item in evidence.get("case_duration_seconds", [])
        if type(item) in {int, float} and float(item) >= 0
    ]
    if not durations:
        return {}
    return {
        "source": "foundation_runtime_case_durations",
        "count": len(durations),
        "p50": _percentile(durations, 0.50),
        "p95": _percentile(durations, 0.95),
        "p99": _percentile(durations, 0.99),
    }


def _latency_summary(snapshot: dict[str, Any], evidence: dict[str, Any]) -> dict[str, object]:
    live = _dict(snapshot.get("live_signals"))
    observability = _dict(live.get("observability"))
    latency = _dict(observability.get("latency_ms"))
    if all(type(latency.get(key)) in {int, float} for key in ("p50", "p95", "p99")):
        return {
            "source": "admin_runtime_observability_latency_ms",
            "p50": latency["p50"],
            "p95": latency["p95"],
            "p99": latency["p99"],
            "max": latency.get("max", 0),
            "avg": latency.get("avg", 0),
        }
    return _duration_percentiles_from_evidence(evidence)


def _cleanup_proof_verified(evidence: dict[str, Any]) -> bool:
    cleanup = _dict(evidence.get("cleanup_proof"))
    if not cleanup:
        return False
    candidates = [_dict(cleanup.get("after")), cleanup]
    for item in candidates:
        if item.get("status") != "verified":
            continue
        remaining = _dict(item.get("remaining_counts"))
        if remaining and any(_safe_int(value) != 0 for value in remaining.values()):
            continue
        return True
    return False


def _load_values(
    *,
    snapshot: dict[str, Any],
    foundation_evidence: dict[str, Any],
    evidence_ref_prefix: str,
) -> dict[str, object]:
    runtime_identity = _dict(snapshot.get("runtime_identity"))
    capacity = _dict(snapshot.get("capacity"))
    limits = _dict(capacity.get("limits"))
    live = _dict(snapshot.get("live_signals"))
    queue = _dict(live.get("queue"))
    database_pool = _dict(live.get("database_pool"))
    admission = _dict(live.get("admission"))
    sandbox = _dict(live.get("sandbox"))
    observability = _dict(live.get("observability"))
    summary = _dict(foundation_evidence.get("summary"))
    checks = _dict(foundation_evidence.get("checks"))
    queue_admission = _dict(checks.get("queue_admission"))
    sandbox_workspace = _dict(checks.get("sandbox_workspace"))

    raw_values = {
        "commit_sha": str(runtime_identity.get("commit_sha") or foundation_evidence.get("runtime_subject_commit_sha")),
        "api_worker_image_labels": f"{evidence_ref_prefix}/runtime-source-identity-and-image-labels.json",
        "frontend_commit_or_image_label": f"{evidence_ref_prefix}/frontend-runtime-health.json",
        "runtime_profile": str(runtime_identity.get("profile") or "b3-recorded-live-run"),
        "api_worker_process_counts": {
            "evidence_ref": f"{evidence_ref_prefix}/api-worker-process-counts.json",
            "source": "operator_docker_ps_readback",
        },
        "database_pool_settings": _dict(limits.get("database_pool")) or _dict(database_pool.get("configured")),
        "redis_queue_settings": {
            "queue_limits": _dict(limits.get("queue")),
            "queue_capacity": _dict(queue.get("capacity")),
            "queue_sample": _dict(queue.get("sample")),
        },
        "admission_worker_queue_sandbox_model_settings": {
            "admission": _dict(limits.get("admission")),
            "worker": _dict(limits.get("worker")),
            "queue": _dict(limits.get("queue")),
            "sandbox": _dict(limits.get("sandbox")),
            "model_gateway": _dict(limits.get("model_gateway")),
        },
        "peak_and_sustained_queue_depths": {
            "snapshot_depths": _dict(queue.get("depths")),
            "queue_admission": {
                "queue_position_sample_count": _safe_int(queue_admission.get("queue_position_sample_count")),
                "queue_probe_sample_count": _safe_int(queue_admission.get("queue_probe_sample_count")),
                "queue_position_duplicate_count": _safe_int(queue_admission.get("queue_position_duplicate_count")),
                "queue_probe_source": str(queue_admission.get("queue_probe_source") or "missing"),
            },
        },
        "active_worker_runs_users_and_tenants": {
            "tenant_count": _safe_int(summary.get("tenant_count")),
            "user_count": _safe_int(summary.get("user_count")),
            "session_count": _safe_int(summary.get("session_count")),
            "run_count": _safe_int(summary.get("run_count")),
            "concurrent_request_count": _safe_int(summary.get("concurrent_request_count")),
            "max_observed_concurrency": _safe_int(summary.get("max_observed_concurrency")),
            "active_runs": _safe_int(admission.get("active_runs")),
            "active_users": _safe_int(admission.get("active_users")),
            "saturated_users": _safe_int(admission.get("saturated_users")),
        },
        "database_pool_waiting_and_saturation": {
            "requests_waiting": _safe_int(database_pool.get("requests_waiting")),
            "max_waiting": _safe_int(database_pool.get("max_waiting")),
            "stats": _dict(database_pool.get("stats")),
        },
        "latency_p50_p95_p99": _latency_summary(snapshot, foundation_evidence),
        "error_taxonomy_counts": {
            "error_count": _safe_int(observability.get("error_count")),
            "error_categories": _dict(observability.get("error_categories")),
            "terminal_run_failure_count": len(foundation_evidence.get("terminal_run_failures") or []),
        },
        "dead_letter_counts": {
            "source": "admin_runtime_queue_depths",
            "dead_letter": _safe_int(_dict(queue.get("depths")).get("dead_letter")),
        },
        "cleanup_proof": f"{evidence_ref_prefix}/foundation-runtime-cleanup-proof.json",
        "sandbox_pressure_and_cleanup": {
            "active_leases": _safe_int(sandbox.get("active_leases")),
            "released_leases": _safe_int(_dict(sandbox.get("leases")).get("released")),
            "sandbox_lease_sample_count": _safe_int(sandbox_workspace.get("sandbox_lease_sample_count")),
            "cross_scope_lease_leaks": _safe_int(sandbox_workspace.get("cross_scope_lease_leaks")),
        },
    }
    return {
        key: compact
        for key, value in raw_values.items()
        if (compact := _compact_value(value)) is not None
    }


def _input_errors(
    *,
    snapshot: dict[str, Any],
    foundation_evidence: dict[str, Any],
    foundation_errors: list[str],
    readiness: dict[str, Any],
) -> list[str]:
    errors = list(foundation_errors)
    if not snapshot:
        errors.append("runtime_evidence_snapshot_missing")
    if foundation_evidence and readiness.get("verified") is not True:
        errors.append("foundation_runtime_evidence_not_verified")
    runtime_commit = str(_dict(snapshot.get("runtime_identity")).get("commit_sha") or "")
    evidence_commit = str(foundation_evidence.get("runtime_subject_commit_sha") or "")
    if runtime_commit and evidence_commit and runtime_commit != evidence_commit:
        errors.append("runtime_subject_commit_mismatch")
    if foundation_evidence and not _cleanup_proof_verified(foundation_evidence):
        errors.append("foundation_runtime_cleanup_proof_not_verified")
    return errors


def build_recorded_gate_values_from_live_run(
    *,
    runtime_evidence: dict[str, Any],
    foundation_runtime_evidence: dict[str, Any],
    evidence_ref_prefix: str,
) -> dict[str, Any]:
    """Build per-gate operator value payloads from verified live-run evidence."""
    snapshot = _runtime_snapshot(runtime_evidence)
    foundation_evidence, foundation_errors = _foundation_runtime_evidence(foundation_runtime_evidence)
    readiness = (
        build_foundation_runtime_concurrency_readiness(foundation_evidence)
        if foundation_evidence
        else build_foundation_runtime_concurrency_readiness()
    )
    errors = _input_errors(
        snapshot=snapshot,
        foundation_evidence=foundation_evidence,
        foundation_errors=foundation_errors,
        readiness=readiness,
    )
    values_by_gate: dict[str, dict[str, object]] = {}
    if not errors:
        values = _load_values(
            snapshot=snapshot,
            foundation_evidence=foundation_evidence,
            evidence_ref_prefix=evidence_ref_prefix.strip("/"),
        )
        missing_fields = [field for field in _REQUIRED_EVIDENCE_FIELDS if field not in values]
        errors.extend(f"runtime_evidence_field_{field}_missing" for field in missing_fields)
        if not errors:
            values_by_gate = {
                gate: {field: values[field] for field in _REQUIRED_EVIDENCE_FIELDS}
                for gate in LOAD_TEST_GATES
            }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "operator_value_files_ready" if not errors else "blocked_incomplete_inputs",
        "input_errors": errors,
        "foundation_runtime_readiness": {
            "status": readiness.get("status"),
            "verified": readiness.get("verified"),
            "failures": readiness.get("failures", []),
        },
        "runtime_identity": _dict(snapshot.get("runtime_identity")) if snapshot else {},
        "recorded_gates": list(values_by_gate) if not errors else [],
        "required_evidence_fields": list(_REQUIRED_EVIDENCE_FIELDS),
        "values_by_gate": values_by_gate,
        "does_not_raise_defaults": True,
        "does_not_close_b3_gate": True,
        "next_step": "run capacity_recorded_gate_batch_from_values.py with the generated operator input directory",
    }


def write_operator_value_files(result: dict[str, Any], output_dir: Path) -> list[str]:
    if result.get("status") != "operator_value_files_ready":
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    values_by_gate = _dict(result.get("values_by_gate"))
    for gate in LOAD_TEST_GATES:
        values = _dict(values_by_gate.get(gate))
        if not values:
            continue
        path = output_dir / f"capacity-operator-reviewed-evidence-values-{gate.replace('_', '-')}.json"
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path.name)
    manifest = {
        key: result[key]
        for key in (
            "schema_version",
            "status",
            "runtime_identity",
            "recorded_gates",
            "required_evidence_fields",
            "does_not_raise_defaults",
            "does_not_close_b3_gate",
            "next_step",
        )
    }
    manifest_path = output_dir / "capacity-recorded-gate-values-from-live-run.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(manifest_path.name)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize B3 recorded-gate operator value files from verified "
            "Foundation Runtime live-run evidence and a matching capacity "
            "runtime snapshot. Bounded probe JSON is rejected."
        )
    )
    parser.add_argument("--runtime-evidence-json", required=True)
    parser.add_argument("--foundation-runtime-evidence-json", required=True)
    parser.add_argument("--operator-input-dir", required=True)
    parser.add_argument(
        "--evidence-ref-prefix",
        default="capacity-evidence/b3-recorded-live-run",
        help="Safe relative evidence reference prefix stored inside generated values.",
    )
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    result = build_recorded_gate_values_from_live_run(
        runtime_evidence=_read_json(args.runtime_evidence_json),
        foundation_runtime_evidence=_read_json(args.foundation_runtime_evidence_json),
        evidence_ref_prefix=args.evidence_ref_prefix,
    )
    written = write_operator_value_files(result, Path(args.operator_input_dir))
    output = {
        **{key: value for key, value in result.items() if key != "values_by_gate"},
        "written_files": written,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "operator_value_files_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

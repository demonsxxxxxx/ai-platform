import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA = "ai-platform.capacity-bounded-load-harness.v1"
OPERATOR_ACKNOWLEDGEMENT = "send-bounded-load-without-default-raise"
_SUPPORTED_GATE = "api_read_write_burst"
_READ_ONLY_ENDPOINTS = (
    {
        "path": "/api/ai/health",
        "method": "GET",
        "purpose": "read-only API health probe",
    },
    {
        "path": "/api/ai/admin/runtime/overview",
        "method": "GET",
        "purpose": "read-only Admin Runtime capacity/backpressure projection probe",
    },
)
_LOAD_TEST_EVIDENCE_STATUS = "probe_only_not_recorded"
_SECRET_MARKERS = (
    "secret",
    "password",
    "api_key",
    "authorization",
    "bearer",
    "database_url",
    "redis_url",
    "raw_storage_key",
    "storage_key",
    "sandbox_workdir",
    "executor_private_payload",
)


def _safe_base_url(value: str) -> str:
    raw = str(value or "http://127.0.0.1:8020").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "http://127.0.0.1:8020"
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _bounded_int(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _endpoint_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _common_payload(
    *,
    base_url: str,
    gate: str,
    request_count: int,
    concurrency: int,
    execute: bool,
) -> dict[str, Any]:
    supported = gate == _SUPPORTED_GATE
    return {
        "schema_version": CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA,
        "gate": gate if supported else "unsupported",
        "supported_gate": _SUPPORTED_GATE,
        "status": "dry_run",
        "base_url": _safe_base_url(base_url),
        "request_count": _bounded_int(request_count, default=10, minimum=1, maximum=200),
        "concurrency": _bounded_int(concurrency, default=2, minimum=1, maximum=20),
        "execute": execute,
        "endpoints": [dict(item) for item in _READ_ONLY_ENDPOINTS] if supported else [],
        "operator_acknowledgement_required": True,
        "required_operator_acknowledgement": OPERATOR_ACKNOWLEDGEMENT,
        "load_test_evidence_status": _LOAD_TEST_EVIDENCE_STATUS,
        "gate_evidence_compatibility": "not_accepted_by_capacity_gate_readiness",
        "does_not_raise_defaults": True,
        "does_not_mark_gate_recorded": True,
        "ordinary_user_exposure": "none",
        "writes_runtime_state": False,
    }


def build_capacity_bounded_load_harness_plan(
    *,
    base_url: str,
    gate: str = _SUPPORTED_GATE,
    request_count: int = 10,
    concurrency: int = 2,
) -> dict[str, Any]:
    """Build a bounded capacity probe plan without sending runtime load."""
    return _common_payload(
        base_url=base_url,
        gate=gate,
        request_count=request_count,
        concurrency=concurrency,
        execute=False,
    )


def _safe_headers(*, user_id: str, tenant_id: str, roles: str) -> dict[str, str]:
    return {
        "X-AI-User-ID": _safe_header_value(user_id, "codex-capacity-audit"),
        "X-AI-Tenant-ID": _safe_header_value(tenant_id, "default"),
        "X-AI-Roles": _safe_header_value(roles, "admin"),
        "Accept": "application/json",
    }


def _safe_header_value(value: str, default: str) -> str:
    text = str(value or default).strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-,")
    cleaned = "".join(ch for ch in text if ch in allowed)
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return default
    return cleaned[:128] or default


def _request_once(url: str, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    started = time.perf_counter()
    request = Request(url, headers=headers, method="GET")
    status = 0
    content_type = ""
    observed_sections: list[str] = []
    error_type = None
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - operator-provided internal URL.
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            body = response.read(1024 * 1024)
            if "application/json" in content_type.lower():
                parsed = json.loads(body.decode("utf-8"))
                if isinstance(parsed, dict):
                    observed_sections = sorted(
                        key for key in parsed if key in {
                            "capacity",
                            "database_pool",
                            "queue",
                            "admission",
                            "backpressure",
                            "sandbox",
                            "observability",
                            "status",
                        }
                    )
    except HTTPError as exc:
        status = int(exc.code)
        error_type = None
    except (OSError, URLError, TimeoutError):
        error_type = "request_error"
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_type = "json_parse_error"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "status": status,
        "latency_ms": elapsed_ms,
        "content_type": content_type.split(";")[0].strip()[:64],
        "observed_sections": observed_sections,
        "error_type": error_type,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def _latency_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["latency_ms"]) for item in results]
    return {
        "count": len(values),
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
    }


def _status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        key = str(item.get("status") or 0)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _error_type_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        error_type = item.get("error_type")
        if isinstance(error_type, str) and error_type:
            counts[error_type] = counts.get(error_type, 0) + 1
    return dict(sorted(counts.items()))


def _observed_sections(results: list[dict[str, Any]]) -> list[str]:
    sections: set[str] = set()
    for item in results:
        source = item.get("observed_sections")
        if isinstance(source, list):
            sections.update(section for section in source if isinstance(section, str))
    return sorted(sections)


def _triggered_stop_conditions(status_counts: dict[str, int], error_counts: dict[str, int]) -> list[str]:
    triggered: list[str] = []
    total = sum(status_counts.values())
    five_xx = sum(count for status, count in status_counts.items() if status.startswith("5"))
    if total and five_xx / total > 0.05:
        triggered.append("http_5xx_rate_exceeds_threshold")
    non_2xx = sum(
        count
        for status, count in status_counts.items()
        if status != "0" and not status.startswith("2")
    )
    if non_2xx and "http_5xx_rate_exceeds_threshold" not in triggered:
        triggered.append("http_non_2xx_response_detected")
    if error_counts:
        triggered.append("request_errors_detected")
    return triggered


def run_capacity_bounded_load_harness(
    *,
    base_url: str,
    gate: str = _SUPPORTED_GATE,
    request_count: int = 10,
    concurrency: int = 2,
    execute: bool = False,
    operator_acknowledgement: str | None = None,
    user_id: str = "codex-capacity-audit",
    tenant_id: str = "default",
    roles: str = "admin",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Run a bounded read-only capacity probe only after explicit acknowledgement."""
    payload = _common_payload(
        base_url=base_url,
        gate=gate,
        request_count=request_count,
        concurrency=concurrency,
        execute=execute,
    )
    if payload["gate"] != _SUPPORTED_GATE:
        payload.update({"status": "blocked_unsupported_gate", "sent_requests": 0})
        return payload
    if not execute:
        return payload
    if operator_acknowledgement != OPERATOR_ACKNOWLEDGEMENT:
        payload.update(
            {
                "status": "blocked_missing_operator_acknowledgement",
                "sent_requests": 0,
            }
        )
        return payload

    headers = _safe_headers(user_id=user_id, tenant_id=tenant_id, roles=roles)
    endpoint_urls = [
        _endpoint_url(payload["base_url"], endpoint["path"])
        for endpoint in payload["endpoints"]
    ]
    urls = [endpoint_urls[index % len(endpoint_urls)] for index in range(int(payload["request_count"]))]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=int(payload["concurrency"])) as executor:
        futures = [
            executor.submit(_request_once, url, headers, float(timeout_seconds))
            for url in urls
        ]
        for future in as_completed(futures):
            results.append(future.result())

    http_status_counts = _status_counts(results)
    error_counts = _error_type_counts(results)
    triggered = _triggered_stop_conditions(http_status_counts, error_counts)
    status = "probe_failed_stop_condition_triggered" if triggered else "probe_completed_not_gate_evidence"
    payload.update(
        {
            "status": status,
            "sent_requests": len(results),
            "http_status_counts": http_status_counts,
            "error_type_counts": error_counts,
            "latency_ms": _latency_summary(results),
            "observed_admin_runtime_sections": _observed_sections(results),
            "cleanup_proof_status": "not_applicable_read_only_probe",
            "stop_condition_status": "triggered" if triggered else "passed",
            "triggered_stop_conditions": triggered,
        }
    )
    return payload


def render_capacity_bounded_load_harness_markdown(payload: dict[str, Any]) -> str:
    """Render bounded harness output as operator-readable Markdown."""
    endpoints = "\n".join(
        f"- `{item['method']} {item['path']}`: {item['purpose']}"
        for item in payload.get("endpoints", [])
        if isinstance(item, dict)
    ) or "- none"
    status_counts = payload.get("http_status_counts") or {}
    status_rows = "\n".join(
        f"| {status} | {count} |" for status, count in status_counts.items()
    ) or "| none | 0 |"
    return (
        "# ai-platform Capacity Bounded Load Harness\n\n"
        f"Schema: `{payload['schema_version']}`\n\n"
        f"Status: `{payload['status']}`\n\n"
        f"Gate: `{payload['gate']}`\n\n"
        f"Base URL: `{payload['base_url']}`\n\n"
        f"Requests: `{payload['request_count']}`; concurrency: `{payload['concurrency']}`\n\n"
        "## Safety Policy\n\n"
        "- Default mode is dry-run.\n"
        "- Real probes require explicit operator acknowledgement.\n"
        "- This read-only probe does not mark a capacity gate as recorded.\n"
        "- Do not raise production concurrency defaults from this output.\n\n"
        f"- load_test_evidence_status: `{payload['load_test_evidence_status']}`\n"
        f"- gate_evidence_compatibility: `{payload['gate_evidence_compatibility']}`\n"
        f"- does_not_mark_gate_recorded: `{str(payload['does_not_mark_gate_recorded']).lower()}`\n\n"
        "## Endpoints\n\n"
        f"{endpoints}\n\n"
        "## HTTP Status Counts\n\n"
        "| Status | Count |\n"
        "| --- | --- |\n"
        f"{status_rows}\n"
    )

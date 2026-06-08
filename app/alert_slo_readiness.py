from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.alert-slo-readiness.v1"
GATE_NAME = "G9 Alert / SLO Rule Template"


_RULES: list[dict[str, Any]] = [
    {
        "id": "queue_depth_no_lease_progress",
        "category": "queue",
        "signal": "queue.queued_depth plus queue.lease_success_count",
        "slo_target": "queued work keeps leasing while worker capacity is available",
        "warning_threshold": "queued_depth > 0 and lease_success_count == 0 for 5m",
        "critical_threshold": "queued_depth > 0, worker_capacity_available == true, and lease_success_count == 0 for 10m",
        "evidence_source": "admin runtime overview queue and backpressure projection",
    },
    {
        "id": "database_pool_waiting_pressure",
        "category": "database",
        "signal": "database_pool.waiting and database_pool.max_waiting",
        "slo_target": "database pool wait queue remains bounded during run creation and admin reads",
        "warning_threshold": "waiting > 0 for 5m",
        "critical_threshold": "waiting >= max_waiting * 0.8 for 2m",
        "evidence_source": "admin runtime overview database_pool projection",
    },
    {
        "id": "worker_active_run_saturation",
        "category": "worker",
        "signal": "backpressure.active_worker_runs plus queue.queued_depth",
        "slo_target": "worker saturation is visible before queue latency becomes hidden backlog",
        "warning_threshold": "active_worker_runs >= max_active_worker_runs for 10m and queued_depth > 0",
        "critical_threshold": "active_worker_runs >= max_active_worker_runs for 30m and queued_depth increases",
        "evidence_source": "admin runtime overview capacity, queue, and backpressure projection",
    },
    {
        "id": "model_gateway_timeout_spike",
        "category": "model_gateway",
        "signal": "observability.error_categories.model_gateway",
        "slo_target": "model gateway timeout spikes are separated from executor and queue failures",
        "warning_threshold": "model_gateway errors >= 5 in 10m",
        "critical_threshold": "model_gateway errors >= 20 in 10m or repeated timeout bursts across 3 windows",
        "evidence_source": "admin runtime overview observability error category projection",
    },
    {
        "id": "sandbox_orphan_cleanup_regression",
        "category": "sandbox",
        "signal": "sandbox.cleanup_orphans plus sandbox.provider",
        "slo_target": "sandbox cleanup regressions stay visible while Docker provider remains gated",
        "warning_threshold": "orphan_count > 0 after cleanup",
        "critical_threshold": "orphan_count > 0 for 30m or cleanup job reports failure",
        "evidence_source": "admin runtime overview sandbox projection and cleanup evidence",
    },
    {
        "id": "error_taxonomy_spike",
        "category": "error_taxonomy",
        "signal": "observability.error_categories",
        "slo_target": "stable error categories drive operations before beta rollout",
        "warning_threshold": "any category count doubles versus previous 30m baseline",
        "critical_threshold": "unknown category appears or any category remains elevated for 3 windows",
        "evidence_source": "admin runtime overview observability error category projection",
    },
    {
        "id": "capacity_load_evidence_missing",
        "category": "capacity_gate",
        "signal": "capacity.missing_load_test_gates",
        "slo_target": "production concurrency defaults do not increase without recorded capacity evidence",
        "warning_threshold": "any load-test gate is missing before release candidate",
        "critical_threshold": "operator attempts production concurrency increase while load-test gates are missing",
        "evidence_source": "capacity gate readiness snapshot and admin runtime capacity projection",
    },
]

_EXPECTED_RULE_IDS = tuple(rule["id"] for rule in _RULES)


def _validate_rules(rules: list[dict[str, Any]]) -> None:
    rule_ids = tuple(str(rule.get("id") or "") for rule in rules)
    if rule_ids != _EXPECTED_RULE_IDS:
        raise RuntimeError("alert_slo_rule_template_drift")
    categories = [str(rule.get("category") or "") for rule in rules]
    if len(categories) != len(set(categories)):
        raise RuntimeError("alert_slo_category_drift")


def build_alert_slo_readiness() -> dict[str, Any]:
    """Build a source-level G9 alert/SLO template without enabling alert delivery."""
    rules = [dict(rule) for rule in _RULES]
    _validate_rules(rules)
    categories = [str(rule["category"]) for rule in rules]
    open_gaps = [
        "alert_rules_runtime_dashboard_and_211_acceptance",
        "alert_delivery_channel_policy",
        "slo_threshold_runtime_calibration",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "active_alerting_policy": "template_only_not_enabled",
        "rules": rules,
        "summary": {
            "rule_count": len(rules),
            "categories": categories,
        },
        "open_gaps": open_gaps,
        "evidence_policy": (
            "rule templates narrow the G9 alerts gap only; dashboard wiring, delivery policy, "
            "runtime calibration, review, tests, and 211 smoke remain required before enabling alerts"
        ),
    }

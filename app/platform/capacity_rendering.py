"""Operator-facing rendering for capacity baseline data.

Rendering is kept separate from capacity collection and readiness evaluation so
those paths can evolve without pulling Markdown concerns into the domain logic.
"""

from typing import Any


def render_capacity_baseline_markdown(baseline: dict[str, Any]) -> str:
    """Render a capacity baseline snapshot as operator-readable Markdown."""
    limits = baseline["limits"]
    model_gateway_limit = limits["model_gateway"].get("request_concurrency_limit")
    configured_model_gateway_limit = limits["model_gateway"].get(
        "configured_request_concurrency_limit"
    )
    model_gateway_concurrency = (
        str(model_gateway_limit)
        if model_gateway_limit is not None
        else f"configured={configured_model_gateway_limit}; not enforced; load-test required"
        if configured_model_gateway_limit is not None
        else "unbounded by platform; load-test required"
    )
    rows = [
        ("API request concurrency", "unbounded by platform; load-test required"),
        ("Active worker runs", str(limits["worker"]["max_active_worker_runs"])),
        ("Per-user active runs", str(limits["admission"]["max_active_runs_per_user"])),
        ("DB pool max size", str(limits["database_pool"]["max_size"])),
        ("DB pool max waiting", str(limits["database_pool"]["max_waiting"])),
        ("Tenant queue processing limit", str(limits["queue"]["tenant_processing_limit"])),
        ("User queue processing limit", str(limits["queue"]["user_processing_limit"])),
        ("Queue lease scan limit", str(limits["queue"]["lease_scan_limit"])),
        ("Sandbox provider", str(limits["sandbox"]["container_provider"])),
        (
            "Sandbox active containers",
            (
                f"ephemeral={limits['sandbox']['max_active_ephemeral_containers']}, "
                f"persistent={limits['sandbox']['max_active_persistent_containers']}"
            ),
        ),
        ("Model gateway concurrency", model_gateway_concurrency),
    ]
    table = "\n".join(f"| {name} | {value} |" for name, value in rows)
    gates = "\n".join(f"- {gate}" for gate in baseline["load_test_gates"])
    warnings = "\n".join(f"- {warning}" for warning in baseline["warnings"])
    policy = baseline["model_gateway_backpressure_policy"]
    policy_fields = "\n".join(
        f"- `{field}`" for field in policy["required_admin_runtime_fields"]
    )
    return (
        "# ai-platform Capacity Baseline\n\n"
        f"Schema: `{baseline['schema_version']}`\n\n"
        "| Capacity term | Current configured value |\n"
        "| --- | --- |\n"
        f"{table}\n\n"
        "## Load-Test Gates\n\n"
        f"{gates}\n\n"
        "## Production Default Policy\n\n"
        "Do not raise production concurrency defaults without recorded load-test evidence.\n\n"
        "## Model Gateway Backpressure Policy\n\n"
        f"Schema: `{policy['schema_version']}`\n\n"
        f"Status: `{policy['status']}`\n\n"
        f"Config signal: `{policy['config_signal']}`\n\n"
        f"Default limit policy: `{policy['default_limit_policy']}`\n\n"
        f"Required load-test gate: `{policy['required_load_test_gate']}`\n\n"
        f"Enforcement status: `{policy['enforcement_status']}`\n\n"
        "Required Admin Runtime fields:\n\n"
        f"{policy_fields}\n\n"
        "## Current Warnings\n\n"
        f"{warnings}\n"
    )

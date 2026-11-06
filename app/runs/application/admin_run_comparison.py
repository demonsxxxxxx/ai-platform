"""Explicit two-Run comparison. Historical measurements retain their own scopes."""
from typing import Any

from app.runs.domain.run_performance import build_run_latency


def build_admin_run_comparison(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    configurations = {side: value["inspection"]["configuration"] for side, value in (("left", left), ("right", right))}
    fields = ("model_id", "model_value", "model_gateway_revision", "auto_compact_window", "max_output_tokens", "profile_revision", "profile_hash", "skills")
    differences = [{"field": field, "left": configurations["left"].get(field), "right": configurations["right"].get(field)}
                   for field in fields if configurations["left"].get(field) != configurations["right"].get(field)]
    samples = {}
    for side, value in (("left", left), ("right", right)):
        samples[side] = {**build_run_latency(value["run"]), **value.get("usage", {})}
    same_model = all(configurations["left"].get(field) is not None and configurations["left"].get(field) == configurations["right"].get(field)
                     for field in ("model_id", "model_gateway_revision"))
    metrics = []
    for name in ("total_ms", "queue_ms", "execution_ms", "input_tokens", "output_tokens", "total_tokens", "estimated_cost_minor"):
        a, b = samples["left"].get(name), samples["right"].get(name)
        recorded = type(a) is int and type(b) is int and a >= 0 and b >= 0
        temporal = name.endswith("_ms")
        settled = all(value["run"].get("status") in {"succeeded", "failed", "cancelled"} for value in (left, right))
        compatible = recorded and settled and (temporal or same_model) and name != "estimated_cost_minor"
        reason = None if compatible else "run_not_terminal" if not settled else "missing_measurement" if not recorded else "cost_unit_not_recorded" if name == "estimated_cost_minor" else "model_or_revision_mismatch"
        metrics.append({"name": name, "left": a, "right": b, "compatible": compatible,
                        "difference": b - a if compatible else None, "reason": reason,
                        "measurement": "platform_lifecycle.v1" if temporal else "recorded_run_usage"})
    return {"contract_version": "ai-platform.admin-run-comparison.v1", "left_run_id": left["run"]["run_id"],
            "right_run_id": right["run"]["run_id"], "configuration_differences": differences, "metrics": metrics,
            "artifacts": {"left": left["artifacts"], "right": right["artifacts"]},
            "selection": "explicit", "task_acceptance": "not_recorded",
            "results": {side: {"answer": value.get("public_result", {}).get("response", ""),
                               "answer_projection": value.get("public_result", {}).get("answer_projection", {}),
                               "stop_category": value.get("public_result", {}).get("model", {}).get("stop_category"),
                               "coverage": value.get("public_result", {}).get("coverage", {}),
                               "artifacts_truncated": value.get("artifacts_truncated", False)}
                        for side, value in (("left", left), ("right", right))},
            "context": {side: {"agent_id": value["run"].get("agent_id"), "execution_kind": value["run"].get("execution_kind"), "status": value["run"].get("status"), "input_equivalence": "not_established"} for side, value in (("left", left), ("right", right))}}

"""Execution result metrics projected for Run persistence and public events."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def _int_payload_value(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _int_mapping_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            value = payload.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _usd_cost_to_minor_units(value: Any) -> int:
    try:
        minor_units = (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return max(int(minor_units), 0)


def _sdk_usage_observability(executor_payload: dict[str, Any]) -> dict[str, Any]:
    usage = executor_payload.get("sdk_usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _int_mapping_value(usage, "input_tokens", "input")
    input_tokens += _int_mapping_value(usage, "cache_creation_input_tokens")
    input_tokens += _int_mapping_value(usage, "cache_read_input_tokens")
    output_tokens = _int_mapping_value(usage, "output_tokens", "output")
    total_tokens = _int_mapping_value(usage, "total_tokens", "total")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    estimated_cost_minor = _int_mapping_value(usage, "estimated_cost_minor", "cost_minor")
    if estimated_cost_minor <= 0:
        estimated_cost_minor = _usd_cost_to_minor_units(
            usage.get("total_cost_usd") or usage.get("cost_usd") or usage.get("estimated_cost_usd")
        )
    return {
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {"estimated_cost_minor": estimated_cost_minor},
    }


def _has_sdk_observability(executor_payload: dict[str, Any]) -> bool:
    sdk_observability = _sdk_usage_observability(executor_payload)
    token_counts = sdk_observability["token_counts"]
    return (
        token_counts["input"] > 0
        or token_counts["output"] > 0
        or token_counts["total"] > 0
        or sdk_observability["cost"]["estimated_cost_minor"] > 0
    )


def executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int,
) -> dict[str, Any]:
    sdk_observability = _sdk_usage_observability(executor_payload)
    sdk_token_counts = sdk_observability["token_counts"]
    input_tokens = _int_payload_value(executor_payload, "input_token_count", sdk_token_counts["input"])
    output_tokens = _int_payload_value(executor_payload, "output_token_count", sdk_token_counts["output"])
    total_default = sdk_token_counts["total"] or (input_tokens + output_tokens)
    total_tokens = _int_payload_value(executor_payload, "total_token_count", total_default)
    return {
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {
            "estimated_cost_minor": _int_payload_value(
                executor_payload,
                "estimated_cost_minor",
                sdk_observability["cost"]["estimated_cost_minor"],
            ),
        },
    }


def event_observability_kwargs(observability: dict[str, Any], executor_payload: dict[str, Any]) -> dict[str, Any]:
    metric_keys = {
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_minor",
    }
    if not any(key in executor_payload for key in metric_keys) and not _has_sdk_observability(
        executor_payload
    ):
        return {}
    token_counts = observability["token_counts"]
    return {
        "latency_ms": observability["latency_ms"],
        "input_token_count": token_counts["input"],
        "output_token_count": token_counts["output"],
        "total_token_count": token_counts["total"],
        "estimated_cost_minor": observability["cost"]["estimated_cost_minor"],
    }

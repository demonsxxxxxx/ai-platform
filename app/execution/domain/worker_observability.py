"""Execution result metrics projected for Run persistence and public events."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any

from app.execution.domain.token_metrics import mapped_token_count, sum_token_counts, token_count


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


def _usd_cost_to_minor_units(value: Any, *, unknown: int | None = 0) -> int | None:
    if isinstance(value, bool):
        return unknown
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= Decimal("21474836.48"):
            return unknown
        minor_units = (amount * Decimal(100)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )
        return int(minor_units) if minor_units <= 2**31 - 1 else unknown
    except (DecimalException, TypeError, ValueError, OverflowError):
        return unknown


def _sdk_usage_observability(executor_payload: dict[str, Any]) -> dict[str, Any]:
    usage = executor_payload.get("sdk_usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = sum_token_counts(
        mapped_token_count(usage, "input_tokens", "input"),
        mapped_token_count(usage, "cache_creation_input_tokens"),
        mapped_token_count(usage, "cache_read_input_tokens"),
    )
    output_tokens = mapped_token_count(usage, "output_tokens", "output")
    total_tokens = mapped_token_count(usage, "total_tokens", "total")
    if total_tokens == 0:
        total_tokens = sum_token_counts(input_tokens, output_tokens)
    coverage = usage.get("cost_coverage")
    # New producers explicitly distinguish incomplete cumulative observations.
    # Historical carriers without this marker retain their existing reader.
    estimated_cost_minor = None if coverage is not None else _int_mapping_value(usage, "estimated_cost_minor", "cost_minor")
    if coverage == "complete":
        estimated_cost_minor = _usd_cost_to_minor_units(usage.get("total_cost_usd"), unknown=None)
    elif coverage is None and estimated_cost_minor <= 0:
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
        **({"runtime_private_payload": {"sdk_usage": {"cost_coverage": coverage}}}
           if coverage in ("complete", "partial", "unavailable") else {}),
    }


def _has_sdk_observability(executor_payload: dict[str, Any]) -> bool:
    sdk_observability = _sdk_usage_observability(executor_payload)
    token_counts = sdk_observability["token_counts"]
    return (
        (token_counts["input"] or 0) > 0
        or (token_counts["output"] or 0) > 0
        or (token_counts["total"] or 0) > 0
        or (sdk_observability["cost"]["estimated_cost_minor"] or 0) > 0
    )


def run_elapsed_ms(started_at: object, *, observed_at: datetime) -> int | None:
    """Measure persisted Run elapsed time; invalid clocks remain unknown."""
    if (
        not isinstance(started_at, datetime)
        or started_at.utcoffset() is None
        or observed_at.utcoffset() is None
        or started_at > observed_at
    ):
        return None
    return int((observed_at - started_at).total_seconds() * 1000)


def executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int | None,
) -> dict[str, Any]:
    sdk_observability = _sdk_usage_observability(executor_payload)
    sdk_token_counts = sdk_observability["token_counts"]
    def projected_count(key: str, default: int | None) -> int | None:
        value = executor_payload.get(key)
        # Preserve historical missing/zero fallback, but reject bool explicitly.
        if value is None or (not isinstance(value, bool) and value == 0):
            return default
        return token_count(value)

    input_tokens = projected_count("input_token_count", sdk_token_counts["input"])
    output_tokens = projected_count("output_token_count", sdk_token_counts["output"])
    total_default = sdk_token_counts["total"]
    if total_default == 0:
        total_default = sum_token_counts(input_tokens, output_tokens)
    total_tokens = projected_count("total_token_count", total_default)
    if None in (input_tokens, output_tokens, total_tokens):
        input_tokens = output_tokens = total_tokens = 0
    return {
        **({"runtime_private_payload": sdk_observability["runtime_private_payload"]}
           if "runtime_private_payload" in sdk_observability else {}),
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens or 0,
            "output": output_tokens or 0,
            "total": total_tokens or 0,
        },
        "cost": {
            "estimated_cost_minor": sdk_observability["cost"]["estimated_cost_minor"]
            if "runtime_private_payload" in sdk_observability else _int_payload_value(
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

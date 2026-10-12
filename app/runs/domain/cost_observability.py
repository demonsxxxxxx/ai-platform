"""Read newly marked Run cost observations while preserving legacy column readers."""

from typing import Any


def _coverage(result: object) -> object:
    private = result.get("runtime_private_payload") if isinstance(result, dict) else None
    usage = private.get("sdk_usage") if isinstance(private, dict) else None
    return usage.get("cost_coverage") if isinstance(usage, dict) else None


def private_cost_observation(result: object) -> dict[str, Any]:
    """Copy only the platform's bounded coverage enum, never a private payload."""
    coverage = _coverage(result)
    return ({"runtime_private_payload": {"sdk_usage": {"cost_coverage": coverage}}}
            if coverage in ("complete", "partial", "unavailable") else {})


def projected_estimated_cost_minor(value: Any, result: object) -> Any:
    """Only the new private marker changes authority; legacy values are untouched."""
    coverage = _coverage(result)
    if coverage not in ("complete", "partial", "unavailable"):
        return value
    cost = result.get("cost") if isinstance(result, dict) else None
    amount = cost.get("estimated_cost_minor") if isinstance(cost, dict) else None
    if coverage == "complete" and type(amount) is int and 0 <= amount <= 2**31 - 1:
        return amount
    return None


def projected_event_cost(row: dict[str, Any], *, ordinary: bool = False) -> dict[str, Any]:
    payload = row.get("payload_json")
    payload = payload if isinstance(payload, dict) else {}
    source = payload if _coverage(payload) is not None else payload.get("result")
    amount = projected_estimated_cost_minor(row.get("estimated_cost_minor"), source)
    if _coverage(source) in ("complete", "partial", "unavailable") and amount is None:
        return {}
    if ordinary:
        # Preserve the ordinary-user boundary: never coerce bool/string values.
        amount = min(max(amount, 0), 100_000_000) if type(amount) is int else 0
    else:
        amount = int(amount or 0)
    return {"cost": {"estimated_cost_minor": amount}}

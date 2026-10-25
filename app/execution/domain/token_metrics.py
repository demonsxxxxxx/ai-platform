"""Non-authoritative runtime token counters accepted by durable Run metrics.

Unknown observations retain the existing zero-default projection. Internally None
keeps an invalid component from becoming a plausible partial aggregate.
"""

from typing import Any

MAX_TOKEN_COUNT = 2**31 - 1


def token_count(value: Any) -> int | None:
    """Preserve legacy int coercion for valid counters, never clamp telemetry."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, float) and not 0 <= value <= MAX_TOKEN_COUNT:
            return None
        count = int(value)
        return count if 0 <= count <= MAX_TOKEN_COUNT else None
    except (TypeError, ValueError, OverflowError):
        return None


def sum_token_counts(*values: int | None) -> int | None:
    if any(value is None for value in values):
        return None
    return token_count(sum(values))


def mapped_token_count(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return token_count(mapping[key])
    return 0

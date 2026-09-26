"""Stable trace identifiers shared by application contexts."""

from uuid import uuid4


def standard_trace_id(seed: str | None = None) -> str:
    if seed:
        normalized = seed.replace("run_", "", 1).replace("-", "_")
        return f"trace_{normalized}"
    return f"trace_{uuid4().hex}"

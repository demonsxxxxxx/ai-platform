"""Mint observation identity only at producer capture, never during validation."""

from uuid import uuid4

from app.sandbox.domain.runtime_diagnostics import (
    _fit_runtime_diagnostics,
    normalize_sdk_runtime_diagnostics,
)


def capture_sdk_runtime_diagnostics(value: object) -> dict:
    payload = normalize_sdk_runtime_diagnostics(value)
    if not payload:
        return payload
    parent = None
    for observation in payload["failure_observations"]:
        if "observation_id" not in observation:
            observation["observation_id"] = f"obs_{uuid4().hex}"
            if parent is not None:
                observation["parent_observation_id"] = parent
        parent = observation["observation_id"]
    return _fit_runtime_diagnostics(payload)

from __future__ import annotations

import uuid


def sdk_session_id_for_run(run_id: str) -> str:
    """Return the public per-Run surrogate; never expose the provider session ID."""
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id_required_for_sdk_session")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-platform-sdk-run:{normalized_run_id}"))


__all__ = ["sdk_session_id_for_run"]

from __future__ import annotations

import uuid


def sdk_session_id_for_run(run_id: str) -> str:
    """Derive a deterministic SDK session ID for one immutable platform run.

    Context continuity is reconstructed from the database-backed context pack.
    This identifier deliberately scopes an SDK invocation to its run and holds
    no process-local transcript or lock state.
    """

    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id_required_for_sdk_session")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-platform-sdk-run:{normalized_run_id}"))

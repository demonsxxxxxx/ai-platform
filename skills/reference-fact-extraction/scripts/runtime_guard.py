from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


ENV_NAME = "REFERENCE_FACT_EXTRACTION_INTERNAL_CONTEXT"
SCHEMA_VERSION = "reference-fact-extraction-internal-context-v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_context(value: str | None = None) -> dict[str, Any] | None:
    raw = os.environ.get(ENV_NAME) if value is None else value
    if not raw:
        return None
    try:
        context = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(context, dict):
        return None
    if context.get("schema_version") != SCHEMA_VERSION:
        return None
    return context


def require_internal_context(script_name: str) -> dict[str, Any]:
    context = _load_context()
    if context is not None:
        return context

    payload = {
        "status": "rejected_direct_internal_script_call",
        "script": script_name,
        "required_entrypoint": "scripts/skill_step.py",
        "message": "请通过 scripts/skill_step.py 进入；该脚本是 skill 内部实现，不能直接调用。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(64)


def make_internal_env(caller: str, reason: str) -> dict[str, str]:
    env = os.environ.copy()
    parent = _load_context()
    trace = []
    if parent and isinstance(parent.get("trace"), list):
        trace = list(parent["trace"])
    trace.append({"caller": caller, "reason": reason, "issued_at": now_iso()})
    env[ENV_NAME] = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "issuer": caller,
            "reason": reason,
            "issued_at": now_iso(),
            "trace": trace,
        },
        ensure_ascii=False,
    )
    return env

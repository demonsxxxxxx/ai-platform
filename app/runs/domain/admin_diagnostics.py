"""Administrator-only Run diagnostic projection rules."""

from typing import Any


def admin_runtime_diagnostics_from_run(run: object) -> dict[str, Any]:
    if not isinstance(run, dict) or run.get("status") != "failed":
        return {}
    result = run.get("result_json")
    diagnostics = result.get("runtime_diagnostics") if isinstance(result, dict) else None
    return diagnostics if isinstance(diagnostics, dict) else {}

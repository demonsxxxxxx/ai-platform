from __future__ import annotations

import argparse
import json

from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "ai-platform.trace-audit-export-readiness.v1"
EXPORT_CONTRACT_SCHEMA_VERSION = "ai-platform.trace-audit-export-contract.v1"
GATE_NAME = "G9 Trace / Audit Export"

_REQUIRED_FIELDS = [
    "export_id",
    "commit_sha",
    "tenant_id",
    "requested_by",
    "requested_at",
    "time_range",
    "filters",
    "artifact_refs_public",
    "redaction_scan_status",
    "review_status",
]
_ALLOWED_EVENT_SOURCES = [
    "run_event_public_projection",
    "audit_event_public_projection",
    "admin_runtime_observability_summary",
    "release_evidence_entry",
]
_FORBIDDEN_MARKER_CLASSES = [
    "executor private payload",
    "raw storage key",
    "sandbox workdir",
    "secret material",
    "API key",
    "bearer token",
    "database URL",
    "Redis URL",
]
_EXPORT_CONTRACT = {
    "schema_version": EXPORT_CONTRACT_SCHEMA_VERSION,
    "write_path": "audit.trace_exports.<export_id>",
    "required_fields": _REQUIRED_FIELDS,
    "allowed_event_sources": _ALLOWED_EVENT_SOURCES,
    "accepted_redaction_scan_statuses": ["passed"],
    "accepted_review_statuses": ["reviewed", "accepted"],
    "forbidden_marker_classes": _FORBIDDEN_MARKER_CLASSES,
    "does_not_export_raw_runtime_payloads": True,
    "does_not_close_g9": True,
}
_OPEN_GAPS = [
    "trace_audit_export_runtime_acceptance",
    "trace_audit_export_dashboard_acceptance",
    "trace_audit_export_controlled_host_acceptance",
]


def build_trace_audit_export_readiness() -> dict[str, Any]:
    """Build the source-level G9 trace/audit export contract without exporting runtime data."""
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "active_export_policy": "contract_only_not_runtime_export",
        "export_contract": deepcopy(_EXPORT_CONTRACT),
        "open_gaps": list(_OPEN_GAPS),
        "does_not_close_g9": True,
    }


def render_trace_audit_export_readiness_markdown(readiness: dict[str, object]) -> str:
    """Render the trace/audit export contract as operator-readable Markdown."""
    contract = readiness["export_contract"] if isinstance(readiness.get("export_contract"), dict) else {}
    required_fields = contract.get("required_fields") if isinstance(contract, dict) else []
    event_sources = contract.get("allowed_event_sources") if isinstance(contract, dict) else []
    gaps = readiness.get("open_gaps")
    field_lines = "\n".join(f"- `{field}`" for field in required_fields if isinstance(field, str))
    source_lines = "\n".join(f"- `{source}`" for source in event_sources if isinstance(source, str))
    gap_lines = "\n".join(f"- `{gap}`" for gap in gaps if isinstance(gap, str)) if isinstance(gaps, list) else ""
    return (
        "# ai-platform G9 Trace / Audit Export Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Active export policy: `{readiness['active_export_policy']}`\n\n"
        f"Export contract: `{contract.get('schema_version')}`\n\n"
        f"Write path: `{contract.get('write_path')}`\n\n"
        "Required fields:\n\n"
        f"{field_lines}\n\n"
        "Allowed event sources:\n\n"
        f"{source_lines}\n\n"
        "Open gaps:\n\n"
        f"{gap_lines}\n\n"
        "This contract does not close G9 and does not export raw runtime payloads.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform G9 trace/audit export contract.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_trace_audit_export_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_trace_audit_export_readiness_markdown(readiness))


if __name__ == "__main__":
    main()

from __future__ import annotations

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
    "trace_audit_export_211_acceptance",
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

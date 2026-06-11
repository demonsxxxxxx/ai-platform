from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance


SCHEMA_VERSION = "ai-platform.release-evidence-readiness.v1"
ENTRY_SCHEMA_VERSION = "ai-platform.release-evidence-entry.v1"
RETENTION_POLICY_SCHEMA_VERSION = "ai-platform.release-evidence-retention-policy.v1"
GATE_NAME = "G9 Release Evidence Export"

_EXPORT_LOCATION = {
    "type": "repository_path",
    "path": "docs/release-evidence/",
    "index": "docs/release-evidence/README.md",
    "write_policy": "append_reviewed_redacted_evidence_entries_only",
}
_REQUIRED_FIELDS = [
    "evidence_id",
    "commit_sha",
    "gate",
    "issue_refs",
    "artifact_kind",
    "captured_at",
    "source_ref",
    "evidence_ref",
    "redaction_scan_status",
    "review_status",
]
_FIELD_SEMANTICS = {
    "commit_sha": "verified subject commit for the runtime, capacity, frontend, or governance artifact under review",
    "runtime_subject_commit_sha": (
        "runtime source revision proven by 211 source marker and API/worker image labels "
        "when artifact_kind is 211_runtime_smoke"
    ),
    "record_commit_sha": (
        "not embedded because a git commit cannot contain its own final hash; "
        "use VCS history to identify the commit that introduced or updated an evidence record"
    ),
}
_CONDITIONAL_FIELDS = {
    "211_runtime_smoke": [
        "runtime_subject_commit_sha",
    ],
}
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
_ACCEPTED_ARTIFACT_KINDS = [
    "211_runtime_smoke",
    "capacity_gate_readiness",
    "frontend_packaged_runtime_smoke",
    "frontend_release_traceability",
    "governance_readiness",
    "observability_readiness",
]
_RETENTION_POLICY = {
    "schema_version": RETENTION_POLICY_SCHEMA_VERSION,
    "status": "contract_only_not_runtime_enforced",
    "default_retention_days": 180,
    "minimum_retention_days": 30,
    "requires_review_before_delete": True,
    "delete_only_reviewed_redacted_entries": True,
    "forbidden_delete_targets": [
        "raw runtime payload",
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "unreviewed evidence draft",
    ],
    "does_not_close_g9": True,
}
_OPEN_GAPS = [
    "release_evidence_runtime_export_acceptance",
    "release_evidence_retention_runtime_acceptance",
]


def build_release_evidence_readiness() -> dict[str, Any]:
    """Build the source-level G9 release evidence export-location contract."""
    export_acceptance = build_release_evidence_export_acceptance()
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "active_export_policy": "location_contract_only_not_runtime_export",
        "export_location": deepcopy(_EXPORT_LOCATION),
        "evidence_contract": {
            "schema_version": ENTRY_SCHEMA_VERSION,
            "write_path": "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json",
            "required_fields": list(_REQUIRED_FIELDS),
            "field_semantics": deepcopy(_FIELD_SEMANTICS),
            "conditional_fields": deepcopy(_CONDITIONAL_FIELDS),
            "accepted_artifact_kinds": list(_ACCEPTED_ARTIFACT_KINDS),
            "accepted_redaction_scan_statuses": ["passed"],
            "accepted_review_statuses": ["reviewed", "accepted"],
            "forbidden_marker_classes": list(_FORBIDDEN_MARKER_CLASSES),
            "does_not_export_raw_runtime_payloads": True,
            "does_not_close_g9": True,
        },
        "export_acceptance": export_acceptance,
        "retention_policy": deepcopy(_RETENTION_POLICY),
        "open_gaps": list(_OPEN_GAPS),
        "does_not_close_g9": True,
    }

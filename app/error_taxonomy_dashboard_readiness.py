from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.error_taxonomy import ERROR_CATEGORY_DEFINITIONS


SCHEMA_VERSION = "ai-platform.error-taxonomy-dashboard-readiness.v1"
DASHBOARD_CONTRACT_SCHEMA_VERSION = "ai-platform.error-taxonomy-dashboard-contract.v1"
GATE_NAME = "G9 Error Taxonomy Dashboard"

_REQUIRED_ADMIN_RUNTIME_FIELDS = [
    "observability.error_categories",
    "observability.error_types",
    "observability.recent_failures",
    "observability_readiness.error_taxonomy",
]
_ALLOWED_DISPLAY_FIELDS = [
    "category",
    "count",
    "definition",
    "trend_window",
    "recent_failure_refs_public",
    "last_seen_at",
]
_FORBIDDEN_PAYLOAD_CLASSES = [
    "executor private payload",
    "raw storage key",
    "sandbox workdir",
    "secret material",
    "API key",
    "bearer token",
    "database URL",
    "Redis URL",
]
_DASHBOARD_CONTRACT = {
    "schema_version": DASHBOARD_CONTRACT_SCHEMA_VERSION,
    "required_admin_runtime_fields": _REQUIRED_ADMIN_RUNTIME_FIELDS,
    "required_category_ids": list(ERROR_CATEGORY_DEFINITIONS),
    "allowed_display_fields": _ALLOWED_DISPLAY_FIELDS,
    "unknown_category_policy": "unknown_category_visible_but_raw_payload_hidden",
    "same_tenant_admin_only": True,
    "forbidden_payload_classes": _FORBIDDEN_PAYLOAD_CLASSES,
    "does_not_close_g9": True,
}
_OPEN_GAPS = [
    "error_taxonomy_dashboard_runtime_acceptance",
    "error_taxonomy_dashboard_visual_acceptance",
    "error_taxonomy_dashboard_211_acceptance",
]


def build_error_taxonomy_dashboard_readiness() -> dict[str, Any]:
    """Build the source-level G9 error taxonomy dashboard contract without enabling dashboard acceptance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "active_dashboard_policy": "contract_only_not_runtime_dashboard_acceptance",
        "dashboard_contract": deepcopy(_DASHBOARD_CONTRACT),
        "open_gaps": list(_OPEN_GAPS),
        "does_not_close_g9": True,
    }

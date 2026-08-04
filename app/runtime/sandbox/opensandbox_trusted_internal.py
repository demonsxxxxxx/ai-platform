"""Deprecated import seam for historical OpenSandbox lease cleanup only.

The executable trusted-internal profile has been retired. New code must import
governed policy from :mod:`opensandbox_policy`; these exports exist only until
pre-governance persisted leases have been terminalized and their callers are
migrated to :mod:`opensandbox_legacy_cleanup`.
"""

from app.runtime.sandbox.opensandbox_legacy_cleanup import (
    SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
    trusted_internal_cleanup_identity_is_authorized,
    trusted_internal_cleanup_labels_from_persisted_row,
    trusted_internal_orphan_cleanup_identity_is_authorized,
    trusted_internal_orphan_cleanup_metadata_filter,
    trusted_internal_runtime_lease_payload_matches_row,
)
from app.runtime.sandbox.opensandbox_policy import SANDBOX_SECURITY_PROFILE_GOVERNED

__all__ = [
    "SANDBOX_SECURITY_PROFILE_GOVERNED",
    "SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL",
    "trusted_internal_cleanup_identity_is_authorized",
    "trusted_internal_cleanup_labels_from_persisted_row",
    "trusted_internal_orphan_cleanup_identity_is_authorized",
    "trusted_internal_orphan_cleanup_metadata_filter",
    "trusted_internal_runtime_lease_payload_matches_row",
]

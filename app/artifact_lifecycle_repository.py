"""Deprecated import facade for the split persistence boundaries.

Canonical ownership lives under :mod:`app.persistence`. This module contains no
SQL or lifecycle decisions and may be removed after the documented import
compatibility window.
"""

from app.persistence.artifacts import (
    get_admin_artifact,
    get_artifact,
    get_authorized_artifact,
    list_revealed_artifact_sessions,
    list_revealed_artifacts,
    queue_expired_artifacts_for_deletion,
)
from app.persistence.file_deletions import (
    FILE_DELETE_PENDING_STATES,
    FILE_DELETE_PUBLIC_STATES,
    FileDeletionBlockedError,
    queue_unbound_file_for_deletion,
)
from app.persistence.object_deletions import (
    OUTBOX_TARGET_ARTIFACT,
    OUTBOX_TARGET_FILE,
    ObjectDeletionStateError,
    claim_object_deletions,
    complete_object_deletion,
    fail_object_deletion,
    requeue_dead_letter_object_deletion,
)
from app.persistence.retention import (
    get_data_retention_backlog,
    purge_deleted_memory_records,
)


__all__ = [
    "FILE_DELETE_PENDING_STATES",
    "FILE_DELETE_PUBLIC_STATES",
    "OUTBOX_TARGET_ARTIFACT",
    "OUTBOX_TARGET_FILE",
    "FileDeletionBlockedError",
    "ObjectDeletionStateError",
    "claim_object_deletions",
    "complete_object_deletion",
    "fail_object_deletion",
    "get_admin_artifact",
    "get_artifact",
    "get_authorized_artifact",
    "get_data_retention_backlog",
    "list_revealed_artifact_sessions",
    "list_revealed_artifacts",
    "purge_deleted_memory_records",
    "queue_expired_artifacts_for_deletion",
    "queue_unbound_file_for_deletion",
    "requeue_dead_letter_object_deletion",
]

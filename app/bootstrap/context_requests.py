"""Assemble Context HTTP contracts for the legacy router registration.

Keep protocol schemas outside the Context in-process application API.
"""

from app.context.transport.requests import (
    ContextSnapshotRequest as ContextSnapshotRequest,
    ShareContextSnapshotRequest as ShareContextSnapshotRequest,
    MemoryRecordRequest as MemoryRecordRequest,
    MemoryPolicyRequest as MemoryPolicyRequest,
    MemoryRedactionPreviewRequest as MemoryRedactionPreviewRequest,
)

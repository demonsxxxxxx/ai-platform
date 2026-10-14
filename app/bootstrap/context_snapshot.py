"""Bind Context snapshot recording to the existing persistence adapters."""

from app.context.api import ContextSnapshotRecorder
from app.context.infrastructure import snapshot_postgres
from app.context_manifest import public_context_manifest_projection
from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION
from app.streaming.infrastructure import run_events_postgres


def build_context_snapshot_recorder() -> ContextSnapshotRecorder:
    return ContextSnapshotRecorder(
        create_snapshot=snapshot_postgres.create_context_snapshot,
        bind_run_snapshot=snapshot_postgres.update_run_context_snapshot_ref,
        append_event=run_events_postgres.append_event,
        public_manifest=public_context_manifest_projection,
        schema_version=CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    )

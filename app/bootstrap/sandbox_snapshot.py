"""Compose the Sandbox snapshot adapter with the shared bounded I/O executor."""

from functools import partial

from app.platform.storage_io_completion import StorageIOCompletion
from app.sandbox.infrastructure.host_bind_snapshot import snapshot_with_physical_owner
from app.storage import run_storage_io


collect_host_bind_snapshot = partial(
    snapshot_with_physical_owner, storage_io=run_storage_io,
    completion_factory=StorageIOCompletion,
)

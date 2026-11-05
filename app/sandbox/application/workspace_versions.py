"""Private workspace persistence ports; composition supplies host-bind I/O."""
from collections.abc import Callable, Mapping
from typing import Any


def save_workspace_version(*, request: Any, workspace_lease: Any, stop_evidence: Mapping,
                           storage: Callable[..., dict], limits: Mapping | None = None) -> dict:
    """Storage runs under the caller's exact Attempt/generation physical owner."""
    return storage(request=request, workspace_lease=workspace_lease,
                   stop_evidence=stop_evidence, limits=limits)


def verify_workspace_version(*, receipt: Mapping, request: Any, storage: Callable[..., dict]) -> dict:
    """Verify the saved manifest and complete inventory without changing user files."""
    return storage(receipt=receipt, request=request)


def restore_workspace_version(*, receipt: Mapping, request: Any, target_workspace_lease: Any,
                              storage: Callable[..., dict], authorized_input_paths=(),
                              authorized_skill_names=()) -> dict:
    """Storage restores before current inputs, configuration and Skill staging."""
    return storage(receipt=receipt, request=request, target_workspace_lease=target_workspace_lease,
                   authorized_input_paths=authorized_input_paths,
                   authorized_skill_names=authorized_skill_names)


def restore_workspace_skill_outputs(*, receipt: Mapping, restore_receipt: Mapping, request: Any,
                                    target_workspace_lease: Any, staging_evidence: Mapping,
                                    storage: Callable[..., dict], authorized_skill_names=()) -> dict:
    """Publish deferred output after trusted Skill staging, before execution."""
    return storage(receipt=receipt, restore_receipt=restore_receipt, request=request,
                   target_workspace_lease=target_workspace_lease, staging_evidence=staging_evidence,
                   authorized_skill_names=authorized_skill_names)

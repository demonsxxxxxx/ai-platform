import inspect

from app import artifact_lifecycle_repository as legacy_artifact_lifecycle
from app import repositories
from app.persistence import artifacts, file_deletions, object_deletions, retention


def test_repository_facade_binds_each_lifecycle_operation_to_one_canonical_module():
    canonical = {
        "claim_object_deletions": object_deletions.claim_object_deletions,
        "complete_object_deletion": object_deletions.complete_object_deletion,
        "fail_object_deletion": object_deletions.fail_object_deletion,
        "requeue_dead_letter_object_deletion": (
            object_deletions.requeue_dead_letter_object_deletion
        ),
        "queue_unbound_file_for_deletion": file_deletions.queue_unbound_file_for_deletion,
        "queue_expired_artifacts_for_deletion": (
            artifacts.queue_expired_artifacts_for_deletion
        ),
        "get_artifact": artifacts.get_artifact,
        "get_authorized_artifact": artifacts.get_authorized_artifact,
        "get_admin_artifact": artifacts.get_admin_artifact,
        "list_revealed_artifacts": artifacts.list_revealed_artifacts,
        "list_revealed_artifact_sessions": artifacts.list_revealed_artifact_sessions,
        "purge_deleted_memory_records": retention.purge_deleted_memory_records,
        "get_data_retention_backlog": retention.get_data_retention_backlog,
    }

    for name, implementation in canonical.items():
        assert getattr(repositories, name) is implementation
        assert getattr(legacy_artifact_lifecycle, name) is implementation


def test_legacy_artifact_lifecycle_module_contains_no_repository_implementation():
    locally_defined_functions = [
        name
        for name, value in vars(legacy_artifact_lifecycle).items()
        if inspect.isfunction(value)
        and value.__module__ == legacy_artifact_lifecycle.__name__
    ]

    assert locally_defined_functions == []
    assert (
        repositories.FileDeletionBlockedError is file_deletions.FileDeletionBlockedError
    )
    assert (
        repositories.ObjectDeletionStateError
        is object_deletions.ObjectDeletionStateError
    )

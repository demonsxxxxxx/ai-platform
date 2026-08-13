import inspect

from app import artifact_lifecycle_repository as legacy_artifact_lifecycle
from app import agent_conversation_repository as legacy_agent_conversations
from app import persistence_limits as legacy_persistence_limits
from app import repositories
from app.conversations.infrastructure import postgres as conversation_persistence
from app.persistence import (
    RepositoryNotFoundError as legacy_repository_not_found,
    artifacts,
    file_deletions,
    object_deletions,
    retention,
)
from app.platform.postgres import limits as postgres_limits
from app.platform.postgres.errors import RepositoryNotFoundError
from app.runs.infrastructure import postgres as run_persistence


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


def test_conversation_repository_facades_bind_to_one_canonical_adapter():
    repository_symbols = (
        "append_message",
        "create_session",
        "ensure_workspace_belongs_to_tenant",
        "get_authorized_lambchat_session",
        "get_authorized_session_projection",
        "get_session_for_action",
        "list_authorized_messages",
        "list_authorized_sessions",
        "list_authorized_user_messages_for_runs",
        "list_session_messages_for_fork",
        "mark_session_deleted",
        "update_session_title",
    )

    for name in repository_symbols:
        assert getattr(repositories, name) is getattr(conversation_persistence, name)
    assert (
        legacy_agent_conversations.list_authorized_agent_conversations
        is conversation_persistence.list_authorized_agent_conversations
    )
    assert legacy_repository_not_found is RepositoryNotFoundError
    assert repositories.RepositoryNotFoundError is RepositoryNotFoundError


def test_legacy_agent_conversation_module_contains_no_repository_implementation():
    locally_defined_functions = [
        name
        for name, value in vars(legacy_agent_conversations).items()
        if inspect.isfunction(value)
        and value.__module__ == legacy_agent_conversations.__name__
    ]

    assert locally_defined_functions == []


def test_persistence_limit_facade_binds_each_symbol_to_one_canonical_module():
    symbols = (
        "ARTIFACT_MANIFEST_MAX_BYTES",
        "AUDIT_PAYLOAD_MAX_BYTES",
        "CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES",
        "MESSAGE_CONTENT_MAX_BYTES",
        "MESSAGE_METADATA_MAX_BYTES",
        "PersistenceSizeLimitError",
        "RUN_EVENT_MESSAGE_MAX_BYTES",
        "RUN_EVENT_PAYLOAD_MAX_BYTES",
        "RUN_INPUT_MAX_BYTES",
        "RUN_RESULT_MAX_BYTES",
        "RUN_STEP_PAYLOAD_MAX_BYTES",
        "compact_json_dumps",
        "ensure_json_size",
        "ensure_text_size",
        "json_size_bytes",
    )

    for name in symbols:
        assert getattr(legacy_persistence_limits, name) is getattr(postgres_limits, name)


def test_run_repository_facade_binds_each_primitive_to_one_canonical_adapter():
    symbols = (
        "_stage_run_tool_permission_terminalization",
        "acquire_user_active_run_admission_lock",
        "count_active_runs_for_user",
        "enforce_user_active_run_admission",
        "enforce_user_active_run_admission_under_lock",
        "get_active_resume_for_source_run",
        "get_active_retry_for_source_run",
        "get_run",
        "get_run_identity",
    )

    for name in symbols:
        assert getattr(repositories, name) is getattr(run_persistence, name)

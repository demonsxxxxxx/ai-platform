import pytest

from app import repositories, run_event_repository
from app.persistence_limits import (
    ARTIFACT_MANIFEST_MAX_BYTES,
    AUDIT_PAYLOAD_MAX_BYTES,
    CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES,
    MESSAGE_CONTENT_MAX_BYTES,
    RUN_EVENT_MESSAGE_MAX_BYTES,
    RUN_EVENT_PAYLOAD_MAX_BYTES,
    RUN_INPUT_MAX_BYTES,
    RUN_RESULT_MAX_BYTES,
    PersistenceSizeLimitError,
    compact_json_dumps,
    ensure_json_size,
    ensure_text_size,
)


class NoDatabaseWrites:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("oversized values must fail before database access")


def oversized_json(limit):
    return {"value": "x" * (limit + 1)}


def test_text_limits_count_utf8_bytes_not_python_characters():
    ensure_text_size("药" * 2, max_bytes=6, code="too_large")
    with pytest.raises(PersistenceSizeLimitError, match="too_large"):
        ensure_text_size("药" * 3, max_bytes=8, code="too_large")


def test_json_limits_use_deterministic_compact_utf8_and_safe_invalid_errors():
    payload = {"emoji": "🚀", "cjk": "药"}
    serialized = compact_json_dumps(payload)
    exact_size = len(serialized.encode("utf-8"))

    assert serialized == '{"cjk":"药","emoji":"🚀"}'
    ensure_json_size(payload, max_bytes=exact_size, code="payload_too_large")
    with pytest.raises(PersistenceSizeLimitError, match="payload_too_large"):
        ensure_json_size(payload, max_bytes=exact_size - 1, code="payload_too_large")
    with pytest.raises(PersistenceSizeLimitError, match="payload_invalid"):
        ensure_json_size({"value": float("nan")}, max_bytes=100, code="payload_too_large")
    with pytest.raises(PersistenceSizeLimitError, match="payload_invalid"):
        ensure_json_size({"value": "\ud800"}, max_bytes=100, code="payload_too_large")


@pytest.mark.asyncio
async def test_run_input_and_result_reject_oversize_before_database_access():
    conn = NoDatabaseWrites()
    with pytest.raises(repositories.RepositoryConflictError, match="run_input_too_large"):
        await repositories.create_run(
            conn,
            tenant_id="default",
            workspace_id="default",
            session_id="session-a",
            user_id="user-a",
            agent_id="general-agent",
            skill_id="general-chat",
            input_json=oversized_json(RUN_INPUT_MAX_BYTES),
        )
    with pytest.raises(repositories.RepositoryConflictError, match="run_result_too_large"):
        await repositories.complete_run(
            conn,
            tenant_id="default",
            run_id="run-a",
            result_json=oversized_json(RUN_RESULT_MAX_BYTES),
        )


@pytest.mark.asyncio
async def test_message_manifest_audit_and_snapshot_have_safe_stable_errors():
    conn = NoDatabaseWrites()
    with pytest.raises(repositories.RepositoryConflictError, match="message_content_too_large"):
        await repositories.append_message(
            conn,
            tenant_id="default",
            session_id="session-a",
            run_id=None,
            role="user",
            content="x" * (MESSAGE_CONTENT_MAX_BYTES + 1),
        )
    with pytest.raises(repositories.RepositoryConflictError, match="artifact_manifest_too_large"):
        await repositories.create_artifact(
            conn,
            artifact_id="artifact-a",
            tenant_id="default",
            run_id="run-a",
            artifact_type="file",
            label="report",
            content_type="application/octet-stream",
            storage_key="private/report.bin",
            size_bytes=1,
            manifest_json=oversized_json(ARTIFACT_MANIFEST_MAX_BYTES),
        )
    with pytest.raises(repositories.RepositoryConflictError, match="audit_payload_too_large"):
        await repositories.append_audit_log(
            conn,
            tenant_id="default",
            user_id="user-a",
            action="test",
            target_type="run",
            target_id="run-a",
            payload_json=oversized_json(AUDIT_PAYLOAD_MAX_BYTES),
        )
    with pytest.raises(repositories.RepositoryConflictError, match="context_snapshot_payload_too_large"):
        await repositories.create_context_snapshot(
            conn,
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            trace_id="trace-a",
            context_kind="executor",
            included_message_ids=[],
            included_file_ids=[],
            included_artifact_ids=[],
            included_memory_record_ids=[],
            redaction_summary_json={},
            payload_json=oversized_json(CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES),
        )


def test_run_event_message_and_payload_limits_are_enforced_before_ledger_write():
    with pytest.raises(PersistenceSizeLimitError, match="run_event_message_too_large"):
        run_event_repository._ledger_event_from_values(
            event_type="status",
            stage="worker",
            message="x" * (RUN_EVENT_MESSAGE_MAX_BYTES + 1),
        )
    with pytest.raises(PersistenceSizeLimitError, match="run_event_payload_too_large"):
        run_event_repository._ledger_event_from_values(
            event_type="status",
            stage="worker",
            payload=oversized_json(RUN_EVENT_PAYLOAD_MAX_BYTES),
        )

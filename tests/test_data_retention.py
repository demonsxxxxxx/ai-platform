from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import data_retention


@asynccontextmanager
async def fake_transaction():
    yield object()


class RecordingStorage:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.deleted = []

    def delete_object(self, *, storage_key):
        if self.fail:
            raise RuntimeError("minio unavailable with private detail")
        self.deleted.append(storage_key)


def settings():
    return SimpleNamespace(
        data_retention_worker_cleanup_enabled=True,
        data_retention_worker_cleanup_interval_seconds=300,
        artifact_retention_cleanup_limit=10,
        object_delete_batch_limit=12,
        object_delete_max_attempts=5,
        object_delete_retry_base_seconds=60,
        object_delete_retry_cap_seconds=3600,
        memory_physical_purge_limit=11,
        memory_physical_purge_grace_days=7,
        run_event_retention_days=0,
        context_snapshot_retention_days=0,
        audit_retention_days=0,
        message_retention_days=0,
        file_retention_days=0,
    )


@pytest.mark.asyncio
async def test_retention_maintenance_receipts_successful_object_delete(monkeypatch):
    calls = []
    storage = RecordingStorage()
    data_retention._next_cleanup_at = 0

    async def queue(_conn, *, limit):
        calls.append(("queue", limit))
        return [{"tenant_id": "default", "artifact_id": "art-a"}]

    async def purge(_conn, *, grace_days, limit):
        calls.append(("purge", grace_days, limit))
        return [{"tenant_id": "default", "id": "mem-a"}]

    async def claim(_conn, *, limit, max_attempts):
        calls.append(("claim", limit, max_attempts))
        return [
            {
                "id": "out-a",
                "tenant_id": "default",
                "target_type": "artifact",
                "artifact_id": "art-a",
                "file_id": None,
                "storage_key": "private/a",
                "lease_generation": 1,
            }
        ]

    async def complete(_conn, **kwargs):
        calls.append(("complete", kwargs))
        return True

    async def audit(_conn, **kwargs):
        calls.append(("audit", kwargs["payload_json"]))

    monkeypatch.setattr(data_retention, "transaction", fake_transaction)
    monkeypatch.setattr(
        data_retention.repositories, "queue_expired_artifacts_for_deletion", queue
    )
    monkeypatch.setattr(
        data_retention.repositories, "purge_deleted_memory_records", purge
    )
    monkeypatch.setattr(data_retention.repositories, "claim_object_deletions", claim)
    monkeypatch.setattr(
        data_retention.repositories, "complete_object_deletion", complete
    )
    monkeypatch.setattr(data_retention.repositories, "append_audit_log", audit)

    result = await data_retention.run_data_retention_maintenance(
        settings(), now=10, storage=storage
    )

    assert result == {
        "status": "completed",
        "queued_artifacts": 1,
        "purged_memory": 1,
        "claimed_objects": 1,
        "deleted_objects": 1,
        "failed_objects": 0,
    }
    assert storage.deleted == ["private/a"]
    assert "private/a" not in str(calls)
    assert ("queue", 10) in calls
    assert ("claim", 12, 5) in calls


@pytest.mark.asyncio
async def test_retention_failure_records_only_safe_error_code(monkeypatch):
    failures = []
    data_retention._next_cleanup_at = 0

    async def empty(*_args, **_kwargs):
        return []

    async def claim(_conn, *, limit, max_attempts):
        return [
            {
                "id": "out-a",
                "tenant_id": "default",
                "target_type": "artifact",
                "artifact_id": "art-a",
                "file_id": None,
                "storage_key": "private/a",
                "lease_generation": 3,
            }
        ]

    async def fail(_conn, **kwargs):
        failures.append(kwargs)

    monkeypatch.setattr(data_retention, "transaction", fake_transaction)
    monkeypatch.setattr(
        data_retention.repositories, "queue_expired_artifacts_for_deletion", empty
    )
    monkeypatch.setattr(
        data_retention.repositories, "purge_deleted_memory_records", empty
    )
    monkeypatch.setattr(data_retention.repositories, "claim_object_deletions", claim)
    monkeypatch.setattr(data_retention.repositories, "fail_object_deletion", fail)

    result = await data_retention.run_data_retention_maintenance(
        settings(), now=10, storage=RecordingStorage(fail=True)
    )

    assert result["failed_objects"] == 1
    assert failures == [
        {
            "outbox_id": "out-a",
            "tenant_id": "default",
            "lease_generation": 3,
            "error_code": "object_delete_runtimeerror",
            "max_attempts": 5,
            "retry_base_seconds": 60,
            "retry_cap_seconds": 3600,
        }
    ]


@pytest.mark.asyncio
async def test_permanent_failure_does_not_starve_later_object(monkeypatch):
    completed = []
    failures = []
    data_retention._next_cleanup_at = 0

    async def empty(*_args, **_kwargs):
        return []

    async def claim(_conn, *, limit, max_attempts):
        return [
            {
                "id": "out-bad",
                "tenant_id": "default",
                "target_type": "artifact",
                "artifact_id": "art-bad",
                "file_id": None,
                "storage_key": "bad",
                "lease_generation": 5,
            },
            {
                "id": "out-good",
                "tenant_id": "default",
                "target_type": "file",
                "artifact_id": None,
                "file_id": "file-good",
                "storage_key": "good",
                "lease_generation": 8,
            },
        ]

    async def fail(_conn, **kwargs):
        failures.append(kwargs["outbox_id"])
        return "dead_letter"

    async def complete(_conn, **kwargs):
        completed.append(kwargs["outbox_id"])
        return True

    class SelectiveStorage:
        def delete_object(self, *, storage_key):
            if storage_key == "bad":
                raise RuntimeError("permanent")

    monkeypatch.setattr(data_retention, "transaction", fake_transaction)
    monkeypatch.setattr(
        data_retention.repositories, "queue_expired_artifacts_for_deletion", empty
    )
    monkeypatch.setattr(
        data_retention.repositories, "purge_deleted_memory_records", empty
    )
    monkeypatch.setattr(data_retention.repositories, "claim_object_deletions", claim)
    monkeypatch.setattr(data_retention.repositories, "fail_object_deletion", fail)
    monkeypatch.setattr(
        data_retention.repositories, "complete_object_deletion", complete
    )

    result = await data_retention.run_data_retention_maintenance(
        settings(), now=10, storage=SelectiveStorage()
    )

    assert failures == ["out-bad"]
    assert completed == ["out-good"]
    assert result["failed_objects"] == 1
    assert result["deleted_objects"] == 1


@pytest.mark.asyncio
async def test_receipt_conflict_is_retried_under_the_same_claim_generation(monkeypatch):
    failures = []
    data_retention._next_cleanup_at = 0

    async def empty(*_args, **_kwargs):
        return []

    async def claim(_conn, *, limit, max_attempts):
        return [
            {
                "id": "out-file",
                "tenant_id": "default",
                "target_type": "file",
                "artifact_id": None,
                "file_id": "file-a",
                "storage_key": "private/file-a",
                "lease_generation": 11,
            }
        ]

    async def complete(_conn, **_kwargs):
        return False

    async def fail(_conn, **kwargs):
        failures.append(kwargs)
        return "failed"

    monkeypatch.setattr(data_retention, "transaction", fake_transaction)
    monkeypatch.setattr(
        data_retention.repositories, "queue_expired_artifacts_for_deletion", empty
    )
    monkeypatch.setattr(
        data_retention.repositories, "purge_deleted_memory_records", empty
    )
    monkeypatch.setattr(data_retention.repositories, "claim_object_deletions", claim)
    monkeypatch.setattr(
        data_retention.repositories, "complete_object_deletion", complete
    )
    monkeypatch.setattr(data_retention.repositories, "fail_object_deletion", fail)

    result = await data_retention.run_data_retention_maintenance(
        settings(),
        now=10,
        storage=RecordingStorage(),
    )

    assert result["deleted_objects"] == 0
    assert result["failed_objects"] == 1
    assert failures == [
        {
            "outbox_id": "out-file",
            "tenant_id": "default",
            "lease_generation": 11,
            "error_code": "object_delete_receipt_conflict",
            "max_attempts": 5,
            "retry_base_seconds": 60,
            "retry_cap_seconds": 3600,
        }
    ]


def test_undecided_retention_classes_are_explicitly_fail_safe():
    projection = data_retention.retention_policy_projection(settings())
    assert projection["disabled_fail_safe"] == [
        "audit",
        "context_snapshots",
        "files",
        "messages",
        "run_events",
    ]
    assert projection["unsupported_not_implemented"] == []
    assert set(projection["runtime_status"].values()) == {"disabled_fail_safe"}
    assert projection["artifact_retention"] == {"selection_batch_limit": 10}
    assert projection["object_deletion"] == {
        "batch_limit": 12,
        "max_attempts": 5,
        "retry_base_seconds": 60,
        "retry_cap_seconds": 3600,
        "canonical_environment_prefix": "OBJECT_DELETE_",
        "legacy_environment_prefix": "ARTIFACT_OBJECT_DELETE_",
        "legacy_supported_until": "2026-10-31",
        "precedence": "canonical_over_legacy",
    }


def test_retention_projection_accepts_legacy_object_delete_attributes():
    configured = settings()
    del configured.object_delete_batch_limit
    del configured.object_delete_max_attempts
    del configured.object_delete_retry_base_seconds
    del configured.object_delete_retry_cap_seconds
    configured.artifact_object_delete_max_attempts = 6
    configured.artifact_object_delete_retry_base_seconds = 75
    configured.artifact_object_delete_retry_cap_seconds = 750

    projection = data_retention.retention_policy_projection(configured)

    assert projection["object_deletion"]["batch_limit"] == 10
    assert projection["object_deletion"]["max_attempts"] == 6
    assert projection["object_deletion"]["retry_base_seconds"] == 75
    assert projection["object_deletion"]["retry_cap_seconds"] == 750


@pytest.mark.asyncio
async def test_nonzero_unimplemented_retention_is_reported_and_never_runs_cleanup(
    monkeypatch,
):
    configured = settings()
    configured.run_event_retention_days = 7

    async def forbidden_transaction():
        raise AssertionError(
            "unsupported retention must fail before opening a transaction"
        )

    monkeypatch.setattr(data_retention, "transaction", forbidden_transaction)
    projection = data_retention.retention_policy_projection(configured)
    result = await data_retention.run_data_retention_maintenance(configured, now=10)

    assert projection["unsupported_not_implemented"] == ["run_events"]
    assert projection["runtime_status"]["run_events"] == "unsupported_not_implemented"
    assert result == {
        "status": "unsupported_retention_configuration",
        "unsupported_retention_classes": ["run_events"],
        "deleted_objects": 0,
    }

"""Real PostgreSQL fences around lossless provider image I/O and cleanup."""

import hashlib
import json
from pathlib import Path

import pytest
from tests.support.db_transactions import event_loop_policy as event_loop_policy

from app.context.application.provider_sessions import ProviderSessionUseCases
from app.context.domain.provider_sessions import ProviderSessionContinuityError
from app.context.infrastructure.provider_epochs import PostgresProviderEpochRepository
from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields
from app.persistence import object_deletions
from app.storage import StoredObject
from tests.test_provider_session_recovery_postgres import INITIAL, PROVIDER_ID, recovery_db as _recovery_db

from tests.test_conversation_recovery_postgres import (
    lifecycle_db as provider_lifecycle_db,
    SCOPE, SCOPE_MAP, workspace as saved_workspace, settlement as sdk_settlement,
)

lifecycle_db = provider_lifecycle_db
recovery_db = _recovery_db


class ImageStorage:
    def __init__(self):
        self.objects = {}

    def close(self):
        pass

    def put_bytes(self, *, storage_key, content, content_type):
        self.objects[storage_key] = content
        return StoredObject(storage_key, hashlib.sha256(content).hexdigest(), len(content))

    def get_bytes_bounded(self, *, storage_key, max_bytes):
        value = self.objects[storage_key]
        assert len(value) <= max_bytes
        return value


def image_entry():
    encoded = "QUJD" * 100_000
    return {"type": "user", "uuid": "image-user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "synthetic-read", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}},
        ]},
    ]}, "toolUseResult": {"type": "image", "file": {"type": "image/png", "base64": encoded}},
        "unknown": {"negative_zero": -0.0, "float": 1e21}}


def operation(action, **kwargs):
    return dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                agent_id="agent", run_id="run", attempt_id="attempt", provider_session_id=PROVIDER_ID,
                action=action, subpath=None, entries=[], expected_sequence=None) | kwargs


@pytest.fixture
async def media_db(recovery_db):
    async with recovery_db() as conn:
        await conn.execute("""
            create table tenants(id text primary key);
            insert into tenants values('tenant');
            create table artifacts(id text primary key, tenant_id text, storage_key text,
              lifecycle_state text, manifest_json jsonb, deleted_at timestamptz);
            create table files(id text primary key, tenant_id text, storage_key text,
              lifecycle_state text, deleted_at timestamptz);
        """)
        source = (Path(__file__).resolve().parents[1] / "app/schema.sql").read_text()
        start = source.index("create table if not exists object_deletion_outbox")
        await conn.execute(source[start:source.index("create table if not exists audit_logs", start)])
    return recovery_db


@pytest.fixture
async def media_service(media_db):
    storage = ImageStorage()

    async def unlocked_io(fn, **kwargs):
        async with media_db() as conn:
            # NOWAIT fails if either callback phase accidentally holds its authority lock over I/O.
            await conn.execute("select 1 from provider_session_heads for update nowait")
            await conn.execute("select 1 from provider_session_epochs for update nowait")
            await conn.execute("select 1 from sessions for update nowait")
            await conn.execute("select 1 from runs for update nowait")
        return fn(**kwargs)

    service = ProviderSessionUseCases(PostgresProviderEpochRepository(), image_fields=provider_session_image_fields,
                                     storage_factory=lambda: storage, storage_io=unlocked_io)
    return service, storage


async def execute(connect, service, request):
    async with connect() as conn:
        transfer = await service.prepare_callback_media(conn, **request)
    await service.transfer_callback_media(transfer)
    async with connect() as conn:
        result = await service.execute_callback(conn, **request, media=transfer)
    return result, transfer


async def test_image_append_restore_and_duplicate_ack(media_db, media_service, monkeypatch):
    service, storage = media_service
    entry = image_entry()
    request = operation("append", entries=[entry], expected_sequence=3)
    result, first = await execute(media_db, service, request)
    assert result.last_sequence == 3 and len(storage.objects) == 1
    async with media_db() as conn:
        row = await (await conn.execute("select entry_json, image_references from provider_session_entries where sequence=3")).fetchone()
        assert len(json.dumps(row["entry_json"])) < 1000
        assert len(row["image_references"]) == 2
        assert row["image_references"][0]["image_id"] == row["image_references"][1]["image_id"]
    restored, _ = await execute(media_db, service, operation("load"))
    assert list(restored.entries) == INITIAL + [entry]
    assert json.dumps(restored.entries[-1], sort_keys=True) == json.dumps(entry, sort_keys=True)
    def unavailable(**_kwargs):
        raise AssertionError("A committed ACK retry must not upload again")

    monkeypatch.setattr(storage, "put_bytes", unavailable)
    duplicate, second = await execute(media_db, service, request)
    assert duplicate == result and second.objects == []
    async with media_db() as conn:
        rows = await (await conn.execute("select id, lifecycle_state, provisional from provider_session_images order by created_at")).fetchall()
        assert rows[0] == {"id": first.objects[0]["id"], "lifecycle_state": "active", "provisional": False}
        assert len(rows) == 1
        assert (await (await conn.execute("select count(*) as n from provider_session_entries")).fetchone())["n"] == 3


@pytest.mark.parametrize("mutation", ["fence", "lease", "deleted", "cleanup"])
async def test_image_upload_cannot_commit_after_authority_changes(media_db, media_service, mutation):
    service, storage = media_service
    request = operation("append", entries=[image_entry()], expected_sequence=3)
    async with media_db() as conn:
        transfer = await service.prepare_callback_media(conn, **request)
    await service.transfer_callback_media(transfer)
    async with media_db() as conn:
        if mutation == "fence":
            await conn.execute("update run_attempts set owner_generation=3")
        elif mutation == "lease":
            await conn.execute("update sandbox_leases set expires_at=now()-interval '1 second'")
        elif mutation == "deleted":
            await conn.execute("update sessions set status='deleted'")
        else:
            await conn.execute("update object_deletion_outbox set available_at=now()-interval '1 second'")
            assert len(await object_deletions.claim_object_deletions(conn)) == 1
    with pytest.raises(ProviderSessionContinuityError):
        async with media_db() as conn:
            await service.execute_callback(conn, **request, media=transfer)
    async with media_db() as conn:
        row = await (await conn.execute("select next_sequence, entry_count from provider_session_epochs")).fetchone()
        assert row == {"next_sequence": 3, "entry_count": 2}
        assert (await (await conn.execute("select lifecycle_state from provider_session_images")).fetchone())["lifecycle_state"] == "delete_pending"
    assert len(storage.objects) == 1  # Durable outbox owns the now-uncommittable upload.


@pytest.mark.parametrize("corruption", ["bytes", "missing", "cross_scope"])
async def test_image_restore_fails_closed(media_db, media_service, corruption):
    service, storage = media_service
    _, transfer = await execute(media_db, service, operation("append", entries=[image_entry()], expected_sequence=3))
    key = transfer.objects[0]["storage_key"]
    if corruption == "bytes":
        storage.objects[key] = b"bad"
    elif corruption == "missing":
        storage.objects.pop(key)
    else:
        async with media_db() as conn:
            await conn.execute("update provider_session_images set user_id='other-user'")
    with pytest.raises((ProviderSessionContinuityError, KeyError)):
        await execute(media_db, service, operation("load"))


@pytest.mark.parametrize("physical", [False, True])
async def test_session_deletion_queues_private_image_without_losing_cleanup_owner(media_db, media_service, physical):
    service, _storage = media_service
    _, transfer = await execute(media_db, service, operation("append", entries=[image_entry()], expected_sequence=3))
    async with media_db() as conn:
        await conn.execute("delete from sessions" if physical else "update sessions set status='deleted'")
        assert len(await service.queue_images_for_deletion(conn)) == 1
        claimed = await object_deletions.claim_object_deletions(conn)
        assert len(claimed) == 1 and claimed[0]["target_type"] == "provider_image"
        item = claimed[0]
        assert not await object_deletions.complete_object_deletion(conn, outbox_id=item["id"], tenant_id="tenant",
                                                                 lease_generation=item["lease_generation"] + 1)
        assert await object_deletions.complete_object_deletion(conn, outbox_id=item["id"], tenant_id="tenant",
                                                              lease_generation=item["lease_generation"])
        row = await (await conn.execute("select lifecycle_state from provider_session_images where id=%s", (transfer.objects[0]["id"],))).fetchone()
        assert row["lifecycle_state"] == "deleted"


async def test_abandoned_upload_cleanup_repeats_after_late_write(media_db, media_service):
    service, _storage = media_service
    async with media_db() as conn:
        await service.prepare_callback_media(conn, **operation("append", entries=[image_entry()], expected_sequence=3))
        await conn.execute("update object_deletion_outbox set available_at=now()-interval '1 second'")
        first = (await object_deletions.claim_object_deletions(conn))[0]
        assert await object_deletions.complete_object_deletion(conn, outbox_id=first["id"], tenant_id="tenant", lease_generation=first["lease_generation"])
        await conn.execute("update object_deletion_outbox set updated_at=now()-interval '2 days'")
        second = (await object_deletions.claim_object_deletions(conn))[0]
        assert second["id"] == first["id"] and second["lease_generation"] == first["lease_generation"] + 1


@pytest.mark.parametrize("rejection", ["sequence", "raw_capacity", "structured_capacity", "duplicate_conflict"])
async def test_rejected_append_reserves_no_object(media_db, media_service, rejection):
    service, storage = media_service
    sequence = 3
    async with media_db() as conn:
        if rejection == "sequence":
            sequence = 4
        elif rejection == "duplicate_conflict":
            sequence = 1
        elif rejection == "raw_capacity":
            await conn.execute("update provider_session_epochs set transcript_bytes=67108864")
        else:
            await conn.execute("update provider_session_epochs set structured_bytes=8388608")
    with pytest.raises(ProviderSessionContinuityError):
        async with media_db() as conn:
            await service.prepare_callback_media(conn, **operation("append", entries=[image_entry()], expected_sequence=sequence))
    async with media_db() as conn:
        assert (await (await conn.execute("select count(*) as n from provider_session_images")).fetchone())["n"] == 0
    assert storage.objects == {}


async def test_unfinished_upload_reservations_are_bounded(media_db, media_service, monkeypatch):
    from app.context.infrastructure import provider_images

    monkeypatch.setattr(provider_images, "MAX_PROVIDER_IMAGE_FIELDS", 1)
    service, storage = media_service
    request = operation("append", entries=[image_entry()], expected_sequence=3)
    async with media_db() as conn:
        await service.prepare_callback_media(conn, **request)
    with pytest.raises(ProviderSessionContinuityError, match="image_upload_pending"):
        async with media_db() as conn:
            await service.prepare_callback_media(conn, **request)
    assert storage.objects == {}


async def test_cancel_between_upload_and_commit_preserves_original_tail_writer(media_db, media_service):
    service, _storage = media_service
    request = operation("append", entries=[image_entry()], expected_sequence=3)
    async with media_db() as conn:
        transfer = await service.prepare_callback_media(conn, **request)
    await service.transfer_callback_media(transfer)
    async with media_db() as conn:
        await conn.execute("update runs set cancel_requested_at=now()")
        await conn.execute("update run_attempts set status='cancel_requested',owner_generation=3")
    async with media_db() as conn:
        result = await service.execute_callback(conn, **request, media=transfer)
        assert result.last_sequence == 3
        assert (await (await conn.execute("select owner_generation from provider_session_images")).fetchone())["owner_generation"] == 2


@pytest.mark.parametrize("revoke", [False, True])
async def test_callback_route_keeps_io_outside_locks_and_rechecks_authority(media_db, media_service, monkeypatch, revoke):
    from fastapi import HTTPException
    from app.context.application import provider_sessions
    from app.routes import runtime_callbacks
    from app.runtime.sandbox.contracts import ProviderSessionCallbackRequest

    service, _storage = media_service
    monkeypatch.setattr(provider_sessions, "_use_cases", service)
    monkeypatch.setattr(runtime_callbacks, "transaction", media_db)
    monkeypatch.setattr(runtime_callbacks, "_require_valid_callback_token", lambda *_args, **_kwargs: None)
    checks = []

    async def authority(conn, **binding):
        checks.append(binding)
        await conn.execute("select 1 from runs for update")
        if revoke and len(checks) == 2:
            raise HTTPException(409, "sandbox_runtime_owner_generation_stale")
        return {key: value for key, value in operation("append").items() if key.endswith("_id")}, {}

    monkeypatch.setattr(runtime_callbacks, "_lock_callback_authority", authority)
    request = ProviderSessionCallbackRequest(action="append", run_id="run", attempt_id="attempt",
        callback_token_id="synthetic", provider_session_id=PROVIDER_ID, entries=[image_entry()], expected_sequence=3)
    if revoke:
        with pytest.raises(HTTPException) as error:
            await runtime_callbacks.provider_session_callback(request, callback_token="synthetic")
        assert error.value.status_code == 409
    else:
        result = await runtime_callbacks.provider_session_callback(request, callback_token="synthetic")
        assert result.last_sequence == 3
    assert len(checks) == 2 and all(check["allow_provider_tail"] for check in checks)
    async with media_db() as conn:
        row = await (await conn.execute("select next_sequence from provider_session_epochs")).fetchone()
        assert row["next_sequence"] == (3 if revoke else 4)


async def test_provider_image_outbox_is_invisible_to_older_worker_states(media_db, media_service):
    service, _storage = media_service
    async with media_db() as conn:
        await service.prepare_callback_media(conn, **operation("append", entries=[image_entry()], expected_sequence=3))
        cursor = await conn.execute("""
            select id from object_deletion_outbox
            where state in ('pending','processing','failed','dead_letter','deleted',
                            'file_pending','file_processing','file_failed','file_dead_letter','file_deleted')
        """)
        assert await cursor.fetchall() == []
        assert (await (await conn.execute("select state from object_deletion_outbox")).fetchone())["state"] == "provider_image_pending"


async def test_archiving_session_does_not_delete_its_images(media_db, media_service):
    service, _storage = media_service
    await execute(media_db, service, operation("append", entries=[image_entry()], expected_sequence=3))
    async with media_db() as conn:
        await conn.execute("update sessions set status='archived'")
        assert await service.queue_images_for_deletion(conn) == []
        assert await object_deletions.claim_object_deletions(conn) == []


async def test_real_minio_provider_image_round_trip_and_deletion(media_db, monkeypatch):
    import os
    from types import SimpleNamespace
    from urllib.parse import urlparse
    from uuid import uuid4
    from botocore.exceptions import ClientError
    from app import storage as storage_module

    endpoint = os.getenv("AI_PLATFORM_PROVIDER_IMAGE_MINIO_URL")
    if not endpoint:
        pytest.skip("AI_PLATFORM_PROVIDER_IMAGE_MINIO_URL is not configured")
    assert urlparse(endpoint).hostname in {"127.0.0.1", "localhost", "::1"}
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    settings = SimpleNamespace(s3_endpoint_url=endpoint, s3_bucket=f"provider-image-test-{uuid4().hex}",
        s3_access_key_id="synthetic-provider-images", s3_secret_access_key="synthetic-provider-images-local-test-only",
        s3_region="us-east-1")
    monkeypatch.setattr(storage_module, "get_settings", lambda: settings)
    storage = storage_module.ObjectStorage()
    service = ProviderSessionUseCases(PostgresProviderEpochRepository(), image_fields=provider_session_image_fields,
        storage_factory=storage_module.ObjectStorage, storage_io=storage_module.run_storage_io)
    entry = image_entry()
    try:
        storage.ensure_bucket()
        result, transfer = await execute(media_db, service, operation("append", entries=[entry], expected_sequence=3))
        assert result.last_sequence == 3
        key = transfer.objects[0]["storage_key"]
        objects = storage.client.list_objects_v2(Bucket=storage.bucket)
        assert objects["KeyCount"] == 1 and objects["Contents"][0]["Key"] == key
        restored, _ = await execute(media_db, service, operation("load"))
        assert list(restored.entries) == INITIAL + [entry]
        await storage_module.run_storage_io(storage.put_bytes, storage_key=key, content=b"corrupt",
                                            content_type="application/octet-stream")
        with pytest.raises(ProviderSessionContinuityError, match="integrity_mismatch"):
            await execute(media_db, service, operation("load"))
        async with media_db() as conn:
            await conn.execute("update sessions set status='deleted'")
            await service.queue_images_for_deletion(conn)
            claimed = (await object_deletions.claim_object_deletions(conn))[0]
        await storage_module.run_storage_io(storage.delete_object, storage_key=claimed["storage_key"])
        async with media_db() as conn:
            assert await object_deletions.complete_object_deletion(conn, outbox_id=claimed["id"],
                tenant_id=claimed["tenant_id"], lease_generation=claimed["lease_generation"])
        with pytest.raises(ClientError) as error:
            storage.client.head_object(Bucket=storage.bucket, Key=key)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404
    finally:
        if storage._bucket_ready:
            remaining = storage.client.list_objects_v2(Bucket=storage.bucket)
            assert not remaining.get("IsTruncated")
            for obj in remaining.get("Contents", []):
                storage.delete_object(storage_key=obj["Key"])
            storage.client.delete_bucket(Bucket=storage.bucket)
        storage.client.close()


@pytest.fixture
async def image_lifecycle_db(lifecycle_db, media_db):
    assert lifecycle_db is media_db
    return lifecycle_db


async def _settle_image_source(connect, service, *, final_sequence):
    from app.context.infrastructure import provider_epochs
    from app.context.infrastructure.provider_continuation import settle_provider_continuation
    from tests.test_provider_session_recovery_postgres import request_cancel
    async with connect() as conn:
        await request_cancel(conn)
    async with service.verification_scope(connect, tenant_id="tenant", run_id="run"):
        async with connect() as conn:
            recovery = await settle_provider_continuation(conn, tenant_id="tenant", run_id="run", attempt_id="attempt",
                final_sequence=final_sequence, workspace_receipt=saved_workspace(), stop_evidence="provider_stopped",
                sdk_settlement={**sdk_settlement(), "owner_generation": 3, "writer_owner_generation": 2})
            await conn.execute("update runs set status='cancelled' where id='run'")
            await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="run")
    return recovery


async def _admit_image_copy(connect):
    from app.context.infrastructure import provider_epochs
    from app.context.domain.conversation_recovery import make_provider_coverage_authority_receipt
    from psycopg.types.json import Jsonb
    async with connect() as conn:
        await conn.execute("insert into runs(tenant_id,id,status,session_generation,context_snapshot_id) values('tenant','copy','running',3,'snapshot-copy')")
        await provider_epochs.claim_provider_lineage(conn, scope=SCOPE, run_id="copy")
        coverage = await provider_epochs.read_provider_coverage(conn, scope=SCOPE, run_id="copy", session_generation=3)
        receipt = make_provider_coverage_authority_receipt(coverage=coverage, scope=SCOPE_MAP,
            through_session_generation=3, current_message_id="copy-user")
        await conn.execute("insert into run_context_snapshots values('snapshot-copy','tenant','workspace','user','session','copy','executor',%s)", (Jsonb(receipt),))
        await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content) values('copy-user','tenant','session','copy','user','new input')")
    return dict(source_sha256=coverage["source_sha256"], message_count=coverage["message_count"],
        authority_schema_version=coverage["authority_schema_version"], current_message_id="copy-user",
        messages=[], selected_message_count=0, selected_turn_count=0)


async def _bind_image_copy(conn, context):
    from app.context.infrastructure import provider_epochs
    from psycopg.types.json import Jsonb
    frozen = await provider_epochs.prepare_provider_epoch(conn, scope=SCOPE, run_id="copy", conversation_context=context)
    await conn.execute("""insert into run_attempts(tenant_id,id,run_id,status,owner_kind,owner_id,owner_generation,
        execution_spec_sha256,execution_spec_schema_version,execution_spec_json,ordinal)
        values('tenant','copy-attempt','copy','running','queue_worker','worker',4,%s,'ai-platform.execution-spec.v3',%s,1)""",
        ("d" * 64, Jsonb({"context_pack": {"conversation_context": frozen}})))
    await conn.execute("""insert into sandbox_leases(id,tenant_id,run_id,attempt_id,status,expires_at,lease_payload_json)
        values('copy-lease','tenant','copy','copy-attempt','active',now()+interval '5 minutes',%s)""", (Jsonb(dict(owner_generation=4)),))
    return frozen


@pytest.mark.parametrize("rollback", [False, True])
async def test_native_image_epoch_copy_preserves_ack_media_children_and_transaction_rollback(image_lifecycle_db, media_service, rollback):
    from app.context.infrastructure import provider_epochs
    from app.context.infrastructure.provider_images import queue_provider_images_for_deletion
    from app.context.infrastructure.provider_continuation import settle_provider_continuation
    connect = image_lifecycle_db
    service, storage = media_service
    main, child = image_entry(), {**image_entry(), "uuid": "child-image"}
    await execute(connect, service, operation("append", entries=[main], expected_sequence=3))
    await execute(connect, service, operation("append", entries=[child], subpath="subagents/child", expected_sequence=4))
    native = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "native-create",
              "name": "mcp__vendor__create", "input": {"project": {"name": "Synthetic object"}}}]}}
    await execute(connect, service, operation("append", entries=[native], expected_sequence=5))
    recovery = await _settle_image_source(connect, service, final_sequence=5)
    assert recovery["state"] == "resumable"
    async with connect() as conn:
        original = await (await conn.execute("select * from provider_session_append_receipts where epoch_id='epoch' order by expected_sequence")).fetchall()
    context = await _admit_image_copy(connect)
    async with service.verification_scope(connect, tenant_id="tenant", run_id="copy"):
        if rollback:
            with pytest.raises(RuntimeError, match="synthetic rollback"):
                async with connect() as conn:
                    await _bind_image_copy(conn, context)
                    raise RuntimeError("synthetic rollback")
        else:
            async with connect() as conn:
                frozen = await _bind_image_copy(conn, context)
                # An independent cleanup cannot acquire the shared image's deletion lock.
                assert await queue_provider_images_for_deletion(conn) == []
    async with connect() as conn:
        assert await (await conn.execute("select * from provider_session_append_receipts where epoch_id='epoch' order by expected_sequence")).fetchall() == original
        assert (await (await conn.execute("select count(*) as n from provider_session_epochs")).fetchone())["n"] == (1 if rollback else 2)
    if rollback:
        return
    assert frozen["provider_session_id"] == PROVIDER_ID and frozen["provider_epoch_id"] != "epoch"
    load = operation("load", run_id="copy", attempt_id="copy-attempt", provider_epoch_id=frozen["provider_epoch_id"])
    restored, _ = await execute(connect, service, load)
    assert list(restored.entries) == INITIAL + [main, native]
    children, _ = await execute(connect, service, {**load, "action": "list_subkeys", "include_child_entries": True})
    assert children.child_entries == {"subagents/child": [child]}
    async with service.verification_scope(connect, tenant_id="tenant", run_id="copy"):
        async with connect() as conn:
            native_input = await provider_epochs.read_verified_native_invocation(conn, scope=SCOPE_MAP, epoch_id="epoch",
                source_run_id="run", source_attempt_id="attempt", final_sequence=5, invocation_id="native-create", native_tool_name="mcp__vendor__create")
            assert native_input == native["message"]["content"][0]["input"]
    # A new current append retains the same independent structured and full-byte counters.
    appended = {"type": "user", "uuid": "copy-user", "message": {"content": "new input"}}
    request = {**load, "action": "append", "entries": [appended], "expected_sequence": 6}
    first, _ = await execute(connect, service, request)
    second, ticket = await execute(connect, service, request)
    assert second == first and ticket.objects == []
    async with service.verification_scope(connect, tenant_id="tenant", run_id="copy"):
        async with connect() as conn:
            result = await settle_provider_continuation(conn, tenant_id="tenant", run_id="copy", attempt_id="copy-attempt",
                final_sequence=6, workspace_receipt=saved_workspace(run="copy", attempt="copy-attempt"),
                stop_evidence="provider_stopped", sdk_settlement={**sdk_settlement(), "owner_generation": 4})
            assert result["state"] == "resumable"
    assert len(storage.objects) == 2  # Appends own objects; native copies only read them.
    async with connect() as conn:
        await conn.execute("delete from provider_session_entries where epoch_id='epoch'")
        assert await queue_provider_images_for_deletion(conn) == []
    async with connect() as conn:
        await conn.execute("delete from provider_session_entries where epoch_id=%s", (frozen["provider_epoch_id"],))
        assert len(await queue_provider_images_for_deletion(conn)) == 2
        claimed = await object_deletions.claim_object_deletions(conn)
        assert len(claimed) == 2


@pytest.mark.parametrize("mutation", ["image_metadata", "image_owner", "ack", "generation", "deleted_session"])
async def test_epoch_image_preload_rejects_metadata_and_authority_changes_without_settlement(image_lifecycle_db, media_service, mutation):
    from app.context.infrastructure.provider_epochs import verified_provider_prefix
    from app.context.domain.provider_sessions import ProviderSessionConflictError
    service, _ = media_service
    connect = image_lifecycle_db
    await execute(connect, service, operation("append", entries=[image_entry()], expected_sequence=3))
    async with service.verification_scope(connect, tenant_id="tenant", run_id="run"):
        async with connect() as conn:
            statements = {
                "image_metadata": "update provider_session_images set storage_key='private/replaced'",
                "image_owner": "update provider_session_images set user_id='other-user'",
                "ack": "update provider_session_append_receipts set batch_sha256=repeat('0',64) where expected_sequence=3",
                "generation": "update run_attempts set owner_generation=3 where id='attempt'",
                "deleted_session": "update sessions set status='deleted'",
            }
            await conn.execute(statements[mutation])
        with pytest.raises(ProviderSessionConflictError):
            async with connect() as conn:
                epoch = await (await conn.execute("select * from provider_session_epochs where id='epoch'")).fetchone()
                await verified_provider_prefix(conn, epoch=epoch, final_sequence=3)
    async with connect() as conn:
        assert (await (await conn.execute("select recovery_json from provider_session_epochs where id='epoch'")).fetchone())["recovery_json"] is None


async def test_source_retirement_and_image_copy_serialize_without_deleting_live_copy(image_lifecycle_db, media_service):
    import asyncio
    from app.context.infrastructure.provider_images import queue_provider_images_for_deletion
    connect = image_lifecycle_db
    service, _ = media_service
    image = image_entry()
    await execute(connect, service, operation("append", entries=[image], expected_sequence=3))
    await _settle_image_source(connect, service, final_sequence=3)
    context = await _admit_image_copy(connect)
    entered = asyncio.Event()

    async def retire_source_entries():
        async with connect() as conn:
            entered.set()
            await conn.execute("delete from provider_session_entries where epoch_id='epoch'")

    retirement = None
    try:
        async with service.verification_scope(connect, tenant_id="tenant", run_id="copy"):
            async with connect() as conn:
                frozen = await _bind_image_copy(conn, context)
                retirement = asyncio.create_task(retire_source_entries())
                await entered.wait()
                done, _ = await asyncio.wait({retirement}, timeout=0.05)
                assert not done, "copy holds the original ledger through its atomic commit"
        await asyncio.wait_for(retirement, timeout=3)
    finally:
        if retirement is not None and not retirement.done():
            retirement.cancel()
            await asyncio.gather(retirement, return_exceptions=True)
    async with connect() as conn:
        assert await queue_provider_images_for_deletion(conn) == []
    restored, _ = await execute(connect, service, operation("load", run_id="copy", attempt_id="copy-attempt",
                                                          provider_epoch_id=frozen["provider_epoch_id"]))
    assert list(restored.entries) == INITIAL + [image]
    async with connect() as conn:
        await conn.execute("delete from provider_session_entries where epoch_id=%s", (frozen["provider_epoch_id"],))
        assert len(await queue_provider_images_for_deletion(conn)) == 1

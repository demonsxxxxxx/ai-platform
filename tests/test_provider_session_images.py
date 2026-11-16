import base64
import copy

import pytest

from app.context.domain.provider_media import (
    restore_provider_images,
    split_provider_images,
)
from app.context.domain.provider_sessions import ProviderSessionContinuityError
from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields


def image_entry(data: str) -> dict:
    return {
        "type": "user", "uuid": "synthetic-entry",
        "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "synthetic-read", "content": [{
                "type": "image", "source": {"type": "base64", "data": data, "media_type": "image/png"},
            }],
        }]},
        "toolUseResult": {"type": "image", "file": {"base64": data, "type": "image/png", "originalSize": 247173}},
        "unknown": {"negative_zero": -0.0, "scientific": 1e21, "text": data},
    }


def test_provider_images_round_trip_deduplicates_both_cli_image_locations():
    data = base64.b64encode(b"x" * 247173).decode()
    original = image_entry(data)
    unchanged = copy.deepcopy(original)
    projection = split_provider_images([original], image_fields=provider_session_image_fields)
    assert original == unchanged
    assert len(projection.images) == 1
    assert len(projection.references[0]) == 2
    assert projection.entries[0]["toolUseResult"]["file"]["base64"] == ""
    assert projection.entries[0]["message"]["content"][0]["content"][0]["source"]["data"] == ""
    assert projection.entries[0]["unknown"] == original["unknown"]
    values = {}
    for ref in projection.references[0]:
        ref["image_id"] = "synthetic-image"
        values[ref["image_id"]] = projection.images[ref["sha256"]].content.decode()
    restored = restore_provider_images(projection.entries[0], projection.references[0], values=values)
    assert restored == original


def test_provider_image_codec_preserves_unknown_and_textual_image_examples():
    original = {"type": "assistant", "message": {"content": [{"type": "text", "text": '{"type":"image","data":"xyz"}'}]}, "unknown": {"type": "image", "data": "unchanged"}}
    projection = split_provider_images([original], image_fields=provider_session_image_fields)
    assert projection.entries == [original]
    assert projection.references == [[]]
    assert projection.images == {}


@pytest.mark.parametrize("tamper", ["missing", "value", "path", "digest", "size", "extra", "duplicate"])
def test_provider_image_restore_rejects_missing_or_tampered_payload(tamper):
    original = image_entry("encoded-synthetic-value")
    projection = split_provider_images([original], image_fields=provider_session_image_fields)
    refs = projection.references[0]
    for ref in refs:
        ref["image_id"] = "synthetic-image"
    values = {"synthetic-image": "encoded-synthetic-value"}
    if tamper == "missing":
        values.clear()
    elif tamper == "value":
        values["synthetic-image"] = "different"
    elif tamper == "path":
        refs[0]["path"] = ["message", "content", -1]
    elif tamper == "digest":
        refs[0]["sha256"] = "0" * 64
    elif tamper == "size":
        refs[0]["size_bytes"] += 1
    elif tamper == "extra":
        refs[0]["extra"] = "untrusted"
    else:
        refs.append(refs[0])
    with pytest.raises(ProviderSessionContinuityError):
        restore_provider_images(projection.entries[0], refs, values=values)


@pytest.mark.parametrize("mime", [[], {}, None, 1, "image/unknown"])
def test_unrecognized_image_metadata_stays_opaque(mime):
    original = {"message": {"content": [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": "abc"}},
        {"type": "image", "mimeType": mime, "data": "abc"},
    ]}, "toolUseResult": {"type": "image", "file": {"type": mime, "base64": "abc"}}}
    projection = split_provider_images([original], image_fields=provider_session_image_fields)
    assert projection.entries == [original] and projection.images == {}


def test_image_codec_caps_payload_and_reference_count(monkeypatch):
    from app.context.domain import provider_media

    monkeypatch.setattr(provider_media, "MAX_PROVIDER_IMAGE_BYTES", 3)
    with pytest.raises(ProviderSessionContinuityError, match="image_too_large"):
        split_provider_images([image_entry("abcd")], image_fields=provider_session_image_fields)
    monkeypatch.setattr(provider_media, "MAX_PROVIDER_IMAGE_FIELDS", 1)
    with pytest.raises(ProviderSessionContinuityError, match="image_count_exceeded"):
        split_provider_images([image_entry("abc")], image_fields=provider_session_image_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_image_client_closes_only_after_abandoned_write_finishes(interruption):
    import asyncio
    import hashlib
    import threading
    from app.context.application.provider_sessions import ProviderSessionUseCases
    from app.context.domain.provider_media import ProviderMediaTransfer
    from app.storage import StoredObject, StorageIOTimeoutError, run_storage_io

    loop = asyncio.get_running_loop()
    started, closed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    writes = []
    projection = split_provider_images([image_entry("abc")], image_fields=provider_session_image_fields)
    digest = next(iter(projection.images))
    media = ProviderMediaTransfer("epoch", 1, 1,
        [{"id": "image", "storage_key": "synthetic/image", "sha256": digest, "size_bytes": 3}], projection)

    class Storage:
        def put_bytes(self, *, storage_key, content, content_type):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3)
            writes.append(content)
            return StoredObject(storage_key, hashlib.sha256(content).hexdigest(), len(content))

        def close(self):
            assert writes == [b"abc"]
            loop.call_soon_threadsafe(closed.set)

    async def bounded_io(fn):
        return await run_storage_io(fn, timeout_seconds=0.03 if interruption == "timeout" else None)

    service = ProviderSessionUseCases(object(), storage_factory=Storage, storage_io=bounded_io)
    task = asyncio.create_task(service.transfer_callback_media(media))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if interruption == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if interruption == "cancel" else StorageIOTimeoutError):
            await task
        assert not closed.is_set() and not media.completed and media.values == {}
    finally:
        release.set()
        await asyncio.wait_for(closed.wait(), 1)
        await asyncio.gather(task, return_exceptions=True)
    assert writes == [b"abc"] and not media.completed


def _image_epoch_ledger():
    from app.context.infrastructure.provider_epochs import _canonical, batch_digest
    from app.context.domain.provider_sessions import provider_entry_json_bytes
    originals = [image_entry("QUJD" * 32),
                 {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "native-a",
                    "name": "mcp__vendor__create", "input": {"project": {"name": "Synthetic project"}}}]}},
                 {**image_entry("REVG" * 32), "uuid": "child-image"}]
    projection = split_provider_images(originals, image_fields=provider_session_image_fields)
    objects, values = [], {}
    for index, (digest, payload) in enumerate(projection.images.items()):
        image_id = "image-" + str(index)
        objects.append(dict(id=image_id, storage_key="private/" + image_id,
                            sha256=digest, size_bytes=len(payload.content)))
        values[image_id] = payload.content.decode()
        for refs in projection.references:
            for ref in refs:
                if ref["sha256"] == digest:
                    ref["image_id"] = image_id
    rows, receipts = [], []
    for index, (original, entry, refs) in enumerate(zip(originals, projection.entries, projection.references)):
        sequence = index + 1
        subpath = "subagents/child" if index == 2 else ""
        rows.append(dict(sequence=sequence, subpath=subpath, entry_json=entry,
            entry_canonical_json=_canonical(entry).decode(), canonical_matches=True,
            image_format="image-payloads-v1" if refs else None, image_references=refs))
        receipts.append(dict(expected_sequence=sequence, entry_count=1, last_sequence=sequence,
                             batch_sha256=batch_digest(subpath, [original])))
    epoch = dict(id="epoch", provider_session_id="native-provider", next_sequence=4, entry_count=3,
        transcript_bytes=sum(provider_entry_json_bytes(entry) for entry in originals),
        structured_bytes=sum(provider_entry_json_bytes(entry) for entry in projection.entries),
        tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session", agent_id="agent")
    return epoch, rows, receipts, objects, values, originals


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "entry", "reference", "receipt", "counter", "storage_key",
                                     "image_digest", "generation", "attempt", "owner", "revoked"])
async def test_epoch_image_proof_rechecks_locked_ledger_and_current_owner_after_unlocked_io(monkeypatch, mutation):
    from contextlib import asynccontextmanager
    from app.context.application import provider_sessions
    from app.context.api import provider_session_verification_scope
    from app.context.infrastructure import provider_epoch_verification as verification
    from app.context.infrastructure.provider_epochs import PostgresProviderEpochRepository, verified_provider_prefix, _canonical
    from app.context.domain.provider_sessions import ProviderSessionConflictError

    epoch, rows, receipts, objects, values, originals = _image_epoch_ledger()
    binding = {key: epoch[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}
    binding.update(attempt_id="attempt", owner_generation=4, execution_spec_sha256="d" * 64)
    original_rows = copy.deepcopy(rows)
    locked = 0
    reads = []
    revoked = False

    async def plan(*args, **kwargs):
        return None if revoked else (copy.deepcopy(binding), ["epoch"])

    async def snapshot(*args, **kwargs):
        assert locked
        return copy.deepcopy((epoch, rows, receipts, objects))

    async def images(*args, **kwargs):
        assert locked
        return copy.deepcopy(objects)

    class Conn:
        async def execute(self, query, params):
            class Cursor:
                async def fetchall(self):
                    return copy.deepcopy(rows if "from provider_session_entries" in query else receipts)
                async def fetchone(self):
                    return {"last_sequence": params[1]} if any(r["last_sequence"] == params[1] for r in receipts) else None
            return Cursor()

    @asynccontextmanager
    async def transaction():
        nonlocal locked
        locked += 1
        try:
            yield Conn()
        finally:
            locked -= 1

    class Storage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            obj = next(obj for obj in objects if obj["storage_key"] == storage_key)
            assert max_bytes == obj["size_bytes"]
            reads.append(obj["id"])
            return values[obj["id"]].encode()

        def close(self):
            pass

    async def storage_io(transfer):
        assert locked == 0, "no DB transaction may span object storage I/O"
        return transfer()

    service = provider_sessions.ProviderSessionUseCases(
        PostgresProviderEpochRepository(), image_fields=provider_session_image_fields,
        storage_factory=Storage, storage_io=storage_io,
    )
    monkeypatch.setattr(provider_sessions, "_use_cases", service)
    monkeypatch.setattr(verification, "authorized_epoch_read_plan", plan)
    monkeypatch.setattr(verification, "snapshot_epoch_ledger", snapshot)
    monkeypatch.setattr(verification, "read_epoch_images", images)
    async with provider_session_verification_scope(transaction, tenant_id="tenant", run_id="run", user_id="user"):
        assert reads == [obj["id"] for obj in objects]
        if mutation == "entry":
            rows[1]["entry_json"]["message"]["content"][0]["input"]["project"]["name"] = "changed"
        elif mutation == "reference":
            rows[0]["image_references"][0]["size_bytes"] += 1
        elif mutation == "receipt":
            receipts[0]["batch_sha256"] = "0" * 64
        elif mutation == "counter":
            epoch["structured_bytes"] += 1
        elif mutation == "storage_key":
            objects[0]["storage_key"] = "private/replaced"
        elif mutation == "image_digest":
            objects[0]["sha256"] = "0" * 64
        elif mutation == "generation":
            binding["owner_generation"] += 1
        elif mutation == "attempt":
            binding["attempt_id"] = "replacement"
        elif mutation == "owner":
            binding["user_id"] = "other"
        elif mutation == "revoked":
            revoked = True
        async with transaction() as conn:
            if mutation:
                with pytest.raises(ProviderSessionConflictError):
                    await verified_provider_prefix(conn, epoch=epoch, final_sequence=3)
            else:
                prefix, total = await verified_provider_prefix(conn, epoch=epoch, final_sequence=3)
                assert total == sum(len(_canonical(entry)) for entry in originals)
                assert prefix == original_rows, "copy the original image references, never plaintext image payloads"
                blocks = verification.native_call_blocks("epoch", final_sequence=2)
                assert blocks[0]["input"] == originals[1]["message"]["content"][0]["input"]
                assert verification.native_call_blocks("epoch", final_sequence=1) == []
    assert verification._active_proofs.get() is None
    assert locked == 0
    if mutation is None:
        async with transaction() as conn:
            with pytest.raises(ProviderSessionConflictError, match="verification_required"):
                await verified_provider_prefix(conn, epoch=epoch, final_sequence=3)


def test_epoch_image_proof_requires_original_complete_ack_and_discards_image_bytes():
    from app.context.infrastructure.provider_epoch_verification import finish_epoch_proof
    from app.context.domain.provider_media import ProviderMediaTransfer
    epoch, rows, receipts, objects, values, _ = _image_epoch_ledger()
    media = ProviderMediaTransfer("epoch", 4, 4, objects, values=values, completed=True)
    proof = finish_epoch_proof(epoch, rows, receipts, media)
    assert values[objects[0]["id"]] not in repr(proof)
    receipts[0]["batch_sha256"] = "0" * 64
    with pytest.raises(ProviderSessionContinuityError, match="integrity_mismatch"):
        finish_epoch_proof(epoch, rows, receipts, media)

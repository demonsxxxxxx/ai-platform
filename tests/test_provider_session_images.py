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

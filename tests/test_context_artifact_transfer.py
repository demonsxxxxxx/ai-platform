"""Artifact bytes transfer contract, bounded physical I/O and local publication."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app import storage as storage_module
from app.artifacts.api import ARTIFACT_MAX_FILE_BYTES
from app.context.application.artifact_broker import broker_artifact_transfer
from app.context.application.artifact_transfer import download_artifact_transfer
from app.context.domain.artifact_transfer import (
    ArtifactTransferError,
    ArtifactTransferSource,
)
from app.context.infrastructure.artifact_download import download_artifact_object
from app.context.retrieval import ContextRetrievalDenied
from app.context.transport.artifact_transfer import ArtifactTransferResponse
from app.runtime.sandbox.context_retrieval_client import PlatformContextRetrievalClient
from app.runtime.sandbox.contracts import ContextRetrievalScope
from app.storage import DownloadedObject, ObjectStorage, run_storage_io


REAL_ASYNC_CLIENT = httpx.AsyncClient

POSIX = pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="requires POSIX no-follow directory descriptors",
)
SCOPE = dict(
    tenant_id="tenant-a",
    workspace_id="workspace-a",
    user_id="user-a",
    session_id="session-a",
    run_id="run-a",
    agent_id="agent-a",
)


class GeneratedBody:
    def __init__(self, size, *, error=None):
        self.left = size
        self.requests = []
        self.closed = False
        self.error = error

    def read(self, amount):
        assert 0 < amount <= 65536
        self.requests.append(amount)
        chunk = b"x" * min(self.left, amount)
        self.left -= len(chunk)
        if not chunk and self.error:
            raise self.error
        return chunk

    def close(self):
        self.closed = True


def storage_with_body(body):
    storage = object.__new__(ObjectStorage)
    storage.bucket = "synthetic"
    storage.client = SimpleNamespace(
        get_object=lambda **kwargs: {"Body": body}, close=lambda: None
    )
    return storage


def source(size, *, sha256=None, artifact_id="artifact-a"):
    return ArtifactTransferSource.from_row(
        {
            "size_bytes": size,
            "storage_key": "private/key",
            "label": "report.bin",
            "run_id": "source-run",
            "sha256": sha256,
        },
        artifact_id=artifact_id,
        max_bytes=ARTIFACT_MAX_FILE_BYTES,
    )


class FileStream(httpx.AsyncByteStream):
    def __init__(self, path, *, fail=False):
        self.path, self.fail, self.closed, self.chunks = path, fail, False, []

    async def __aiter__(self):
        with Path(self.path).open("rb") as stream:
            while chunk := stream.read(65536):
                self.chunks.append(len(chunk))
                yield chunk
                await asyncio.sleep(0)
                if self.fail:
                    raise httpx.ReadError("synthetic interrupted stream")

    async def aclose(self):
        self.closed = True


def client_for(
    monkeypatch, workspace, downloaded, artifact_source, *, headers=None, fail=False
):
    streams = []
    real_client = REAL_ASYNC_CLIENT

    def reply(request):
        assert request.headers["accept"] == "application/octet-stream"
        assert request.headers["accept-encoding"] == "identity"
        assert (
            json.loads(request.content)["action"] == "stage_run_artifact_to_workspace"
        )
        stream = FileStream(downloaded.path, fail=fail)
        streams.append(stream)
        return httpx.Response(
            200,
            headers=headers
            or {
                "Content-Type": "application/octet-stream",
                "Content-Length": str(downloaded.size_bytes),
                "X-Context-Artifact": json.dumps(
                    artifact_source.metadata(sha256=downloaded.sha256)
                ),
            },
            stream=stream,
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    return PlatformContextRetrievalClient(
        callback_url="http://synthetic/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a",
        callback_token="synthetic-secret",
        attempt_id="attempt-a",
        scope=ContextRetrievalScope(**SCOPE),
        workspace_root=workspace,
    ), streams


async def stage(client, **arguments):
    return await client.execute(
        "stage_run_artifact_to_workspace",
        SimpleNamespace(**SCOPE),
        {"artifact_id": "artifact-a", **arguments},
    )


@POSIX
@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 20 * 1024 * 1024, ARTIFACT_MAX_FILE_BYTES])
async def test_bounded_storage_to_stream_to_relative_workspace_and_preserve_edits(
    tmp_path, monkeypatch, size
):
    body = GeneratedBody(size)
    downloaded = await download_artifact_object(
        storage_with_body(body), run_storage_io, "private/key", size
    )
    try:
        artifact_source = source(size)
        client, streams = client_for(monkeypatch, tmp_path, downloaded, artifact_source)
        result = await stage(client)
        target = tmp_path / result["workspace_path"]
        assert result == {
            **result,
            "artifact_id": "artifact-a",
            "name": "report.bin",
            "source_run_id": "source-run",
            "workspace_path": "context/artifact-a/report.bin",
            "bytes_staged": size,
            "reused": False,
        }
        assert target.stat().st_size == size
        assert body.closed and max(body.requests) <= 65536
        assert all(n <= 65536 for n in streams[0].chunks) and streams[0].closed
        target.write_bytes(b"edited locally")
        repeated = await stage(client)
        assert repeated["reused"] is True and target.read_bytes() == b"edited locally"
        assert "synthetic-secret" not in str(result) and "private/key" not in str(
            result
        )
        assert not list(tmp_path.rglob("*.part"))
    finally:
        Path(downloaded.path).unlink(missing_ok=True)


def test_artifact_collection_and_restore_share_limit_and_oversize_fails_before_io():
    from app.context.api import ARTIFACT_STAGE_MAX_BYTES
    from app.runtime.sandbox.container_provider import (
        _OPENSANDBOX_COLLECT_MAX_FILE_BYTES,
    )

    assert (
        ARTIFACT_STAGE_MAX_BYTES
        == _OPENSANDBOX_COLLECT_MAX_FILE_BYTES
        == ARTIFACT_MAX_FILE_BYTES
    )
    with pytest.raises(ArtifactTransferError, match="too_large"):
        source(ARTIFACT_MAX_FILE_BYTES + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("size,digest", [(3, None), (4, "0" * 64)])
async def test_source_size_and_historical_digest_mismatch_remove_temp(
    tmp_path, size, digest
):
    path = tmp_path / "download"
    path.write_bytes(b"test")

    async def download(*args):
        return DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)

    with pytest.raises(ArtifactTransferError, match="identity_mismatch"):
        await download_artifact_transfer(source(size, sha256=digest), download)
    assert not path.exists()


@POSIX
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["length", "hash", "encoding", "disconnect", "timeout"]
)
async def test_client_rejects_corrupt_or_interrupted_transfer_without_publishing(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "source"
    path.write_bytes(b"test")
    downloaded = DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    metadata = source(4).metadata(sha256=downloaded.sha256)
    if failure == "hash":
        metadata["sha256"] = "0" * 64
    length = 5 if failure == "length" else 4
    metadata["size_bytes"] = length
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(length),
        "X-Context-Artifact": json.dumps(metadata),
    }
    if failure == "encoding":
        headers["Content-Encoding"] = "gzip"
    client, streams = client_for(
        monkeypatch,
        tmp_path,
        downloaded,
        source(4),
        headers=headers,
        fail=failure == "disconnect",
    )
    if failure == "timeout":

        async def timeout(self):
            yield b"x"
            raise httpx.ReadTimeout("synthetic timeout")

        monkeypatch.setattr(FileStream, "__aiter__", timeout)
    with pytest.raises((ContextRetrievalDenied, RuntimeError)):
        await stage(client)
    assert not (tmp_path / "context/artifact-a/report.bin").exists()
    assert not list(tmp_path.rglob("*.part")) and streams[0].closed


@POSIX
@pytest.mark.asyncio
async def test_concurrent_stage_and_same_name_ids_do_not_overwrite(
    tmp_path, monkeypatch
):
    path = tmp_path / "source"
    path.write_bytes(b"test")
    downloaded = DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    client, _ = client_for(monkeypatch, tmp_path, downloaded, source(4))
    results = await asyncio.gather(stage(client), stage(client))
    assert sorted(r["reused"] for r in results) == [False, True]
    client_b, _ = client_for(
        monkeypatch, tmp_path, downloaded, source(4, artifact_id="artifact-b")
    )
    other = await stage(client_b, artifact_id="artifact-b")
    assert results[0]["workspace_path"] != other["workspace_path"]
    assert (tmp_path / other["workspace_path"]).read_bytes() == b"test"


@POSIX
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "foreign"])
async def test_unowned_or_linked_targets_fail_closed(tmp_path, monkeypatch, kind):
    path = tmp_path / "source"
    path.write_bytes(b"test")
    target = tmp_path / "context/artifact-a/report.bin"
    target.parent.mkdir(parents=True)
    if kind == "symlink":
        target.symlink_to(path)
    elif kind == "hardlink":
        os.link(path, target)
    else:
        target.write_bytes(b"foreign")
    downloaded = DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    client, _ = client_for(monkeypatch, tmp_path, downloaded, source(4))
    with pytest.raises(ContextRetrievalDenied, match="target_conflict"):
        await stage(client)
    assert path.read_bytes() == b"test" and not list(tmp_path.rglob("*.part"))


@pytest.mark.asyncio
async def test_cancelled_physical_download_joins_and_cleans_late_path_and_storage_slot(
    tmp_path,
):
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "late"
    closed = []

    def download(**kwargs):
        entered.set()
        release.wait(5)
        path.write_bytes(b"late")
        return DownloadedObject(str(path), "0" * 64, 4)

    storage = SimpleNamespace(
        download_to_tempfile=download, close=lambda: closed.append(True)
    )
    task = asyncio.create_task(
        download_artifact_object(storage, run_storage_io, "private", 4)
    )
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists() and closed == [True]


@pytest.mark.asyncio
async def test_response_send_failure_cleans_temp_and_closes_reader(tmp_path):
    path = tmp_path / "download"
    path.write_bytes(b"test")
    response = ArtifactTransferResponse(
        source(4), DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    )

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("synthetic failed send")

    async def receive():
        await asyncio.Future()

    with pytest.raises(BaseException):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert not path.exists()


class BrokerRetrieval:
    def _validated_arguments(self, action, arguments):
        return action, arguments

    def _scoped_identity(self, identity, **kwargs):
        return identity

    def _run_identity(self, identity):
        return {k: v for k, v in identity.items() if k != "agent_id"}

    def _required_identifier(self, arguments, name):
        return arguments[name]

    async def _get_artifact_row(self, **scope):
        return {
            "size_bytes": 4,
            "storage_key": "private/key",
            "label": "report.bin",
            "run_id": "source-run",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["none", "snapshot", "lease", "cancel", "scope", "unavailable"]
)
async def test_broker_download_outside_transactions_and_recheck_before_publication(
    tmp_path, change
):
    transaction_depth, admissions, events = 0, 0, []

    @asynccontextmanager
    async def transaction():
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            yield object()
        finally:
            transaction_depth -= 1

    async def authority(conn, **kwargs):
        nonlocal admissions
        admissions += 1
        assert transaction_depth == 1
        status = "cancelling" if admissions > 1 and change == "cancel" else "running"
        if admissions > 1 and change == "unavailable":
            raise ContextRetrievalDenied("context_scope_denied")
        return {
            **SCOPE,
            "status": status,
            "user_id": "other" if admissions > 1 and change == "scope" else "user-a",
        }, {"id": "new" if admissions > 1 and change == "lease" else "lease-a"}

    async def snapshot(conn, **kwargs):
        return {
            "id": "new" if admissions > 1 and change == "snapshot" else "snapshot-a",
            "payload_json": {"context_manifest": {}},
        }

    path = tmp_path / "download"

    async def download(*args):
        assert transaction_depth == 0
        path.write_bytes(b"test")
        return DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)

    async def event(*args, **kwargs):
        events.append(kwargs)

    request = SimpleNamespace(
        **{k: SCOPE[k] for k in ("run_id", "session_id")},
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        action="stage_run_artifact_to_workspace",
        arguments={"artifact_id": "artifact-a"},
    )
    call = broker_artifact_transfer(
        request,
        transaction=transaction,
        lock_authority=authority,
        load_snapshot=snapshot,
        retrieval_factory=lambda conn: BrokerRetrieval(),
        download=download,
        tools=lambda manifest: [request.action],
        record_event=event,
    )
    if change != "none":
        with pytest.raises((ArtifactTransferError, ContextRetrievalDenied)):
            await call
        assert not path.exists() and not events
    else:
        _, downloaded = await call
        assert downloaded.path == str(path) and len(events) == 1 and admissions == 2
        path.unlink()


@POSIX
@pytest.mark.asyncio
@pytest.mark.parametrize("size", [20 * 1024 * 1024, ARTIFACT_MAX_FILE_BYTES])
async def test_real_s3_restore_after_workspace_removal_and_publish_new_version(
    tmp_path, monkeypatch, size
):
    """Optional real MinIO; no mocked storage and no production credentials."""
    endpoint = os.getenv("AI_PLATFORM_ARTIFACT_MINIO_URL")
    if not endpoint:
        pytest.skip("AI_PLATFORM_ARTIFACT_MINIO_URL is not configured")
    from urllib.parse import urlparse

    assert urlparse(endpoint).hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
        "artifact-s3",
    }
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1,artifact-s3")
    settings = SimpleNamespace(
        s3_endpoint_url=endpoint,
        s3_bucket=f"artifact-transfer-test-{uuid4().hex}",
        s3_access_key_id="synthetic-artifact-tests",
        s3_secret_access_key="synthetic-artifact-tests-only",
        s3_region="us-east-1",
    )
    monkeypatch.setattr(storage_module, "get_settings", lambda: settings)
    storage = ObjectStorage()
    storage.ensure_bucket()
    original = tmp_path / "original.bin"
    with original.open("wb") as output:
        for _ in range(size // 65536):
            output.write(b"x" * 65536)
    try:
        stored = storage.put_file(
            storage_key="original",
            source_path=str(original),
            content_type="application/octet-stream",
        )
        for turn in range(2):
            workspace = tmp_path / "attempt"
            downloaded = await download_artifact_object(
                ObjectStorage(), run_storage_io, "original", size
            )
            try:
                client, _ = client_for(monkeypatch, workspace, downloaded, source(size))
                result = await stage(client)
                assert result["bytes_staged"] == size
                if turn == 0:
                    shutil.rmtree(workspace)
                else:
                    target = workspace / result["workspace_path"]
                    with target.open("r+b") as output:
                        output.write(b"new version")
                    storage.put_file(
                        storage_key="new-version",
                        source_path=str(target),
                        content_type="application/octet-stream",
                    )
                    assert (await stage(client))["reused"] is True
            finally:
                Path(downloaded.path).unlink()
        unchanged = await download_artifact_object(
            ObjectStorage(), run_storage_io, "original", size
        )
        try:
            assert unchanged.sha256 == stored.sha256 and unchanged.size_bytes == size
        finally:
            Path(unchanged.path).unlink()
    finally:
        storage.delete_object(storage_key="original")
        storage.delete_object(storage_key="new-version")
        storage.client.delete_bucket(Bucket=storage.bucket)
        storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [20 * 1024 * 1024, ARTIFACT_MAX_FILE_BYTES])
async def test_download_large_objects_is_chunk_bounded_on_all_platforms(size):
    body = GeneratedBody(size)
    downloaded = await download_artifact_object(
        storage_with_body(body), run_storage_io, "private/key", size
    )
    try:
        assert downloaded.size_bytes == size and body.closed
        assert max(body.requests) <= 65536 and len(body.requests) > size // 65536
    finally:
        Path(downloaded.path).unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, OSError("synthetic read failure")])
async def test_physical_storage_oversize_or_read_failure_closes_body_and_removes_owned_temp(
    monkeypatch, tmp_path, error
):
    real_temporary = storage_module.tempfile.NamedTemporaryFile
    monkeypatch.setattr(
        storage_module.tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: real_temporary(dir=tmp_path, **kwargs),
    )
    body = GeneratedBody(1 if error else ARTIFACT_MAX_FILE_BYTES + 1, error=error)
    with pytest.raises((storage_module.ObjectStorageSizeLimitError, OSError)):
        await download_artifact_object(
            storage_with_body(body),
            run_storage_io,
            "private/key",
            ARTIFACT_MAX_FILE_BYTES,
        )
    assert body.closed and not list(tmp_path.iterdir())


@POSIX
@pytest.mark.asyncio
async def test_cancelled_client_stage_removes_owned_partial_and_closes_response(
    tmp_path, monkeypatch
):
    path = tmp_path / "source"
    path.write_bytes(b"test")
    downloaded = DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    waiting = asyncio.Event()

    async def pause(self):
        yield b"x"
        waiting.set()
        await asyncio.Future()

    monkeypatch.setattr(FileStream, "__aiter__", pause)
    client, streams = client_for(monkeypatch, tmp_path, downloaded, source(4))
    task = asyncio.create_task(stage(client))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.rglob("*.part")) and streams[0].closed
    assert not (tmp_path / "context/artifact-a/report.bin").exists()


@pytest.mark.asyncio
async def test_download_timeout_keeps_owner_until_late_completion(tmp_path):
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "late"
    closed = []

    def operation(**kwargs):
        entered.set()
        release.wait(5)
        path.write_bytes(b"test")
        return DownloadedObject(str(path), "0" * 64, 4)

    async def io(*args, **kwargs):
        return await run_storage_io(*args, timeout_seconds=0.01, **kwargs)

    task = asyncio.create_task(
        download_artifact_object(
            SimpleNamespace(
                download_to_tempfile=operation, close=lambda: closed.append(True)
            ),
            io,
            "private/key",
            4,
        )
    )
    await asyncio.to_thread(entered.wait, 5)
    await asyncio.sleep(0.05)
    assert not task.done() and not closed
    release.set()
    with pytest.raises(storage_module.StorageIOTimeoutError):
        await task
    assert not path.exists() and closed == [True]


@POSIX
@pytest.mark.asyncio
async def test_production_response_asgi_to_actual_executor_client_wiring(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI, Request

    app = FastAPI()
    paths = []

    @app.post("/context-retrieval")
    async def endpoint(request: Request):
        payload = await request.json()
        assert (
            payload["attempt_id"] == "attempt-a"
            and payload["arguments"]["artifact_id"] == "artifact-a"
        )
        assert request.headers["accept"] == "application/octet-stream"
        body = GeneratedBody(20 * 1024 * 1024)
        downloaded = await download_artifact_object(
            storage_with_body(body), run_storage_io, "private/key", 20 * 1024 * 1024
        )
        paths.append(Path(downloaded.path))
        return ArtifactTransferResponse(source(20 * 1024 * 1024), downloaded)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(
            transport=httpx.ASGITransport(app=app), **kwargs
        ),
    )
    client = PlatformContextRetrievalClient(
        callback_url="http://synthetic/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a",
        callback_token="synthetic",
        attempt_id="attempt-a",
        scope=ContextRetrievalScope(**SCOPE),
        workspace_root=tmp_path,
    )
    result = await stage(client)
    assert (tmp_path / result["workspace_path"]).stat().st_size == 20 * 1024 * 1024
    assert paths and all(not path.exists() for path in paths)


@POSIX
@pytest.mark.asyncio
async def test_symlinked_parent_cannot_escape_workspace(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "context").symlink_to(outside, target_is_directory=True)
    path = tmp_path / "source"
    path.write_bytes(b"test")
    downloaded = DownloadedObject(str(path), hashlib.sha256(b"test").hexdigest(), 4)
    client, streams = client_for(monkeypatch, workspace, downloaded, source(4))
    with pytest.raises(RuntimeError):
        await stage(client)
    assert not list(outside.iterdir()) and streams[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "context_artifact_identity_mismatch",
        "context_artifact_unavailable",
        "raw key=secret",
    ],
)
async def test_stream_client_preserves_only_safe_server_error_details(
    tmp_path, monkeypatch, reason
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, json={"detail": reason})
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    )
    client = PlatformContextRetrievalClient(
        callback_url="http://synthetic/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a",
        callback_token="synthetic",
        attempt_id="attempt-a",
        scope=ContextRetrievalScope(**SCOPE),
        workspace_root=tmp_path,
    )
    with pytest.raises(ContextRetrievalDenied) as error:
        await stage(client)
    assert str(error.value) == (
        reason if reason.startswith("context_artifact_") else "context_scope_denied"
    )
    assert not list(tmp_path.iterdir())


@POSIX
@pytest.mark.parametrize("replacement_timing", ["after-stat", "before-link"])
def test_artifact_publication_rejects_unlinked_verified_fd_without_publishing(
    tmp_path, monkeypatch, replacement_timing
):
    from app.context.domain import artifact_workspace

    owner = artifact_workspace.ArtifactWorkspace()
    real_stat, real_link = os.stat, os.link
    replacement = tmp_path / "unverified"
    replacement.write_bytes(b"unchecked content")
    written = None
    replaced = False

    def replace_part(path, directory_fd):
        nonlocal replaced
        assert not written.closed
        os.replace(replacement, path, dst_dir_fd=directory_fd)
        replaced = True

    def stat_then_replace(path, *args, **kwargs):
        node = real_stat(path, *args, **kwargs)
        if str(path).endswith(".part") and not replaced and replacement_timing == "after-stat":
            replace_part(path, kwargs["dir_fd"])
        return node

    def link_verified_fd(source, destination, **kwargs):
        assert source == f"/proc/self/fd/{written.fileno()}"
        assert kwargs["follow_symlinks"] is True and not written.closed
        if replacement_timing == "before-link":
            replace_part(target.temporary, kwargs["dst_dir_fd"])
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "stat", stat_then_replace)
    monkeypatch.setattr(os, "link", link_verified_fd)
    with owner.target(
        tmp_path, "artifact-a", "report.bin", source_identity=("verified-source",)
    ) as target:
        with pytest.raises(ArtifactTransferError, match="safe_filesystem_required"):
            with target.output() as output:
                written = output
                output.write(b"verified bytes")
        assert target.key not in owner._published
    assert replaced and written.closed
    assert not (tmp_path / "context/artifact-a/report.bin").exists()
    assert not list(tmp_path.rglob("*.part"))


@POSIX
def test_artifact_publication_preserves_postlink_concurrent_target_replacement(
    tmp_path, monkeypatch
):
    from app.context.domain.artifact_workspace import ArtifactWorkspace

    owner = ArtifactWorkspace()
    real_link = os.link
    concurrent = tmp_path / "concurrent"
    concurrent.write_bytes(b"someone else's target")
    published = tmp_path / "context/artifact-a/report.bin"
    written = None

    def link_then_replace(source, destination, **kwargs):
        assert source == f"/proc/self/fd/{written.fileno()}" and not written.closed
        real_link(source, destination, **kwargs)
        assert published.read_bytes() == b"verified bytes"
        os.replace(concurrent, destination, dst_dir_fd=kwargs["dst_dir_fd"])

    monkeypatch.setattr(os, "link", link_then_replace)
    with owner.target(
        tmp_path, "artifact-a", "report.bin", source_identity=("verified-source",)
    ) as target:
        with target.output() as output:
            written = output
            output.write(b"verified bytes")
        assert owner._published[target.key] == ("verified-source",)
        assert target.reuse() == len(b"someone else's target")
    assert written.closed and published.read_bytes() == b"someone else's target"
    assert not list(tmp_path.rglob("*.part"))


@POSIX
def test_artifact_publication_fails_closed_without_procfd_link(tmp_path, monkeypatch):
    from app.context.domain.artifact_workspace import ArtifactWorkspace

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("synthetic unavailable procfd")

    monkeypatch.setattr(os, "link", unavailable)
    owner = ArtifactWorkspace()
    with owner.target(
        tmp_path, "artifact-a", "report.bin", source_identity=("verified-source",)
    ) as target:
        with pytest.raises(ArtifactTransferError, match="safe_filesystem_required"):
            with target.output() as output:
                output.write(b"verified bytes")
        assert target.key not in owner._published
    assert not (tmp_path / "context/artifact-a/report.bin").exists()
    assert not list(tmp_path.rglob("*.part"))


@POSIX
def test_artifact_publication_keeps_preexisting_target_no_clobber(tmp_path):
    from app.context.domain.artifact_workspace import ArtifactWorkspace

    owner = ArtifactWorkspace()
    with owner.target(
        tmp_path, "artifact-a", "report.bin", source_identity=("verified-source",)
    ) as target:
        path = tmp_path / "context/artifact-a/report.bin"
        path.write_bytes(b"existing edited target")
        with pytest.raises(FileExistsError):
            with target.output() as output:
                output.write(b"verified bytes")
        assert path.read_bytes() == b"existing edited target"
        assert target.key not in owner._published
    assert not list(tmp_path.rglob("*.part"))


@POSIX
def test_artifact_publication_close_failure_never_removes_final_target(tmp_path, monkeypatch):
    from app.context.domain.artifact_workspace import ArtifactWorkspace

    real_fdopen = os.fdopen

    class FailingClose:
        def __init__(self, fd, mode):
            self.output = real_fdopen(fd, mode)

        def __getattr__(self, name):
            return getattr(self.output, name)

        def close(self):
            self.output.close()
            raise OSError("synthetic close failure after verified publication")

    monkeypatch.setattr(os, "fdopen", FailingClose)
    owner = ArtifactWorkspace()
    with owner.target(
        tmp_path, "artifact-a", "report.bin", source_identity=("verified-source",)
    ) as target:
        with pytest.raises(OSError, match="synthetic close failure"):
            with target.output() as output:
                output.write(b"verified bytes")
        assert (tmp_path / "context/artifact-a/report.bin").read_bytes() == b"verified bytes"
        assert target.reuse() == len(b"verified bytes")
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.asyncio
async def test_inline_artifact_without_download_port_has_safe_unavailable_error(monkeypatch):
    from app.context.retrieval import ContextRetrievalAuthority

    async def row(conn, **kwargs):
        return {"size_bytes": 4, "storage_key": "private/key", "label": "report.bin"}

    monkeypatch.setattr("app.context.infrastructure.sources_postgres.get_scoped_context_artifact", row)
    authority = ContextRetrievalAuthority.for_connection(object(), object())
    with pytest.raises(ContextRetrievalDenied, match="context_artifact_unavailable"):
        await authority.stage_run_artifact_to_workspace(
            **{k: v for k, v in SCOPE.items() if k != "agent_id"},
            artifact_id="artifact-a", workspace_root="unused",
        )


@POSIX
@pytest.mark.asyncio
@pytest.mark.parametrize("composition", ["connection", "worker-bootstrap"])
async def test_inline_artifact_explicit_physical_download_port(tmp_path, monkeypatch, composition):
    from functools import partial
    from app.bootstrap import context as context_bootstrap
    from app.context.retrieval import ContextRetrievalAuthority

    body = GeneratedBody(4)
    storage = storage_with_body(body)
    async def row(conn, **kwargs):
        assert kwargs["artifact_id"] == "artifact-a"
        return {"size_bytes": 4, "storage_key": "private/key", "label": "report.bin"}
    @asynccontextmanager
    async def transaction():
        yield object()
    monkeypatch.setattr("app.context.infrastructure.sources_postgres.get_scoped_context_artifact", row)
    if composition == "connection":
        authority = ContextRetrievalAuthority.for_connection(
            object(), storage, artifact_download=partial(
                download_artifact_object, storage, run_storage_io, close_storage=False,
            ),
        )
    else:
        monkeypatch.setattr(context_bootstrap, "ObjectStorage", lambda: storage)
        monkeypatch.setattr(context_bootstrap, "transaction", transaction)
        authority = context_bootstrap.worker_context_retrieval_authority(tmp_path)
    result = await authority.stage_run_artifact_to_workspace(
        **{k: v for k, v in SCOPE.items() if k != "agent_id"}, artifact_id="artifact-a",
        workspace_root=str(tmp_path),
    )
    assert (tmp_path / result["workspace_path"]).read_bytes() == b"xxxx"
    assert body.closed and body.requests and max(body.requests) <= 65536

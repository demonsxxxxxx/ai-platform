from pathlib import Path

import pytest

from app.execution.infrastructure.original_file_delivery import (
    OriginalFileDelivery,
    OriginalFileDeliveryError,
)


@pytest.mark.asyncio
async def test_original_file_delivery_writes_under_bound_inputs_and_returns_relative_ref(tmp_path: Path):
    delivery = OriginalFileDelivery(
        cwd=tmp_path,
        tenant_id="tenant",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        run_id="run",
        attempt_id="attempt",
        lease_id="lease",
    )
    result = await delivery(
        {
            "format": "profile-drive.original-file.v1",
            "source": "profile",
            "path": "remote/private.docx",
            "name": "private.docx",
            "size_bytes": 5,
            "content_type": "application/octet-stream",
        },
        b"bytes",
    )
    target = tmp_path / result["workspace_path"]
    assert result["workspace_path"].startswith("inputs/")
    assert target.read_bytes() == b"bytes"
    assert target.stat().st_mode & 0o222 == 0
    assert str(tmp_path) not in result["workspace_path"]
    assert "remote" not in result["workspace_path"]


@pytest.mark.asyncio
async def test_original_file_delivery_requires_lease_binding(tmp_path: Path):
    delivery = OriginalFileDelivery(
        cwd=tmp_path, tenant_id="tenant", workspace_id="workspace", user_id="user",
        session_id="session", run_id="run", attempt_id="attempt",
    )
    with pytest.raises(OriginalFileDeliveryError, match="original_file_binding_invalid"):
        await delivery({"name": "file.bin", "size_bytes": 0}, b"")


@pytest.mark.asyncio
async def test_original_file_delivery_rejects_mismatch_and_cleans_owned_files(tmp_path: Path):
    delivery = OriginalFileDelivery(
        cwd=tmp_path,
        tenant_id="tenant",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        run_id="run",
        attempt_id="attempt",
        lease_id="lease",
    )
    metadata = {
        "format": "profile-drive.original-file.v1",
        "source": "public",
        "path": "public/empty.bin",
        "name": "empty.bin",
        "size_bytes": 1,
        "content_type": "application/octet-stream",
    }
    with pytest.raises(OriginalFileDeliveryError, match="original_file_size_mismatch"):
        await delivery(metadata, b"")
    metadata["size_bytes"] = 5
    await delivery(metadata, b"bytes")
    assert list((tmp_path / "inputs").rglob("*"))
    await delivery.cleanup()
    assert not list((tmp_path / "inputs" / ".mcp-original").rglob("*"))


@pytest.mark.asyncio
async def test_original_file_delivery_cleans_agent_nested_files_and_preserves_outside_sentinel(tmp_path: Path):
    delivery = OriginalFileDelivery(
        cwd=tmp_path,
        tenant_id="tenant",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        run_id="run",
        attempt_id="attempt",
        lease_id="lease",
    )
    result = await delivery(
        {"name": "nested.bin", "size_bytes": 5},
        b"bytes",
    )
    owned = tmp_path / result["workspace_path"]
    outside = tmp_path / "outside-sentinel.txt"
    outside.write_text("keep", encoding="utf-8")
    (owned.parent / "agent-dir").mkdir()
    (owned.parent / "agent-dir" / "agent-file.txt").write_text("agent", encoding="utf-8")
    await delivery.cleanup()
    assert not owned.parent.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert delivery.cleanup_incomplete is False


@pytest.mark.asyncio
async def test_original_file_delivery_retains_cleanup_debt_on_failure(tmp_path: Path, monkeypatch):
    delivery = OriginalFileDelivery(
        cwd=tmp_path, tenant_id="tenant", workspace_id="workspace", user_id="user",
        session_id="session", run_id="run", attempt_id="attempt", lease_id="lease",
    )
    result = await delivery({"name": "debt.bin", "size_bytes": 1}, b"x")
    owned = tmp_path / result["workspace_path"]
    original_remove = delivery._remove_owned_tree
    monkeypatch.setattr(delivery, "_remove_owned_tree", lambda _path: (_ for _ in ()).throw(OSError("locked")))
    assert await delivery.cleanup() is False
    assert delivery.cleanup_incomplete is True
    monkeypatch.setattr(delivery, "_remove_owned_tree", original_remove)
    assert await delivery.cleanup() is True
    assert not owned.parent.exists()


@pytest.mark.asyncio
async def test_original_file_delivery_rejects_symlinked_inputs(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    inputs = tmp_path / "inputs"
    try:
        inputs.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    delivery = OriginalFileDelivery(
        cwd=tmp_path,
        tenant_id="tenant",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        run_id="run",
        attempt_id="attempt",
        lease_id="lease",
    )
    with pytest.raises(OriginalFileDeliveryError):
        await delivery(
            {"name": "x.bin", "size_bytes": 0},
            b"",
        )
    assert not list(outside.iterdir())

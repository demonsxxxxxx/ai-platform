import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.sandbox.domain.host_bind as host_bind

from app.runtime.sandbox.contracts import SandboxRuntimeRequest, WorkspaceLease
from app.sandbox.domain.host_bind import (
    OpenSandboxHostBindError,
    read_host_bind_control_file,
    resolve_opensandbox_host_bind_source,
    snapshot_host_bind_delivery_files,
    validate_host_bind_delivery_files,
)
from app.sandbox import api as sandbox_api
from app.sandbox.domain import workspace_policy


def test_retired_opensandbox_files_api_collection_contract_is_absent():
    for module in (sandbox_api, workspace_policy):
        assert not hasattr(module, "opensandbox_collection_entry")
        assert not hasattr(module, "opensandbox_listing_matches_file")
    assert not hasattr(sandbox_api, "opensandbox_delivery_paths")


def _request(**overrides) -> SandboxRuntimeRequest:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "input_message": "hello",
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "model": "deepseek-v4-flash",
        "permissions": ["sandbox.execute"],
        "callback_url": "http://callback",
        "callback_token_id": "cbt:run-a:attempt-a",
    }
    values.update(overrides)
    return SandboxRuntimeRequest(**values)


def _attempt(root: Path, request: SandboxRuntimeRequest) -> Path:
    return (
        root
        / "tenants"
        / request.tenant_id
        / "workspaces"
        / request.workspace_id
        / "users"
        / request.user_id
        / "sessions"
        / request.session_id
        / "runs"
        / request.run_id
        / "attempts"
        / request.attempt_id
    )


def _workspace_lease(root: Path, request: SandboxRuntimeRequest) -> WorkspaceLease:
    attempt = _attempt(root, request)
    workspace = attempt / "workspace"
    inputs = workspace / "inputs"
    logs = attempt / "logs"
    return WorkspaceLease(
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        session_id=request.session_id,
        run_id=request.run_id,
        host_root=str(attempt),
        workspace_host_path=str(workspace),
        workspace_container_path="/workspace",
        inputs_host_path=str(inputs),
        logs_host_path=str(logs),
    )


def test_resolves_only_the_exact_attempt_workspace(tmp_path):
    root = tmp_path / "workspaces"
    request = _request()
    lease = _workspace_lease(root, request)

    assert resolve_opensandbox_host_bind_source(root, request, lease) == Path(
        lease.workspace_host_path
    )

    mismatched = lease.model_copy(update={"workspace_host_path": lease.inputs_host_path})
    with pytest.raises(OpenSandboxHostBindError, match="authoritative Attempt workspace"):
        resolve_opensandbox_host_bind_source(root, request, mismatched)

    with pytest.raises(OpenSandboxHostBindError, match="scope"):
        resolve_opensandbox_host_bind_source(
            root,
            _request(user_id="other-user"),
            lease,
        )


def test_rejects_symlinked_attempt_workspace(tmp_path):
    root = tmp_path / "workspaces"
    request = _request(
        tenant_id="t",
        workspace_id="w",
        user_id="u",
        session_id="s",
        run_id="r",
        attempt_id="a",
    )
    attempt = _attempt(root, request)
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir(parents=True)
    attempt.mkdir(parents=True)
    (attempt / "logs").mkdir()
    try:
        (attempt / "workspace").symlink_to(real_workspace, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    lease = WorkspaceLease(
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        session_id=request.session_id,
        run_id=request.run_id,
        host_root=str(attempt),
        workspace_host_path=str(attempt / "workspace"),
        workspace_container_path="/workspace",
        inputs_host_path=str(attempt / "workspace" / "inputs"),
        logs_host_path=str(attempt / "logs"),
    )

    with pytest.raises(OpenSandboxHostBindError, match="symlinked"):
        resolve_opensandbox_host_bind_source(
            root,
            request,
            lease,
            require_existing=True,
        )


@pytest.mark.skipif(
    os.name != "posix"
    or not getattr(os, "O_DIRECTORY", None)
    or os.open not in os.supports_dir_fd,
    reason="requires POSIX no-follow descriptor traversal",
)
def test_snapshots_delivery_files_outside_the_mounted_workspace(tmp_path):
    attempt = tmp_path / "attempt"
    workspace = attempt / "workspace"
    output = workspace / "outputs" / "delivery"
    output.mkdir(parents=True)
    for directory in (attempt, workspace, workspace / "outputs", output):
        directory.chmod(0o700)
    source = output / "report.txt"
    source.write_text("first", encoding="utf-8")
    source.chmod(0o600)
    root_manifest = workspace / ".manifest.json"
    root_manifest.write_text("user manifest", encoding="utf-8")
    root_manifest.chmod(0o600)
    stale_directory = attempt / f".{host_bind._SNAPSHOT_DIRECTORY}.tmp-{'a' * 32}"
    stale_directory.mkdir(mode=0o700)
    (stale_directory / "private-copy.txt").write_text("stale", encoding="utf-8")
    stale_manifest = attempt / f".{host_bind._SNAPSHOT_MANIFEST}.tmp-{'b' * 32}"
    stale_manifest.write_text("stale", encoding="utf-8")
    stale_manifest.chmod(0o600)

    snapshot = snapshot_host_bind_delivery_files(
        workspace,
        attempt,
        ["outputs/delivery/report.txt", ".manifest.json"],
        max_files=3,
        max_file_bytes=32,
        max_total_bytes=64,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
    )
    source.write_text("second", encoding="utf-8")

    assert snapshot.parent == attempt
    assert not snapshot.is_relative_to(workspace)
    assert (snapshot / "outputs" / "delivery" / "report.txt").read_text(
        encoding="utf-8"
    ) == "first"
    assert (snapshot / ".manifest.json").read_text(encoding="utf-8") == "user manifest"
    assert not stale_directory.exists()
    assert not stale_manifest.exists()
    assert snapshot_host_bind_delivery_files(
        workspace,
        attempt,
        ["outputs/delivery/report.txt", ".manifest.json"],
        max_files=3,
        max_file_bytes=32,
        max_total_bytes=64,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
    ) == snapshot


@pytest.mark.skipif(
    os.name != "posix"
    or not getattr(os, "O_DIRECTORY", None)
    or os.open not in os.supports_dir_fd,
    reason="requires POSIX no-follow descriptor traversal",
)
def test_snapshot_publication_is_serialized_per_attempt(tmp_path, monkeypatch):
    attempt = tmp_path / "attempt"
    workspace = attempt / "workspace"
    output = workspace / "outputs" / "delivery"
    output.mkdir(parents=True)
    for directory in (attempt, workspace, workspace / "outputs", output):
        directory.chmod(0o700)
    (output / "report.txt").write_text("first", encoding="utf-8")
    (output / "report.txt").chmod(0o600)

    original = host_bind._snapshot_host_bind_delivery_files_unlocked
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def wrapped(*args, **kwargs):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        try:
            return original(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(host_bind, "_snapshot_host_bind_delivery_files_unlocked", wrapped)

    def collect(_index):
        return snapshot_host_bind_delivery_files(
            workspace,
            attempt,
            ["outputs/delivery/report.txt"],
            max_files=2,
            max_file_bytes=32,
            max_total_bytes=32,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshots = list(executor.map(collect, range(2)))

    assert maximum_active == 1
    assert snapshots[0] == snapshots[1]


@pytest.mark.skipif(
    os.name != "posix"
    or not getattr(os, "O_DIRECTORY", None)
    or os.open not in os.supports_dir_fd,
    reason="requires POSIX no-follow descriptor traversal",
)
def test_validates_declared_host_bind_files_without_following_links(tmp_path):
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "delivery"
    output.mkdir(parents=True)
    artifact = output / "report.txt"
    artifact.write_text("result", encoding="utf-8")
    artifact.chmod(0o600)

    validated = validate_host_bind_delivery_files(
        workspace,
        ["outputs/delivery/report.txt"],
        max_files=2,
        max_file_bytes=32,
        max_total_bytes=32,
    )

    assert [(item.relative_path, item.size_bytes) for item in validated] == [
        ("outputs/delivery/report.txt", 6)
    ]

    linked = output / "linked.txt"
    try:
        linked.symlink_to(artifact)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    with pytest.raises(OpenSandboxHostBindError, match="unavailable"):
        validate_host_bind_delivery_files(
            workspace,
            ["outputs/delivery/linked.txt"],
            max_files=2,
            max_file_bytes=32,
            max_total_bytes=32,
        )


@pytest.mark.skipif(
    os.name != "posix"
    or not getattr(os, "O_DIRECTORY", None)
    or os.open not in os.supports_dir_fd,
    reason="requires POSIX no-follow descriptor traversal",
)
def test_reads_only_the_fixed_bounded_control_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / ".ai-platform-opensandbox-lease.json"
    marker.write_text('{"attempt_id":"attempt-a"}', encoding="utf-8")
    marker.chmod(0o600)

    assert read_host_bind_control_file(
        workspace,
        ".ai-platform-opensandbox-lease.json",
    ) == '{"attempt_id":"attempt-a"}'

    with pytest.raises(OpenSandboxHostBindError, match="name"):
        read_host_bind_control_file(workspace, "../outside")

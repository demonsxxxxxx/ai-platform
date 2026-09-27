import errno
import json
import os
import stat
import struct
import subprocess
from pathlib import Path

import pytest

from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.runtime.sandbox.workspace_manager import PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS


def request(**overrides) -> SandboxRuntimeRequest:
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
        "browser_enabled": True,
        "model": "deepseek-v4-flash",
        "permissions": ["sandbox.execute"],
        "callback_url": "http://callback",
        "callback_token_id": "cbt_run_a",
    }
    values.update(overrides)
    return SandboxRuntimeRequest(**values)


def expected_run_root(root: Path) -> Path:
    return (
        root
        / "tenants"
        / "tenant-a"
        / "workspaces"
        / "workspace-a"
        / "users"
        / "user-a"
        / "sessions"
        / "session-a"
        / "runs"
        / "run-a"
        / "attempts"
        / "attempt-a"
    )


def test_prepare_creates_platform_workspace_namespace(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request())

    run_root = expected_run_root(tmp_path)
    meta_path = run_root / "runtime" / "meta.json"

    assert Path(lease.host_root) == run_root
    assert (run_root / "workspace").is_dir()
    assert (run_root / "workspace" / "inputs").is_dir()
    assert (run_root / "workspace" / "outputs" / "delivery").is_dir()
    assert (run_root / "workspace" / ".ai-platform").is_dir()
    claude_instructions = run_root / "workspace" / "CLAUDE.md"
    assert claude_instructions.read_text(encoding="utf-8") == PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS
    assert "默认使用简体中文回复用户" in PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS
    assert stat.S_IMODE(claude_instructions.stat().st_mode) == 0o444
    assert (run_root / "logs").is_dir()
    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "sandbox_mode": "ephemeral",
        "browser_enabled": True,
    }
    if os.name == "posix":
        for directory in (
            run_root,
            run_root / "workspace",
            run_root / "workspace" / "inputs",
            run_root / "workspace" / "outputs",
            run_root / "workspace" / "outputs" / "delivery",
            run_root / "workspace" / ".ai-platform",
            run_root / "logs",
            run_root / "runtime",
        ):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o755
        assert stat.S_IMODE(meta_path.stat().st_mode) == 0o644


def test_prepare_creates_missing_workspace_root(tmp_path):
    root = tmp_path / "missing-root"

    lease = SandboxWorkspaceManager(root=root).prepare(request())

    assert Path(lease.host_root).is_dir()
    assert root.is_dir()


def test_prepare_preserves_existing_workspace_root_mode(tmp_path):
    if os.name != "posix":
        pytest.skip("requires POSIX mode semantics")

    root = tmp_path / "existing-root"
    root.mkdir(mode=0o700)

    SandboxWorkspaceManager(root=root).prepare(request())

    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_prepare_removes_inherited_writable_default_acl(tmp_path):
    if os.name != "posix" or not hasattr(os, "setxattr"):
        pytest.skip("requires POSIX extended ACL support")

    root_parent = tmp_path / "acl-parent"
    root_parent.mkdir()
    acl_entries = (
        (0x01, 0o7, 0),  # ACL_USER_OBJ
        (0x02, 0o7, os.getuid() + 1),  # ACL_USER
        (0x04, 0o0, 0),  # ACL_GROUP_OBJ
        (0x10, 0o7, 0),  # ACL_MASK
        (0x20, 0o0, 0),  # ACL_OTHER
    )
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, permissions, identifier)
        for tag, permissions, identifier in acl_entries
    )
    try:
        os.setxattr(root_parent, "system.posix_acl_default", acl, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            pytest.skip("filesystem does not support POSIX default ACLs")
        raise

    root = root_parent / "managed"
    manager = SandboxWorkspaceManager(root=root)
    lease = manager.prepare(request())
    delivery = Path(lease.workspace_host_path) / "outputs" / "delivery"
    created = delivery / "sandbox-created.txt"
    previous_umask = os.umask(0o022)
    try:
        created.write_text("synthetic output", encoding="utf-8")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(delivery.stat().st_mode) == 0o755
    assert stat.S_IMODE(created.stat().st_mode) == 0o644
    for directory in (root, Path(lease.host_root), delivery):
        for name in ("system.posix_acl_default", "system.posix_acl_access"):
            with pytest.raises(OSError) as exc_info:
                os.getxattr(directory, name, follow_symlinks=False)
            assert exc_info.value.errno == errno.ENODATA


def test_prepare_rejects_symlinked_workspace_component(tmp_path):
    if os.name != "posix":
        pytest.skip("requires POSIX symlink semantics")

    root = tmp_path / "managed"
    outside = tmp_path / "outside"
    (root / "tenants" / "tenant-a").mkdir(parents=True)
    outside.mkdir()
    (root / "tenants" / "tenant-a" / "workspaces").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        SandboxWorkspaceManager(root=root).prepare(request())
    assert not (outside / "workspace-a").exists()


@pytest.mark.parametrize(
    "provider",
    ["docker", "Docker", " docker ", "opensandbox", "OpenSandbox", " opensandbox "],
)
def test_prepare_fails_closed_for_windows_host_bind(tmp_path, monkeypatch, provider):
    if os.name != "nt":
        pytest.skip("requires the Windows host-bind fallback")

    class StubSettings:
        sandbox_container_provider = provider

    monkeypatch.setattr("app.runtime.sandbox.workspace_manager.get_settings", lambda: StubSettings())

    with pytest.raises(OSError, match="secure host-bind"):
        SandboxWorkspaceManager(root=tmp_path).prepare(request())


def test_prepare_rejects_windows_junction_root(tmp_path):
    if os.name != "nt":
        pytest.skip("requires Windows junction semantics")

    target = tmp_path / "outside"
    target.mkdir()
    junction = tmp_path / "workspace-root"
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(OSError):
            SandboxWorkspaceManager(root=junction).prepare(request())
        assert not (target / "tenants").exists()
    finally:
        junction.rmdir()


def test_prepare_rejects_windows_junction_parent(tmp_path):
    if os.name != "nt":
        pytest.skip("requires Windows junction semantics")

    target = tmp_path / "outside"
    (target / "managed").mkdir(parents=True)
    junction_parent = tmp_path / "workspace-parent"
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction_parent), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(OSError):
            SandboxWorkspaceManager(root=junction_parent / "managed").prepare(request())
        assert not (target / "managed" / "tenants").exists()
    finally:
        junction_parent.rmdir()


def test_prepare_restores_platform_claude_instructions(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)
    manager.prepare(request())
    claude_instructions = expected_run_root(tmp_path) / "workspace" / "CLAUDE.md"
    claude_instructions.chmod(0o600)
    claude_instructions.write_text("overridden", encoding="utf-8")

    manager.prepare(request())

    assert claude_instructions.read_text(encoding="utf-8") == PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS
    assert stat.S_IMODE(claude_instructions.stat().st_mode) == 0o444


def test_workspace_lease_paths_match_platform_namespace(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request(browser_enabled=False))

    run_root = expected_run_root(tmp_path)
    assert Path(lease.host_root) == run_root
    assert Path(lease.workspace_host_path) == run_root / "workspace"
    assert Path(lease.inputs_host_path) == run_root / "workspace" / "inputs"
    assert Path(lease.logs_host_path) == run_root / "logs"
    assert lease.workspace_container_path == "/workspace"


def test_same_run_attempts_receive_distinct_workspace_authority(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    first = manager.prepare(request(attempt_id="attempt-a"))
    second = manager.prepare(request(attempt_id="attempt-b"))

    assert first.workspace_host_path != second.workspace_host_path
    assert Path(first.workspace_host_path).parts[-3:] == ("attempts", "attempt-a", "workspace")
    assert Path(second.workspace_host_path).parts[-3:] == ("attempts", "attempt-b", "workspace")


def test_user_visible_payload_hides_host_root_and_uses_workspace_mount(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request())

    payload = lease.user_visible_payload()
    assert str(tmp_path) not in json.dumps(payload)
    assert payload == {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


def test_explicit_posix_root_does_not_load_settings(monkeypatch, tmp_path):
    def fail_settings_load():
        raise AssertionError("explicit POSIX root must not load settings")

    monkeypatch.setattr("app.runtime.sandbox.workspace_manager.os.name", "posix")
    monkeypatch.setattr("app.runtime.sandbox.workspace_manager.get_settings", fail_settings_load)

    assert str(SandboxWorkspaceManager(root=tmp_path).root) == tmp_path.as_posix()


def test_manager_uses_settings_root_when_root_not_provided(monkeypatch, tmp_path):
    class StubSettings:
        sandbox_workspace_root = str(tmp_path / "configured-root")

    monkeypatch.setattr("app.runtime.sandbox.workspace_manager.get_settings", lambda: StubSettings())

    manager = SandboxWorkspaceManager()

    assert manager.root == tmp_path / "configured-root"

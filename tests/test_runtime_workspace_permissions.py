import os
import stat

import pytest

from app.runtime.sandbox.workspace_permissions import (
    RUNTIME_GID,
    RUNTIME_UID,
    WorkspacePermissionError,
    _drop_runtime_privileges,
    _probe_runtime_workspace,
    _validate_namespace_directory_identity,
    initialize_runtime_workspace,
)


def _attempt_workspace(root):
    attempt = root.joinpath(
        "tenants",
        "tenant-a",
        "workspaces",
        "workspace-a",
        "users",
        "user-a",
        "sessions",
        "session-a",
        "runs",
        "run-a",
        "attempts",
        "attempt-a",
    )
    workspace = attempt / "workspace"
    workspace.mkdir(parents=True)
    return attempt, workspace


def _use_current_runtime_identity(monkeypatch, root):
    from app.runtime.sandbox import workspace_permissions

    monkeypatch.setattr(workspace_permissions, "RUNTIME_WORKSPACE_ROOT", root)
    monkeypatch.setattr(workspace_permissions, "RUNTIME_UID", os.geteuid())
    monkeypatch.setattr(workspace_permissions, "RUNTIME_GID", os.getegid())
    monkeypatch.setattr(workspace_permissions, "_drop_runtime_privileges", lambda: None)


@pytest.mark.skipif(os.name != "posix", reason="workspace initializer requires POSIX descriptors")
def test_initializer_prepares_namespace_and_ignores_workspace_contents(tmp_path, monkeypatch):
    from app.runtime.sandbox import workspace_permissions

    attempt, workspace = _attempt_workspace(tmp_path)
    payload = workspace / "payload.txt"
    payload.write_text("retained\n", encoding="utf-8")
    payload.chmod(0o666)
    hard_link = workspace / "payload-copy.txt"
    os.link(payload, hard_link)
    same_directory_link = workspace / "payload-link"
    same_directory_link.symlink_to(payload.name)
    opaque_directory = workspace / "opaque"
    opaque_directory.mkdir()
    opaque_directory.chmod(0o000)
    workspace.chmod(0o777)
    retained = {
        workspace: stat.S_IMODE(workspace.stat().st_mode),
        payload: stat.S_IMODE(payload.stat().st_mode),
        opaque_directory: stat.S_IMODE(opaque_directory.stat().st_mode),
    }
    workspace_inode = workspace.stat().st_ino
    real_scandir = os.scandir

    def reject_workspace_scan(path):
        if isinstance(path, int) and os.fstat(path).st_ino == workspace_inode:
            pytest.fail("initializer must not enumerate attempt workspace contents")
        return real_scandir(path)

    _use_current_runtime_identity(monkeypatch, tmp_path)
    monkeypatch.setattr(workspace_permissions.os, "scandir", reject_workspace_scan)

    try:
        initialize_runtime_workspace()

        assert payload.read_text(encoding="utf-8") == "retained\n"
        assert os.readlink(same_directory_link) == payload.name
        assert os.stat(payload).st_nlink == 2
        assert {path: stat.S_IMODE(path.stat().st_mode) for path in retained} == retained
        assert not (tmp_path / ".ai-platform-runtime-write-probe").exists()
        assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
        assert workspace.is_dir()
    finally:
        opaque_directory.chmod(0o700)
        workspace.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="workspace initializer requires POSIX descriptors")
def test_initializer_rejects_root_symlink_without_following_it(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "marker.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    root_link = tmp_path / "workspace-root-link"
    root_link.symlink_to(target, target_is_directory=True)
    _use_current_runtime_identity(monkeypatch, root_link)

    with pytest.raises(WorkspacePermissionError, match="runtime workspace root is unavailable"):
        initialize_runtime_workspace()

    assert marker.read_text(encoding="utf-8") == "untouched\n"


@pytest.mark.skipif(os.name != "posix", reason="workspace initializer requires POSIX descriptors")
def test_initializer_rejects_namespace_symlink_without_following_it(tmp_path, monkeypatch):
    root = tmp_path / "workspace-root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    outside_mode = stat.S_IMODE(outside.stat().st_mode)
    (root / "tenants").symlink_to(outside, target_is_directory=True)
    _use_current_runtime_identity(monkeypatch, root)

    with pytest.raises(WorkspacePermissionError, match="workspace namespace entry is a symbolic link"):
        initialize_runtime_workspace()

    assert marker.read_text(encoding="utf-8") == "untouched\n"
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode


@pytest.mark.skipif(os.name != "posix", reason="workspace initializer requires POSIX descriptors")
def test_initializer_fails_explicitly_when_workspace_root_is_not_owner_writable(tmp_path, monkeypatch):
    _use_current_runtime_identity(monkeypatch, tmp_path)
    tmp_path.chmod(0o555)

    try:
        with pytest.raises(WorkspacePermissionError, match="runtime workspace root is not owner-writable"):
            initialize_runtime_workspace()
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o555
    finally:
        tmp_path.chmod(0o700)


@pytest.mark.parametrize(
    ("uid", "gid"),
    [(1000, 1000), (0, RUNTIME_GID), (RUNTIME_UID, 0)],
)
def test_namespace_identity_rejects_foreign_or_mixed_owners(uid, gid):
    with pytest.raises(WorkspacePermissionError, match="foreign workspace namespace owner"):
        _validate_namespace_directory_identity(
            "tenants/tenant-a",
            device=7,
            uid=uid,
            gid=gid,
            root_device=7,
        )


def test_namespace_identity_rejects_cross_filesystem_directory():
    with pytest.raises(WorkspacePermissionError, match="workspace namespace crosses filesystem boundary"):
        _validate_namespace_directory_identity(
            "tenants/tenant-a",
            device=8,
            uid=0,
            gid=0,
            root_device=7,
        )


def test_runtime_identity_drop_checks_effective_uid_and_gid(monkeypatch):
    from app.runtime.sandbox import workspace_permissions

    events = []
    monkeypatch.setattr(workspace_permissions.os, "setgroups", lambda groups: events.append(("groups", groups)))
    monkeypatch.setattr(workspace_permissions.os, "setgid", lambda gid: events.append(("gid", gid)))
    monkeypatch.setattr(workspace_permissions.os, "setuid", lambda uid: events.append(("uid", uid)))
    monkeypatch.setattr(workspace_permissions.os, "geteuid", lambda: RUNTIME_UID + 1)
    monkeypatch.setattr(workspace_permissions.os, "getegid", lambda: RUNTIME_GID)

    with pytest.raises(WorkspacePermissionError, match="runtime identity drop did not take effect"):
        _drop_runtime_privileges()

    assert events == [("groups", []), ("gid", RUNTIME_GID), ("uid", RUNTIME_UID)]


def test_runtime_workspace_probe_removes_its_sentinel_after_readback_failure(monkeypatch):
    from app.runtime.sandbox import workspace_permissions

    opened = iter([41, 42])
    unlinked = []
    monkeypatch.setattr(workspace_permissions.os, "open", lambda *args, **kwargs: next(opened))
    monkeypatch.setattr(workspace_permissions.os, "write", lambda fd, payload: len(payload))
    monkeypatch.setattr(workspace_permissions.os, "read", lambda fd, size: b"wrong")
    monkeypatch.setattr(workspace_permissions.os, "close", lambda fd: None)
    monkeypatch.setattr(
        workspace_permissions.os,
        "unlink",
        lambda name, *, dir_fd: unlinked.append((name, dir_fd)),
    )

    with pytest.raises(WorkspacePermissionError, match="sentinel readback mismatch"):
        _probe_runtime_workspace(9)

    assert unlinked == [(".ai-platform-runtime-write-probe", 9)]

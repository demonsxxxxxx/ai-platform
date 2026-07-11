import stat

import pytest

from app.runtime.sandbox.workspace_permissions import (
    RUNTIME_GID,
    RUNTIME_UID,
    WorkspaceNode,
    WorkspacePermissionError,
    _probe_runtime_workspace,
    validate_workspace_snapshot,
)


def node(
    path: str,
    *,
    uid: int = 0,
    gid: int = 0,
    mode: int = stat.S_IFREG | 0o600,
    device: int = 7,
    link_count: int = 1,
) -> WorkspaceNode:
    return WorkspaceNode(
        relative_path=path,
        uid=uid,
        gid=gid,
        mode=mode,
        device=device,
        link_count=link_count,
    )


def test_runtime_identity_is_fixed_and_non_root():
    assert (RUNTIME_UID, RUNTIME_GID) == (10001, 10001)


def test_workspace_snapshot_accepts_only_root_or_target_owned_regular_tree():
    validate_workspace_snapshot(
        root_device=7,
        nodes=[
            node(".", mode=stat.S_IFDIR | 0o755),
            node("runtime", uid=RUNTIME_UID, gid=RUNTIME_GID, mode=stat.S_IFDIR | 0o700),
            node("runtime/meta.json", mode=stat.S_IFREG | 0o600),
        ],
    )


@pytest.mark.parametrize(
    ("unsafe_node", "message"),
    [
        (node("foreign", uid=1000, gid=1000), "foreign workspace owner"),
        (node("root-user-only", uid=0, gid=10001), "foreign workspace owner"),
        (node("target-user-only", uid=10001, gid=0), "foreign workspace owner"),
        (node("link", mode=stat.S_IFLNK | 0o777), "unsupported workspace entry type"),
        (node("pipe", mode=stat.S_IFIFO | 0o600), "unsupported workspace entry type"),
        (node("socket", mode=stat.S_IFSOCK | 0o600), "unsupported workspace entry type"),
        (node("device", device=8), "workspace entry crosses filesystem boundary"),
        (node("world-write", mode=stat.S_IFREG | 0o602), "unsafe workspace mode"),
        (node("set-id", mode=stat.S_IFREG | stat.S_ISUID | 0o600), "unsafe workspace mode"),
        (node("sticky", mode=stat.S_IFREG | stat.S_ISVTX | 0o600), "unsafe workspace mode"),
        (node("hard-link", link_count=2), "workspace hard links are not allowed"),
        (node("not-writable", mode=stat.S_IFREG | 0o400), "workspace entry is not owner-writable"),
        (node("bad-directory", mode=stat.S_IFDIR | 0o500), "workspace entry is not owner-writable"),
    ],
)
def test_workspace_snapshot_rejects_unsafe_entries_before_migration(unsafe_node, message):
    with pytest.raises(WorkspacePermissionError, match=message):
        validate_workspace_snapshot(
            root_device=7,
            nodes=[node(".", mode=stat.S_IFDIR | 0o755), unsafe_node],
        )


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

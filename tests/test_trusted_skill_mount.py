from pathlib import Path

import pytest

from app.platform.sandbox import trusted_skill_mount
from app.platform.sandbox.errors import ContainerStartFailedError


def prepare(root: Path):
    return trusted_skill_mount.prepare_trusted_skill_mount(
        workspace_host_path=str(root / "workspace"),
        host_root=str(root),
        workspace_container_path="/workspace/",
    )


@pytest.fixture
def root(tmp_path):
    (tmp_path / "workspace/.claude/skills").mkdir(parents=True)
    return tmp_path


def test_mount_scrubs_settings_preserves_skill_content_and_stable_fingerprint(root):
    claude = root / "workspace/.claude"
    (claude / "skills/approved.md").write_text("approved content")
    (claude / "settings.json").write_text("untrusted host settings")
    target = root / "outside-settings"
    target.write_text("must not be modified")
    (claude / "settings.local.json").symlink_to(target)

    mount = prepare(root)

    assert mount.host_path == claude.resolve()
    assert mount.container_path == "/workspace/.claude"
    assert len(mount.fingerprint) == 64
    assert prepare(root) == mount
    assert not (claude / "settings.json").exists()
    assert not (claude / "settings.local.json").is_symlink()
    assert target.read_text() == "must not be modified"
    assert (claude / "skills/approved.md").read_text() == "approved content"


@pytest.mark.parametrize("relative", ["workspace", "workspace/.claude", "workspace/.claude/skills"])
def test_mount_rejects_symlink_at_each_directory_boundary(root, relative):
    node = root / relative
    moved = node.with_name(f"{node.name}-target")
    node.rename(moved)
    node.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ContainerStartFailedError, match="invalid"):
        prepare(root)


def test_mount_rejects_workspace_outside_trusted_root(root):
    other = root / "other"
    other.mkdir()
    with pytest.raises(ContainerStartFailedError, match="escapes trusted run root"):
        trusted_skill_mount.prepare_trusted_skill_mount(
            workspace_host_path=str(root / "workspace"),
            host_root=str(other),
            workspace_container_path="/workspace",
        )


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_mount_rejects_directory_in_settings_position(root, name):
    (root / "workspace/.claude" / name).mkdir()
    with pytest.raises(ContainerStartFailedError, match="settings path is invalid"):
        prepare(root)


@pytest.mark.parametrize("relative", ["workspace/.claude", "workspace/.claude/skills"])
def test_mount_rejects_directory_replacement_during_scrubbing(root, relative, monkeypatch):
    scrub = trusted_skill_mount._scrub_host_project_settings

    def replace_directory(claude):
        scrub(claude)
        target = root / relative
        target.rename(root / "replaced")
        target.mkdir()
        if relative.endswith(".claude"):
            (target / "skills").mkdir()

    monkeypatch.setattr(trusted_skill_mount, "_scrub_host_project_settings", replace_directory)
    with pytest.raises(ContainerStartFailedError, match="changed during validation"):
        prepare(root)


def test_mount_preserves_scrub_error_chain(root, monkeypatch):
    settings = root / "workspace/.claude/settings.json"
    settings.write_text("untrusted")
    unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == settings:
            raise PermissionError("synthetic permission error")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(ContainerStartFailedError, match="cannot be scrubbed") as error:
        prepare(root)
    assert isinstance(error.value.__cause__, PermissionError)


def test_mount_detects_settings_reappearing_after_unlink(root, monkeypatch):
    settings = root / "workspace/.claude/settings.json"
    settings.write_text("untrusted")
    unlink = Path.unlink

    def recreate(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path == settings:
            path.write_text("reappeared")
        return result

    monkeypatch.setattr(Path, "unlink", recreate)
    with pytest.raises(ContainerStartFailedError, match="cannot be scrubbed"):
        prepare(root)

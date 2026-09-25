import json
import os
from pathlib import Path

import pytest

from app.sandbox.infrastructure import workspace_storage_migration as migration


pytestmark = pytest.mark.skipif(os.name != "posix", reason="deployment migration is Linux-only")


def _source_tree(root: Path) -> None:
    (root / "tenants" / "tenant-a").mkdir(parents=True)
    first = root / "tenants" / "tenant-a" / "state.json"
    first.write_text('{"state":"ready"}', encoding="utf-8")
    first.chmod(0o600)
    second = root / "tenants" / "tenant-a" / "report.txt"
    second.write_text("report", encoding="utf-8")
    second.chmod(0o640)


def _write_incomplete_marker(source: Path, target: Path) -> None:
    inventory = migration._inventory_tree(source)
    (target / ".ai-platform-workspace-migration-v1.incomplete").write_text(
        json.dumps(migration._inventory_payload(inventory, status="incomplete")) + "\n",
        encoding="utf-8",
    )


def test_workspace_storage_migration_copies_verifies_and_is_idempotent(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)

    first = migration.migrate_workspace_storage(source, target)
    second = migration.migrate_workspace_storage(source, target)

    assert first == second
    assert first.files == 2
    assert first.bytes == len('{"state":"ready"}'.encode()) + len(b"report")
    assert (target / "tenants" / "tenant-a" / "state.json").read_text(
        encoding="utf-8"
    ) == '{"state":"ready"}'
    marker = json.loads(
        (target / ".ai-platform-workspace-migration-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker == {
        "bytes": first.bytes,
        "digest": first.digest,
        "directories": first.directories,
        "files": first.files,
        "schema_version": "ai-platform.workspace-storage-migration.v1",
        "status": "complete",
    }
    assert not (target / ".ai-platform-workspace-migration-v1.incomplete").exists()


def test_workspace_storage_migration_discards_stale_marker_temporary(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _source_tree(source)
    stale = target / migration._MARKER_TEMP_DIRECTORY / f"{migration._INCOMPLETE_MARKER}.tmp-123"
    stale.parent.mkdir(mode=0o700)
    stale.write_text("partial marker", encoding="utf-8")
    stale.chmod(0o600)

    inventory = migration.migrate_workspace_storage(source, target)

    assert inventory.files == 2
    assert not stale.exists()
    assert (target / ".ai-platform-workspace-migration-v1.json").exists()


def test_workspace_storage_migration_rejects_reserved_marker_file_name(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    reserved = source / f"{migration._INCOMPLETE_MARKER}.tmp-123"
    reserved.write_text("ordinary user data", encoding="utf-8")

    with pytest.raises(
        migration.WorkspaceStorageMigrationError,
        match="reserved marker name",
    ):
        migration.migrate_workspace_storage(source, target)



def test_workspace_storage_migration_resumes_only_matching_partial_target(
    monkeypatch, tmp_path
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    original = migration._copy_or_verify_file
    calls = 0

    def fail_after_first(source_path, target_path, source_node):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise migration.WorkspaceStorageMigrationError("synthetic interruption")
        return original(source_path, target_path, source_node)

    monkeypatch.setattr(migration, "_copy_or_verify_file", fail_after_first)
    with pytest.raises(migration.WorkspaceStorageMigrationError, match="interruption"):
        migration.migrate_workspace_storage(source, target)
    assert (target / ".ai-platform-workspace-migration-v1.incomplete").exists()

    monkeypatch.setattr(migration, "_copy_or_verify_file", original)
    inventory = migration.migrate_workspace_storage(source, target)

    assert inventory.files == 2
    assert (target / ".ai-platform-workspace-migration-v1.json").exists()


def test_workspace_storage_migration_rejects_source_change_after_interruption(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    original = migration._copy_or_verify_file
    calls = 0

    def interrupt(source_path, target_path, source_node):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise migration.WorkspaceStorageMigrationError("synthetic interruption")
        return original(source_path, target_path, source_node)

    monkeypatch.setattr(migration, "_copy_or_verify_file", interrupt)
    with pytest.raises(migration.WorkspaceStorageMigrationError, match="interruption"):
        migration.migrate_workspace_storage(source, target)
    monkeypatch.setattr(migration, "_copy_or_verify_file", original)
    (source / "tenants" / "tenant-a" / "state.json").write_text(
        "changed while admission should be stopped",
        encoding="utf-8",
    )

    with pytest.raises(migration.WorkspaceStorageMigrationError, match="source changed"):
        migration.migrate_workspace_storage(source, target)


def test_workspace_storage_migration_discards_stale_atomic_temp_and_resumes(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    (target / "tenants" / "tenant-a").mkdir(parents=True)
    _write_incomplete_marker(source, target)
    (target / "tenants" / "tenant-a" / ".state.json.ai-platform-migration-tmp").write_text(
        "partial", encoding="utf-8"
    )

    inventory = migration.migrate_workspace_storage(source, target)

    assert inventory.files == 2
    assert not (target / "tenants" / "tenant-a" / ".state.json.ai-platform-migration-tmp").exists()


def test_workspace_storage_migration_allows_target_growth_after_cutover(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    first = migration.migrate_workspace_storage(source, target)
    new_attempt = target / "tenants" / "tenant-a" / "new-attempt.txt"
    new_attempt.write_text("new run", encoding="utf-8")

    assert migration.migrate_workspace_storage(source, target) == first


def test_workspace_storage_migration_rejects_reserved_marker_name_after_cutover(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    migration.migrate_workspace_storage(source, target)
    reserved = target / f"{migration._INCOMPLETE_MARKER}.tmp-123"
    reserved.write_text("unexpected target data", encoding="utf-8")

    with pytest.raises(
        migration.WorkspaceStorageMigrationError,
        match="reserved marker name",
    ):
        migration.migrate_workspace_storage(source, target)


def test_workspace_storage_migration_revalidates_completed_source(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    migration.migrate_workspace_storage(source, target)
    (source / "tenants" / "tenant-a" / "state.json").write_text("drift", encoding="utf-8")

    with pytest.raises(
        migration.WorkspaceStorageMigrationError,
        match="completion inventory mismatch",
    ):
        migration.migrate_workspace_storage(source, target)


def test_workspace_storage_migration_finishes_valid_marker_interruption(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _source_tree(source)
    expected = migration.migrate_workspace_storage(source, target)
    _write_incomplete_marker(source, target)

    assert migration.migrate_workspace_storage(source, target) == expected
    assert not (target / ".ai-platform-workspace-migration-v1.incomplete").exists()


def test_workspace_storage_migration_fails_closed_on_extra_or_unsupported_nodes(
    tmp_path,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "foreign.txt").write_text("foreign", encoding="utf-8")

    with pytest.raises(migration.WorkspaceStorageMigrationError, match="not empty"):
        migration.migrate_workspace_storage(source, target)

    target.joinpath("foreign.txt").unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (source / "linked.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    with pytest.raises(migration.WorkspaceStorageMigrationError, match="unsupported node"):
        migration.migrate_workspace_storage(source, target)


def test_workspace_storage_migration_rejects_mismatched_resume(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "state.txt").write_text("source", encoding="utf-8")
    _write_incomplete_marker(source, target)
    (target / "state.txt").write_text("different", encoding="utf-8")

    with pytest.raises(migration.WorkspaceStorageMigrationError, match="content mismatch"):
        migration.migrate_workspace_storage(source, target)
    assert not (target / ".ai-platform-workspace-migration-v1.json").exists()

"""Private stopped-hostbind recovery, Linux selectors are intentionally explicit."""
import copy
import json
from contextlib import contextmanager
import os
import sys
import socket
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest

from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.sandbox import api as sandbox_api
from app.sandbox.api import WorkspaceVersionError
from app.sandbox.domain.workspace_version import workspace_version_exclusion
from app.sandbox.infrastructure import workspace_versions as versions
from app.skills.registry import BuiltinSkill
from app.skills.stager import SkillStager

linux = pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux no-follow/flock/fsync; run on Linux")


def save_workspace_version(*, workspace_root, **kwargs):
    return sandbox_api.save_workspace_version(
        storage=partial(versions.save_workspace_version, workspace_root=workspace_root), **kwargs,
    )


def restore_workspace_version(*, workspace_root, **kwargs):
    return sandbox_api.restore_workspace_version(
        storage=partial(versions.restore_workspace_version, workspace_root=workspace_root), **kwargs,
    )


def restore_skill_outputs(root, receipt, restored, req, lease, staged, **evidence_changes):
    evidence = dict(state="skills_staged", execution_phase="before_executor_start",
                    scope=versions._scope(req), owner_generation=req.owner_generation,
                    staged_skill_names=staged)
    evidence.update(evidence_changes)
    return sandbox_api.restore_workspace_skill_outputs(
        storage=partial(versions.restore_workspace_skill_outputs, workspace_root=root),
        receipt=receipt, restore_receipt=restored, request=req, target_workspace_lease=lease,
        staging_evidence=evidence, authorized_skill_names=("editor",),
    )


def stage_current_skill(root, workspace, *, packaged_output=False):
    source = root / "current-skill"
    put(source, "SKILL.md", "CURRENT SKILL CODE")
    put(source, "scripts/task.py", "print('current code')")
    if packaged_output:
        put(source, "output/current.txt", "CURRENT PACKAGED OUTPUT")
    return SkillStager().stage_skills(
        workspace=workspace,
        skills=[BuiltinSkill(name="editor", description="synthetic", path=source,
                             version="new-version", source={"kind": "synthetic"})],
    )


def request(**changes):
    fields = dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
                  session_id="session-a", run_id="run-a", attempt_id="attempt-a", agent_id="general-agent",
                  input_message="hello", sandbox_mode="ephemeral", model="synthetic", callback_url="http://callback",
                  callback_token_id="cbt-a", tool_policy_subjects=[dict(identity="Skill", registered=True,
                  declared=True, active=True, distributed=True, declared_identities=["Skill"], allowed_skill_names=["editor"])])
    return SandboxRuntimeRequest(**(fields | changes))


def stop(req, state="provider_stopped"):
    return dict(state=state, scope=versions._scope(req), owner_generation=req.owner_generation)


def put(root, name, text="draft"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def save(root, req=None, **kw):
    req = req or request()
    manager = SandboxWorkspaceManager(root=root)
    lease = manager.prepare(req)
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=root, **kw)
    return receipt


def restore(root, receipt, req=None, **kw):
    req = req or request(run_id="run-b", attempt_id="attempt-b")
    manager = SandboxWorkspaceManager(root=root)
    lease = manager.reserve(req)
    result = restore_workspace_version(receipt=receipt, request=req, target_workspace_lease=lease,
                                       workspace_root=root, **kw)
    return Path(lease.workspace_host_path), result


@pytest.mark.parametrize("path,reason", [
    ("inputs/file.docx", "authorized_inputs"), ("context/file-a/original.docx", "authorized_inputs"),
    (".home/auth", "runtime_private"), (".ai-platform/token", "runtime_private"),
    ("CLAUDE.md", "runtime_private"), (".tmp/.env", "credentials"),
    (".tmp/.ssh/id_rsa", "credentials"), ("project/.env.production", "credentials"),
    (".claude/skills/editor/SKILL.md", "skill_configuration"),
    (".claude/settings.json", "skill_configuration"),
    (".claude/skills/revoked/output/draft.txt", "skill_configuration"),
    (".claude/skills/editor/output/draft.txt", None), (".tmp/draft.txt", None),
    ("draft.txt", None),
])
def test_private_version_policy_is_distinct_from_artifact_delivery(path, reason):
    assert workspace_version_exclusion(path, skill_names={"editor"}) == reason


@pytest.mark.parametrize("change", [dict(state="unverified"), dict(scope={}), dict(owner_generation=2), dict(owner_generation=True)])
def test_save_rejects_unstopped_wrong_scope_and_generation_before_io(tmp_path, change):
    req = request()
    evidence = stop(req) | change
    with pytest.raises(WorkspaceVersionError, match="workspace_writer_unverified"):
        save_workspace_version(request=req, workspace_lease=None, stop_evidence=evidence, workspace_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_restore_rejects_wrong_session_before_io(tmp_path):
    receipt = dict(scope=versions._scope(request(session_id="other-session")))
    with pytest.raises(WorkspaceVersionError, match="workspace_scope_invalid"):
        restore_workspace_version(receipt=receipt, request=request(), target_workspace_lease=None, workspace_root=tmp_path)


def test_secure_version_io_fails_closed_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(versions.bind, "_secure_descriptor_walk_supported", lambda: False)
    req = request()
    with pytest.raises(WorkspaceVersionError, match="workspace_secure_io_unavailable"):
        save_workspace_version(request=req, workspace_lease=None, stop_evidence=stop(req), workspace_root=tmp_path)


@linux
def test_empty_plain_text_version_without_live_sandbox(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).reserve(req)
    Path(lease.workspace_host_path).mkdir(mode=0o755)
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    assert receipt["state"] == "saved"
    assert receipt["file_count"] == receipt["total_bytes"] == 0
    target, result = restore(tmp_path, receipt)
    assert result["state"] == "restored"
    assert list(target.iterdir()) == []


@linux
@pytest.mark.parametrize("fault", [None, "missing_data", "modified", "extra", "missing_file"])
@pytest.mark.parametrize("empty", [False, True])
def test_verify_saved_version_checks_complete_inventory_without_mutation(tmp_path, fault, empty):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).reserve(req)
    workspace = Path(lease.workspace_host_path)
    workspace.mkdir(mode=0o755)
    if not empty:
        put(workspace, "draft.txt", "original")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    version = tmp_path / receipt["source_ref"]
    data = version / "data"
    if fault == "missing_data":
        if not empty:
            (data / "draft.txt").unlink()
        data.rmdir()
    elif fault == "modified":
        put(data, "draft.txt", "tampered")
    elif fault == "extra":
        put(data, "extra.txt", "unexpected")
    elif fault == "missing_file":
        if empty:
            data.rmdir()
        else:
            (data / "draft.txt").unlink()
    before = {str(path.relative_to(version)): path.read_bytes() for path in version.rglob("*") if path.is_file()}
    verify = partial(sandbox_api.verify_workspace_version, request=req, receipt=receipt,
                     storage=partial(versions.verify_workspace_version, workspace_root=tmp_path))
    if fault is None:
        assert verify() == receipt
    else:
        with pytest.raises(WorkspaceVersionError, match="workspace_version_invalid"):
            verify()
    assert {str(path.relative_to(version)): path.read_bytes() for path in version.rglob("*") if path.is_file()} == before


@linux
def test_full_restore_preserves_edits_deletions_scratch_empty_dirs_and_skill_output(tmp_path):
    req = request()
    manager = SandboxWorkspaceManager(root=tmp_path)
    lease = manager.prepare(req)
    root = Path(lease.workspace_host_path)
    put(root, "edited.txt", "after edit")
    put(root, "deleted.txt").unlink()
    put(root, ".tmp/draft.txt", "unfinished")
    executable = put(root, "task.sh", "#!/bin/sh\nprintf done")
    executable.chmod(0o755)
    (root / "empty-directory").mkdir()
    put(root, ".claude/skills/editor/output/report.txt", "report")
    for path in ("inputs/original.docx", "context/file-a/original.docx", ".claude/skills/editor/SKILL.md", ".home/token", "project/.env", ".tmp/.env"):
        put(root, path, "SYNTHETIC-PRIVATE")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    assert receipt["file_count"] == 4
    target, restored = restore(tmp_path, receipt, authorized_skill_names=("editor",))
    assert (target / "edited.txt").read_text() == "after edit"
    assert not (target / "deleted.txt").exists()
    assert (target / ".tmp/draft.txt").read_text() == "unfinished"
    assert (target / "empty-directory").is_dir()
    assert not (target / ".claude").exists()
    assert restored["state"] == "awaiting_skill_staging"
    assert restored["skill_outputs_pending"] == ["editor"]
    for path in ("inputs", "context", ".home", "project/.env", ".tmp/.env", ".claude/skills/editor/SKILL.md"):
        assert not (target / path).exists()
    next_req = request(run_id="run-b", attempt_id="attempt-b")
    next_lease = manager.prepare(next_req)
    staged = stage_current_skill(tmp_path, target)
    assert not (target / ".claude/skills/editor/output").exists()
    completed = restore_skill_outputs(tmp_path, receipt, restored, next_req, next_lease, staged)
    assert completed["restored_skill_names"] == ["editor"]
    assert (target / ".claude/skills/editor/output/report.txt").read_text() == "report"
    assert (target / ".claude/skills/editor/SKILL.md").read_text() == "CURRENT SKILL CODE"
    assert (target / ".claude/skills/editor/scripts/task.py").read_text() == "print('current code')"
    assert restore_skill_outputs(tmp_path, receipt, restored, next_req, next_lease, staged) == completed
    assert (target / "CLAUDE.md").read_text().startswith("# AI Platform")
    assert (target / "edited.txt").read_text() == "after edit"
    assert (target / "edited.txt").stat().st_mode & 0o777 == 0o644
    assert (target / "task.sh").stat().st_mode & 0o777 == 0o755
    assert (target / ".tmp").stat().st_mode & 0o777 == 0o755
    assert not (target / "deleted.txt").exists()


@linux
def test_revoked_skill_output_and_current_input_destination_are_not_restored(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    source = Path(lease.workspace_host_path)
    put(source, ".claude/skills/editor/output/draft.txt")
    put(source, "new-input-destination.docx")
    put(source, "derived.txt", "derived")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    target, result = restore(tmp_path, receipt, authorized_input_paths=("new-input-destination.docx",))
    assert not (target / ".claude/skills/editor").exists()
    assert not (target / "new-input-destination.docx").exists()
    assert (target / "derived.txt").read_text() == "derived"
    assert result["exclusions"] == {"skill_configuration": 1, "authorized_inputs": 1}


@linux
@pytest.mark.parametrize("state", ["provider_stopped", "provider_not_found"])
def test_saved_source_is_immutable_and_save_is_idempotent(tmp_path, state):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    path = put(Path(lease.workspace_host_path), "draft.txt", "A")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req, state), workspace_root=tmp_path)
    path.write_text("failed tail")
    assert save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req, state), workspace_root=tmp_path) == receipt
    target_b, _ = restore(tmp_path, receipt)
    (target_b / "draft.txt").write_text("B failed")
    target_c, _ = restore(tmp_path, receipt, req=request(run_id="run-c", attempt_id="attempt-c"))
    assert (target_c / "draft.txt").read_text() == "A"


@linux
@pytest.mark.parametrize("limits", [dict(max_files=1), dict(max_file_bytes=2), dict(max_total_bytes=3)])
def test_quota_failure_does_not_publish_or_truncate(tmp_path, limits):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt", "large draft")
    with pytest.raises(WorkspaceVersionError, match="workspace_quota_exceeded"):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path, limits=limits)
    assert not (Path(lease.host_root) / versions._VERSION_DIRECTORY).exists()
    assert not list(Path(lease.host_root).glob("*.tmp-*"))
    assert (Path(lease.workspace_host_path) / "draft.txt").read_text() == "large draft"


@linux
def test_interrupted_copy_leaves_no_published_version(tmp_path, monkeypatch):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt")
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(versions.bind, "_copy_stable_delivery_file", interrupt)
    with pytest.raises(KeyboardInterrupt):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    assert not (Path(lease.host_root) / versions._VERSION_DIRECTORY).exists()
    assert not list(Path(lease.host_root).glob("*.tmp-*"))


@linux
def test_symlink_socket_fifo_excluded_without_following_and_hardlink_rejected(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    root = Path(lease.workspace_host_path)
    outside = put(tmp_path, "outside.txt", "OUTSIDE")
    (root / "escape").symlink_to(outside)
    os.mkfifo(root / "fifo")
    with socket.socket(socket.AF_UNIX) as listener:
        descriptor = os.open(root / ".tmp", os.O_RDONLY | os.O_DIRECTORY)
        try:
            listener.bind(f"/proc/self/fd/{descriptor}/s")
            receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
        finally:
            os.close(descriptor)
    target, _ = restore(tmp_path, receipt)
    assert not (target / "escape").exists()
    assert not (target / "fifo").exists()
    assert not (target / ".tmp/s").exists()
    assert outside.read_text() == "OUTSIDE"
    hard_req = request(run_id="run-hard", attempt_id="attempt-hard")
    hard_lease = SandboxWorkspaceManager(root=tmp_path).prepare(hard_req)
    os.link(outside, Path(hard_lease.workspace_host_path) / "hardlink")
    with pytest.raises(WorkspaceVersionError):
        save_workspace_version(request=hard_req, workspace_lease=hard_lease, stop_evidence=stop(hard_req), workspace_root=tmp_path)


@linux
def test_scope_and_source_reference_cannot_select_other_version(tmp_path):
    receipt = save(tmp_path)
    forged = dict(receipt, source_ref="../outside")
    with pytest.raises(WorkspaceVersionError, match="workspace_scope_invalid"):
        restore(tmp_path, forged)
    wrong_scope = copy.deepcopy(receipt)
    wrong_scope["scope"]["user_id"] = "other-user"
    with pytest.raises(WorkspaceVersionError, match="workspace_scope_invalid"):
        restore(tmp_path, wrong_scope)


@linux
def test_existing_target_rejected_without_overlay_or_deleting_data(tmp_path):
    receipt = save(tmp_path)
    req = request(run_id="run-b", attempt_id="attempt-b")
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    existing = put(Path(lease.workspace_host_path), "existing.txt", "keep")
    with pytest.raises(WorkspaceVersionError, match="workspace_target_not_new"):
        restore_workspace_version(receipt=receipt, request=req, target_workspace_lease=lease, workspace_root=tmp_path)
    assert existing.read_text() == "keep"


@linux
def test_corrupt_version_fails_before_publish(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt", "original")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    (tmp_path / receipt["source_ref"] / "data/draft.txt").write_text("tampered")
    with pytest.raises(WorkspaceVersionError, match="workspace_version_invalid"):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    with pytest.raises(WorkspaceVersionError, match="workspace_version_invalid"):
        restore(tmp_path, receipt)
    req_b = request(run_id="run-b", attempt_id="attempt-b")
    assert not Path(SandboxWorkspaceManager(root=tmp_path).reserve(req_b).workspace_host_path).exists()


@linux
def test_concurrent_save_publishes_one_version(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt")
    def seal(_):
        return save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(seal, range(2)))
    assert receipts[0] == receipts[1]


def test_reserve_does_not_create_workspace_and_keeps_public_projection_safe(tmp_path):
    req = request(tenant_id="t", workspace_id="w", user_id="u", session_id="s", run_id="r", attempt_id="a")
    lease = SandboxWorkspaceManager(root=tmp_path).reserve(req)
    assert Path(lease.host_root).is_dir()
    assert Path(lease.logs_host_path).is_dir()
    assert not Path(lease.workspace_host_path).exists()
    assert lease.user_visible_payload() == {"workspace": "/workspace", "inputs": "/workspace/inputs"}


@pytest.mark.parametrize("limits", [{"max_files": 0}, {"max_files": True}, {"unknown": 1}])
def test_invalid_limits_are_rejected_before_filesystem_io(tmp_path, limits):
    req = request()
    with pytest.raises(WorkspaceVersionError, match="workspace_limits_invalid"):
        save_workspace_version(request=req, workspace_lease=None, stop_evidence=stop(req), limits=limits, workspace_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@linux
def test_wrong_lease_and_symlinked_ancestor_do_not_save_outside_scope(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    wrong_lease = lease.model_copy(update={"user_id": "someone-else"})
    with pytest.raises(WorkspaceVersionError):
        save_workspace_version(request=req, workspace_lease=wrong_lease, stop_evidence=stop(req), workspace_root=tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = Path(lease.workspace_host_path)
    root.rename(outside / "workspace")
    root.symlink_to(outside / "workspace", target_is_directory=True)
    with pytest.raises(WorkspaceVersionError):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    assert not (Path(lease.host_root) / versions._VERSION_DIRECTORY).exists()


@linux
def test_interrupted_restore_does_not_publish_or_change_source(tmp_path, monkeypatch):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt", "source")
    receipt = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic I/O interruption")
    monkeypatch.setattr(versions.bind, "_copy_stable_delivery_file", fail)
    with pytest.raises(WorkspaceVersionError, match="workspace_restore_failed"):
        restore(tmp_path, receipt)
    target_req = request(run_id="run-b", attempt_id="attempt-b")
    target = SandboxWorkspaceManager(root=tmp_path).reserve(target_req)
    assert not Path(target.workspace_host_path).exists()
    assert not list(Path(target.host_root).glob(".workspace-restore-v1.tmp-*"))
    assert (tmp_path / receipt["source_ref"] / "data/draft.txt").read_text() == "source"


@linux
def test_existing_save_retries_parent_fsync_after_rename_failure(tmp_path, monkeypatch):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).prepare(req)
    put(Path(lease.workspace_host_path), "draft.txt", "keep")
    actual_fsync = versions._fsync_directory
    calls = []
    def failing_fsync(path):
        calls.append(path)
        raise OSError("synthetic directory fsync failure")
    monkeypatch.setattr(versions, "_fsync_directory", failing_fsync)
    for _ in range(2):
        with pytest.raises(WorkspaceVersionError, match="workspace_save_failed"):
            save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)
        assert (Path(lease.host_root) / versions._VERSION_DIRECTORY).is_dir()
    assert calls == [Path(lease.host_root)] * 2
    monkeypatch.setattr(versions, "_fsync_directory", actual_fsync)
    assert save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req), workspace_root=tmp_path)["state"] == "saved"


@linux
@pytest.mark.parametrize("excluded", [False, True])
def test_scan_budget_bounds_enumeration_before_sorting_including_excluded(tmp_path, monkeypatch, excluded):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).reserve(req)
    root = Path(lease.workspace_host_path)
    root.mkdir()
    for index in range(20):
        put(root, f".env.{index}" if excluded else f"draft-{index}.txt")
    source_identity = versions._directory_identity(root)
    actual_scandir = os.scandir
    consumed = []
    @contextmanager
    def tracked_scandir(path):
        tracked = isinstance(path, int) and [os.fstat(path).st_dev, os.fstat(path).st_ino] == source_identity
        with actual_scandir(path) as entries:
            def observed():
                for entry in entries:
                    if tracked:
                        consumed.append(entry.name)
                    yield entry
            yield observed()
    monkeypatch.setattr(versions.os, "scandir", tracked_scandir)
    with pytest.raises(WorkspaceVersionError, match="workspace_quota_exceeded"):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req),
                               workspace_root=tmp_path, limits={"max_scan_nodes": 4})
    assert len(consumed) == 5  # Stop at the first over-budget node, not after consuming the iterator.
    assert not (Path(lease.host_root) / versions._VERSION_DIRECTORY).exists()
    assert not list(Path(lease.host_root).glob("*.tmp-*"))
    assert len(list(root.iterdir())) == 20


@linux
def test_scan_budget_is_shared_across_nested_directories(tmp_path):
    req = request()
    lease = SandboxWorkspaceManager(root=tmp_path).reserve(req)
    root = Path(lease.workspace_host_path)
    for name in ("a", "b"):
        for index in range(2):
            put(root, f"{name}/.env.{index}")
    with pytest.raises(WorkspaceVersionError, match="workspace_quota_exceeded"):
        save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req),
                               workspace_root=tmp_path, limits={"max_scan_nodes": 5})
    assert not (Path(lease.host_root) / versions._VERSION_DIRECTORY).exists()
    saved = save_workspace_version(request=req, workspace_lease=lease, stop_evidence=stop(req),
                                   workspace_root=tmp_path, limits={"max_scan_nodes": 6})
    assert saved["file_count"] == 0
    assert saved["exclusions"] == {"credentials": 4}


def staged_restore_fixture(root, *, packaged_output=False):
    req = request()
    source_lease = SandboxWorkspaceManager(root=root).prepare(req)
    put(Path(source_lease.workspace_host_path), ".claude/skills/editor/output/draft.txt", "PRESERVED TASK OUTPUT")
    saved = save_workspace_version(request=req, workspace_lease=source_lease, stop_evidence=stop(req), workspace_root=root)
    target_req = request(run_id="run-b", attempt_id="attempt-b")
    target, restored = restore(root, saved, req=target_req, authorized_skill_names=("editor",))
    target_lease = SandboxWorkspaceManager(root=root).prepare(target_req)
    staged = stage_current_skill(root, target, packaged_output=packaged_output)
    return saved, restored, target_req, target_lease, staged, target


@linux
def test_post_staging_output_conflict_preserves_packaged_output_and_code(tmp_path):
    saved, restored, req, lease, staged, target = staged_restore_fixture(tmp_path, packaged_output=True)
    with pytest.raises(WorkspaceVersionError, match="workspace_skill_output_conflict"):
        restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
    assert (target / ".claude/skills/editor/output/current.txt").read_text() == "CURRENT PACKAGED OUTPUT"
    assert not (target / ".claude/skills/editor/output/draft.txt").exists()
    assert (target / ".claude/skills/editor/SKILL.md").read_text() == "CURRENT SKILL CODE"
    assert (tmp_path / saved["source_ref"] / "data/.claude/skills/editor/output/draft.txt").read_text() == "PRESERVED TASK OUTPUT"
    assert not list(Path(lease.host_root).glob(".workspace-skill-output-v1.tmp-*"))


@linux
@pytest.mark.parametrize("condition", ["absent", "exists", "symlink", "identity_changed", "published"])
def test_revoked_unpublished_plan_retry_preserves_current_outputs(tmp_path, monkeypatch, condition):
    saved, restored, req, lease, staged, target = staged_restore_fixture(tmp_path)
    attempt = Path(lease.host_root)
    skill = target / ".claude/skills/editor"
    output = skill / "output"
    journal = attempt / versions._RESTORE_BINDING
    actual_fsync = versions._fsync_directory

    def fail_after_plan_replace(path):
        if path == attempt:
            raise OSError("synthetic journal publication fsync failure")
        actual_fsync(path)

    monkeypatch.setattr(versions, "_fsync_directory", fail_after_plan_replace)
    with pytest.raises(WorkspaceVersionError, match="workspace_skill_output_restore_failed"):
        restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
    binding = json.loads(journal.read_text())
    assert binding["publications"]["editor"]["published"] is False
    assert not os.path.lexists(output)
    monkeypatch.setattr(versions, "_fsync_directory", actual_fsync)
    if condition == "exists":
        put(output, "current.txt", "CURRENT OUTPUT")
    elif condition == "symlink":
        destination = tmp_path / "external-output"
        put(destination, "current.txt", "CURRENT OUTPUT")
        output.symlink_to(destination, target_is_directory=True)
    elif condition == "identity_changed":
        (skill / "SKILL.md").write_text("REPLACED SKILL CODE")
    elif condition == "published":
        binding["publications"]["editor"]["published"] = True
        versions._write_restore_binding(attempt, binding)
    revoked = req.model_copy(update={"tool_policy_subjects": []})
    if condition == "absent":
        result = restore_skill_outputs(tmp_path, saved, restored, revoked, lease, staged)
        assert result["state"] == "restored"
        assert result["restored_skill_names"] == []
        assert result["exclusions"] == {"skill_configuration": 1}
        assert json.loads(journal.read_text())["publications"] == {}
    else:
        with pytest.raises(WorkspaceVersionError, match="workspace_staging_unverified"):
            restore_skill_outputs(tmp_path, saved, restored, revoked, lease, staged)
        assert json.loads(journal.read_text())["publications"] == binding["publications"]
    if condition in {"exists", "symlink"}:
        assert (output / "current.txt").read_text() == "CURRENT OUTPUT"
    else:
        assert not os.path.lexists(output)
    assert (skill / "SKILL.md").read_text() == (
        "REPLACED SKILL CODE" if condition == "identity_changed" else "CURRENT SKILL CODE"
    )
    assert (tmp_path / saved["source_ref"] / "data/.claude/skills/editor/output/draft.txt").read_text() == "PRESERVED TASK OUTPUT"


@pytest.mark.parametrize("changes", [
    {"execution_phase": "running"}, {"scope": {}}, {"owner_generation": 2},
    {"state": "not_staged"}, {"owner_generation": True},
])
def test_post_staging_rejects_live_wrong_scope_and_stale_generation_before_io(tmp_path, changes):
    req = request(run_id="run-b", attempt_id="attempt-b")
    with pytest.raises(WorkspaceVersionError, match="workspace_staging_unverified"):
        restore_skill_outputs(tmp_path, {"scope": versions._scope(request())}, {}, req, None, ["editor"], **changes)
    assert list(tmp_path.iterdir()) == []


@linux
@pytest.mark.parametrize("condition", ["wrong_restore", "missing_skill", "missing_markdown", "revoked", "symlink_skill",
                                       "corrupt_source", "replaced_workspace", "new_generation"])
def test_post_staging_checks_binding_real_staging_authority_and_manifest(tmp_path, condition):
    saved, restored, req, lease, staged, target = staged_restore_fixture(tmp_path)
    if condition == "wrong_restore":
        restored = dict(restored, restore_id="other-restore")
    elif condition == "missing_skill":
        staged = []
    elif condition == "missing_markdown":
        (target / ".claude/skills/editor/SKILL.md").unlink()
    elif condition == "new_generation":
        req = req.model_copy(update={"owner_generation": 2})
    elif condition == "replaced_workspace":
        target.rename(target.parent / "moved-workspace")
        SandboxWorkspaceManager(root=tmp_path).prepare(req)
        staged = stage_current_skill(tmp_path, target)
    elif condition == "revoked":
        req = req.model_copy(update={"tool_policy_subjects": []})
    elif condition == "symlink_skill":
        skill = target / ".claude/skills/editor"
        skill.rename(target / "moved-skill")
        skill.symlink_to(target / "moved-skill", target_is_directory=True)
    else:
        (tmp_path / saved["source_ref"] / "data/.claude/skills/editor/output/draft.txt").write_text("CORRUPTED SOURCE")
    if condition == "revoked":
        result = restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
        assert result["restored_skill_names"] == []
        assert result["exclusions"] == {"skill_configuration": 1}
    else:
        with pytest.raises(WorkspaceVersionError):
            restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
    assert not (target / ".claude/skills/editor/output").exists()
    if condition != "missing_markdown":
        assert (target / ".claude/skills/editor/SKILL.md").read_text() == "CURRENT SKILL CODE"


@linux
def test_post_staging_retry_fsyncs_owned_publication_without_overlay(tmp_path, monkeypatch):
    saved, restored, req, lease, staged, target = staged_restore_fixture(tmp_path)
    skill = target / ".claude/skills/editor"
    actual_fsync = versions._fsync_directory
    calls = []
    def fail_skill_fsync(path):
        if path == skill:
            calls.append(path)
            raise OSError("synthetic output publication fsync failure")
        return actual_fsync(path)
    monkeypatch.setattr(versions, "_fsync_directory", fail_skill_fsync)
    identity = None
    for _ in range(2):
        with pytest.raises(WorkspaceVersionError, match="workspace_skill_output_restore_failed"):
            restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
        current = versions._directory_identity(skill / "output")
        if identity is not None:
            assert current == identity
        identity = current
        assert (skill / "output/draft.txt").read_text() == "PRESERVED TASK OUTPUT"
    assert len(calls) == 2
    monkeypatch.setattr(versions, "_fsync_directory", actual_fsync)
    assert restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)["state"] == "restored"
    assert versions._directory_identity(skill / "output") == identity
    (skill / "output/draft.txt").write_text("LIVE MODIFIED OUTPUT")
    with pytest.raises(WorkspaceVersionError):
        restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
    assert (skill / "output/draft.txt").read_text() == "LIVE MODIFIED OUTPUT"


@linux
def test_post_staging_interruption_does_not_publish_or_delete_current_code(tmp_path, monkeypatch):
    saved, restored, req, lease, staged, target = staged_restore_fixture(tmp_path)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(versions.bind, "_copy_stable_delivery_file", interrupt)
    with pytest.raises(KeyboardInterrupt):
        restore_skill_outputs(tmp_path, saved, restored, req, lease, staged)
    assert not (target / ".claude/skills/editor/output").exists()
    assert (target / ".claude/skills/editor/SKILL.md").read_text() == "CURRENT SKILL CODE"
    assert (tmp_path / saved["source_ref"] / "data/.claude/skills/editor/output/draft.txt").read_text() == "PRESERVED TASK OUTPUT"
    assert not list(Path(lease.host_root).glob(".workspace-skill-output-v1.tmp-*"))

import base64
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.path_safety import ensure_creatable_inside
from app.skills.api import (
    PinnedSkillMismatch,
    pin_manifests_for_result,
    select_pinned_skill_snapshots,
    staged_skill_manifests,
    stage_pinned_skill_snapshot,
)
from app.skills.registry import skill_content_hash
from app.skills.stager import ensure_skill_staging_directory, write_skill_staging_file


def _prepare(workspace_root: Path, target: Path) -> None:
    ensure_creatable_inside(workspace_root, target, "outside workspace")
    if target.exists():
        shutil.rmtree(target)
    ensure_skill_staging_directory(workspace_root, target)
    ensure_creatable_inside(workspace_root, target, "outside workspace")


def _write(target: Path, output: Path, content: bytes, skill_name: str) -> None:
    ensure_creatable_inside(target, output, "invalid pinned skill file path")
    ensure_skill_staging_directory(target, output.parent)
    write_skill_staging_file(output, content)


def test_pinned_selection_rejects_missing_and_mismatched_pins(tmp_path):
    skills = [SimpleNamespace(name="synthetic", version="local")]
    selected, missing = select_pinned_skill_snapshots(
        skills, ["synthetic"], {}, tmp_path, materialize=lambda *args: None
    )
    assert selected == []
    assert missing[0]["reason"] == "missing_pinned_manifest"

    def mismatched(*args):
        raise PinnedSkillMismatch("hash mismatch", actual_content_hash="actual")

    selected, failures = select_pinned_skill_snapshots(
        skills,
        ["synthetic"],
        {"synthetic": {"content_hash": "expected", "files": [{"relative_path": "SKILL.md"}]}},
        tmp_path,
        materialize=mismatched,
    )
    assert selected == []
    assert failures == [{
        "skill_id": "synthetic",
        "expected_content_hash": "expected",
        "actual_content_hash": "actual",
        "reason": "hash mismatch",
    }]
    assert pin_manifests_for_result(
        {"synthetic": {"content_hash": "expected", "files": [{"content_base64": "private"}]}},
        ["synthetic"],
    ) == [{
        "content_hash": "expected",
        "version": "expected",
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }]
    assert staged_skill_manifests(
        [SimpleNamespace(name="synthetic", version="expected", description="test", source={})],
        used_skill_names=["synthetic"],
        pins={"synthetic": {"dependency_ids": ["synthetic", "unavailable"]}},
    )[0]["dependency_ids"] == ["synthetic"]


def test_pinned_snapshot_verifies_hash_and_removes_mismatch(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    content = b"---\nname: synthetic\ndescription: test\n---\n"
    (source / "SKILL.md").write_bytes(content)
    actual_hash = skill_content_hash(source)
    pin = {
        "content_hash": actual_hash,
        "files": [{
            "relative_path": "SKILL.md",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "size_bytes": len(content),
        }],
    }
    root = tmp_path / "workspace" / "skills"
    root.parent.mkdir()
    arguments = {
        "prepare_target": _prepare,
        "write_file": _write,
        "has_skill_markdown": lambda path: (path / "SKILL.md").is_file(),
        "content_hash": skill_content_hash,
        "remove_invalid_target": lambda path: shutil.rmtree(path, ignore_errors=True),
        "max_file_bytes": 1024,
        "max_total_bytes": 1024,
    }

    staged, version = stage_pinned_skill_snapshot("synthetic", pin, root, **arguments)
    assert (staged / "SKILL.md").read_bytes() == content
    assert version == actual_hash
    with pytest.raises(PinnedSkillMismatch) as error:
        stage_pinned_skill_snapshot("synthetic", {**pin, "content_hash": "wrong"}, root, **arguments)
    assert error.value.actual_content_hash == actual_hash
    assert not staged.exists()
    with pytest.raises(ValueError, match="invalid pinned skill file path"):
        stage_pinned_skill_snapshot(
            "synthetic", {**pin, "files": [{**pin["files"][0], "relative_path": "../escape"}]}, root, **arguments
        )
    assert not (tmp_path / "workspace" / "escape").exists()

import base64
import os
import stat

import pytest

from app.execution.api import artifact_type
from app.bootstrap.skills import materialize_worker_pinned_skill
from app.skills.api import select_pinned_skill_snapshots
from app.skills.registry import skill_content_hash


@pytest.mark.parametrize("filename", ["report.txt", "summary.md"])
def test_worker_keeps_text_artifact_classification_after_tuple_suffix_check(filename):
    assert artifact_type(filename) == "report_txt"


def test_pinned_skill_materialization_uses_safe_modes_under_restrictive_umask(tmp_path):
    if os.name != "posix":
        pytest.skip("requires POSIX mode semantics")

    skill_body = b"# Pinned skill\n"
    guide_body = b"guide\n"
    expected = tmp_path / "expected"
    (expected / "references").mkdir(parents=True)
    (expected / "SKILL.md").write_bytes(skill_body)
    (expected / "references" / "guide.md").write_bytes(guide_body)
    pin = {
        "content_hash": skill_content_hash(expected),
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": base64.b64encode(skill_body).decode("ascii"),
                "size_bytes": len(skill_body),
            },
            {
                "relative_path": "references/guide.md",
                "content_base64": base64.b64encode(guide_body).decode("ascii"),
                "size_bytes": len(guide_body),
            },
        ],
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o770)
    previous_umask = os.umask(0o007)
    try:
        materialized = materialize_worker_pinned_skill("report", pin, workspace / ".pins")
    finally:
        os.umask(previous_umask)

    assert materialized.path == workspace / ".pins" / "report"
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o770
    assert stat.S_IMODE((workspace / ".pins").stat().st_mode) == 0o755
    assert stat.S_IMODE(materialized.path.stat().st_mode) == 0o755
    assert stat.S_IMODE((materialized.path / "references").stat().st_mode) == 0o755
    assert stat.S_IMODE((materialized.path / "SKILL.md").stat().st_mode) == 0o644
    assert stat.S_IMODE((materialized.path / "references" / "guide.md").stat().st_mode) == 0o644


def test_worker_rejects_overlong_legacy_skill_pin_before_filesystem_write(tmp_path):
    overlong_path = f"references/{'测' * 85}.md"
    pin = {
        "content_hash": "legacy-hash",
        "files": [
            {
                "relative_path": overlong_path,
                "content_base64": base64.b64encode(b"legacy").decode("ascii"),
                "size_bytes": 6,
            }
        ],
    }

    selected, mismatches = select_pinned_skill_snapshots(
        [],
        ["legacy-skill"],
        {"legacy-skill": pin},
        tmp_path / "workspace" / ".pins" / "skills",
        materialize=materialize_worker_pinned_skill,
    )

    assert selected == []
    assert mismatches == [
        {
            "skill_id": "legacy-skill",
            "expected_content_hash": "legacy-hash",
            "actual_content_hash": "",
            "reason": "invalid pinned skill file path: legacy-skill",
        }
    ]

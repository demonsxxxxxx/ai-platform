import base64

import pytest

from app.execution.api import artifact_type
from app.executors.claude_agent_worker import _select_pinned_skills


@pytest.mark.parametrize("filename", ["report.txt", "summary.md"])
def test_worker_keeps_text_artifact_classification_after_tuple_suffix_check(filename):
    assert artifact_type(filename) == "report_txt"


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

    selected, mismatches = _select_pinned_skills(
        [],
        ["legacy-skill"],
        {"legacy-skill": pin},
        tmp_path / "workspace" / ".pins" / "skills",
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

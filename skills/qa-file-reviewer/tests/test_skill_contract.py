from pathlib import Path
import json
import subprocess
import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
WORKFLOW_GUIDE = SKILL_DIR / "references" / "workflow_guide.md"
BRANCH_AGENTS = SKILL_DIR / "references" / "branch_agents.md"
PROMPT_TEMPLATES = SKILL_DIR / "references" / "prompt_templates.md"
RECORD_DECISION = SKILL_DIR / "scripts" / "record_agent_review_decision.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skill_default_path_requires_agent_multi_review() -> None:
    text = _read(SKILL_MD)

    assert "## Required References" in text
    assert "## Default Review Path" in text
    assert "Claude Agent SDK `Agent`" in text
    assert "default path" in text.lower()
    assert "fast deterministic" in text.lower()
    assert "only when the Agent tool is unavailable" in text
    assert "only when the user explicitly requests a fast deterministic review" in text
    assert "qa-structure-reviewer" in text
    assert "qa-zh-language-reviewer" in text
    assert "qa-en-language-reviewer" in text
    assert "qa-bilingual-reviewer" in text
    assert "qa-data-consistency-reviewer" in text
    assert "qa-risk-classifier" in text
    assert "final merge reviewer" in text.lower()
    assert "--final-reviewer-completed false" in text
    assert "--final-reviewer-completed true" in text
    assert "downgrade reason" in text.lower()


def test_skill_no_longer_uses_fast_path_as_default_heading() -> None:
    text = _read(SKILL_MD)

    assert "## Fast Path" not in text
    assert "Do not read all reference files before starting." not in text


def test_references_describe_forced_multi_agent_flow_and_downgrade_logging() -> None:
    workflow = _read(WORKFLOW_GUIDE)
    branches = _read(BRANCH_AGENTS)
    prompts = _read(PROMPT_TEMPLATES)

    assert "深度审核" in workflow
    assert "平台全权审核" in workflow
    assert "强制走 Claude Agent SDK `Agent` 多 reviewer 路径" in workflow
    assert "降级原因" in workflow
    assert "qa-risk-classifier" in branches
    assert "至少收齐" in branches
    assert "final merge reviewer" in branches.lower()
    assert "必须把结果写成 JSON 文件" in prompts
    assert "risk classifier" in prompts.lower()


def test_record_agent_review_decision_requires_downgrade_reason(tmp_path: Path) -> None:
    output_json = tmp_path / "agent_routing_record.json"

    failed = subprocess.run(
        [
            sys.executable,
            str(RECORD_DECISION),
            str(output_json),
            "--mode",
            "fast_deterministic_downgrade",
            "--requested-review",
            "deep_review",
            "--agent-tool-available",
            "false",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "downgrade reason is required" in failed.stderr.lower()

    succeeded = subprocess.run(
        [
            sys.executable,
            str(RECORD_DECISION),
            str(output_json),
            "--mode",
            "fast_deterministic_downgrade",
            "--requested-review",
            "deep_review",
            "--agent-tool-available",
            "false",
            "--reason",
            "Agent tool unavailable in current runtime.",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert succeeded.returncode == 0, succeeded.stderr

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["execution_mode"] == "fast_deterministic_downgrade"
    assert payload["downgrade_reason"] == "Agent tool unavailable in current runtime."
    assert payload["agent_tool_available"] is False
    assert payload["final_reviewer"] == "qa-final-merge-reviewer"
    assert payload["final_reviewer_completed"] is False

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
PYPROJECT = ROOT / "pyproject.toml"
AGENT_RULES = ROOT / "AGENTS.md"
ISSUE_WORKFLOW = ROOT / "docs" / "agent-rules" / "github-issue-pr-workflow.md"


def test_backend_required_check_is_stable_for_every_main_pull_request():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    pull_request_block = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]
    assert "branches:" in pull_request_block
    assert "- main" in pull_request_block
    assert "paths:" not in pull_request_block
    assert "name: backend required" in workflow
    assert "python -m compileall -q app tools scripts" in workflow
    assert "tests/test_b2_sandbox_readiness.py" in workflow
    assert "tests/test_backend_ci_workflow.py" in workflow
    assert "tests/test_governance_readiness.py" in workflow
    assert "tests/test_release_authority.py" in workflow
    assert "tests/test_contract.py" in workflow
    assert "tests/test_worker_main.py" in workflow


def test_backend_required_check_runs_on_every_main_push():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    push_block = workflow.split("push:", 1)[1].split("workflow_dispatch:", 1)[0]
    assert "branches:" in push_block
    assert "- main" in push_block
    assert "paths:" not in push_block


def test_ruff_is_pinned_in_the_test_extra_without_enabling_broad_linting():
    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    ruff_dependencies = [dependency for dependency in test_dependencies if dependency.startswith("ruff")]

    assert ruff_dependencies == ["ruff==0.11.13"]
    assert all(not dependency.startswith("ruff") for dependency in pyproject["project"]["dependencies"])


def test_backend_required_contract_preserves_high_risk_design_triggers():
    guidance = "\n".join(
        [
            AGENT_RULES.read_text(encoding="utf-8"),
            ISSUE_WORKFLOW.read_text(encoding="utf-8"),
        ]
    )

    for trigger in [
        "security",
        "auth",
        "tenant isolation",
        "release",
        "deployment",
        "runtime",
    ]:
        assert re.search(
            r"Create a separate design for.{0,160}" + re.escape(trigger),
            guidance,
            re.IGNORECASE | re.DOTALL,
        ), trigger

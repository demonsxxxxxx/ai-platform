import os
import re
import subprocess
import sys
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


def test_code_governance_uses_trusted_base_code_for_an_exact_pr_range():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha || github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "pull_request_target:" not in workflow
    assert "ref: ${{ github.event.pull_request.head.sha" not in workflow

    governance_step = workflow.split("- name: Run code governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]
    install_start = workflow.index("- name: Install backend dependencies")
    governance_start = workflow.index("- name: Run code governance")
    assert workflow.index("ref: ${{ github.event.pull_request.base.sha || github.sha }}") < install_start
    assert install_start < governance_start
    assert "if: github.event_name == 'pull_request'" in governance_step
    assert "GOVERNANCE_BASE_REF: ${{ github.event.pull_request.base.sha }}" in governance_step
    assert "GOVERNANCE_HEAD_REF: ${{ github.event.pull_request.head.sha }}" in governance_step
    assert "GOVERNANCE_FETCH_TOKEN: ${{ github.token }}" in governance_step
    assert 'PYTHONSAFEPATH: "1"' in governance_step
    assert "set -euo pipefail" in governance_step
    assert '[[ "$GOVERNANCE_BASE_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert '[[ "$GOVERNANCE_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert 'git -c http.https://github.com/.extraheader="AUTHORIZATION: bearer $GOVERNANCE_FETCH_TOKEN" fetch --no-tags origin "$GOVERNANCE_HEAD_REF"' in governance_step
    assert "unset GOVERNANCE_FETCH_TOKEN" in governance_step
    assert 'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"' in governance_step
    assert 'git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"' in governance_step
    assert 'git worktree add --detach "$GOVERNANCE_HEAD_WORKTREE" "$GOVERNANCE_HEAD_REF"' in governance_step
    assert 'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check' in governance_step
    assert "python tools/code_governance.py" not in governance_step
    assert "git checkout" not in governance_step
    assert '--base-ref "$GOVERNANCE_BASE_REF"' in governance_step
    assert '--head-ref "$GOVERNANCE_HEAD_REF"' in governance_step
    assert "--format text" in governance_step

    governance_run = governance_step.split("run: |", 1)[1]
    assert "${{" not in governance_run
    assert "github.event.pull_request.head.ref" not in workflow
    assert "github.event.pull_request.base.ref" not in workflow


def test_python_safe_path_blocks_a_head_root_ruff_module(tmp_path: Path):
    (tmp_path / "ruff.py").write_text('raise RuntimeError("head ruff.py was imported")\n', encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONSAFEPATH"] = "1"
    environment.pop("PYTHONPATH", None)

    child = "import subprocess, sys; raise SystemExit(subprocess.run([sys.executable, '-m', 'ruff', '--version']).returncode)"
    completed = subprocess.run(
        [sys.executable, "-P", "-c", child],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "head ruff.py was imported" not in completed.stderr


def test_code_governance_uses_test_extra_and_propagates_pr_failures():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    install_start = workflow.index("- name: Install backend dependencies")
    governance_start = workflow.index("- name: Run code governance")
    governance_step = workflow[governance_start : workflow.index("- name: Run sandbox provider targeted tests")]

    assert install_start < governance_start
    assert 'pyproject["project"]["optional-dependencies"]["test"]' in workflow
    assert "continue-on-error:" not in governance_step
    assert "|| true" not in governance_step
    assert "set +e" not in governance_step
    assert "ruff check ." not in workflow
    assert "ruff format" not in workflow


def test_existing_pr_checks_switch_to_the_validated_head_after_governance():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    governance_start = workflow.index("- name: Run code governance")
    head_checkout_start = workflow.index("- name: Checkout validated pull request head for existing checks")
    compile_start = workflow.index("- name: Compile backend sources")
    pytest_start = workflow.index("- name: Run sandbox provider targeted tests")
    head_checkout = workflow[head_checkout_start:pytest_start]

    assert governance_start < head_checkout_start < compile_start < pytest_start
    assert "if: github.event_name == 'pull_request'" in head_checkout
    assert "VALIDATED_PR_HEAD_REF: ${{ github.event.pull_request.head.sha }}" in head_checkout
    assert '[[ "$VALIDATED_PR_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in head_checkout
    assert 'test "$(git rev-parse "$VALIDATED_PR_HEAD_REF^{commit}")" = "$VALIDATED_PR_HEAD_REF"' in head_checkout
    assert 'git checkout --detach "$VALIDATED_PR_HEAD_REF"' in head_checkout


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

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
FRONTEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"
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
    assert "needs: [sandbox-provider, backend-image]" in workflow
    assert "name: packaged backend image build" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "python -m compileall -q app tools scripts" in workflow
    assert "tests/test_b2_sandbox_readiness.py" in workflow
    assert "tests/test_backend_ci_workflow.py" in workflow
    assert "tests/test_packaging_publish_workflow.py" in workflow
    assert "tests/test_release_image_manifest.py" in workflow
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
    import tomllib

    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    ruff_dependencies = [
        requirement
        for dependency in test_dependencies
        if canonicalize_name((requirement := Requirement(dependency)).name) == "ruff"
    ]

    assert [str(requirement) for requirement in ruff_dependencies] == ["ruff==0.11.13"]
    assert all(
        canonicalize_name(Requirement(dependency).name) != "ruff"
        for dependency in pyproject["project"]["dependencies"]
    )


def _frontend_ruff_requirement_resolver():
    workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install Python test dependencies", 1)[1].split(
        "- name: Verify static frontend Python contracts", 1
    )[0]
    install_script = install_step.split("@'\n", 1)[1].split("\n'@ | python -", 1)[0]
    resolver_source = textwrap.dedent(
        install_script.split('with open("pyproject.toml", "rb") as handle:', 1)[0]
    )
    namespace: dict[str, object] = {}
    exec(resolver_source, namespace)  # noqa: S102 -- executes only the repository workflow snippet under test.
    return namespace["resolve_ruff_requirement"]


def test_frontend_static_contracts_install_only_the_pinned_test_extra_ruff():
    import tomllib

    workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    install_step = workflow.split("- name: Install Python test dependencies", 1)[1].split(
        "- name: Verify static frontend Python contracts", 1
    )[0]

    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    assert 'tomllib.load(handle)["project"]["optional-dependencies"]["test"]' in install_step
    assert "from packaging.requirements import InvalidRequirement, Requirement" in install_step
    assert "from packaging.utils import canonicalize_name" in install_step
    assert "for dependency in test_dependencies:" in install_step
    assert 'canonicalize_name(requirement.name) == "ruff"' in install_step
    assert "if len(ruff_requirements) != 1:" in install_step
    assert "CANONICAL_RUFF_VERSION.fullmatch(version)" in install_step
    assert 'return f"ruff=={version}"' in install_step
    assert '[sys.executable, "-m", "pip", "install", ruff_requirement]' in install_step
    assert "*test_dependencies" not in install_step
    assert '["project"]["dependencies"]' not in install_step
    assert "tests/test_backend_ci_workflow.py" in workflow

    resolver = _frontend_ruff_requirement_resolver()
    assert resolver(test_dependencies) == "ruff==0.11.13"


def test_frontend_ruff_requirement_resolver_normalizes_a_canonical_pin():
    resolver = _frontend_ruff_requirement_resolver()

    assert resolver(["pytest>=8.2.0", "Ruff == 0.11.13"]) == "ruff==0.11.13"


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        pytest.param(["ruff"], "exact canonical version pin", id="unpinned"),
        pytest.param(
            ["ruff==0.11.13", "ruff @ https://example.invalid/ruff.whl"],
            "expected exactly one Ruff test dependency",
            id="multiple-including-url",
        ),
        pytest.param(
            ['ruff==0.11.13; python_version >= "3.11"'],
            "exact canonical version pin",
            id="marker",
        ),
        pytest.param(["ruff[cli]==0.11.13"], "exact canonical version pin", id="extras"),
        pytest.param(["ruff===0.11.13"], "exact canonical version pin", id="arbitrary-equals"),
        pytest.param(["ruff==0.11.*"], "exact canonical version pin", id="wildcard"),
        pytest.param(["ruff>=0.11.13"], "exact canonical version pin", id="range"),
        pytest.param(["ruff==00.11.13"], "exact canonical version pin", id="noncanonical-version"),
    ],
)
def test_frontend_ruff_requirement_resolver_rejects_noncanonical_declarations(
    dependencies: list[str], message: str
):
    resolver = _frontend_ruff_requirement_resolver()

    with pytest.raises(RuntimeError, match=message):
        resolver(["pytest>=8.2.0", *dependencies])


def test_code_governance_uses_trusted_base_code_for_an_exact_pr_range():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "fetch-depth: 0" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha || github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "persist-credentials: true" not in workflow
    assert "pull_request_target:" not in workflow

    governance_step = workflow.split("- name: Run code governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]
    install_start = workflow.index("- name: Install trusted-base governance dependency")
    governance_start = workflow.index("- name: Run code governance")
    pre_governance = workflow[:governance_start]
    assert (
        workflow.index("ref: ${{ github.event.pull_request.base.sha || github.sha }}")
        < install_start
    )
    assert install_start < governance_start
    assert "github.event.pull_request.head" not in pre_governance
    assert "refs/pull/" not in pre_governance
    assert "if: github.event_name == 'pull_request'" in governance_step
    assert "GOVERNANCE_PR_NUMBER: ${{ github.event.number }}" in governance_step
    assert (
        "GOVERNANCE_BASE_REF: ${{ github.event.pull_request.base.sha }}"
        in governance_step
    )
    assert (
        "GOVERNANCE_HEAD_REF: ${{ github.event.pull_request.head.sha }}"
        in governance_step
    )
    assert "GOVERNANCE_FETCH_TOKEN: ${{ github.token }}" in governance_step
    assert 'PYTHONSAFEPATH: "1"' in governance_step
    assert "set -euo pipefail" in governance_step
    assert '[[ "$GOVERNANCE_PR_NUMBER" =~ ^[1-9][0-9]*$ ]]' in governance_step
    assert '[[ "$GOVERNANCE_BASE_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert '[[ "$GOVERNANCE_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in governance_step
    assert (
        'GOVERNANCE_PULL_REF="refs/remotes/origin/pull/$GOVERNANCE_PR_NUMBER/head"'
        in governance_step
    )
    assert (
        'GOVERNANCE_FETCH_BASIC="$(printf \'x-access-token:%s\' "$GOVERNANCE_FETCH_TOKEN" '
        '| base64 --wrap=0)"' in governance_step
    )
    assert 'echo "::add-mask::$GOVERNANCE_FETCH_BASIC"' in governance_step
    fetch_command = (
        'git -c http.https://github.com/.extraheader="AUTHORIZATION: basic '
        '$GOVERNANCE_FETCH_BASIC" fetch --no-tags origin '
        '"+refs/pull/$GOVERNANCE_PR_NUMBER/head:$GOVERNANCE_PULL_REF"'
    )
    assert fetch_command in governance_step
    assert "unset GOVERNANCE_FETCH_TOKEN GOVERNANCE_FETCH_BASIC" in governance_step
    assert (
        'test "$(git rev-parse "$GOVERNANCE_PULL_REF^{commit}")" = '
        '"$GOVERNANCE_HEAD_REF"' in governance_step
    )
    assert 'git cat-file -e "$GOVERNANCE_BASE_REF^{commit}"' in governance_step
    assert 'git cat-file -e "$GOVERNANCE_HEAD_REF^{commit}"' in governance_step
    assert (
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
        in governance_step
    )
    assert (
        'git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"'
        in governance_step
    )
    assert (
        'git worktree add --detach "$GOVERNANCE_HEAD_WORKTREE" "$GOVERNANCE_HEAD_REF"'
        in governance_step
    )
    assert (
        'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check'
        in governance_step
    )
    assert "python tools/code_governance.py" not in governance_step
    assert "git checkout" not in governance_step
    assert '--base-ref "$GOVERNANCE_BASE_REF"' in governance_step
    assert '--head-ref "$GOVERNANCE_HEAD_REF"' in governance_step
    assert "--format text" in governance_step

    governance_run = governance_step.split("run: |", 1)[1]
    assert "${{" not in governance_run
    assert "github.event.pull_request.head.ref" not in workflow
    assert "github.event.pull_request.base.ref" not in workflow
    assert "github.head_ref" not in workflow

    governance_lines = [
        line.strip() for line in governance_run.splitlines() if line.strip()
    ]
    fetch_index = governance_lines.index(fetch_command)
    unset_index = governance_lines.index(
        "unset GOVERNANCE_FETCH_TOKEN GOVERNANCE_FETCH_BASIC"
    )
    fetched_ref_check_index = governance_lines.index(
        'test "$(git rev-parse "$GOVERNANCE_PULL_REF^{commit}")" = "$GOVERNANCE_HEAD_REF"'
    )
    ancestry_index = governance_lines.index(
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"'
    )
    base_worktree_index = governance_lines.index(
        'git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"'
    )
    governance_command_index = next(
        index
        for index, line in enumerate(governance_lines)
        if 'python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check'
        in line
    )
    assert (
        governance_lines.index('echo "::add-mask::$GOVERNANCE_FETCH_BASIC"')
        < fetch_index
    )
    assert unset_index == fetch_index + 1
    assert fetch_index < fetched_ref_check_index < ancestry_index < base_worktree_index
    assert base_worktree_index < governance_command_index


def test_code_governance_rejects_credential_and_untrusted_ref_fallbacks():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    governance_step = workflow.split("- name: Run code governance", 1)[1].split(
        "- name: Checkout validated pull request head for existing checks", 1
    )[0]
    normalized = governance_step.lower()

    assert "authorization: bearer" not in normalized
    assert 'fetch --no-tags origin "$governance_head_ref"' not in normalized
    assert "refs/heads/" not in normalized
    assert "git config" not in normalized
    assert "--local" not in normalized
    assert "--global" not in normalized
    assert "http.extraheader" not in normalized
    assert "continue-on-error:" not in governance_step
    assert "|| true" not in governance_step
    assert "set +e" not in governance_step


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


def test_code_governance_uses_exact_trusted_base_bootstrap_and_propagates_pr_failures():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    install_start = workflow.index("- name: Install trusted-base governance dependency")
    governance_start = workflow.index("- name: Run code governance")
    governance_step = workflow[governance_start : workflow.index("- name: Run sandbox provider targeted tests")]

    assert install_start < governance_start
    assert "python -m pip install ruff==0.11.13" in workflow
    assert "python -m pip install --upgrade pip" not in workflow
    assert "uv lock --check" in workflow
    assert "uv sync --locked --extra test --no-install-project" in workflow
    assert "continue-on-error:" not in governance_step
    assert "|| true" not in governance_step
    assert "set +e" not in governance_step
    assert "ruff check ." not in workflow
    assert "ruff format" not in workflow


def test_existing_pr_checks_switch_to_the_validated_head_after_governance():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    governance_start = workflow.index("- name: Run code governance")
    head_checkout_start = workflow.index(
        "- name: Checkout validated pull request head for existing checks"
    )
    compile_start = workflow.index("- name: Compile backend sources")
    locked_install_start = workflow.index(
        "- name: Install candidate dependencies from the lock authority"
    )
    pytest_start = workflow.index("- name: Run sandbox provider targeted tests")
    head_checkout = workflow[head_checkout_start:pytest_start]

    assert (
        governance_start
        < head_checkout_start
        < locked_install_start
        < compile_start
        < pytest_start
    )
    assert "if: github.event_name == 'pull_request'" in head_checkout
    assert (
        "VALIDATED_PR_HEAD_REF: ${{ github.event.pull_request.head.sha }}"
        in head_checkout
    )
    assert '[[ "$VALIDATED_PR_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]' in head_checkout
    assert (
        'test "$(git rev-parse "$VALIDATED_PR_HEAD_REF^{commit}")" = "$VALIDATED_PR_HEAD_REF"'
        in head_checkout
    )
    assert 'git checkout --detach "$VALIDATED_PR_HEAD_REF"' in head_checkout
    assert 'test "$(git rev-parse HEAD)" = "$VALIDATED_PR_HEAD_REF"' in head_checkout


def test_backend_image_job_builds_every_candidate_and_checks_the_runtime_contract():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    image_job = workflow.split("  backend-image:", 1)[1].split("  required:", 1)[0]
    startup_step = image_job.split("- name: Verify backend image startup", 1)[1]

    assert "paths:" not in workflow
    assert "if:" not in image_job
    assert "needs: sandbox-provider" in image_job
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in image_job
    assert "persist-credentials: false" in image_job
    assert "IMAGE_SOURCE_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}" in image_job
    assert "- name: Resolve image source repository" in image_job
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in image_job
    assert '[[ "$IMAGE_SOURCE_HEAD_REPOSITORY" =~ ^[A-Za-z0-9]' in image_job
    assert 'image_source_repository="https://github.com/${IMAGE_SOURCE_HEAD_REPOSITORY}.git"' in image_job
    assert 'printf \'IMAGE_SOURCE_REPOSITORY=%s\\n\' "$image_source_repository" >> "$GITHUB_ENV"' in image_job
    assert "IMAGE_SOURCE_REPOSITORY: https://github.com/${{ github.repository }}.git" not in image_job
    assert "docker build" in image_job
    assert "-f Dockerfile" in image_job
    assert "uv.lock" not in image_job  # The real Docker build proves lock consumption.
    assert 'config["User"] == "10001:10001"' in image_job
    assert 'config["Entrypoint"] == ["/app/docker-entrypoint.sh"]' in image_job
    assert 'labels["org.opencontainers.image.revision"]' in image_job
    assert 'labels["ai-platform.source-repository"]' in image_job
    assert '--env IMAGE_SOURCE_COMMIT="$IMAGE_SOURCE_COMMIT"' in image_job
    assert "import app.main, claude_agent_sdk" in image_job
    assert "http://127.0.0.1:18020/api/ai/health" in image_job
    assert "python - <<'PY'" not in startup_step
    assert 'BACKEND_HEALTH_FILE="$RUNNER_TEMP/backend-health.json" python -c' in startup_step
    assert "backend_container_state=" in startup_step
    assert "docker logs --tail 80" in startup_step
    assert "backend_redacted_container_log_tail_lines=" in startup_step
    assert "backend_container_log_signal=redacted" in startup_step
    assert "| sed -E" not in startup_step
    assert "exit 1" in startup_step
    assert "docker push" not in image_job
    assert "docker compose" not in image_job.lower()


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

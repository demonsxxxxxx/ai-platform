from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.trusted_governance import (
    BACKEND_WORKFLOW_PATH,
    CANDIDATE_GATE_PATH,
    TRUSTED_RUNNER_PATH,
    TRUSTED_WORKFLOW_PATH,
    TrustedGovernanceError,
    main,
    validate_transition,
    validate_trusted_workflow,
)


ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        input=None if input_text is None else input_text.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode().strip()


def _commit(root: Path, message: str = "fixture") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _initialize_repository(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Trusted Governance Test")
    _git(root, "config", "user.email", "trusted-governance@example.invalid")
    _commit(root)


def _copy_contract_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    shutil.copytree(ROOT / ".github/workflows", root / ".github/workflows")
    for relative_path in (
        TRUSTED_RUNNER_PATH,
        CANDIDATE_GATE_PATH,
    ):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, target)
    _initialize_repository(root)
    return root


def _replace(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(before) == 1
    path.write_text(text.replace(before, after), encoding="utf-8")


def _write_backend(root: Path, mode: str, *, duplicate_governance: bool = False) -> None:
    validation_id = "backend-validation" if mode == "legacy" else "backend-preflight"
    validation_name = (
        "backend validation and governance" if mode == "legacy" else "backend preflight"
    )
    validation_result = "VALIDATION_RESULT" if mode == "legacy" else "PREFLIGHT_RESULT"
    validation_step = (
        "      - name: Run code and architecture governance\n"
        "        run: python -P /accepted/tools/code_governance.py check\n"
        if mode == "legacy"
        else "      - name: Compile backend\n"
        "        run: python -m compileall -q app tools scripts\n"
    )
    if duplicate_governance:
        validation_step += (
            "      - name: Hidden duplicate\n"
            "        run: python tools/architecture_governance.py check\n"
        )
    payload = f"""name: backend fixture
permissions:
  contents: read
jobs:
  {validation_id}:
    name: {validation_name}
    steps:
{validation_step}  backend-tests:
    name: tests
    needs: {validation_id}
  agent-skill-contracts:
    name: contracts
    needs: {validation_id}
  backend-image:
    name: image
    needs: {validation_id}
  required:
    name: backend required
    runs-on: ubuntu-latest
    needs: [{validation_id}, backend-tests, agent-skill-contracts, backend-image]
    if: ${{{{ always() }}}}
    timeout-minutes: 5
    steps:
      - name: Require backend, Agent/Skill, and packaged image acceptance
        env:
          {validation_result}: ${{{{ needs.{validation_id}.result }}}}
          BACKEND_TESTS_RESULT: ${{{{ needs.backend-tests.result }}}}
          AGENT_SKILL_RESULT: ${{{{ needs.agent-skill-contracts.result }}}}
          IMAGE_RESULT: ${{{{ needs.backend-image.result }}}}
        run: |
          test "${validation_result}" = "success"
          test "$BACKEND_TESTS_RESULT" = "success"
          test "$AGENT_SKILL_RESULT" = "success"
          test "$IMAGE_RESULT" = "success"
"""
    path = root / BACKEND_WORKFLOW_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    _commit(root, f"{mode} backend")


def test_repository_v2_contract_accepts_the_current_preflight_backend(
    tmp_path: Path,
) -> None:
    root = _copy_contract_root(tmp_path, "current")
    validate_trusted_workflow(root)
    validate_transition(root, root, changed_paths=())


def test_v2_contract_rejects_duplicate_workflow_keys(tmp_path: Path) -> None:
    root = _copy_contract_root(tmp_path, "duplicate")
    path = root / TRUSTED_WORKFLOW_PATH
    _replace(
        path,
        "permissions:\n  contents: read\n",
        "permissions:\n  contents: read\npermissions:\n  contents: write\n",
    )
    _commit(root)

    with pytest.raises(TrustedGovernanceError, match="duplicate workflow key"):
        validate_trusted_workflow(root)


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        (
            "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "uses: attacker/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "accepted actions/checkout commit",
        ),
        (
            "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "uses: actions/setup-python@0000000000000000000000000000000000000000",
            "accepted actions/setup-python commit",
        ),
        (
            "permissions:\n  contents: read",
            "permissions:\n  contents: write",
            "permissions changed",
        ),
        (
            "python -m pip install ruff==0.11.13 PyYAML==6.0.3",
            "python -m pip install ruff PyYAML requests",
            "dependency command is not accepted",
        ),
        (
            'GOVERNANCE_PYTHON_VERSION: "3.13.14"',
            'GOVERNANCE_PYTHON_VERSION: "3.14.0"',
            "Python version is not accepted",
        ),
        (
            "fetch-depth: 0",
            "fetch-depth: 1",
            "checkout inputs changed",
        ),
    ],
)
def test_v2_contract_rejects_weakened_trusted_workflow(
    tmp_path: Path, before: str, after: str, message: str
) -> None:
    root = _copy_contract_root(tmp_path, "weakened")
    _replace(root / TRUSTED_WORKFLOW_PATH, before, after)
    _commit(root)

    with pytest.raises(TrustedGovernanceError, match=message):
        validate_trusted_workflow(root)


def test_v2_contract_rejects_candidate_launcher_changes(tmp_path: Path) -> None:
    root = _copy_contract_root(tmp_path, "launcher")
    _replace(
        root / TRUSTED_WORKFLOW_PATH,
        'git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"',
        'git merge-base "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"',
    )
    _commit(root)

    with pytest.raises(TrustedGovernanceError, match="launcher changed"):
        validate_trusted_workflow(root)


def test_candidate_context_permissions_and_launcher_are_fixed(tmp_path: Path) -> None:
    permissions = _copy_contract_root(tmp_path, "candidate-permissions")
    _replace(
        permissions / TRUSTED_WORKFLOW_PATH,
        "      deployments: read\n      pull-requests: read",
        "      deployments: write\n      pull-requests: read",
    )
    _commit(permissions)
    with pytest.raises(TrustedGovernanceError, match="candidate permissions changed"):
        validate_trusted_workflow(permissions)

    launcher = _copy_contract_root(tmp_path, "candidate-launcher")
    _replace(
        launcher / TRUSTED_WORKFLOW_PATH,
        'python -P "$GITHUB_WORKSPACE/tools/sse_candidate_gate.py"',
        'python -P "$GOVERNANCE_HEAD_WORKTREE/tools/sse_candidate_gate.py"',
    )
    _commit(launcher)
    with pytest.raises(TrustedGovernanceError, match="candidate launcher changed"):
        validate_trusted_workflow(launcher)


def test_candidate_gate_logic_requires_a_governance_migration(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "candidate-gate-base")
    head = _copy_contract_root(tmp_path, "candidate-gate-head")
    _replace(
        head / CANDIDATE_GATE_PATH,
        'ENVIRONMENT = "sse-candidate"',
        'ENVIRONMENT = "other"',
    )
    _commit(head)

    with pytest.raises(TrustedGovernanceError, match="gate executable logic changed"):
        validate_transition(
            base,
            head,
            changed_paths=(CANDIDATE_GATE_PATH.as_posix(),),
        )


def test_designated_candidate_delivery_writer_is_narrowly_admitted(
    tmp_path: Path,
) -> None:
    base = _copy_contract_root(tmp_path, "writer-base")
    head = _copy_contract_root(tmp_path, "writer-head")
    writer_path = head / ".github/workflows/ai-platform-sse-candidate-delivery.yml"
    writer_path.write_text(
        "name: ai-platform SSE candidate delivery\n"
        "on:\n  workflow_dispatch:\n    inputs:\n"
        "      pr_number:\n        required: true\n        type: string\n"
        "permissions:\n  contents: read\n"
        "jobs:\n"
        "  build:\n    name: build quarantined candidate\n"
        "    runs-on: ubuntu-24.04\n"
        "    permissions:\n      contents: read\n      packages: write\n"
        "    steps:\n      - run: true\n"
        "  publish-deployment:\n    name: publish candidate Deployment\n"
        "    runs-on: ubuntu-24.04\n"
        "    environment: sse-candidate-delivery\n"
        "    permissions:\n      actions: read\n      contents: read\n"
        "      deployments: write\n      pull-requests: read\n"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(head)

    validate_transition(
        base,
        head,
        changed_paths=(".github/workflows/ai-platform-sse-candidate-delivery.yml",),
    )


@pytest.mark.parametrize(
    ("trigger", "environment", "extra_permission", "reason"),
    [
        ("pull_request_target", "sse-candidate-delivery", "", "manual dispatch"),
        ("workflow_dispatch", "production", "", "writer isolation"),
        (
            "workflow_dispatch",
            "sse-candidate-delivery",
            "      statuses: write\n",
            "writer is forbidden",
        ),
    ],
)
def test_designated_candidate_delivery_writer_rejects_unsafe_authority(
    tmp_path: Path,
    trigger: str,
    environment: str,
    extra_permission: str,
    reason: str,
) -> None:
    base = _copy_contract_root(tmp_path, f"unsafe-writer-base-{trigger}")
    head = _copy_contract_root(tmp_path, f"unsafe-writer-head-{trigger}")
    writer_path = head / ".github/workflows/ai-platform-sse-candidate-delivery.yml"
    writer_path.write_text(
        "name: ai-platform SSE candidate delivery\n"
        f"on:\n  {trigger}: {{}}\n"
        "permissions:\n  contents: read\n"
        "jobs:\n  publish-deployment:\n"
        "    name: publish candidate Deployment\n"
        "    runs-on: ubuntu-24.04\n"
        f"    environment: {environment}\n"
        "    permissions:\n      contents: read\n      deployments: write\n"
        f"{extra_permission}"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(head)

    with pytest.raises(TrustedGovernanceError, match=reason):
        validate_transition(
            base,
            head,
            changed_paths=(
                ".github/workflows/ai-platform-sse-candidate-delivery.yml",
            ),
        )


def test_alternate_candidate_context_or_writer_is_rejected(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "alternate-base")

    writer = _copy_contract_root(tmp_path, "alternate-writer")
    writer_path = writer / ".github/workflows/alternate.yml"
    writer_path.write_text(
        "name: alternate\non: pull_request\npermissions:\n  deployments: write\n"
        "jobs:\n  alternate:\n    name: alternate\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(writer)
    with pytest.raises(TrustedGovernanceError, match="writer is forbidden"):
        validate_transition(
            base,
            writer,
            changed_paths=(".github/workflows/alternate.yml",),
        )

    merged = _copy_contract_root(tmp_path, "merged-writer")
    merged_path = merged / ".github/workflows/merged.yml"
    merged_path.write_text(
        "name: merged\non: pull_request\npermissions:\n  contents: read\n"
        "writer: &writer\n  deployments: write\n"
        "jobs:\n  alternate:\n    name: alternate\n    runs-on: ubuntu-latest\n"
        "    permissions:\n      <<: *writer\n      contents: read\n"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(merged)
    with pytest.raises(TrustedGovernanceError, match="merge keys are forbidden"):
        validate_transition(
            base,
            merged,
            changed_paths=(".github/workflows/merged.yml",),
        )

    inherited = _copy_contract_root(tmp_path, "inherited-writer")
    inherited_path = inherited / ".github/workflows/inherited.yml"
    inherited_path.write_text(
        "name: inherited\non: pull_request\n"
        "jobs:\n  alternate:\n    name: alternate\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(inherited)
    with pytest.raises(TrustedGovernanceError, match="explicit workflow permissions"):
        validate_transition(
            base,
            inherited,
            changed_paths=(".github/workflows/inherited.yml",),
        )

    duplicate = _copy_contract_root(tmp_path, "alternate-context")
    duplicate_path = duplicate / ".github/workflows/alternate.yml"
    duplicate_path.write_text(
        "name: alternate\non: pull_request\npermissions:\n  contents: read\n"
        "jobs:\n  alternate:\n    name: sse ${{ matrix.middle }}\n"
        "    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
        "        middle: [candidate acceptance]\n"
        "    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    _commit(duplicate)
    with pytest.raises(TrustedGovernanceError, match="context must be unique"):
        validate_transition(
            base,
            duplicate,
            changed_paths=(".github/workflows/alternate.yml",),
        )


def test_v2_contract_rejects_deleted_candidate_workflow(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = tmp_path / "head"
    for relative_path in (
        BACKEND_WORKFLOW_PATH,
        TRUSTED_RUNNER_PATH,
        CANDIDATE_GATE_PATH,
    ):
        target = head / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, target)
    _initialize_repository(head)

    with pytest.raises(TrustedGovernanceError, match="protected file is missing"):
        validate_transition(
            base,
            head,
            changed_paths=(TRUSTED_WORKFLOW_PATH.as_posix(),),
        )


def test_backend_transition_is_monotonic(tmp_path: Path) -> None:
    legacy = _copy_contract_root(tmp_path, "legacy")
    preflight = _copy_contract_root(tmp_path, "preflight")
    _write_backend(legacy, "legacy")
    _write_backend(preflight, "preflight")

    validate_transition(
        legacy,
        preflight,
        changed_paths=(BACKEND_WORKFLOW_PATH.as_posix(),),
    )
    validate_transition(preflight, preflight, changed_paths=())
    with pytest.raises(TrustedGovernanceError, match="cannot return to legacy"):
        validate_transition(
            preflight,
            legacy,
            changed_paths=(BACKEND_WORKFLOW_PATH.as_posix(),),
        )


def test_backend_preflight_rejects_duplicate_governance(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    _write_backend(base, "preflight")
    _write_backend(head, "preflight", duplicate_governance=True)

    with pytest.raises(TrustedGovernanceError, match="must not duplicate"):
        validate_transition(
            base,
            head,
            changed_paths=(BACKEND_WORKFLOW_PATH.as_posix(),),
        )


def test_backend_required_dependencies_remain_fail_closed(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    _replace(
        head / BACKEND_WORKFLOW_PATH,
        "needs: [backend-preflight, backend-tests, agent-skill-contracts, backend-image]",
        "needs: [backend-preflight]",
    )
    _commit(head)

    with pytest.raises(TrustedGovernanceError, match="required dependencies changed"):
        validate_transition(
            base,
            head,
            changed_paths=(BACKEND_WORKFLOW_PATH.as_posix(),),
        )


def test_backend_required_assertions_remain_fail_closed(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    _replace(
        head / BACKEND_WORKFLOW_PATH,
        'test "$IMAGE_RESULT" = "success"',
        "true",
    )
    _commit(head)

    with pytest.raises(TrustedGovernanceError, match="required assertions changed"):
        validate_transition(
            base,
            head,
            changed_paths=(BACKEND_WORKFLOW_PATH.as_posix(),),
        )


def test_trusted_runner_rejects_candidate_logic_changes(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    _replace(
        head / TRUSTED_RUNNER_PATH,
        'return mode\n\n\ndef _policy_is_expansion',
        'return "legacy"\n\n\ndef _policy_is_expansion',
    )
    _commit(head)

    with pytest.raises(TrustedGovernanceError, match="executable logic changed"):
        validate_transition(
            base,
            head,
            changed_paths=(TRUSTED_RUNNER_PATH.as_posix(),),
        )


def test_trusted_runner_accepts_only_standalone_allowlist_expansion(
    tmp_path: Path,
) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    _replace(
        head / TRUSTED_RUNNER_PATH,
        '"actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1",),',
        '"actions/checkout": ('
        '"3d3c42e5aac5ba805825da76410c181273ba90b1", '
        '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),',
    )
    _commit(head)

    validate_transition(
        base,
        head,
        changed_paths=(TRUSTED_RUNNER_PATH.as_posix(),),
    )
    with pytest.raises(TrustedGovernanceError, match="must be standalone"):
        validate_transition(
            base,
            head,
            changed_paths=(TRUSTED_RUNNER_PATH.as_posix(), "README.md"),
        )


def test_protected_workflow_symlink_is_rejected_from_git_tree(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = _copy_contract_root(tmp_path, "head")
    blob = _git(
        head,
        "hash-object",
        "-w",
        "--stdin",
        input_text="../../accepted-base/ai-platform-trusted-governance-v2.yml",
    )
    _git(
        head,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{blob},{TRUSTED_WORKFLOW_PATH.as_posix()}",
    )
    _git(head, "commit", "-q", "-m", "symlink workflow")

    with pytest.raises(TrustedGovernanceError, match="must be a regular blob"):
        validate_transition(
            base,
            head,
            changed_paths=(TRUSTED_WORKFLOW_PATH.as_posix(),),
        )


def test_cli_reports_a_bounded_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _copy_contract_root(tmp_path, "invalid")
    _replace(
        root / TRUSTED_WORKFLOW_PATH,
        "contents: read",
        "contents: write",
    )
    _commit(root)
    head = _git(root, "rev-parse", "HEAD")

    assert main(
        [
            "validate",
            "--base-root",
            str(root),
            "--head-root",
            str(root),
            "--base-ref",
            head,
            "--head-ref",
            head,
        ]
    ) == 2
    output = capsys.readouterr().out
    assert output.startswith("trusted_governance=failed reason=")
    assert str(tmp_path) not in output

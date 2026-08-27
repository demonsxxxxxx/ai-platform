from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.trusted_governance import (
    BACKEND_WORKFLOW_PATH,
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
    for relative_path in (
        TRUSTED_WORKFLOW_PATH,
        BACKEND_WORKFLOW_PATH,
        TRUSTED_RUNNER_PATH,
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


def test_repository_v2_contract_accepts_the_current_legacy_backend(
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


def test_v2_contract_rejects_deleted_candidate_workflow(tmp_path: Path) -> None:
    base = _copy_contract_root(tmp_path, "base")
    head = tmp_path / "head"
    for relative_path in (BACKEND_WORKFLOW_PATH, TRUSTED_RUNNER_PATH):
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
        "needs: [backend-validation, backend-tests, agent-skill-contracts, backend-image]",
        "needs: [backend-validation]",
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

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml


TRUSTED_WORKFLOW_PATH = Path(
    ".github/workflows/ai-platform-trusted-governance-v2.yml"
)
BACKEND_WORKFLOW_PATH = Path(".github/workflows/ai-platform-backend.yml")
TRUSTED_RUNNER_PATH = Path("tools/trusted_governance.py")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_POLICY_NAMES = frozenset(
    {
        "_ALLOWED_ACTIONS",
        "_ALLOWED_PYTHON_VERSIONS",
        "_ALLOWED_DEPENDENCY_COMMANDS",
    }
)
_ALLOWED_ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1",),
    "actions/setup-python": ("5fda3b95a4ea91299a34e894583c3862153e4b97",),
}
_ALLOWED_PYTHON_VERSIONS = ("3.13.14",)
_ALLOWED_DEPENDENCY_COMMANDS = (
    "python -m pip install ruff==0.11.13 PyYAML==6.0.3",
)

EXPECTED_GOVERNANCE_RUN = r"""set -euo pipefail
test "$GITHUB_REPOSITORY" = "demonsxxxxxx/ai-platform"
[[ "$GOVERNANCE_PR_NUMBER" =~ ^[1-9][0-9]*$ ]]
[[ "$GOVERNANCE_BASE_REF" =~ ^[0-9a-f]{40}$ ]]
[[ "$GOVERNANCE_HEAD_REF" =~ ^[0-9a-f]{40}$ ]]
GOVERNANCE_PULL_REF="refs/remotes/origin/pull/$GOVERNANCE_PR_NUMBER/head"
GOVERNANCE_FETCH_BASIC="$(printf 'x-access-token:%s' "$GOVERNANCE_FETCH_TOKEN" | base64 --wrap=0)"
echo "::add-mask::$GOVERNANCE_FETCH_BASIC"
git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $GOVERNANCE_FETCH_BASIC" fetch --no-tags origin "+refs/pull/$GOVERNANCE_PR_NUMBER/head:$GOVERNANCE_PULL_REF"
unset GOVERNANCE_FETCH_TOKEN GOVERNANCE_FETCH_BASIC
test "$(git rev-parse "$GOVERNANCE_PULL_REF^{commit}")" = "$GOVERNANCE_HEAD_REF"
git cat-file -e "$GOVERNANCE_BASE_REF^{commit}"
git cat-file -e "$GOVERNANCE_HEAD_REF^{commit}"
test "$(git rev-parse "$GOVERNANCE_BASE_REF^{commit}")" = "$GOVERNANCE_BASE_REF"
test "$(git rev-parse "$GOVERNANCE_HEAD_REF^{commit}")" = "$GOVERNANCE_HEAD_REF"
git merge-base --is-ancestor "$GOVERNANCE_BASE_REF" "$GOVERNANCE_HEAD_REF"
GOVERNANCE_BASE_WORKTREE="$RUNNER_TEMP/trusted-governance-v2-base"
GOVERNANCE_HEAD_WORKTREE="$RUNNER_TEMP/trusted-governance-v2-head"
git worktree add --detach "$GOVERNANCE_BASE_WORKTREE" "$GOVERNANCE_BASE_REF"
git worktree add --detach "$GOVERNANCE_HEAD_WORKTREE" "$GOVERNANCE_HEAD_REF"
python -P "$GOVERNANCE_BASE_WORKTREE/tools/trusted_governance.py" validate \
  --base-root "$GOVERNANCE_BASE_WORKTREE" \
  --head-root "$GOVERNANCE_HEAD_WORKTREE" \
  --base-ref "$GOVERNANCE_BASE_REF" \
  --head-ref "$GOVERNANCE_HEAD_REF"
(
  cd "$GOVERNANCE_HEAD_WORKTREE"
  python -P "$GOVERNANCE_BASE_WORKTREE/tools/code_governance.py" check \
    --base-ref "$GOVERNANCE_BASE_REF" \
    --head-ref "$GOVERNANCE_HEAD_REF" \
    --format text
  python -P "$GOVERNANCE_BASE_WORKTREE/tools/architecture_governance.py" check \
    --authority-ref "$GOVERNANCE_BASE_REF" \
    --base-ref "$GOVERNANCE_BASE_REF" \
    --head-ref "$GOVERNANCE_HEAD_REF" \
    --format text
)"""


class TrustedGovernanceError(RuntimeError):
    pass


class UniqueKeyLoader(yaml.BaseLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise TrustedGovernanceError(f"duplicate workflow key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TrustedGovernanceError(f"{label} must be a mapping")
    return value


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TrustedGovernanceError(f"{label} must be a list")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise TrustedGovernanceError(
            f"{label} keys must be {sorted(expected)}; got {sorted(actual)}"
        )


def _git_blob_text(root: Path, relative_path: Path) -> str:
    path = relative_path.as_posix()
    tree = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-z", "HEAD", "--", path],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if tree.returncode != 0 or not tree.stdout:
        raise TrustedGovernanceError(f"protected file is missing: {path}")
    entries = [entry for entry in tree.stdout.split(b"\0") if entry]
    if len(entries) != 1:
        raise TrustedGovernanceError(f"protected path is ambiguous: {path}")
    metadata, separator, recorded_path = entries[0].partition(b"\t")
    fields = metadata.split()
    if (
        separator != b"\t"
        or recorded_path.decode("utf-8") != path
        or len(fields) != 3
        or fields[0] != b"100644"
        or fields[1] != b"blob"
        or _COMMIT_SHA.fullmatch(fields[2].decode("ascii")) is None
    ):
        raise TrustedGovernanceError(f"protected file must be a regular blob: {path}")
    blob = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", fields[2].decode("ascii")],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if blob.returncode != 0:
        raise TrustedGovernanceError(f"cannot read protected blob: {path}")
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TrustedGovernanceError(
            f"protected file must be UTF-8 text: {path}"
        ) from error


def load_workflow(root: Path, relative_path: Path) -> Mapping[str, object]:
    try:
        payload = yaml.load(
            _git_blob_text(root, relative_path), Loader=UniqueKeyLoader
        )
    except yaml.YAMLError as error:
        raise TrustedGovernanceError(
            f"cannot parse workflow {relative_path.name}"
        ) from error
    return _mapping(payload, relative_path.name)


def _assignment_name(node: ast.stmt) -> str | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _runner_policy(source: str) -> dict[str, object]:
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        raise TrustedGovernanceError("trusted runner must parse as Python") from error
    assignments = {
        name: [node for node in module.body if _assignment_name(node) == name]
        for name in _POLICY_NAMES
    }
    if any(len(nodes) != 1 for nodes in assignments.values()):
        raise TrustedGovernanceError(
            "trusted runner must define each allowlist exactly once"
        )
    try:
        policy = {
            name: ast.literal_eval(nodes[0].value)
            for name, nodes in assignments.items()
        }
    except (TypeError, ValueError) as error:
        raise TrustedGovernanceError(
            "trusted runner allowlists must be pure literals"
        ) from error
    _validate_policy(policy)
    return policy


def _validate_policy(policy: Mapping[str, object]) -> None:
    actions = _mapping(policy.get("_ALLOWED_ACTIONS"), "trusted action allowlist")
    if set(actions) != {"actions/checkout", "actions/setup-python"}:
        raise TrustedGovernanceError("trusted action repositories changed")
    for repository, commits in actions.items():
        if not isinstance(repository, str) or not isinstance(commits, tuple) or not commits:
            raise TrustedGovernanceError("trusted action allowlist is invalid")
        if any(
            not isinstance(commit, str) or _COMMIT_SHA.fullmatch(commit) is None
            for commit in commits
        ) or len(commits) != len(set(commits)):
            raise TrustedGovernanceError("trusted action commits must be unique pins")

    python_versions = policy.get("_ALLOWED_PYTHON_VERSIONS")
    if not isinstance(python_versions, tuple) or not python_versions:
        raise TrustedGovernanceError("trusted Python allowlist is invalid")
    if any(
        not isinstance(version, str)
        or re.fullmatch(r"3\.[0-9]+\.[0-9]+", version) is None
        for version in python_versions
    ) or len(python_versions) != len(set(python_versions)):
        raise TrustedGovernanceError("trusted Python versions must be unique pins")

    commands = policy.get("_ALLOWED_DEPENDENCY_COMMANDS")
    if not isinstance(commands, tuple) or not commands:
        raise TrustedGovernanceError("trusted dependency allowlist is invalid")
    command_pattern = re.compile(
        r"python -m pip install ruff==[0-9]+(?:\.[0-9]+)+ "
        r"PyYAML==[0-9]+(?:\.[0-9]+)+"
    )
    if any(
        not isinstance(command, str) or command_pattern.fullmatch(command) is None
        for command in commands
    ) or len(commands) != len(set(commands)):
        raise TrustedGovernanceError(
            "trusted dependency commands must be unique exact pins"
        )


def _runner_logic(source: str) -> str:
    module = ast.parse(source)
    module.body = [
        node for node in module.body if _assignment_name(node) not in _POLICY_NAMES
    ]
    return ast.dump(module, include_attributes=False)


def _action(
    step: Mapping[str, object],
    repository: str,
    label: str,
    policy: Mapping[str, object],
) -> None:
    uses = step.get("uses")
    if not isinstance(uses, str):
        raise TrustedGovernanceError(f"{label} must use an approved pinned action")
    prefix = f"{repository}@"
    commit = uses.removeprefix(prefix)
    actions = _mapping(policy["_ALLOWED_ACTIONS"], "trusted action allowlist")
    allowed_commits = actions[repository]
    if not uses.startswith(prefix) or commit not in allowed_commits:
        raise TrustedGovernanceError(
            f"{label} must use an accepted {repository} commit"
        )


def validate_trusted_workflow(
    root: Path, policy: Mapping[str, object] | None = None
) -> None:
    effective_policy = policy or _runner_policy(
        _git_blob_text(root, TRUSTED_RUNNER_PATH)
    )
    workflow = load_workflow(root, TRUSTED_WORKFLOW_PATH)
    _exact_keys(
        workflow,
        {"name", "on", "permissions", "concurrency", "env", "jobs"},
        "trusted workflow",
    )
    if workflow["name"] != "ai-platform trusted governance v2":
        raise TrustedGovernanceError("trusted workflow name changed")
    if workflow["on"] != {
        "pull_request_target": {
            "branches": ["main"],
            "types": ["opened", "synchronize", "reopened", "edited"],
        }
    }:
        raise TrustedGovernanceError("trusted workflow triggers changed")
    if workflow["permissions"] != {"contents": "read"}:
        raise TrustedGovernanceError("trusted workflow permissions changed")
    if workflow["concurrency"] != {
        "group": "ai-platform-trusted-governance-v2-${{ github.event.pull_request.number }}",
        "cancel-in-progress": "true",
    }:
        raise TrustedGovernanceError("trusted workflow concurrency changed")

    env = _mapping(workflow["env"], "trusted workflow env")
    _exact_keys(env, {"GOVERNANCE_PYTHON_VERSION"}, "trusted workflow env")
    python_version = env["GOVERNANCE_PYTHON_VERSION"]
    if python_version not in effective_policy["_ALLOWED_PYTHON_VERSIONS"]:
        raise TrustedGovernanceError("trusted Python version is not accepted")

    jobs = _mapping(workflow["jobs"], "trusted workflow jobs")
    _exact_keys(jobs, {"trusted-governance-v2"}, "trusted workflow jobs")
    job = _mapping(jobs["trusted-governance-v2"], "trusted governance v2 job")
    _exact_keys(
        job,
        {"name", "runs-on", "timeout-minutes", "steps"},
        "trusted governance v2 job",
    )
    if job["name"] != "trusted governance v2":
        raise TrustedGovernanceError("trusted governance context changed")
    if job["runs-on"] != "ubuntu-24.04" or job["timeout-minutes"] != "10":
        raise TrustedGovernanceError("trusted governance runner contract changed")

    steps = [
        _mapping(step, f"trusted governance step {index}")
        for index, step in enumerate(_sequence(job["steps"], "trusted governance steps"))
    ]
    if [step.get("name") for step in steps] != [
        "Checkout exact trusted base",
        "Set up trusted Python",
        "Install trusted governance dependencies",
        "Run accepted-base exact-range governance",
    ]:
        raise TrustedGovernanceError("trusted governance steps changed")

    checkout, setup_python, install, governance = steps
    _exact_keys(checkout, {"name", "uses", "with"}, "trusted checkout step")
    _action(checkout, "actions/checkout", "trusted checkout step", effective_policy)
    if checkout["with"] != {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }:
        raise TrustedGovernanceError("trusted checkout inputs changed")

    _exact_keys(setup_python, {"name", "uses", "with"}, "trusted Python step")
    _action(
        setup_python,
        "actions/setup-python",
        "trusted Python step",
        effective_policy,
    )
    if setup_python["with"] != {
        "python-version": "${{ env.GOVERNANCE_PYTHON_VERSION }}"
    }:
        raise TrustedGovernanceError("trusted Python inputs changed")

    _exact_keys(install, {"name", "run"}, "trusted dependency step")
    install_command = install["run"]
    if install_command not in effective_policy["_ALLOWED_DEPENDENCY_COMMANDS"]:
        raise TrustedGovernanceError("trusted dependency command is not accepted")

    _exact_keys(governance, {"name", "env", "run"}, "trusted governance step")
    if governance["env"] != {
        "GOVERNANCE_PR_NUMBER": "${{ github.event.number }}",
        "GOVERNANCE_BASE_REF": "${{ github.event.pull_request.base.sha }}",
        "GOVERNANCE_HEAD_REF": "${{ github.event.pull_request.head.sha }}",
        "GOVERNANCE_FETCH_TOKEN": "${{ github.token }}",
        "PYTHONSAFEPATH": "1",
    }:
        raise TrustedGovernanceError("trusted governance environment changed")
    run = governance["run"]
    if not isinstance(run, str) or run.rstrip() != EXPECTED_GOVERNANCE_RUN:
        raise TrustedGovernanceError("trusted governance launcher changed")


def _backend_mode(root: Path) -> str:
    workflow = load_workflow(root, BACKEND_WORKFLOW_PATH)
    jobs = _mapping(workflow.get("jobs"), "backend workflow jobs")
    has_legacy = "backend-validation" in jobs
    has_preflight = "backend-preflight" in jobs
    if has_legacy == has_preflight:
        raise TrustedGovernanceError(
            "backend workflow must define exactly one validation mode"
        )

    mode = "legacy" if has_legacy else "preflight"
    validation_id = "backend-validation" if has_legacy else "backend-preflight"
    expected_name = (
        "backend validation and governance" if has_legacy else "backend preflight"
    )
    validation = _mapping(jobs[validation_id], f"{validation_id} job")
    if validation.get("name") != expected_name:
        raise TrustedGovernanceError(f"{validation_id} name changed")

    for job_id in ("backend-tests", "agent-skill-contracts", "backend-image"):
        job = _mapping(jobs.get(job_id), f"{job_id} job")
        if job.get("needs") != validation_id:
            raise TrustedGovernanceError(
                f"{job_id} must depend only on {validation_id}"
            )

    required = _mapping(jobs.get("required"), "backend required job")
    _exact_keys(
        required,
        {"name", "runs-on", "needs", "if", "timeout-minutes", "steps"},
        "backend required job",
    )
    if (
        required["name"] != "backend required"
        or required["runs-on"] != "ubuntu-latest"
        or required["if"] != "${{ always() }}"
        or required["timeout-minutes"] != "5"
    ):
        raise TrustedGovernanceError("backend required aggregate changed")
    if required["needs"] != [
        validation_id,
        "backend-tests",
        "agent-skill-contracts",
        "backend-image",
    ]:
        raise TrustedGovernanceError("backend required dependencies changed")

    validation_result = "VALIDATION_RESULT" if mode == "legacy" else "PREFLIGHT_RESULT"
    required_steps = _sequence(required["steps"], "backend required steps")
    if len(required_steps) != 1:
        raise TrustedGovernanceError("backend required assertion step changed")
    required_step = _mapping(required_steps[0], "backend required assertion step")
    expected_required_step = {
        "name": "Require backend, Agent/Skill, and packaged image acceptance",
        "env": {
            validation_result: f"${{{{ needs.{validation_id}.result }}}}",
            "BACKEND_TESTS_RESULT": "${{ needs.backend-tests.result }}",
            "AGENT_SKILL_RESULT": "${{ needs.agent-skill-contracts.result }}",
            "IMAGE_RESULT": "${{ needs.backend-image.result }}",
        },
        "run": "\n".join(
            (
                f'test "${validation_result}" = "success"',
                'test "$BACKEND_TESTS_RESULT" = "success"',
                'test "$AGENT_SKILL_RESULT" = "success"',
                'test "$IMAGE_RESULT" = "success"',
            )
        ),
    }
    normalized_required_step = dict(required_step)
    run = normalized_required_step.get("run")
    if isinstance(run, str):
        normalized_required_step["run"] = run.rstrip()
    if normalized_required_step != expected_required_step:
        raise TrustedGovernanceError("backend required assertions changed")

    steps = _sequence(validation.get("steps"), f"{validation_id} steps")
    run_commands = "\n".join(
        str(_mapping(step, f"{validation_id} step").get("run", ""))
        for step in steps
    )
    governance_commands = (
        "tools/code_governance.py",
        "tools/architecture_governance.py",
    )
    if mode == "legacy":
        names = [
            _mapping(step, "legacy validation step").get("name") for step in steps
        ]
        if names.count("Run code and architecture governance") != 1:
            raise TrustedGovernanceError("legacy governance step changed")
    elif any(command in run_commands for command in governance_commands):
        raise TrustedGovernanceError(
            "backend preflight must not duplicate trusted governance"
        )

    return mode


def _policy_is_expansion(
    base_policy: Mapping[str, object], head_policy: Mapping[str, object]
) -> bool:
    base_actions = _mapping(base_policy["_ALLOWED_ACTIONS"], "base actions")
    head_actions = _mapping(head_policy["_ALLOWED_ACTIONS"], "head actions")
    action_expansion = all(
        set(base_actions[repository]) <= set(head_actions[repository])
        for repository in base_actions
    )
    python_expansion = set(base_policy["_ALLOWED_PYTHON_VERSIONS"]) <= set(
        head_policy["_ALLOWED_PYTHON_VERSIONS"]
    )
    dependency_expansion = set(base_policy["_ALLOWED_DEPENDENCY_COMMANDS"]) <= set(
        head_policy["_ALLOWED_DEPENDENCY_COMMANDS"]
    )
    return action_expansion and python_expansion and dependency_expansion


def _changed_paths(root: Path, base_ref: str, head_ref: str) -> tuple[str, ...]:
    if _COMMIT_SHA.fullmatch(base_ref) is None or _COMMIT_SHA.fullmatch(head_ref) is None:
        raise TrustedGovernanceError("governance refs must be exact commit SHAs")
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base_ref,
            head_ref,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise TrustedGovernanceError("cannot enumerate governance range")
    try:
        return tuple(
            path.decode("utf-8")
            for path in completed.stdout.split(b"\0")
            if path
        )
    except UnicodeDecodeError as error:
        raise TrustedGovernanceError("governance paths must be UTF-8") from error


def validate_transition(
    base_root: Path,
    head_root: Path,
    *,
    changed_paths: Sequence[str],
) -> None:
    base_runner = _git_blob_text(base_root, TRUSTED_RUNNER_PATH)
    head_runner = _git_blob_text(head_root, TRUSTED_RUNNER_PATH)
    base_policy = _runner_policy(base_runner)
    head_policy = _runner_policy(head_runner)
    if _runner_logic(base_runner) != _runner_logic(head_runner):
        raise TrustedGovernanceError("trusted runner executable logic changed")
    if base_runner != head_runner:
        if tuple(changed_paths) != (TRUSTED_RUNNER_PATH.as_posix(),):
            raise TrustedGovernanceError(
                "trusted runner allowlist changes must be standalone"
            )
        if base_policy == head_policy or not _policy_is_expansion(
            base_policy, head_policy
        ):
            raise TrustedGovernanceError(
                "trusted runner changes must only expand pure allowlists"
            )

    validate_trusted_workflow(base_root, base_policy)
    validate_trusted_workflow(head_root, head_policy)
    base_mode = _backend_mode(base_root)
    head_mode = _backend_mode(head_root)
    if base_mode == "preflight" and head_mode != "preflight":
        raise TrustedGovernanceError(
            "backend governance migration cannot return to legacy mode"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate accepted-base trusted governance workflow contracts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--base-root", type=Path, required=True)
    validate.add_argument("--head-root", type=Path, required=True)
    validate.add_argument("--base-ref", required=True)
    validate.add_argument("--head-ref", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        validate_transition(
            args.base_root,
            args.head_root,
            changed_paths=_changed_paths(
                args.base_root,
                args.base_ref,
                args.head_ref,
            ),
        )
    except TrustedGovernanceError as error:
        print(f"trusted_governance=failed reason={error}")
        return 2
    print("trusted_governance=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

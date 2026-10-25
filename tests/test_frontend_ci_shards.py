import copy
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from tests.support.yaml_contracts import load_unique_yaml


ROOT = Path(__file__).resolve().parents[1]


def _inputs():
    workflow = load_unique_yaml(
        (ROOT / ".github/workflows/ai-platform-frontend.yml").read_text(encoding="utf-8")
    )
    scripts = json.loads(
        (ROOT / "frontend/web/package.json").read_text(encoding="utf-8")
    )["scripts"]
    return workflow, scripts


def _assert_test_shard_inventory(workflow, scripts):
    job = workflow["jobs"]["frontend-tests"]
    assert job["runs-on"] == "windows-latest"
    assert "needs" not in job
    assert job["strategy"]["fail-fast"] == "false"
    shards = job["strategy"]["matrix"]["shard"]
    assert 3 <= len(shards) <= 4
    assert len(shards) == len(set(shards))
    assigned = {}
    default_directory = job.get("defaults", {}).get("run", {}).get("working-directory")
    for step in job["steps"]:
        command = step.get("run", "").strip()
        if "corepack pnpm run test:" not in command:
            continue
        match = re.fullmatch(r"matrix\.shard == '([a-z-]+)'", step.get("if", ""))
        assert match is not None
        shard = match[1]
        assert shard in shards and shard not in assigned
        assert step.get("working-directory", default_directory) == "frontend/web"
        commands = command.split(" && ")
        assert all(re.fullmatch(r"corepack pnpm run test:[\w-]+", item) for item in commands)
        assigned[shard] = commands
    assert set(assigned) == set(shards)
    expected = [
        command.strip()
        for command in scripts["ci:verify"].split("&&")
        if command.strip().startswith("corepack pnpm run test:")
    ]
    actual = [command for commands in assigned.values() for command in commands]
    assert Counter(actual) == Counter(expected)
    assert all(count == 1 for count in Counter(actual).values())
    assert {command.split()[-1] for command in expected} == {
        name for name in scripts if name.startswith("test:")
    }
    for command in expected:
        for runner in scripts[command.split()[-1]].split(" && "):
            assert "--test" in runner.split()
            assert "--test-concurrency=2" in runner.split()
            assert "--import ./scripts/lobe-ui-test-setup.mjs" in runner


def test_parallel_shards_preserve_every_full_local_ci_test_stage_and_runner():
    _assert_test_shard_inventory(*_inputs())


@pytest.mark.parametrize("mutation", [
    "missing-stage", "duplicate-stage", "missing-shard", "duplicate-shard",
    "wrong-route", "wrong-directory", "higher-concurrency",
])
def test_shard_inventory_rejects_incomplete_or_changed_execution(mutation):
    workflow, scripts = copy.deepcopy(_inputs())
    job = workflow["jobs"]["frontend-tests"]
    first = next(step for step in job["steps"] if "corepack pnpm run test:" in step.get("run", ""))
    if mutation == "missing-stage":
        first["run"] = first["run"].rsplit(" && ", 1)[0]
    elif mutation == "duplicate-stage":
        first["run"] += " && " + first["run"].split(" && ")[0]
    elif mutation == "missing-shard":
        job["strategy"]["matrix"]["shard"].pop()
    elif mutation == "duplicate-shard":
        job["strategy"]["matrix"]["shard"].append(job["strategy"]["matrix"]["shard"][0])
    elif mutation == "wrong-route":
        first["if"] = "matrix.shard == 'unassigned'"
    elif mutation == "wrong-directory":
        job["defaults"]["run"]["working-directory"] = "."
    else:
        scripts["test:auth"] = scripts["test:auth"].replace("--test-concurrency=2", "--test-concurrency=4")
    with pytest.raises(AssertionError):
        _assert_test_shard_inventory(workflow, scripts)


def test_projection_and_python_contracts_are_independent_of_js_setup():
    workflow, _ = _inputs()
    job = workflow["jobs"]["frontend-contracts"]
    assert job["runs-on"] == "windows-latest"
    assert "needs" not in job
    assert all("setup-node" not in step.get("uses", "") for step in job["steps"])
    audit = next(step for step in job["steps"] if step["name"] == "Audit frontend projections")
    assert audit["run"] == "python tools/frontend_projection_audit.py --format json"
    assert "if" not in audit and "continue-on-error" not in audit
    tests = workflow["jobs"]["frontend-tests"]
    assert all("setup-python" not in step.get("uses", "") and "setup-uv" not in step.get("uses", "") for step in tests["steps"])


def _ci_verify_steps_without_workflow_owner(workflow, scripts):
    def expand(command, seen=()):
        command = command.strip()
        yield command
        script = re.fullmatch(r"corepack pnpm run ([\w:-]+)", command)
        if script and script[1] in scripts and script[1] not in seen:
            for item in scripts[script[1]].split("&&"):
                yield from expand(item, (*seen, script[1]))

    def normalize(command):
        # The local runner resolves the repository Python; CI calls Python directly.
        return re.sub(r"^node scripts/run-python-tool\.mjs \.\./\.\./tools/", "python tools/", command)

    owned = {
        normalize(item)
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        for command in step.get("run", "").split("&&")
        for item in expand(command)
    }
    return [
        command.strip()
        for command in scripts["ci:verify"].split("&&")
        if normalize(command.strip()) not in owned
    ]


def test_every_local_ci_verify_step_runs_in_the_frontend_workflow():
    assert _ci_verify_steps_without_workflow_owner(*_inputs()) == []


def test_ci_verify_ownership_rejects_a_local_only_step():
    workflow, scripts = copy.deepcopy(_inputs())
    scripts["ci:verify"] += " && node scripts/local-only-gate.mjs"
    assert _ci_verify_steps_without_workflow_owner(workflow, scripts) == [
        "node scripts/local-only-gate.mjs"
    ]

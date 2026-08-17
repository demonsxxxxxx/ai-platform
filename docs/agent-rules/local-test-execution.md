# Local Test Execution

Status: normative agent procedure
Owner: platform architecture
Architecture authority: [`../architecture/ci-test-readiness-governance.md`](../architecture/ci-test-readiness-governance.md)
Delivery authority: [`github-issue-pr-workflow.md`](github-issue-pr-workflow.md)

## Purpose

This procedure makes local pytest execution bounded, observable, and tied to
one explicit Git worktree. It does not select tests automatically and does not
replace required CI, exact-ref pre-push readiness, deployment checks, or
External Acceptance.

The introducing change for the runner proves the runner directly with the
repository's existing pytest command, after first creating `.pytest-tmp/`, and
uses a fresh workspace-local basetemp child. After the runner is accepted on
`main`, Agents use it for ordinary local pytest stages. A candidate-owned runner
cannot certify its own introduction as trusted readiness authority.

## Required Entry Point

Run a named owning stage from the target worktree root:

```bash
python tools/run_test_stage.py \
  --stage sandbox-runtime-owning \
  --timeout-seconds 300 \
  -- tests/test_sandbox_runtime.py::test_named_behavior
```

Run an explicit bounded compatibility stage only after the owning stage passes:

```bash
python tools/run_test_stage.py \
  --stage sandbox-runtime-compat \
  --timeout-seconds 300 \
  -- tests/test_sandbox_executor_app.py \
     tests/test_sandbox_executor_client.py \
     tests/test_sandbox_runtime.py
```

Use `--require-zero-skips` when the result is intended to prove a required
integration dependency. An opt-in local integration may omit that flag and may
skip, but the resulting `passed_with_skips` report is not required-integration
evidence.

The runner accepts only explicit `tests/*.py` files or `file.py::node`
selectors. It rejects directories, pytest options, absolute paths, missing
files, selectors outside `tests/`, duplicate selectors, and invocations from a
worktree subdirectory or a different checkout.

## Execution Invariants

Each stage:

- owns one cross-process lock for its worktree;
- uses the invoking Python interpreter and argument-array subprocess execution;
- streams pytest output with verbose node IDs instead of capturing the complete
  run behind another process;
- creates a unique `.pytest-tmp/test-runs/<run-id>/<stage>/basetemp`;
- writes JUnit XML and `evidence.json` beside that basetemp;
- emits a heartbeat every 15 seconds while pytest remains active;
- stops at its declared timeout, waits a bounded grace period, and then
  terminates the complete owned process tree;
- preserves an ordinary pytest failure exit code; and
- removes caller-provided `PYTEST_*` control variables from the child process,
  records only the removed variable names, and never reports environment values.

Only one stage may run in a worktree. `test_runner_busy` means another stage
owns the lock; do not bypass the lock or start pytest directly. Independent
worktrees may run independent stages when their task ownership permits it.

A timeout is an unknown test result, not a partial pass. The last verbose node
printed is diagnostic context only. No preceding dots, completed modules, or
partial JUnit file may be cited as a passing gate.

## Selection Order

Use the smallest stage that can falsify the change, then expand according to
observed risk:

1. **Owning test.** Run the new or changed falsifiable behavior test by node ID.
2. **Direct regression.** Run tests for the changed module and direct callers.
3. **Isolation.** For shared state or lifecycle changes, prove both isolated
   modules and their bounded grouped execution.
4. **Integration.** Run a real dependency only in its owned local or CI lane.
5. **Static checks.** Run only relevant compile, Ruff, schema, TypeScript, or
   frontend checks.
6. **Fixed-SHA readiness.** After an authorized commit, invoke the immutable
   authority procedure in `github-issue-pr-workflow.md`.

Do not run full-repository pytest as a routine confidence step. It requires an
explicit user decision tied to a named risk and does not replace focused proof.
Do not broaden Ruff or another static tool to unrelated paths merely because a
focused command was entered incorrectly.

## Async And Background Work

A test touching event loops, background tasks, clients, subprocesses, ports,
Redis, database pools, or application lifespan must prove cleanup as part of
the owning behavior:

- production tasks have an explicit supervisor, registry, fixture, or lifespan
  owner; a bare untracked `asyncio.create_task` is not acceptable;
- waits use a bounded deadline and tests use events, barriers, or queues instead
  of long real sleeps to establish ordering;
- clients and applications use context managers or explicit `close`/`aclose`;
- cancellation, parent cancellation, shutdown, callback failure, and repeated
  close are covered when those states are reachable;
- the owning service reports no pending tasks after shutdown; and
- a process-spawning test proves that descendants terminate with the stage.

If tests pass individually but fail or hang when grouped, classify the result
as `test_isolation_failure`. Stop expanding the suite and fix leaked tasks,
globals, clients, ports, environment mutation, or fixture teardown. Individual
passes do not override the grouped failure.

## Failure Semantics

Use these categories in issue, PR, and handoff records:

| Category | Meaning | Required action |
| --- | --- | --- |
| `product_test_failure` | A deterministic assertion failed. | Fix behavior or the owning contract; create a new SHA before reusing gate claims. |
| `test_isolation_failure` | Isolated and grouped results differ or grouped execution hangs. | Repair lifecycle or shared-state cleanup before continuing. |
| `test_timeout` | The declared stage deadline expired. | Diagnose the last active node and process state; do not claim partial success. |
| `invalid_test_plan` | Cwd, selector, stage, or pytest selection is invalid. | Correct the plan before running tests. |
| `infrastructure_failure` | Git, Python, process ownership, filesystem, or another required local facility failed. | Repair the facility and rerun the same bounded subject. |
| `required_dependency_missing` | A required integration stage skipped its real dependency. | Provision the dependency; a skip is not evidence. |
| `baseline_reproduced` | The exact failing node and normalized failure signature reproduce at the fixed base. | Record the base evidence and track the defect separately; do not deselect it permanently. |
| `governance_violation` | Source or verification violates repository authority. | Fix the named rule and rerun against a new fixed subject when needed. |

The runner does not automatically rerun failures or declare a baseline match.
Baseline comparison uses the same node ID, dependency versions, environment,
and command against the locked base. A historical failure or a different stack
is not baseline proof.

## Evidence

The local report records the repository root, exact HEAD, stage, selectors,
timeout, start and finish times, duration, return code, pytest counts, cleanup
status, removed `PYTEST_*` variable names, and workspace-relative
basetemp/JUnit/evidence paths. It intentionally does not record environment
values.

Report the observed evidence level precisely:

- a passing local stage is focused-test evidence for its named selectors;
- `passed_with_skips` is not zero-skip integration evidence;
- local evidence is not GitHub CI, packaged-image, deployment, runtime, or
  External Acceptance evidence; and
- any source fix changes the review subject and invalidates fixed-SHA claims
  from an earlier commit.

## Prohibited Patterns

Do not:

- wrap several suites in `spawnSync` or another all-output capture;
- start pytest from the user profile, repository parent, or a stale worktree;
- supply a system temporary directory as basetemp;
- bypass the worktree lock after `test_runner_busy`;
- rerun the same hanging command without first narrowing the active node;
- infer success from partial output or a terminated parent process;
- keep an invalid test as a permanent deselection; or
- use this local helper as a substitute for immutable pre-push authority.

# Local Test Execution

Local pytest procedure. Evidence and required integration rules live in
[CI governance](../architecture/ci-test-readiness-governance.md); PR handling
lives in the [delivery workflow](github-issue-pr-workflow.md).

## Required Entry Point

From the target worktree root, select existing owning tests:

```bash
python tools/run_test_stage.py \
  --stage source-authority-docs \
  --timeout-seconds 300 \
  -- tests/test_source_authority_docs.py
```

The example checks repository documents. Replace the stage and selectors with
tests for the actual change. The runner accepts explicit `.py` files under
`tests/` and `file.py::node` selectors, including new untracked tests. It rejects
directories, missing or duplicate selectors, external paths, and pytest flags.

## Selection Order

Start with checks that can falsify the changed behavior. Expand to direct
callers, grouped isolation checks, and real dependencies when risk requires it;
there is no mandatory six-stage sequence. Broader coverage may be split into
bounded explicit stages. Avoid full-repository execution as a routine ritual.
Docs-only changes check affected links, entrypoints, and document contracts.
Do not add permanent tests solely to lock editorial wording or line counts.

## Execution Invariants

Use the runner's worktree lock, timeout, process cleanup, and result reporting.
Do not bypass `test_runner_busy` or start competing pytest processes in that
worktree. Read CLI options with `python tools/run_test_stage.py --help`; the
runner owns temporary directories and subprocess configuration.

Required integration evidence uses real dependencies and `--require-zero-skips`.
A missing dependency or skip does not pass that gate. Optional local runs may
report `passed_with_skips`, with that limitation stated explicitly.

## Isolation

Close created tasks, clients, processes, ports, and modified environment state.
A test that passes alone but fails or hangs in a group has an isolation problem;
repair that cause before broadening or repeating the run. Do not hide failures
with permanent deselection, skip changes, or weakened assertions.

## Failure Semantics

Use the runner's result as recorded:

| Category | Action |
| --- | --- |
| `product_test_failure` | Fix the behavior or establish that the contract changed. |
| `test_isolation_failure` | Repair shared-state or lifecycle cleanup. |
| `test_timeout` | Inspect the last active node and cleanup; no partial pass. |
| `invalid_test_plan` | Correct the worktree, selectors, or invocation. |
| `infrastructure_failure` | Repair the facility or use a capable verification environment. |
| `required_dependency_missing` | Supply the real dependency and rerun. |

A local infrastructure failure may be reported in a draft PR for CI verification
under the delivery workflow. It cannot be relabelled as successful validation.

## Evidence

The runner writes JUnit and `evidence.json` under `.pytest-tmp/test-runs/`.
Report the actual selectors, source, result, and limitations; do not copy a
second evidence ledger into docs. A timeout, incomplete JUnit, or terminated
parent process is not a pass. Compare a claimed baseline failure using the same
selectors, dependency versions, and environment on both revisions.

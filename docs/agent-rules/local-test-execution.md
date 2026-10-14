# Local Test Execution

Status: active local verification procedure.

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
runner owns temporary directories and subprocess configuration. On Windows,
children start suspended and resume only after assignment to the runner's
kill-on-close Job Object. An enclosing host Job remains in effect; the runner
uses nested Jobs instead of requesting permission to break away from the host.

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

## Coverage observations

Prepare the locked test extra, then explicitly request coverage through the same
runner. Choose owning selectors and keep `--require-zero-skips` for required
integration; instrumentation does not supply missing real dependencies:

```bash
python tools/run_test_stage.py \
  --stage run-cov --timeout-seconds 300 --coverage --require-zero-skips \
  -- tests/test_runs_replay.py
```

`--coverage` measures `app/` statements and branches, including unexecuted namespace
packages. Its generated configuration ignores caller `.coveragerc` and coverage
control environment; it still removes `PYTEST_*`. Report/data/config stay in the
stage directory beside JUnit and evidence. Windows SQLite uses a native long-path
prefix for its data file; keep stage names concise because other test fixtures may
still depend on Windows path limits.

The optional evidence field records app scope, Python source digest for app/tests,
runner digest, coverage-report digest, file count and integer numerators/denominators.
The source digest is checked again after execution. Successful tests with a missing,
invalid, incomplete-scope or changed-source report fail as infrastructure evidence.
An existing pytest failure, timeout, interruption or required-skip failure keeps its
original classification. Uninstrumented invocation and the v1 evidence envelope
remain compatible; no old CLI or selector is retired.

Coverage belongs to the exact selected tests and recorded source. A small test
sample measuring all app files does not establish full-suite coverage. Raw coverage
percentage may combine lines and branches; use the separate integer totals for each.
This option does not impose a coverage threshold, collect frontend coverage, or
instrument arbitrary subprocesses. Full/critical-domain baseline, authorized
thresholds and trusted enforcement are separate acceptance work.

## Documentation gardening

The author of a documentation change runs `python tools/check_documentation.py`
from its worktree. It checks changed tracked/untracked first-party Markdown for
rendered local links/images and heading anchors. External URLs are not fetched.
Document purpose/status and navigation remain editorial responsibilities; no fixed
header text, line position or index entry is required by this check. The authority-map
regression reuses this link validator for its existing direct-owner file-link scope.
Historical release evidence, completed plans, agent-rule history and the immutable
Harness blueprint are explicitly reported as outside this current-source check.
Preserve their fixed-source provenance; inspect their owner when editing history.

Backend preflight supplies the full PR-base or push-before SHA and resolves its
common ancestor with HEAD before checking branch changes. Incoming main-only
changes are not attributed to a behind-main PR, and a clean checkout does not
silently inspect only uncommitted files. A manual run
with equal comparison/head may report zero selected documents, not whole-repo proof.
Failures give the source and repair direction. Repair affected links
with the owning document, run this checker and its regressions, and use the ordinary
[delivery workflow](github-issue-pr-workflow.md). No extra calendar bot, review ledger
or second architecture authority is introduced. Renaming or deleting a target still
requires a consumer inventory; this changed-document checker is not that inventory.

## Evidence

The runner writes JUnit and `evidence.json` under `.pytest-tmp/test-runs/`.
Report the actual selectors, source, result, and limitations; do not copy a
second evidence ledger into docs. A timeout, incomplete JUnit, or terminated
parent process is not a pass. Compare a claimed baseline failure using the same
selectors, dependency versions, and environment on both revisions.

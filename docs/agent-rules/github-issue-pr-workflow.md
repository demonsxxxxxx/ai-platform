# GitHub Issue And PR Workflow

This file is the single source for issue/PR records, status language, review
evidence, and closure. Product and deployment invariants remain in the
guardrails and 211 runbook.

## Closure Loop

For goal-sized work, gate closures, and new defects, use:

`issue -> branch -> PR -> focused verification -> review -> merge -> deploy/smoke when required -> close with evidence`

Only concrete GitHub checks applicable to the changed path and actually observed
on the PR count as CI gates. Do not wait for a nonexistent or inapplicable run.
If missing CI is itself a blocker, track it separately instead of expanding an
unrelated product PR.

## Records And Evidence Size

- The linked issue and PR are normally the plan, change description, and durable
  status record. Do not create a spec/plan/status trio by default.
- Create a separate design for security, auth or authorization, tenant isolation,
  release, deployment, runtime, schemas or public contracts, persistence,
  concurrency, infrastructure, or an unresolved cross-module decision.
- Medium or long work may keep one concise phase status document when it improves
  handoff or verification clarity.
- Record blockers and evidence on the issue or PR, not only in chat. Historical
  evidence remains historical and cannot prove current readiness.

## Status Language

- `local partial`: focused local checks or one bounded smoke passed.
- `PR ready`: the candidate and focused evidence are ready for review; it is not
  merged or deployed.
- `reviewed`: required independent review ran and every finding was fixed,
  rejected with evidence, or explicitly deferred.
- `211 verified`: the exact deployed subject passed the required current runtime
  checks on 211.
- `gate closable`: implementation or decision, PR/merge when applicable, review,
  required docs, and required runtime evidence are complete.

Never promote an earlier label into a later one without observing the additional
evidence.

## Issue, Branch, And PR Contract

An issue records scope, acceptance criteria, affected gate, verification and
review requirements, runtime requirement when relevant, and known blockers.

- Keep one coherent PR per issue or gate slice. One PR may cover multiple issues
  only when it satisfies the same coherent acceptance boundary.
- Use a branch name tied to the issue or gate.
- Direct commits to `main` require an explicit user request or documented
  operational exception, with the same evidence recorded afterward.
- A PR states its linked subject, changed behavior/modules, tests observed,
  review state, docs impact, and runtime evidence or why it is unnecessary.
- Use `Closes #N` or `Fixes #N` only when all acceptance criteria, review, and
  required runtime evidence will be satisfied by that merge. Otherwise link the
  issue without auto-close wording.

## Pre-Push Readiness

Before the first push, and after every ordinary merge-up from the PR base, run
the exact-ref readiness gate from the candidate repository root. It is a
bounded local gate; it does not run full-repository pytest.

The gate script is authority code. Never execute
`tools/pre_push_readiness.py` from the candidate checkout: a candidate can
replace that file before the check starts. Fetch the accepted authority commit,
check it out into a detached temporary worktree, and run that immutable copy
with the candidate repository as the working directory. `-P` and
`PYTHONSAFEPATH=1` keep candidate-root imports out of authority bootstrap.

```powershell
git fetch origin main
$candidateRoot = git rev-parse --show-toplevel
$authority = git rev-parse origin/main
$base = $authority
$head = git rev-parse HEAD
$authorityRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ai-platform-readiness-authority-" + [guid]::NewGuid())
git worktree add --detach $authorityRoot $authority
$previousPythonSafePath = $env:PYTHONSAFEPATH
try {
    Set-Location $candidateRoot
    $env:PYTHONSAFEPATH = "1"
    python -P (Join-Path $authorityRoot "tools/pre_push_readiness.py") check --authority-ref $authority --base-ref $base --head-ref $head --format text
}
finally {
    if ($null -eq $previousPythonSafePath) { Remove-Item Env:PYTHONSAFEPATH -ErrorAction SilentlyContinue } else { $env:PYTHONSAFEPATH = $previousPythonSafePath }
    git worktree remove --force $authorityRoot
}
```

`authority`, `base`, and `head` must each resolve to full 40-hex commits. The
authority copy verifies that its own Git object matches `authority`, and that
the authority is accepted by `origin/main`, before it resolves or executes any
candidate-owned code, configuration, or import. It materializes the trusted
governance implementation from the immutable authority Git object and executes
it under `-P` and `PYTHONSAFEPATH=1` before candidate compile, pytest,
frontend, or candidate configuration executes. That governance result is
sealed before candidate commands run; no later stage executes or consults a
mutable authority script for an allow/deny decision. A post-candidate integrity
check reports any authority-worktree mutation instead of silently accepting it.
The normal post-merge command always derives a fresh immutable authority SHA
from accepted `origin/main`.

This tool has a one-time bootstrap boundary: while the introducing change is
only a candidate and accepted `origin/main` does not yet contain the tool, that
candidate cannot run this normal gate or certify itself. It becomes an
authority only after independent review of its fixed SHA and ordinary merge.
Record the candidate's focused tests and independent fixed-SHA review instead;
do not copy the candidate script into the bootstrap command.

The authority creates its owned detached-worktree root under the configured
temporary parent with the short `apr-` basename. It reserves a conservative
Windows directory budget for the observed 163-character staged Skill and
`.pins` suffix plus headroom; if the configured temporary parent cannot meet
that budget, the gate removes only its empty owned root and reports an
`infrastructure_failure` rather than relying on arbitrary long-path settings.

The gate fails `stale_base` before local checks. It then runs compileall, diff
check, bounded changed-scope responsibility checks, changed-file Ruff, and
exact-ref governance. Conventional `app`/`tools`/`scripts` changes select their
changed `tests/test_<stem>.py` mirror; changed test modules are selected only
when present at `head`. A deleted test is never passed to pytest. A changed
`frontend/web` TypeScript or TSX path first verifies the candidate's exact
Git-tree `package.json` and `pnpm-lock.yaml`, requires its pinned
`packageManager` `pnpm@<version>`, and bootstraps only the detached candidate's
`frontend/web/node_modules` with pinned Corepack `pnpm install
--frozen-lockfile --prefer-offline`. The bootstrap uses the normal host
content-addressed pnpm store and Corepack cache; it never links or reuses a
mutable `node_modules` tree from another checkout, and does not create a
throwaway store/cache for every gate. Missing metadata or package manager,
provenance mismatch, or unavailable cache/network/bootstrap command is an
actionable `infrastructure_failure`; the detached `node_modules` is removed
with the temporary worktree on both success and failure. It then runs the
repository-native `corepack pnpm run ci:verify` responsibility command. A changed shared fixture
such as `tests/conftest.py` or a fixture/helper module requires an explicit
bounded `--shared-test-suite tests/test_<name>.py`; the option is invalid when
no named shared fixture changed. An otherwise unclassifiable affected path
always fails closed with `external_check`. A shared suite cannot discharge an
unowned production path; that path remains external until an explicit bounded
responsibility mapping exists. The one explicit root-file mapping is an added
or modified `.code-governance-exception.json`, which selects the existing
`tests/test_code_governance.py` suite. Name-status copy detection uses
`--find-copies=50% --find-copies-harder`, including unchanged source blobs, so
the mapping accepts only literal `A` or `M` status. A `C*`, `R*`, `T*`, `U*`,
or any other status remains `external_check`. A copy or rename touching the
exception at either source or destination fails externally before
documentation, test, or frontend routing. The suite must be an exact
case-sensitive Git-tree blob at `head_ref`; a Windows filesystem case match is
not sufficient. Its deletion follows the deleted-path policy: it is not passed
to pytest and does not select that suite; exact governance still evaluates the
candidate range with the exception absent. A `--shared-test-suite` must use a
canonical relative POSIX `tests/test_*.py` path: absolute paths, backslashes,
empty, dot, and dot-dot components are invalid. It must be an exact Git-tree
blob and resolve within the detached worktree's `tests` directory before
pytest. Every other unowned root configuration or JSON path remains
`external_check`; `--shared-test-suite` cannot bypass it. Preserve the emitted
category and identity in the PR record:

- `stale_base`: merge the current base through the ordinary merge-up flow, then
  run the gate again before pushing.
- `product_test_failure`: fix the named deterministic local test or compile
  failure; the report includes the failing pytest node identity when available.
- `governance_violation`: fix the named policy rule and path; do not treat it
  as a runner failure.
- `infrastructure_failure`: repair the unavailable local command or worktree
  condition and rerun the same candidate range. Cleanup-only failure is this
  category; if cleanup follows a product or governance failure, the primary
  failure remains primary and cleanup is reported alongside it.
- `external_check`: supply the bounded shared suite when applicable, or record
  the required GitHub or other provider check separately from local readiness.

Do not automatically rerun a failed GitHub check. A same-SHA rerun is allowed
only after positive infrastructure evidence on the same SHA identifies an
`infrastructure_failure`; test and governance failures require a new fixed SHA.

## Review And Verification

- Use independent review for high-risk paths and stage-gate work when a suitable
  review path is available. Record the reviewer identity/role, exact scope,
  severity-ranked findings, handling decisions, and observed verification on the
  PR or issue before claiming `reviewed`.
- A local agent review may substitute for a formal GitHub reviewer when recorded
  durably. If fixes follow, re-review the fixed SHA and leave no Critical or
  Important finding unhandled. Do not call an empty GitHub `reviewDecision`
  formally approved.
- Validate findings against current requirements, guardrails, code, and tests.
  Handle each finding by fixing it, rejecting it with evidence, or explicitly
  deferring it without using the deferral to bypass current acceptance.
- Run the narrowest relevant verification first. Before PR, merge, deployment,
  or gate closure, run the changed-scope tests plus the integration or smoke
  checks justified by risk. Full-repository pytest is not a routine gate.
- Projection checks use the correct principal and route; Admin evidence does not
  prove ordinary-user behavior.
- Runtime evidence identifies the exact commit/image/container, route and
  principal where applicable, API health, and target contract behavior.
- Label deployment workarounds as workarounds and track repeated ones as release
  path defects rather than normalizing them.

SDK, worker, skill, terminal, or user-facing runtime diagnostics trace the fault
through `tool registration -> runner selection -> subprocess/terminal -> SDK event -> user-facing error` and leave a minimal reproduction plus observable
log/event evidence. Historical examples are non-normative and live in
`docs/agent-rules/history/github-sdk-diagnostic-examples.md`.

## Closure

Close after evidence, not intent. An issue closes only after its implementation
or no-code decision, applicable merge, focused verification, required review,
docs or roadmap update, and required runtime evidence are recorded. A no-code
issue closes with the decision and its verification evidence.

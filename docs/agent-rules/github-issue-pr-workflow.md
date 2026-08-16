# GitHub Issue And PR Workflow

This file is the single source for issue/PR records, status language, review
evidence, and closure. Product and deployment invariants remain in the
repository authority and the release runbook.

## Closure Loop

For goal-sized work, gate closures, and new defects, use:

`issue -> branch -> PR -> focused verification -> review -> merge -> deploy/smoke when required -> close with evidence`

Only concrete GitHub checks applicable to the changed path and actually observed
on the PR count as CI gates. Track a missing required check separately instead
of expanding an unrelated product PR.

## Records And Evidence

- The linked issue and PR are normally the plan, change description, and durable
  status record. Do not create a spec/plan/status trio by default.
- Create a separate design for security, auth or authorization, tenant isolation,
  release, deployment, runtime, persistence, concurrency, public contracts, or
  infrastructure decisions that need durable explanation.
- Record blockers and evidence on the issue or PR. Historical evidence cannot
  prove current readiness.
- Do not create repository status pages, phase ledgers, or manual-release logs
  for an active change. The issue or PR is the durable status record.

## Change Contract

Before non-mechanical implementation, the issue or persistent-task dispatch
records one compact Change Contract. The PR links that prior record and
reconciles it with the actual diff:

- observable problem and single owning authority;
- repository/worktree, branch, full base/head SHA when available, writable and
  forbidden paths, and explicit non-goals;
- behavior delta including failure/compatibility decisions, plus only the
  security, tenancy, transaction/queue, lifecycle/persistence/event, sandbox,
  and public-projection invariants the changed risk reaches; mark other
  categories not applicable instead of producing boilerplate;
- genuine alternatives and why they lost; use the separate-design rule above
  when rationale needs durable architecture authority;
- acceptance criteria, a falsifiable regression test, required assembled path,
  evidence ceiling, documentation impact, rollback when relevant, and facts
  that stop or reopen design.

Read-only exploration may resolve missing fields. Revise the contract before
changing owner or paths; unrelated findings become separate work. A source-only
change may stop at focused-test evidence when it claims no assembled or runtime
behavior. PR text and checkboxes are claims to verify, not evidence, and a
candidate-controlled test cannot prove the contract existed before coding.
Only risk categories may be marked non-applicable. Behavior, tests, evidence,
review, and rollback require observed facts or a reasoned applicability
statement; a bare `N/A` does not satisfy the contract.

## Status Language

- `local partial`: focused local checks or one bounded smoke passed.
- `PR ready`: the candidate and focused evidence are ready for review; it is not
  merged or deployed.
- `reviewed`: required independent review ran and every finding was fixed,
  rejected with evidence, or explicitly deferred.
- `runtime verified`: the exact deployed subject passed the required checks on
  its operator-approved controlled host.
- `gate closable`: implementation or decision, PR/merge when applicable, review,
  required docs, and required runtime evidence are complete.

Never promote an earlier label without observing the additional evidence.

## Issue, Branch, And PR Contract

An issue records scope, acceptance criteria, verification and review
requirements, runtime requirement when relevant, and known blockers.

- Keep one coherent PR per issue or acceptance boundary and use an issue-linked
  branch.
- Direct commits to `main` require an explicit user request or documented
  operational exception, with the same evidence recorded afterward.
- A PR states its linked subject, changed behavior/modules, tests observed,
  review state, docs impact, and runtime evidence or why it is unnecessary.
- The PR reconciles its declared writable paths with the actual diff and records
  scope revisions. Template text and checked boxes are claims for reviewers and
  gates to verify; they are not evidence by themselves.
- Use `Closes #N` or `Fixes #N` only when the merge will satisfy all acceptance,
  review, and required runtime criteria. Otherwise link without auto-close.

## Pre-Push Readiness

Before the first push and after every ordinary merge-up from the PR base, run
the bounded exact-ref readiness gate from the candidate repository root. It
does not run full-repository pytest.

The gate script is authority code. Never execute
`tools/pre_push_readiness.py` from the candidate checkout. Fetch accepted
`origin/main`, check that authority out into a detached temporary worktree, and
invoke its immutable script against the candidate:

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

Stable invariants:

- `authority`, `base`, and `head` are full 40-hex commits. Authority must be the
  immutable authority Git object accepted by `origin/main`; `-P` and
  `PYTHONSAFEPATH=1` prevent candidate-root imports during bootstrap.
- Authority provenance is verified before candidate-owned code or configuration
  executes. The governance decision is sealed first, and authority integrity is
  checked again after candidate checks.
- Checks stay bounded to the changed risk. Changed backend test modules are
  regression evidence regardless of filename stem; when the effective suite is
  unchanged, declare it with repeatable `--regression-test-suite` paths. A
  backend behavior change with neither form of evidence fails as `external_check`.
- The finite Skill, MCP, schema, and release safety-suite map is frozen and
  remains additive. Frontend coverage continues through `ci:verify`, and changed
  shared fixtures still require an explicit bounded regression suite.
- Governance reports production subsystems and their count, plus production/test
  added LOC and their ratio, as review evidence rather than subsystem or test-size
  violations. Explain reuse or duplication when test additions exceed 300 lines
  or twice the production additions.
- The introducing candidate cannot certify its own new readiness tool. The tool
  becomes authority only after independent fixed-SHA review and ordinary merge.

Preserve the primary category and named failing identity in the PR record:

- `stale_base`: merge up normally and rerun against the new exact range.
- `product_test_failure`: fix the named deterministic check at a new SHA.
- `governance_violation`: fix the named policy rule and path.
- `infrastructure_failure`: repair the unavailable command or worktree condition
  and rerun the same candidate range.
- `external_check`: provide the bounded external or shared check separately.

Do not automatically rerun failed GitHub checks. A same-SHA rerun is allowed
only after positive infrastructure evidence on that SHA; test and governance
failures require a new fixed SHA.

## Review And Verification

- Freeze the exact commit SHA and scope for review. Any fix creates a new review
  subject and requires review of that fixed SHA.
- Use independent review for high-risk paths and gate work when available.
  Record reviewer role, exact scope, severity-ranked findings, decisions, and
  observed verification before claiming `reviewed`.
- Acceptance-blocking findings cannot be deferred to claim readiness or closure,
  and any unresolved Critical or Important finding prevents `reviewed`.
- A local agent review may substitute for a formal GitHub reviewer when recorded
  durably. Do not call an empty GitHub `reviewDecision` formally approved.
- Run the narrowest relevant verification first. Before PR, merge, deployment,
  or closure, run the tests and integration or smoke checks justified by risk.
- Local/source verification, GitHub review and CI, deployment, and runtime
  evidence are distinct states. Admin evidence does not prove ordinary-user
  behavior, and source evidence does not prove the deployed runtime.
- Runtime evidence identifies the exact commit/image/container, route and
  principal where applicable, API health, and target behavior.

SDK, worker, skill, terminal, or user-facing runtime diagnostics trace the fault
through `tool registration -> runner selection -> subprocess/terminal -> SDK event -> user-facing error` and leave a minimal reproduction plus observable
log/event evidence. Historical examples are non-normative and live in
`docs/agent-rules/history/github-sdk-diagnostic-examples.md`.

## Closure

Close after evidence, not intent. An issue closes only after its implementation
or no-code decision, applicable merge, focused verification, required review,
docs impact, and required runtime evidence are recorded.

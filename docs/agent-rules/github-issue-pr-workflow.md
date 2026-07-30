# GitHub Issue And PR Workflow

This file is the single source for issue/PR records, status language, review
evidence, and closure. Product and deployment invariants remain in the
guardrails and 211 runbook.

## Closure Loop

For goal-sized work, gate closures, and new defects, use:

`issue -> branch -> PR -> focused verification -> review -> merge -> deploy/smoke when required -> close with evidence`

Only concrete GitHub checks applicable to the changed path and actually observed
on the PR count as CI gates. Track a missing required check separately instead
of expanding an unrelated product PR.

## Records And Evidence

- The linked issue and PR are normally the plan, change description, and durable
  status record. Do not create a spec/plan/status trio by default.
- Create a separate design only when a security, authorization, tenant,
  persistence, concurrency, public-contract, release, deployment, or
  infrastructure decision needs durable explanation.
- Record blockers and evidence on the issue or PR. Historical evidence cannot
  prove current readiness.

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
- Checks stay bounded to the changed risk. Unowned affected paths fail closed as
  `external_check`; an explicit bounded suite cannot discharge unrelated paths.
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
- Local/source verification, GitHub review and CI, deployment, and 211 runtime
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

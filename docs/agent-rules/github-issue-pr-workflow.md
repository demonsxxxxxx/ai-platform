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
- Add a design when the change affects security, auth or authorization, tenant
  isolation, schemas or public contracts, persistence, concurrency,
  infrastructure, runtime/release behavior, or an unresolved cross-module
  decision.
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

# Multi-Agent Context Workflow

This file governs assistant task lifetimes, ownership, authority, and context
handoff in this repository. It does not define the product's deferred platform
multi-run route or prove B3 SDK subagent capacity.

## Operating Principle

- The main agent owns the user goal, invariants, decisions, integration, final
  verification, and user-facing conclusion.
- Delegate only when isolation, parallelism, continuity, or independent evidence
  is worth more than the dispatch and review cost. Capacity is not a target.
- Keep one owner for every shared file and every deployment mutation. Do not use
  delegation to split tightly coupled design judgment.
- Prefer compact evidence over raw transcripts. A result is usable only after the
  main agent checks the decisive evidence.

## Task Lifetimes And Ownership

### Disposable probes

A disposable probe is a one-shot, read-only context-isolation task. It may
inspect any bounded material whose breadth, noise, or independence makes direct
reading inefficient, then return a compact evidence packet. The subject is not
limited to a fixed task list.

- Give it a self-contained question, search boundary, expected evidence, and
  stop condition. Prefer a fresh context when the tool supports one.
- It may gather, compare, summarize, or independently observe; it does not own
  implementation, workflow continuation, or the final decision.
- It receives no write lease, credential, GitHub or remote mutation authority,
  deployment authority, destructive-operation authority, or decisive high-risk
  review gate.
- Do not turn or re-charter a disposable probe into a writer. If it discovers
  durable work, it returns the subject and evidence, then stops.

### Persistent tasks

Use a persistent, project-bound Codex task for implementation, complex or
multi-generation testing/review, browser acceptance, automation, release,
deployment, or other work that needs durable ownership. Repository work uses an
independent clean worktree.

Every persistent-task dispatch records the goal and role, controller epoch,
task generation, project binding, worktree, branch, base/head SHA, clean state,
writable and forbidden paths, permissions, lease, next event, evidence ceiling,
and terminal condition. A goal or role change requires a new task; a same-goal
change of base or authority requires an explicit re-charter.

## Authority Boundary

- User authorization for one task or main session does not automatically grant
  another task or disposable probe the same authority.
- A task may mutate only subjects explicitly covered by its dispatch and proven
  permission posture. Shared filesystem access alone is not permission.
- Implementation and operational ownership stay in persistent tasks when their
  permission posture is confirmed. The controller consumes compact results and
  performs only decisive checks needed for approval.
- Direct controller mutation is break-glass only: the normal persistent-task
  path is unavailable and the user explicitly authorizes the exact mutation.
  The ordinary source, lease, rollback, evidence, and parity invariants still
  apply; broad standing authorization is insufficient.

## Model And Review Routing

- The default reasoning ceiling for the controller and newly created, resumed,
  or re-chartered tasks is `xhigh`. `max` requires explicit user authorization
  for the exact task and a recorded reason; `ultra` is not routine. A stricter
  current user instruction wins.
- Use an available economical read-only role for disposable probes when it is
  sufficient. Do not hard-code a probe to a particular model name or dispatch
  probes merely to consume capacity.
- Set model and reasoning fields deliberately when the interface exposes them.
  Otherwise use the available configuration and do not claim an unconfirmed
  model-specific or reasoning-specific gate.
- Preserve reviewer independence for high-risk work. A disposable probe is not
  the sole final reviewer for auth, tenant isolation, concurrency, sandboxing,
  public contracts, or deployment.

## Release Lifecycle

- Before creating a release owner or mutation lease, a read-only gate must record
  `RELEASE_READINESS_PASS` for the exact release subject using
  `docs/operations/211-release-operations-runbook.md`.
- A missing, stale, or blocked readiness item records
  `RELEASE_READINESS_BLOCKED`; do not create the release owner, grant a mutation
  lease, or count a release generation.
- After readiness passes, create exactly one project-bound persistent release
  task and one mutation lease. The controller does not run or continuously
  monitor the release; it consumes the terminal packet and may perform one final
  parity check.

## Goal-Level Repair Budget

- Before the first implementation or release fix, record one stable `goal_id`, a
  finite goal-specific `repair_budget_total`, `repair_generation_used`, and
  `repair_budget_remaining`. This workflow has no permanent numeric default.
- The budget belongs to the product or release goal. A new Issue, PR, branch,
  task, controller epoch, or reviewer does not reset it.
- A repair generation starts when a persistent writer is authorized to change
  code or durable runtime state after a blocking result. Re-review and related
  follow-up records remain in that generation until its fixed-SHA result.
- A read-only readiness failure does not consume a generation, but it closes the
  release gate until the blocker is resolved.
- When exhausted, record `GOAL_REPAIR_BUDGET_EXHAUSTED`, revoke write and release
  leases, and return a decision packet with the minimum blocker, simplification
  choices, risks, and one bounded verification plan. Only an explicit user
  decision may increase or reset the budget.

## Context And Result Intake

- Target the main context at 120k tokens or less. For long or output-heavy work,
  maintain one compact checkpoint containing the stable goal, latest decisions,
  active owners and leases, current source/runtime subjects, accepted evidence,
  unresolved risks, and next gates.
- Discard repeated output, failed-path detail, stale plans, disproven hypotheses,
  and raw transcripts after their evidence is compressed.
- A task result should state its conclusion, exact evidence, inspected or changed
  subjects, verification observed, unresolved risks, and recommended next gate.
- Record review evidence and status claims according to
  `docs/agent-rules/github-issue-pr-workflow.md`; chat-only or raw task output is
  not durable PR or issue evidence.

## Recovery And Reporting

- If a turn fails with `No tool output found`, treat it as an orphan-call protocol
  error unless a recorded request demonstrably lacks its output. End that turn,
  restore from the current checkpoint in a new turn, and do not guess a result or
  replay the entire tool sequence.
- The main agent reports the final conclusion. State which delegated work was
  actually used, and never claim an unobserved model, review, test, deployment,
  or runtime result.

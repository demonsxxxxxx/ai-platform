# Multi-Agent Context Workflow

This file governs assistant task lifetimes, ownership, authority, and context
handoff in this repository. It does not define product multi-run behavior or
prove SDK subagent capacity.

## Operating Principle

- The main agent owns the user goal, invariants, decisions, integration, final
  verification, and user-facing conclusion.
- Delegate only when isolation, parallelism, continuity, or independent evidence
  is worth more than dispatch and review cost. Capacity is not a target.
- Keep one writer for every shared file set and one owner for every deployment
  mutation. Do not split tightly coupled design judgment across owners.
- Prefer compact evidence over raw transcripts. The main agent accepts a result
  only after checking its decisive evidence.

## Task Lifetimes

### Disposable probes

A disposable probe is a one-shot, read-only context-isolation task for a bounded
question. It may gather, compare, summarize, or independently observe, but it
does not own implementation, workflow continuation, or the final decision.

- Give it the question, search boundary, expected evidence, and stop condition.
- It must not read or receive a real `.env`, secret, credential, or unredacted
  sensitive runtime payload. Use authorized redacted evidence instead.
- It receives no write lease, remote mutation authority, deployment authority,
  destructive-operation authority, or decisive high-risk review gate.
- Do not turn or re-charter a disposable probe into a writer. Return discovered
  durable work to a persistent owner.

### Persistent tasks

Use a persistent, project-bound Codex task for implementation, multi-round
testing or review, browser acceptance, release, deployment, or other work that
needs durable ownership. Repository work uses an independent clean worktree.

Dispatch records the goal and role, project/worktree/branch, exact base and head,
writable and forbidden paths, permission and lease scope, evidence ceiling, and
terminal condition. A goal or role change requires a new task; changed source or
authority requires explicit re-charter.

## Authority Boundary

- User authorization for one task or main session does not automatically grant
  another task or disposable probe the same authority.
- A task may mutate only subjects explicitly covered by its dispatch and proven
  permission posture. Shared filesystem access alone is not permission.
- Exactly one persistent writer holds a given write scope. Transfer ownership
  only after the prior writer releases it and identifies the exact safe SHA.
- Direct controller mutation is break-glass only: the normal persistent-task
  path is unavailable and the user explicitly authorizes the exact mutation.
  Explicit task authority is required; broad standing authorization is
  insufficient.
- Preserve reviewer independence for auth, tenant isolation, concurrency,
  sandboxing, public contracts, and deployment. A disposable probe is not the
  sole final reviewer for these subjects.

## Release Lifecycle

- Read-only release readiness must pass for the exact release subject under
  `docs/operations/211-release-operations-runbook.md` before granting mutation
  authority. Missing, stale, or blocked evidence keeps the release blocked.
- After readiness passes, use exactly one project-bound persistent release task
  and one mutation lease. Do not run a second release attempt or mutate the host
  outside that lease.
- The release owner returns a terminal evidence packet. The controller may
  perform a final parity check but does not become a second release owner.

## Context And Result Intake

- For long or output-heavy work, maintain one compact checkpoint containing the
  stable goal, current decisions, owners and leases, exact source/runtime
  subjects, accepted evidence, unresolved risks, and next gate.
- Discard repeated output, stale plans, disproven hypotheses, and raw transcripts
  after compressing their decisive evidence.
- A result states its conclusion, exact inspected or changed subjects,
  verification observed, unresolved risks, and recommended next gate.
- Record review evidence and status claims according to
  `docs/agent-rules/github-issue-pr-workflow.md`; chat-only output is not durable
  PR or issue evidence.
- Do not persist a session board or phase ledger in repository docs. The active
  controller checkpoint is the only current coordination record.

## Recovery And Reporting

- If a turn fails with `No tool output found`, treat it as an orphan-call protocol
  error unless a recorded request demonstrably lacks its output. Restore from the
  current checkpoint in a new turn; do not guess a result or replay the entire
  tool sequence.
- The main agent reports the final conclusion. Never claim an unobserved review,
  test, deployment, or runtime result.

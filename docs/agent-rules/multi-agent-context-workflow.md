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
  durable work to the main agent or a designated persistent owner.

### Persistent tasks

Use a persistent delegated task only when work needs continuity beyond a bounded
probe. It reuses its existing project worktree by default. Create another clean
worktree only when concurrent writers need filesystem isolation or an
independent fixed-commit check cannot safely use the existing worktree; a new
issue or task alone is not a reason.

Dispatch records the goal and role, project/worktree/branch, current source
identity, writable and forbidden paths, permission and lease scope, evidence
ceiling, and terminal condition. A goal or role change requires a new task;
changed source or authority requires explicit re-charter. Creating a worktree
does not trigger a dependency install: install only dependencies required by a
selected check and missing from that worktree. At the terminal condition, report
any added worktree and its generated dependency directories for authorized
cleanup instead of retaining them silently.

## Authority Boundary

- User authorization for one task or main session does not automatically grant
  another task or disposable probe the same authority.
- A task may mutate only subjects explicitly covered by its dispatch and proven
  permission posture. Shared filesystem access alone is not permission.
- Exactly one writer holds a given write scope. Transfer ownership only after
  the prior writer releases it and records the current source and worktree state.
- Preserve reviewer independence for auth, tenant isolation, concurrency,
  sandboxing, public contracts, and deployment. A disposable probe is not the
  sole final reviewer for these subjects.

## Release Lifecycle

- Read-only release readiness must pass for the exact release subject under
  `docs/operations/release-operations-runbook.md` before granting mutation
  authority. Missing, stale, or blocked evidence keeps the release blocked.
- After readiness passes, use exactly one designated release owner and one
  mutation lease. Do not run a second release attempt or mutate the host outside
  that lease.
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
- Do not persist a session board or phase ledger in repository docs. Keep current
  coordination state in the active task record.

## Reporting

The main agent reports the final conclusion. Never claim an unobserved review,
test, deployment, or runtime result.

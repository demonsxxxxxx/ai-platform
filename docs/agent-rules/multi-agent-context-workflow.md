# Multi-Agent Context Workflow

Read when delegating coding work or coordinating shared resources. This guide
does not define product multi-Agent execution or SDK capabilities.

## Operating Principle

Use delegation only when parallelism, isolation, or independent review helps.
The main agent owns integration, final verification, and the user-facing result.
A single-owner task needs no delegation packet or additional worktree.

## Task Lifetimes

Give a delegate the goal, source/worktree, allowed writes, verification target,
and completion condition. Use the current task record; no special task taxonomy
or JSON format is required. Update that record when scope changes. A scope
update cannot implicitly expand permissions or replace independent review.

### Disposable probes

Read-only probes inspect a bounded question and return evidence. They do not
inherit implementation, deployment, destructive-operation, or final-review
permission. No real environment files, credentials, or private prompts go to a
probe. Assign any resulting implementation separately with an explicit owner.

### Persistent tasks

Keep a continuing task in its existing worktree unless concurrent writers or
an independent fixed-source check require isolation. Install only dependencies
needed for the selected checks. Report created worktrees and generated
dependencies for authorized cleanup; preserve unrelated local changes.

## Authority Boundary

Keep one writer per overlapping file scope. Before handoff, stop the prior
writer and identify its resulting changes. Shared access grants no additional
authority; delegates receive only permissions explicitly assigned within the
user-authorized task. Independent high-risk review follows the
[PR workflow](github-issue-pr-workflow.md), not the implementer's own claim.

## Release Lifecycle

The [release runbook](../operations/release-operations-runbook.md) controls remote
mutation. Fresh read-only readiness for the exact subject must pass first; use
one designated release owner and one mutation lease. Parallel coding authority
does not authorize parallel release attempts or host mutations outside that lease.

## Context And Result Intake

For long work, keep one compact checkpoint in the active task record: current
goal, decisions, source, ownership, observed evidence, risks, and next action.
Return conclusions and decisive file/test references rather than transcripts.
The main agent checks that evidence before integrating. Current coordination
state stays in the task or PR, not a durable repository status board.

## Reporting

Report actual changes, verification, and unresolved limits. A delegate's
assertion alone does not prove a test, review, deployment, or runtime result.

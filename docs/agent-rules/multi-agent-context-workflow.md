# Multi-Agent Context Workflow

This file governs how agents should use sub-agents while working in this
repository. It is about the assistant's working process, not the platform's
deferred G8 platform-level multi-run orchestration route or B3 SDK subagent
fanout capacity evidence. Assistant sub-agents in this workflow do not prove,
open, or close ordinary-user platform-level multi-run product exposure.

## Goals

- Keep the main agent as the coordinator, decision maker, integrator, and final
  voice to the user.
- Use sub-agents for bounded, independent work that can run in parallel without
  blocking the main agent's immediate next step.
- Keep the main agent context under a target ceiling of 120k tokens so the
  active working state stays clear.
- Prefer summarized evidence over raw transcript accumulation.

## Role Model

- Main agent: understands the user goal, decomposes work, chooses what can be
  delegated, keeps the critical path moving, reviews results, integrates
  changes, verifies outcomes, and reports to the user.
- Worker or explorer agents: own narrow scopes such as one subsystem, one test
  file, one code path, one review concern, or one independent implementation
  slice.
- Summary or review agent: optional. Use only when there are many independent
  outputs or a high-risk result needs an extra review pass. The main agent still
  owns the final conclusion.

## Work Lanes, Preflight, And Evidence Ownership

- Before delegation, the controller must make one task inventory: the intended
  outcome, current subject, writable paths, required checks, and the single
  owner of each item. Eliminate duplicate work before dispatch; do not retain
  duplicate tests, searches, or probes merely to occupy agents.
- Every write lane fails closed before editing. Record that its isolated
  worktree is clean; the recorded base, `origin/main`, merge-base, and `HEAD`
  equal the required current subject; and its writable paths are explicit. A
  dirty coordination root is never an implementation source or deliverable; it
  may only be inspected as a fail-closed comparison.
- Persistent implementation tasks may only make source edits and run affected
  tests, commit, push, and open a Draft PR from that clean isolated worktree
  and explicit write envelope; an implementer cannot be the final reviewer.
  No subagent or ordinary persistent task may access browser/user credentials
  or gain broad remote mutation, cleanup, deployment, or final authority.
- Disposable agents are read-only context compressors. They may summarize
  large logs or test output and search peripheral material, but never perform
  remote writes, deployment, cleanup, credential access, or final release
  decisions.
- Evidence has a default owner: the implementer runs affected tests, compile,
  and `git diff --check`; CI owns standard regression; the reviewer owns code
  review plus a small number of high-risk attack or concurrency probes; the
  controller fills only uncovered final gates.

## Runtime, Deployment, And Review Boundaries

- Confirmed or inherited filesystem, network, or approval capability is a
  technical prerequisite only and never grants authorization. It establishes
  whether a permitted operation is technically possible, not who may perform
  it or make a final claim.
- Only a dedicated persistent runtime verifier or the controller may run a
  state-mutating Redis, Lua, or runtime probe. The probe envelope must name the
  fixed host, test-key prefix, TTL, prohibition on real-key reads, exact
  cleanup, and failure evidence before execution. Do not improvise a
  state-mutating probe from a disposable agent or an unbounded shell session.
- Only the named controller or deployment owner may access browser/user
  credentials, mark Ready, merge, deploy, perform 211 build/recreate/cleanup
  or other mutation, or make release or final claims. Other agents may supply
  read-only evidence but may not share this authority.
- The default review cadence is: invariant preflight; one implementation; one
  complete independent review; one consolidated Critical/Important repair
  batch; and one final re-review. Do not turn review into parallel, repetitive
  fix cycles unless a new material risk is recorded.

## Evidence Intake, Close Sweep, And Rotation

- The controller consumes compressed evidence, not raw long logs or scripts.
  Each evidence item records command, subject, observed time, decisive
  lines/result, and artifact location; label stale or historical evidence as
  such instead of re-presenting it as current.
- After every completed batch, run a close-sweep inventory covering task or
  archive state, worktree classification, and process ownership. Do not bulk
  clean up tasks, worktrees, containers, or artifacts without exact ownership
  proof.
- Record a lessons ledger entry as: `incident -> root cause -> new guardrail ->
  enforcement point -> verification`. Repeated or high-impact lessons must
  become a policy rule or an automated check, rather than remaining a chat-only
  reminder.
- Rotate the controller at every major phase handoff, repeated compaction, or
  material authority change. The mandatory handoff packet contains the
  objective and non-goals, `origin/main`, runtime subject, active owners and
  leases, accepted and stale evidence, risks, next gates, and cleanup
  classifications.

## Delegation Rules

- Delegate only when the active user request, repository rules, and available
  delegation tool policy permit sub-agent work.
- If the available delegation tool requires explicit user authorization for
  sub-agents, do not spawn unless the active user request explicitly asks for
  multi-agent, parallel-agent, delegated, or review-agent work.
- If high-risk or stage-gate work requires independent review but the available
  delegation path is not authorized or not suitable, keep the review gate open
  and record the blocker or acceptable alternate review path on the PR or issue.
- Before delegating, identify the main critical-path task and keep that task in
  the main session unless it is clearly non-blocking.
- Give each sub-agent a self-contained task with a clear scope, expected output,
  and ownership boundary.
- For code-editing sub-agents, assign disjoint file or module ownership and tell
  them that other agents may be editing nearby files.
- Main-session authority is not a sub-agent permission grant. When the active
  user explicitly authorizes the current main thread, the main agent may perform
  repository writes, GitHub writes, 211 sync/deploy/restart, Docker cleanup, and
  other high-risk operational work directly, while still following the
  repository's secret, verification, source-authority, and deployment-cleanup
  rules.
- Sub-agent restrictions must not be read backward as main-session restrictions.
  When the active user authorizes the main thread, keep write, GitHub, 211,
  Docker, deployment, and cleanup operations in the main session and execute
  them directly there instead of delegating them.
- In this workflow, main-thread authorization is a direct-operation allowance,
  not a delegation allowance. Confirmed or inherited filesystem, network, or
  approval capability remains a technical prerequisite only; it never grants
  a subagent authorization for credentials, remote mutation, cleanup,
  deployment, or final authority.
- Standing main-thread phrases such as `主线程全部授权`, `主线程有权限操作`, or
  `执行` authorize the current main session for the active task only. They do
  not authorize sub-agents to perform writes, GitHub writes, Docker, deployment,
  remote runtime, or destructive operations.
- No subagent or ordinary persistent task gains credentials, broad remote
  mutation, cleanup, deployment, or final authority. The only delegated
  exceptions are the bounded persistent implementation envelope and the
  dedicated runtime verifier's fixed-host/test-key-prefix/TTL/no-real-key-read/
  exact-cleanup probe envelope; neither exception permits broad remote or
  release work.
- Do not delegate tasks that are tightly coupled, require continuous cross-file
  design judgment, or would immediately block the main agent.

## Context Budget

- Target main-agent context: 120k tokens or less.
- When the task is long-running, output-heavy, or near the target ceiling, create
  a compact context checkpoint and rely on that checkpoint for subsequent work.
- Checkpoints should preserve:
  - current goal and non-goals;
  - latest user decisions;
  - active constraints and safety boundaries;
  - current source-of-truth files, routes, hosts, issues, or commands;
  - completed work and verification evidence;
  - unresolved questions, risks, and next steps.
- Checkpoints should discard or compress:
  - repeated command output;
  - completed exploration detail;
  - disproven hypotheses;
  - stale plans;
  - raw sub-agent transcripts;
  - logs already reduced to relevant evidence.

## Sub-Agent Output Intake

Sub-agent results should enter the main context as compact summaries, not raw
transcripts. Prefer this shape:

```text
Conclusion:
Evidence:
Files touched or inspected:
Verification run:
Risks or open questions:
Recommended next step:
```

The main agent should review returned changes or conclusions before relying on
them. For high-risk work, run the relevant verification in the main session or
record why verification could not be run.

## GitHub Review Evidence

For goal-sized work, gate work, and PR merge decisions, sub-agent review only
counts as durable review evidence after the main agent records it on the linked
GitHub PR or issue. The GitHub comment should include:

- reviewer role or identifier;
- review scope and files or PRs inspected;
- findings grouped by severity, or an explicit no-blocking-findings result;
- accepted fixes, evidence-backed rejections, or follow-up issues;
- verification commands and observed outcomes, or a clear statement that the
  reviewer stayed read-only and did not run verification;
- status boundary such as `reviewed`, `user-authorized review substitute`, not
  `211 verified`, and not `gate closable`.

Do not rely on a raw sub-agent transcript, a chat-only summary, or an unposted
local note as the sole basis for issue closure or PR merge approval.

## Reporting

- The main agent reports the final answer to the user.
- Do not let sub-agent conclusions bypass main-agent review.
- State which skills or multi-agent workflow were actually used.
- Do not claim model-specific, reasoning-specific, review, test, deployment, or
  211 evidence unless that evidence was directly observed.

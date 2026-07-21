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
  not a delegation allowance. Sub-agents stay read-only for GitHub, Docker, 211,
  deployment, and destructive operations unless inheritance of the main
  session's filesystem, network, approval, and permission posture is explicitly
  confirmed.
- Standing main-thread phrases such as `主线程全部授权`, `主线程有权限操作`, or
  `执行` authorize the current main session for the active task only. They do
  not authorize sub-agents to perform writes, GitHub writes, Docker, deployment,
  remote runtime, or destructive operations.
- Do not delegate write, deployment, remote runtime, long-running operational,
  Docker, GitHub write, or destructive tasks to sub-agents unless the
  delegation path is confirmed to inherit the same filesystem, network,
  approval, and permission posture as the main session.
- Do not delegate tasks that are tightly coupled, require continuous cross-file
  design judgment, or would immediately block the main agent.

## Disposable Subagents Versus Persistent Tasks

- `spawn_agent` creates a disposable, one-shot subagent. Use it only for short,
  bounded, read-only work such as cross-file search, log compression, an
  evidence probe, or a limited independent review. Prefer `fork_turns = "none"`
  and require a compact evidence packet.
- A disposable subagent must not receive a code-write lease, GitHub mutation,
  credential, 211 mutation, deployment, long-running monitor, or continuing
  workflow ownership. Confirmed shared filesystem access does not convert it
  into a persistent owner.
- Use a persistent Codex task created through the thread API for implementation,
  multi-generation or complex testing, PR/review lifecycle work, browser
  acceptance, automation, release, deployment, or any task expected to survive
  more than one bounded turn. Repository work must be project-bound and use an
  independent clean worktree.
- Never extend or re-charter a disposable subagent into a writer or release
  owner. If its investigation discovers durable work, it returns the exact
  subject, evidence, and proposed scope, then stops; the controller creates a
  new persistent task after the relevant gate passes.
- Every persistent-task dispatch must record: `project_binding_status`, task
  lifetime, goal ID, role, controller epoch, worktree, branch, base/head SHA,
  clean status, writable and forbidden paths, permissions, lease ID, next
  event, evidence ceiling, and terminal/exit conditions.

## Model And Reasoning Ceiling

- The controller and every newly created, resumed, or re-chartered task must use
  a reasoning effort no higher than `xhigh`. `max` and `ultra` are forbidden;
  high-risk work changes the model/role pairing and verification depth, not this
  ceiling.
- Prefer Luna with `low` reasoning for simple disposable work that isolates
  broad or noisy context: cross-file search, large-log compression, failed-node
  baseline comparison, inventory/checklist extraction, module-state refresh,
  and peripheral evidence reduction.
- At each new phase, the controller should first ask whether one or two
  independent Luna-low probes would reduce context pollution. Use
  `fork_turns = "none"`, a self-contained prompt, a compact evidence packet, and
  a ten-minute stop. Capacity is not a target, so do not manufacture probe work.
- Luna-low probes are evidence compressors only. They must not implement, own a
  persistent test/review generation, mutate GitHub or 211, deploy, receive
  credentials, or make a final decision for security, authorization,
  concurrency, sandbox, deployment, or public contracts.
- Use Terra at `high` or `xhigh` for bounded implementation and independent
  testing/review, and Sol at `xhigh` for controller reasoning or decisive
  high-risk work. Preserve reviewer independence instead of increasing effort
  above `xhigh`.

## Release Readiness Before Mutation

- A release generation, project-bound release owner, or mutation lease may be
  created only after a read-only gate records `RELEASE_READINESS_PASS` for the
  exact release subject. The controller may run one decisive read-only preflight
  and may use disposable probes to compress noisy evidence; it must not create
  the release owner merely to discover whether the environment is ready.
- The readiness record must prove all of the following for the same fresh
  observation window:
  - exact source commit, publisher commit, target runtime, and target host;
  - an executable host-side test plan for every required POSIX contract, using
    available pytest, an approved isolated offline wheelhouse/virtualenv, or an
    explicitly approved equivalent harness;
  - Docker and Compose capability, including the canonical daemon route;
  - the exact release-authority directory plus key, state, journal, and lock
    inventory and permissions;
  - current per-service Compose ownership and recover/adopt compatibility;
  - no active mutator and no release-lock holder;
  - the exact runtime-only, relabel-only, or full-build plan, including why each
    service needs mutation; and
  - the current runtime subject and an executable rollback subject.
- If any readiness item is missing, stale, or blocked, record
  `RELEASE_READINESS_BLOCKED`, do not create a release owner or mutation lease,
  and do not count a release generation. Resolve the blocker in a separately
  authorized persistent task when code or durable environment work is required.
- After readiness passes, create exactly one project-bound persistent release
  task with an independent clean worktree and the full dispatch envelope. The
  controller consumes its compact terminal packet and performs at most one
  final parity check; it does not execute or continuously monitor the release.

## Goal-Level Repair Budget

- Before the first implementation or release-controller fix, assign one stable
  `goal_id`, `repair_budget_total`, `repair_generation_used`, and
  `repair_budget_remaining`. Unless the user sets another limit, a release goal
  permits at most two repair generations after its initial candidate.
- The budget belongs to the product or release goal, not to an Issue, PR,
  branch, task, controller epoch, or reviewer. Creating or renaming any of those
  does not reset the budget.
- One repair generation begins when a persistent writer is authorized to change
  the candidate after a blocking implementation, test, review, readiness, or
  release result. Local edits, re-review, and a follow-up Issue for the same
  blocker remain part of that generation until its fixed-SHA terminal result.
- A read-only readiness failure with zero code or runtime mutation does not
  consume a repair generation, but it closes the release gate until the blocker
  is resolved. Authorizing a persistent task to change code or durable host
  state consumes one generation before work starts.
- When the budget is exhausted, record `GOAL_REPAIR_BUDGET_EXHAUSTED`, revoke all
  implementation and release leases, and stop. A follow-up Issue may preserve
  the blocker but must not authorize another implementation cycle. The
  controller must give the user a decision packet containing the minimum
  blocker, simplification options, risks, and one bounded verification plan.
  Only an explicit user decision may reset or increase the same goal's budget.

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

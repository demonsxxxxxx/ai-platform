# AI Platform Agent Rules

## Scope

This file applies to the current `ai-platform` repository root.

## Local Verification

- Run the smallest repository-native checks that can falsify the change.
- Run ordinary local pytest stages from the target worktree root through
  `python tools/run_test_stage.py`; the procedure and result semantics live in
  `docs/agent-rules/local-test-execution.md`.
- Do not run full-repository pytest as a routine gate. Run it only when the user
  requests it for a named risk decision.
- Run Docker validation, builds, restarts, and runtime smoke only on a
  Docker-capable environment. Follow
  `docs/operations/release-operations-runbook.md` for release operations.

## Remote Runtime Access

- Access s72 only through SSH MCP. Confirm the configured connection and make
  one bounded, secret-safe connection attempt before reporting it unavailable.
- Do not fall back to system SSH tools, a browser, or local Docker state to infer
  remote runtime state.
- Commands and output must not contain `.env` values, account identifiers,
  passwords, tokens, or prompts.

## Authority

- Use the current repository root, current user instruction, current code and
  tests, and the durable authorities indexed by `docs/README.md`.
- Confirm issue state from GitHub and runtime state from fresh evidence for the
  exact subject. Historical evidence, old paths, and short-term notes are not
  current product or runtime authority.
- Keep current owners, ordering, blockers, and completion state in the active
  task, issue, or pull request rather than durable policy documents.
- `.codegraph` is a navigation cache, not source or runtime authority. Read the
  current source before editing.

## Working And Delegation

- Work in the current project worktree by default. Delegate only when isolation,
  parallelism, continuity, or independent evidence is worth the coordination
  cost.
- Create another worktree only for concurrent writers or an independent
  fixed-commit check that cannot safely use the current worktree. A new issue or
  delegated task alone is not a reason.
- Install only dependencies required by a selected check and missing from that
  worktree. Report added worktrees and generated dependency directories for
  authorized cleanup when the task ends.
- Detailed ownership, permission, release-lease, and handoff rules live in
  `docs/agent-rules/multi-agent-context-workflow.md`.

## Change Control And Delivery

- A focused ordinary change may use its pull request as the complete change
  record. High-risk changes follow the Change Contract, review, verification,
  and delivery rules in `docs/agent-rules/github-issue-pr-workflow.md`.
- Every behavior change needs a falsifiable owning test. Claim assembled or
  runtime behavior only after observing that path. Template text and Agent
  self-report are not evidence.
- `AGENTS.md` is repository coding authority. Product Agent.md content belongs
  to the Agent Profile/Workspace domain and is not implementation authority.

## Product Boundaries

- The platform owns admission, authorization, context binding, queueing,
  sandbox policy, persistence, and public projections. Engine-specific SDK
  types and callbacks terminate inside the Engine adapter.
- Keep tenant, workspace, and user boundaries explicit in queue, quota,
  maintenance, memory, and operational projections.
- Ordinary-user projections must not expose raw skill identifiers, storage
  keys, runtime paths, command fingerprints, executor-private payloads, or
  secret-like data.
- Fake sandbox providers are test-only. Runtime claims require evidence from
  the exact deployed subject on a controlled Docker-capable host.
- Do not mount the Docker socket in the default Compose file and do not copy,
  print, or commit real deployment environment files.

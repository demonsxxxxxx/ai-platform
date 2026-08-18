# AI Platform Agent Rules

## Scope

This file applies to the current `ai-platform` repository root.

## Local Verification

- This Windows workstation currently does not provide a local `docker` command. If `docker` is not recognized, do not repeatedly retry local `docker compose` checks.
- For local readiness, prefer repository-native checks such as:
  - `python -m compileall -q app tools scripts`
  - `python tools/run_test_stage.py --stage example-owning --timeout-seconds 300 -- tests/test_run_test_stage.py`
  - relevant integration or smoke checks for the changed path
- After the local test-stage runner is accepted on `main`, use it for ordinary
  local pytest execution. The introducing change may test the runner directly
  with the repository's existing pytest command and a workspace-local
  basetemp; a candidate-owned runner cannot certify its own introduction.
- Run pytest from the target worktree root with explicit test files or node IDs.
  Do not use nested `spawnSync`/capture runners, bypass the per-worktree lock,
  or treat partial output from a timeout as a pass. If tests pass separately but
  fail or hang together, stop and fix the test-isolation failure.
- The normative local procedure and failure taxonomy live in
  `docs/agent-rules/local-test-execution.md`.
- Do not run or require full-repository pytest by default. Full pytest is
  prohibited as a routine gate because it wastes time; run it only if the user
  explicitly requests it for a specific risk decision.
- Run Docker validation, builds, restarts, and runtime smoke only on a
  Docker-capable environment. The authoritative commands and recovery paths
  live in `docs/operations/release-operations-runbook.md`.
- The local test-stage runner creates a unique basetemp under the workspace-local,
  git-ignored `.pytest-tmp/` directory. Never rely on the system temp path.
  When directly testing the runner itself, create `.pytest-tmp/` first, pass a
  fresh child through `--basetemp`, and report any stale-root reason.

## Remote Runtime Access

- Remote access to s72 is allowed only through SSH MCP.
- Always call `mcp__ssh_mcp_server__list_servers` first to confirm that the
  connection name is configured. A `disconnected` status only means no active
  SSH session exists yet; it is not by itself a connection failure.
- Then make one bounded, secret-safe `mcp__ssh_mcp_server__execute_command`
  call with `connectionName='s72'` so the MCP server can connect on demand.
- Never fall back to system `ssh`, `scp`, or `plink`, local SSH configuration,
  a browser, or local Docker state to infer the remote runtime.
- The remote operation is `BLOCKED` only when SSH MCP is unavailable, the
  connection is absent, or the actual connection attempt fails.
- Commands and output must not contain `.env` values, account identifiers,
  passwords, tokens, or prompts.

## Authority

- Use the current repository root as the local `ai-platform` source.
- Use the current user instruction, current code and tests, the architecture
  documents indexed by `docs/README.md`, fresh evidence for the exact runtime
  subject, and only issues named by the active goal and confirmed from fresh
  GitHub state.
- Do not treat short-term execution notes, old local paths, or historical service layouts as product requirements.
Keep concrete issue numbers, owners, ordering, and current gate state in the
roadmap or Controller Current rather than this durable entry file.

## Documentation Authority

- `docs/README.md` is the document index. It directs durable policy, operations,
  and evidence ownership without becoming a project status board.
- Historical runtime observations belong only in reviewed, redacted structured
  evidence under `docs/release-evidence/`; they never establish current runtime
  state without fresh verification.

## CodeGraph Navigation

- This repository's existing `.codegraph/codegraph.db` is a local navigation
  index, not implementation or runtime authority.
- In Pi, prefer the CodeGraph MCP server's `codegraph_explore` tool for concept,
  symbol, flow, caller/callee, and impact discovery. Phrase the requested graph
  relationship explicitly and keep `maxFiles <= 4`.
- The MCP server runs with its project path fixed to this repository and keeps a
  debounced file watcher active. Treat any pending-file warning as stale data and
  read the current source directly before continuing.
- Always read the current source before editing. Use the native `codegraph` Pi
  tool only for a specific graph action or when MCP is unavailable; run its
  `sync` action before relying on CLI query results.
- Never run `init`, full `index`, or `uninit` automatically. Fall back to
  repository-scoped `fffind`/`ffgrep` for exact text, unsupported files, or empty
  graph results. Never search Pi sessions or protected credential paths, and
  never treat graph output as runtime evidence.

## Change Control

- Before non-mechanical edits, record the Change Contract required by
  `docs/agent-rules/github-issue-pr-workflow.md` in the issue or persistent
  task; the PR links and reconciles that prior record. Read-only exploration may
  fill missing fields, but coding waits for a
  known owner, bounded paths, invariants, acceptance, regression proof, evidence
  ceiling, and stop conditions.
- Revise the contract before expanding scope. Every behavior change needs a
  falsifiable owning test; claim assembled or runtime behavior only after
  observing that path. Template text and Agent self-report are not evidence.
- `AGENTS.md` is repository coding authority. Product Agent.md content belongs
  to the Agent Profile/Workspace domain and is not implementation authority.

## Delivery Workflow

Use GitHub issues and pull requests as the default closure loop for goal-sized
work, gate closures, and newly discovered defects. The detailed issue, review,
fixed-SHA verification, and closure rules live only in
`docs/agent-rules/github-issue-pr-workflow.md`.

`docs/agent-rules/multi-agent-context-workflow.md` is the single source for task
lifetimes, ownership, authority, delegation, release leases, and context
handoff. Do not restate those rules here.

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

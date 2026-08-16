# AI Platform Agent Rules

## Scope

This file applies to the current `ai-platform` repository root.

## Local Verification

- This Windows workstation currently does not provide a local `docker` command. If `docker` is not recognized, do not repeatedly retry local `docker compose` checks.
- For local readiness, prefer repository-native checks such as:
  - `python -m compileall -q app tools scripts`
  - `python -m pytest <changed-or-affected-tests> -q --basetemp .pytest-tmp`
  - relevant integration or smoke checks for the changed path
- Do not run or require full-repository pytest by default. Full pytest is
  prohibited as a routine gate because it wastes time; run it only if the user
  explicitly requests it for a specific risk decision.
- Run Docker validation, builds, restarts, and runtime smoke only on a
  Docker-capable environment. The authoritative commands and recovery paths
  live in `docs/operations/release-operations-runbook.md`.
- Every local pytest invocation must pass a basetemp path under the
  workspace-local, git-ignored `.pytest-tmp/` directory; use
  `--basetemp .pytest-tmp` by default and never rely on the system temp path.
- If stale unreadable content prevents reuse of that root, pass a fresh
  non-existing child such as
  `--basetemp .pytest-tmp\run-verify-<timestamp>` and report the reason.

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

## Change Design And Coding Control

- Before editing a non-mechanical change, establish the Change Contract defined
  in `docs/agent-rules/change-contract.md`. Keep it in the linked issue, PR, or
  persistent-task dispatch; do not create a repository status document for it.
- Do not begin implementation until the contract identifies the observable
  problem, owning authority, exact base, writable and forbidden paths,
  invariants, acceptance criteria, regression proof, evidence ceiling, and stop
  conditions. Read-only exploration may continue to resolve those fields.
- Record genuine alternatives and why they lost. Use
  `docs/decision-notes/README.md` only when that rationale must outlive the
  issue or PR and no current ADR or architecture document already owns it.
- Treat scope as authority. New findings outside the declared behavior or path
  set become separate work unless the contract is explicitly revised before
  editing them. Never hide an architectural expansion inside a bug fix.
- Every behavior change needs the narrowest test or purpose-built check that
  would fail for its regression. When acceptance claims model-, browser-,
  CLI-, worker/SDK-, sandbox-, or external behavior, also verify the nearest
  real assembled path; a pure source-contract change may stop at focused-test
  evidence when its Change Contract declares that ceiling. A mock-only helper
  or an Agent's self-report is not assembled or external evidence.
- Keep source, focused-test, CI/build, packaged-image, deployment, runtime, and
  external-acceptance evidence distinct. A PR template field or checkbox is a
  claim to verify, never evidence by itself.
- Repository coding instructions live in this `AGENTS.md`. Product Agent.md
  content belongs to the Agent Profile/Workspace domain and must not be used as
  repository implementation authority.

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

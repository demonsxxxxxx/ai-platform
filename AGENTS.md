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
  Docker-capable environment. The authoritative 211 commands and recovery paths
  live in `docs/operations/211-release-operations-runbook.md`.
- Every local pytest invocation must pass a basetemp path under the
  workspace-local, git-ignored `.pytest-tmp/` directory; use
  `--basetemp .pytest-tmp` by default and never rely on the system temp path.
- If stale unreadable content prevents reuse of that root, pass a fresh
  non-existing child such as
  `--basetemp .pytest-tmp\run-verify-211-<timestamp>` and report the reason.

## Remote Runtime Access

- Remote access to s72 or s211 is allowed only through SSH MCP.
- Always call `mcp__ssh_mcp_server__list_servers` first, then use
  `mcp__ssh_mcp_server__execute_command` with `connectionName='s72'` or
  `connectionName='s211'`.
- Never fall back to system `ssh`, `scp`, or `plink`, local SSH configuration,
  a browser, or local Docker state to infer the remote runtime.
- If SSH MCP is unavailable or the required connection is not connected, the
  remote operation is `BLOCKED`.
- Commands and output must not contain `.env` values, account identifiers,
  passwords, tokens, or prompts.

## Authority

- Use the current repository root as the local `ai-platform` source.
- `docs/agent-rules/ai-platform-guardrails.md` is the single source for current
  211 paths, services, and product/security boundaries. Use it with the current
  user instruction, current code and tests, fresh runtime evidence, and only
  issues named by the active goal and confirmed from fresh GitHub state.
- Do not treat short-term execution notes, old local paths, or historical service layouts as product requirements.
Keep concrete issue numbers, owners, ordering, and current gate state in the
roadmap or Controller Current rather than this durable entry file.

## Documentation Authority

- `docs/README.md` is the document index. It directs durable policy, operations,
  and evidence ownership without becoming a project status board.
- Historical runtime observations belong only in reviewed, redacted structured
  evidence under `docs/release-evidence/`; they never establish current runtime
  state without fresh verification.

## Delivery Workflow

Use GitHub issues and pull requests as the default closure loop for goal-sized
work, gate closures, and newly discovered defects. The detailed issue, review,
fixed-SHA verification, and closure rules live only in
`docs/agent-rules/github-issue-pr-workflow.md`.

`docs/agent-rules/multi-agent-context-workflow.md` is the single source for task
lifetimes, ownership, authority, delegation, release leases, and context
handoff. Do not restate those rules here.

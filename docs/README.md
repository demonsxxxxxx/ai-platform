# Documentation Authority

This index names durable documentation. It is not a project status report and
does not represent deployed runtime state.

## Governance

- `../AGENTS.md` defines repository-local operating constraints.
- `agent-rules/ai-platform-guardrails.md` defines product and source boundaries.
- `agent-rules/multi-agent-context-workflow.md` defines ownership, leases, and
  handoff.
- `agent-rules/github-issue-pr-workflow.md` defines issue, PR, review, and
  closure evidence.

## Operations

`operations/211-release-operations-runbook.md` is the sole executable 211
release procedure. It requires a read-only readiness packet and one release
owner with one mutation lease. No document here authorizes a manual deployment
or substitutes for current host evidence.

## Contracts And Evidence

Source contracts live in their owning code and focused tests. Public frontend
contracts live in `frontend/`. Reviewed, redacted evidence is stored under
`release-evidence/` and indexed by `release-evidence/README.md`. Evidence is
historical unless its exact subject is freshly verified under the applicable
release or acceptance procedure.

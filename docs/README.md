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
- `architecture/runtime-authorities.md` maps each runtime capability to its
  single business authority and defines the Harness replacement seam.
- `architecture/sandbox-runtime-control-layer.md` defines the Sandbox Runtime
  application authority, target lifecycle, ownership fences, provider port, and
  staged recovery model.
- `architecture/opensandbox-ephemeral-model-credentials.md` defines the
  attempt-bound model-route admission and trusted provider-secret boundary.
- `architecture/docker-packaging.md` defines reproducible dependency authority,
  immutable image bases, and CI image acceptance without registry or runtime authority.

## Operations

`operations/211-release-operations-runbook.md` is the sole executable 211
release procedure. It requires a read-only readiness packet and one release
owner with one mutation lease. No document here authorizes a manual deployment
or substitutes for current host evidence.

`operations/s72-opensandbox-gateway-runbook.md` is the separate root-owned s72
gateway install and rollback authority. It does not replace the 211 procedure or
make a 211 verification claim.

## Contracts And Evidence

Source contracts live in their owning code and focused tests. Public frontend
contracts live in `frontend/`. Reviewed, redacted evidence is stored under
`release-evidence/` and indexed by `release-evidence/README.md`. Evidence is
historical unless its exact subject is freshly verified under the applicable
release or acceptance procedure.

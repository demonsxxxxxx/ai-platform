# ai-platform G6 Governance Readiness

Date: 2026-06-07

This document records the current G6 Tool / Skill / Memory Governance baseline.
It is an operator readiness snapshot, not a gate-closure claim. G6 remains
partial until frontend route policy mapping, release governance evidence,
memory erasure evidence, fail-closed frontend projection audit findings, and
Admin Runtime visual acceptance are complete.

Generate the current readiness snapshot from the repository root:

```powershell
python tools/governance_readiness.py --format markdown
python tools/governance_readiness.py --format json
```

The script intentionally does not print callback tokens, sandbox workspace
roots, raw queue keys, object-storage internal identifiers, executor-private
data, raw runtime paths, real `.env` values, or Skill staging paths.

## Admin Runtime Signal Path

Admins consume the live operational projection at:

```text
GET /api/ai/admin/runtime/overview
```

The overview now exposes:

- `capacity`: current configured capacity ceiling and missing load-test gates.
- `backpressure`: live admission, queue, and DB pool pressure reasons.
- `governance`: G6 readiness domains, implemented controls, open gaps, and
  next checks.

All three projections are admin-only, same-tenant, and designed as public/admin
operational projections. They must not become a path for executor-private data
or secret-like runtime configuration.

## Current G6 Status

| Domain | Implemented baseline | Remaining gap |
| --- | --- | --- |
| Tool permission | Admin tool policy inventory, tenant-scoped policy update audit, user request/decision flow, fail-closed risk/write policy evaluation, public permission-card projection | Route-by-route policy mapping for legacy frontend admin/MCP/model/envvar/channel surfaces, bulk review/history UX, full allow/deny/ask taxonomy for every MCP tool |
| Skill governance | Version registry, promote/rollback release policy, dependency policy materialization, skill snapshot and release-decision lock | Signed package or SBOM release gate, Admin release dashboard acceptance, dependency vulnerability/license policy |
| Memory governance | Session-bound records, ordinary-user opt-out, Admin policy inventory, retention cleanup, redaction, long-term memory fail-closed | Formal delete/export/erasure evidence, bounded office context-pack product contract, redaction policy preview and audit UX |
| Frontend projection | Source migrated into `frontend/web`, `ci:verify`, release traceability CLI, `tools/frontend_projection_audit.py`, `projection:audit` wired as the first frontend `ci:verify` step, public/admin projection audit baseline | Current projection audit blocks on secret-like legacy model/envvar/channel surfaces, ordinary-user G9 acceptance for legacy admin/MCP/model/envvar/channel routes, Admin Runtime governance visual acceptance, frontend image release trace tied to backend/worker commit |

## Gate Rule

Do not close G6 until the remaining gaps above have code, focused tests,
documentation, review, and 211 smoke evidence where runtime behavior is
involved. Do not use this baseline to expand sandbox privilege, expose ordinary
users to raw Skill selection, or broaden multi-agent beta.

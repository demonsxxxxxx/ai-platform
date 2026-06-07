# ai-platform G6 Governance Readiness

Date: 2026-06-07

This document records the current G6 Tool / Skill / Memory Governance baseline.
It is an operator readiness snapshot, not a gate-closure claim. G6 remains
partial until frontend route policy enforcement/remap, release governance
evidence, memory erasure evidence, active env-var profile route remap,
quarantined inactive legacy source remap, packaged frontend image
traceability, and ordinary-user G9 acceptance are complete.

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
| Tool permission | Admin tool policy inventory, tenant-scoped policy update audit, user request/decision flow, fail-closed risk/write policy evaluation, public permission-card projection, audit-visible legacy route policy mapping | Policy enforcement or ai-platform projection remap for legacy frontend admin/MCP/model/envvar/channel surfaces, bulk review/history UX, full allow/deny/ask taxonomy for every MCP tool |
| Skill governance | Version registry, promote/rollback release policy, dependency policy materialization, skill snapshot and release-decision lock | Signed package or SBOM release gate, Admin release dashboard acceptance, dependency vulnerability/license policy |
| Memory governance | Session-bound records, ordinary-user opt-out, Admin policy inventory, retention cleanup, redaction, long-term memory fail-closed | Formal delete/export/erasure evidence, bounded office context-pack product contract, redaction policy preview and audit UX |
| Frontend projection | Source migrated into `frontend/web`, `ci:verify`, release traceability CLI, static `dist/` manifest tied to the current git commit, `tools/frontend_projection_audit.py`, projection audit wired as the first frontend `ci:verify` step, public/admin projection audit baseline, machine-readable legacy route policies, active browser entry graph clear of forbidden private/secret-like projection terms, inactive legacy secret-like sources quarantined, Settings includes an admin-only capacity/backpressure/governance section fed only by `GET /api/ai/admin/runtime/overview`, 211 frontend acceptance for the Admin Runtime section at commit `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35` | Active env-var profile surface needs policy or projection remap, quarantined inactive legacy model/channel sources need ai-platform projection remap, ordinary-user G9 acceptance for legacy admin/MCP/model/envvar/channel routes, packaged frontend image release trace tied to backend/worker commit |

## 211 Acceptance Evidence

On 2026-06-08, the 211 source snapshot and API/worker runtime were updated to
commit `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35` with image
`ai-platform:f579155-g6-readiness`. The 211 frontend entry served the
new `SettingsPanel-BMSHWN-7.js` chunk, and that chunk contained the Admin
Runtime capacity/governance section plus the
`/api/ai/admin/runtime/overview` client route. A container-local Admin Runtime
smoke returned admin HTTP 200, ordinary-user HTTP 403, capacity schema
`ai-platform.capacity-baseline.v1`, governance schema
`ai-platform.governance-readiness.v1`, seven load-test gates, and no scanned
forbidden private projection terms. The frontend release traceability CLI now
records a deterministic static `dist/` manifest for the same git commit; this
does not close packaged frontend image traceability.

## Gate Rule

Do not close G6 until the remaining gaps above have code, focused tests,
documentation, review, and 211 smoke evidence where runtime behavior is
involved. Do not use this baseline to expand sandbox privilege, expose ordinary
users to raw Skill selection, or broaden multi-agent beta.

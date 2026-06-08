# ai-platform G6 Governance Readiness

Date: 2026-06-08

This document records the current G6 Tool / Skill / Memory Governance baseline.
It is an operator readiness snapshot, not a gate-closure claim. G6 remains
partial until frontend route policy enforcement/remap, release governance
evidence, bounded office context-pack contract, quarantined inactive legacy
source remap, packaged frontend image delivery/release acceptance, and
ordinary-user G9 acceptance are complete.

Generate the current readiness snapshot from the repository root:

```powershell
python tools/governance_readiness.py --format markdown
python tools/governance_readiness.py --format json
python tools/tool_policy_readiness.py --format markdown
python tools/tool_policy_readiness.py --format json
python tools/skill_release_readiness.py --format markdown
python tools/skill_release_readiness.py --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json --output skills/<skill-id>/ai-platform-skill-release-review.json
python tools/memory_erasure_readiness.py --format markdown
python tools/memory_erasure_readiness.py --format json
```

The script intentionally does not print callback tokens, sandbox workspace
roots, raw queue keys, object-storage internal identifiers, executor-private
data, raw runtime paths, real `.env` values, or Skill staging paths.

As of 2026-06-08, `tools/governance_readiness.py` also embeds a bounded
frontend projection-audit evidence summary for operator use. The summary keeps
the Admin Runtime hot path lightweight, but the CLI output includes
`domains.frontend_projection.evidence.projection_audit.open_gap_details` with
route counts, route scopes, required remap/hide actions, and quarantined-source
samples. This makes the G6/G9 frontend blockers actionable without exposing raw
storage keys, executor-private payload names, sandbox workdirs, secret-like
values, or local machine paths in the governance readiness JSON.

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
- `observability_readiness`: G9 readiness domains, implemented controls, open
  gaps, and next checks.

All projections are admin-only, same-tenant, and designed as public/admin
operational projections. They must not become a path for executor-private data
or secret-like runtime configuration.

## Current G6 Status

| Domain | Implemented baseline | Remaining gap |
| --- | --- | --- |
| Tool permission | Admin tool policy inventory, tenant-scoped policy update audit, user request/decision flow, fail-closed risk/write policy evaluation, public permission-card projection, audit-visible legacy route policy mapping, secret-safe allow/ask/deny taxonomy evidence through `tools/tool_policy_readiness.py` | Policy enforcement or ai-platform projection remap for legacy frontend admin/MCP/model/envvar/channel surfaces, bulk review/history UX, Admin dashboard acceptance for taxonomy evidence |
| Skill governance | Version registry, promote/rollback release policy, dependency policy materialization, skill snapshot and release-decision lock, secret-safe skill release readiness snapshot, pending review-manifest template entrypoint | Signed package or SBOM release gate, Admin release dashboard acceptance, dependency vulnerability/license policy |
| Memory governance | Session-bound records, ordinary-user opt-out, Admin policy inventory, retention cleanup, redaction, Admin redaction preview/audit route, long-term memory fail-closed, delete/retention/export/redaction-preview erasure evidence snapshot through `tools/memory_erasure_readiness.py` | Bounded office context-pack product contract |
| Frontend projection | Source migrated into `frontend/web`, `ci:verify`, GitHub Actions frontend workflow, release traceability CLI, static `dist` manifest with build-provenance same-commit gate, packaged frontend image definition traceability, non-push CI packaged-image build/provenance contract, `tools/frontend_projection_audit.py`, projection audit wired as the first frontend `ci:verify` step, public/admin projection audit baseline, machine-readable legacy route policies, active-browser legacy route policy audit, active browser entry graph clear of forbidden private/secret-like projection terms, inactive legacy secret-like sources quarantined, Profile env-var surface removed from the active browser entry graph, Settings includes an admin-only capacity/backpressure/governance section fed only by `GET /api/ai/admin/runtime/overview`, 211 frontend acceptance for the Admin Runtime section at commit `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35` | Quarantined inactive legacy model/channel/envvar sources need ai-platform projection remap, ordinary-user G9 acceptance for legacy admin/MCP/model/envvar/channel routes, packaged frontend image smoke and release acceptance on 211 or another Docker-capable host |

The frontend projection evidence now records three current structured blockers:
all legacy production routes still need policy enforcement or ai-platform
projection remap, the active browser entry graph still references 15 legacy
route policies that must be hidden or policy-gated before ordinary-user G9
acceptance, and 40 quarantined legacy source violations remain outside the
active entry graph but must be remapped or removed before rollout.

## Source Readiness Evidence

`tools/tool_policy_readiness.py` now records the offline allow/ask/deny policy
taxonomy for MCP tools with schema `ai-platform.tool-policy-readiness.v1`. The
snapshot is tied to the current `evaluate_tool_policy()` contract for active
tool cases and records six operator-readable cases: one low-risk read-only
auto-allow case, three ask cases for medium/high risk or write-capable tools,
and two deny cases for disabled registry or disabled tenant policy. It also
records decision options `allow_once`, `allow_for_run`, and `deny`, plus the
policy contract that disabled tools, missing tenant policy, expired decisions,
or explicit deny fail closed. This removes the previous taxonomy-evidence gap
from the G6 source baseline. It does not close G6: route enforcement/remap for
legacy frontend surfaces, admin policy bulk/history UX, dashboard acceptance,
and 211 runtime smoke remain required before gate closure.

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
records a deterministic static `dist/` manifest and build provenance gate:
`dist` is same-commit only when `dist/ai-platform-build-provenance.json`
matches a known current git commit, a clean dirty state, and package/lockfile
hashes; missing, stale, dirty, or unknown-dirty provenance is reported as
`built_unverified` instead of being silently tied to the current commit. The
CLI also reports packaged frontend image definition traceability through
`frontend/web/Dockerfile`, `frontend/web/nginx.conf.template`, and
`deploy/ai-platform/docker-compose.frontend.yml`, and fails closed if required
build provenance args, nginx upload/proxy controls, compose args, or packaged
delivery denylist checks regress. The repository also has
`.github/workflows/ai-platform-frontend.yml`, which runs frontend dependency
install, `ci:verify`, frontend release traceability, and a non-push packaged
image build/provenance check for relevant source changes. The image job builds
with `AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}` and
`AI_PLATFORM_BUILD_DIRTY=false`, then reads
`ai-platform-build-provenance.json` from the image to verify same-commit
frontend provenance without Docker compose or `.env` inputs. GitHub Actions run
`27104398690` passed on commit
`11ab56c660385f6790964af3d5bd60e3d4431ff2` for the earlier source workflow.
A later 211 packaged-image build attempt reached the private repository source
but failed before application build because the Docker build host could not
pull required registry/base-image metadata and did not have the required base
images cached. Therefore packaged frontend image release acceptance remains a
separate Docker-capable-host release gate and is not closed by this baseline.

`tools/skill_release_readiness.py` now records a secret-safe offline evidence
snapshot for repository-owned Skills with schema
`ai-platform.skill-release-readiness.v1`. The snapshot reads only the
repository `skills/` inventory, Skill front matter, sanitized evidence file
basenames, content hashes, and the platform dependency policy. It does not output
Skill body content, absolute paths, `.env` values, staging paths, callback
tokens, sandbox workspace roots, or executor-private data. The current source
inventory contains five Skills, four public workbench Skills, one internal
dependency Skill, and one declared dependency edge
`qa-file-reviewer -> minimax-docx`; that dependency is allowed by policy. The
same snapshot records one Skill with package metadata, one Skill with
requirements evidence, and zero Skills with SBOM, license, or vulnerability
review evidence. A missing or empty Skill inventory fails closed as
`skill_inventory_missing_or_empty`; blocked dependency-policy details fail
closed as `skill_dependency_policy_blocked`; and SBOM/license/vulnerability
filenames do not clear release governance unless an explicit review manifest
marks the evidence as passed. Therefore G6 remains blocked by
`signed_skill_package_or_sbom_release_gate` and
`dependency_vulnerability_or_license_policy`, and ordinary users must still not
be exposed to raw Skill selection or staging internals.

The same CLI can now generate a pending review-manifest template:
`python tools/skill_release_readiness.py --review-template --skill-id <skill-id>
--format json`. By default this writes only to stdout; operators must pass
`--output skills/<skill-id>/ai-platform-skill-release-review.json` to create a
file. The template schema is `ai-platform.skill-release-review.v1`, starts with
`status = pending`, keeps `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed` false, and includes required evidence/checklist fields.
This entrypoint does not close the release gate by itself. A Skill remains
blocked until real SBOM or signed-package evidence, license-policy evidence,
vulnerability-scan evidence, and a completed `passed` review manifest are
present and reviewed.

`tools/memory_erasure_readiness.py` now records code/test evidence for
ordinary-user session-scoped soft delete, admin same-tenant soft delete, admin
retention cleanup, worker retention cleanup across scopes, ordinary-user export
excluding deleted/expired rows, admin export using an operator projection
without content/metadata, no content/metadata returning in delete repository
tests, delete/cleanup audit allowlists, and the admin-only
`POST /api/ai/admin/memory/redaction/preview` route. The preview route validates
same-tenant admin scope, returns only redacted preview fields, and writes an
audit payload that records policy scope, mode, change booleans, and redacted
reason without sample content or metadata. This does not close memory
governance by itself: the bounded office context-pack product contract remains
open.

On 2026-06-08, commit `f7c6b0d9114748fa249acb88da6584851c48aa96` was synced to
the 211 repo-local source target and deployed to API/worker with image
`ai-platform:f7c6b0d-g6-memory-redaction-preview`. API and worker labels both
reported the same `org.opencontainers.image.revision`, and
`GET /api/ai/health` returned `{"status":"ok"}`. A 211 route smoke for
`POST /api/ai/admin/memory/redaction/preview` verified admin HTTP 200,
ordinary-user HTTP 403, invalid redaction mode HTTP 422, audit action
`admin.memory.redaction.previewed`, and no response leakage of private payload,
storage key, sandbox workdir, object-storage URL, skill package key, provider
token, bearer token, or client-secret markers. The smoke used the existing 211
runtime `.env` path through compose without printing or copying secret values.
Local source readiness for the same commit reports no missing memory erasure
evidence markers and keeps only
`bounded_context_pack_product_contract_for_office_workflows` open for memory
governance.

## Gate Rule

Do not close G6 until the remaining gaps above have code, focused tests,
documentation, review, and 211 smoke evidence where runtime behavior is
involved. Do not use this baseline to expand sandbox privilege, expose ordinary
users to raw Skill selection, or broaden multi-agent beta.

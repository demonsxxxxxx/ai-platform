# ai-platform G6 Governance Readiness

Date: 2026-06-12

This document records the current G6 Tool / Skill / Memory Governance baseline.
It is an operator readiness snapshot, not a gate-closure claim. G6 remains
partial until frontend route policy enforcement/remap, release governance
evidence, 211 executor context-pack acceptance, document-centric follow-up state,
211 sandbox latency split acceptance, frontend context provenance acceptance,
quarantined inactive legacy source remap, packaged frontend image
delivery/release acceptance, and ordinary-user G9 acceptance are complete.

Generate the current readiness snapshot from the repository root:

```powershell
python tools/governance_readiness.py --format markdown
python tools/governance_readiness.py --format json
python tools/tool_policy_readiness.py --format markdown
python tools/tool_policy_readiness.py --format json
python tools/tool_policy_bulk_review_readiness.py --format markdown
python tools/tool_policy_bulk_review_readiness.py --format json
python tools/skill_release_readiness.py --format markdown
python tools/skill_release_readiness.py --format json
python tools/skill_release_dashboard_readiness.py --format markdown
python tools/skill_release_dashboard_readiness.py --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json --output skills/<skill-id>/ai-platform-skill-release-review.json
python tools/memory_erasure_readiness.py --format markdown
python tools/memory_erasure_readiness.py --format json
python tools/office_context_readiness.py --format markdown
python tools/office_context_readiness.py --format json
python tools/verify_governance_runtime_smoke.py --base-url http://127.0.0.1:8020 --commit-sha <source-tree-commit> --runtime-subject-commit-sha <runtime-subject-commit> --image <runtime-image>
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
| Tool permission | Admin tool policy inventory, tenant-scoped policy update audit, bounded admin change-history projection through `GET /api/ai/admin/tool-policies/history`, user request/decision flow, exact `tool_call_id` / stable request-fingerprint decision lookup source tests, fail-closed risk/write policy evaluation, public permission-card projection, audit-visible legacy route policy mapping, secret-safe allow/ask/deny taxonomy evidence through `tools/tool_policy_readiness.py`, platform-registered-MCP-only policy evidence with ordinary-user custom MCP disabled, and contract-only Admin bulk-review dashboard readiness through `tools/tool_policy_bulk_review_readiness.py` / `admin_policy_bulk_review_dashboard_contract` | Policy enforcement or ai-platform projection remap for legacy frontend admin/MCP/model/envvar/channel surfaces, plus `admin_policy_bulk_review_runtime_acceptance`, `admin_policy_bulk_review_visual_acceptance`, and `admin_policy_bulk_review_211_acceptance` |
| Skill governance | Version registry, promote/rollback release policy, dependency policy materialization, skill snapshot and release-decision lock, secret-safe skill release readiness snapshot, pending review-manifest template entrypoint, source-level `ai-platform.skill-dependency-review-policy.v1` contract, source-level `ai-platform.skill-signed-package-evidence-contract.v1` / `skill_signed_package_evidence_contract`, source-level validation for signed-package evidence JSON, and contract-only Admin Skill release dashboard readiness through `tools/skill_release_dashboard_readiness.py` / `admin_skill_release_dashboard_contract` | SBOM or signed-package release evidence plus reviewed manifests, dependency vulnerability/license evidence, `skill_dependency_review_policy_runtime_acceptance`, plus `admin_skill_release_dashboard_runtime_acceptance`, `admin_skill_release_dashboard_visual_acceptance`, and `admin_skill_release_dashboard_211_acceptance` |
| Memory governance | Session-bound records, ordinary-user opt-out, Admin policy inventory, retention cleanup, redaction, Admin redaction preview/audit route, long-term memory fail-closed, delete/retention/export/redaction-preview erasure evidence snapshot through `tools/memory_erasure_readiness.py`, source-level office context-pack contract/readiness through `tools/office_context_readiness.py`, source-level context-pack persistence/versioning through `source_level_context_pack_persistence_and_versioning`, context snapshot public provenance projection with `context_pack_version` and `context_pack_generated_at`, user-visible context provenance API projection source tests, source-level office execution-tier router tests, executor context-pack prompt injection source tests, and the source-level sandbox cold-start latency split observability contract | 211 executor context-pack acceptance, document-centric follow-up state, 211 sandbox cold-start latency split acceptance, and frontend context provenance acceptance |
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
or explicit deny fail closed. The same snapshot now records the MCP registry
contract: tool usage is limited to platform-registered MCP tools, tenant policy
is same-tenant scoped, unregistered tools deny, and ordinary-user custom MCP is
not allowed. The admin-only
Source tests now bind worker MCP and Claude SDK tool permission reuse to exact
`tool_call_id` semantics for `allow_once` / `deny` and stable request
fingerprints (`input_sha256` or `command_sha256`) for `allow_for_run`; callers
use the explicit `get_exact_tool_permission_decision` repository API instead of
a broad latest-decision lookup. `GET /api/ai/admin/tool-policies/history`
projection now reads the existing
same-tenant audit log for `admin.tool_policy.updated`, applies a bounded limit,
and returns only allowlisted public policy fields. This removes the previous
taxonomy-evidence gap and narrows the history-view gap from the G6 source
baseline. `tools/tool_policy_bulk_review_readiness.py` now records a
contract-only Admin review dashboard baseline with schema
`ai-platform.tool-policy-bulk-review-readiness.v1` and nested
`ai-platform.tool-policy-bulk-review-dashboard-contract.v1`. The contract binds
the dashboard to the existing admin-only same-tenant policy inventory, history,
and single-tool update routes; requires bounded inventory/history, taxonomy
summary, decision options, legacy-route gap summary, filters, per-tool diff
preview, update confirmation, and change-history drilldown; and keeps raw
registry connection details, credentials, tool request bodies, sandbox working
directories, and executor-private runtime data out of the public/admin
projection. This source-level exact-decision evidence does not close G6 by
itself; runtime, visual, 211, and legacy frontend projection acceptance remain
open.

This removes the previous coarse Admin dashboard blocker from the source
baseline and replaces it with explicit runtime, visual, and 211 acceptance
gaps: `admin_policy_bulk_review_runtime_acceptance`,
`admin_policy_bulk_review_visual_acceptance`, and
`admin_policy_bulk_review_211_acceptance`. It does not close G6, does not add a
batch mutation API, and does not permit ordinary-user access to policy
internals. Route enforcement/remap for legacy frontend surfaces, Admin
dashboard acceptance, and 211 runtime smoke remain required before gate closure.

## 211 Acceptance Evidence

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` and image
`ai-platform:d4486eb-observability-evidence-loader`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The 211 source marker, source snapshot, source revision labels, OCI revision
labels, and image internal source marker pointed to
`d4486ebf5a33ce23a632a69bcf07ef1220b61ea3`, and the compose config label pointed to the repo-local 211 deploy
composition. Runtime-subject, runtime-rollout, and source_revision alias labels
still carried prior rollout metadata, and the compose environment-file label
still recorded the old external env-file path. The reviewed release evidence
entry is
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json`.

This smoke records the focused Admin Runtime governance projection proof for
the current `d4486eb` Foundation Alpha POC runtime subject. It does not close
ordinary-user confirmation-card UX, full Admin dashboard/visual acceptance,
signed-package/SBOM review evidence, dependency vulnerability/license evidence,
office context-pack persistence/versioning, 211 executor context-pack
acceptance, execution-tier router acceptance, frontend context provenance
acceptance, stale runtime labels, or broader production governance rollout.

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`b96d02e232176bade455f2af2bc3080f8f372206` and image
`ai-platform:b96d02e-release-evidence-runtime-acceptance`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The 211 API and worker labels matched
the runtime subject, and the compose config label pointed to the repo-local 211
deploy composition. The reviewed release evidence entry is
`docs/release-evidence/foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-governance-runtime-smoke.json`.

This smoke records only the focused Admin Runtime governance projection proof
for the Foundation Alpha POC and the release-evidence-runtime-acceptance
rollout. It does
not close ordinary-user confirmation-card UX, full Admin dashboard/visual
acceptance, signed-package/SBOM review evidence, dependency
vulnerability/license evidence, office context-pack persistence/versioning, 211
executor context-pack acceptance, execution-tier router acceptance, frontend
context provenance acceptance, or broader production governance rollout.

The immediately superseded `948179c73734aa61ed764fb3485f5415fca8f193`
governance smoke remains historical reviewed evidence for the
skill-release-scaffold rollout at
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json`.

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`2384e19dcac2e39fbcf9c27dc990f5774d391422` and image
`ai-platform:2384e19-context-source-provenance`, using operator verifier source
`820669037978237182ecd2fd27c2ffa10a953c0b`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The synced 211 source snapshot marked
the new verifier as runtime-neutral and declared no runtime-affecting delta from
the running `2384e19` image. The reviewed release evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`.

This smoke records only the focused Admin Runtime governance projection proof for
the Foundation Alpha POC. It does not close ordinary-user confirmation-card UX,
full Admin dashboard/visual acceptance, signed-package/SBOM review evidence,
dependency vulnerability/license evidence, office context-pack
persistence/versioning, 211 executor context-pack acceptance, execution-tier
router acceptance, frontend context provenance acceptance, or broader production
governance rollout.

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
marks the evidence as passed and its `evidence_files` entries are non-empty,
non-placeholder, secret-safe, and matched to actual Skill evidence files.
The CLI can also write external pending review inputs under the repository
release-evidence tree with
`python tools/skill_release_readiness.py --write-evidence-scaffold --skill-id <skill-id> --evidence-root docs/release-evidence/skill-release --format json`.
That scaffold uses schema `ai-platform.skill-release-evidence-scaffold.v1`,
binds SBOM, license-policy, vulnerability, and pending review-manifest
references to `external-release-evidence/<skill-id>/...`, and keeps
`status = pending_review`; it is an operator handoff artifact and does not
change the Skill content hash or close G6 by itself.
If an operator prematurely changes the review manifest to `passed` or sets
review flags to true while the referenced SBOM/license/vulnerability files still
carry scaffold markers such as `pending_review` or `review_required`, readiness
fails closed with category-specific evidence-not-reviewed errors.
The same snapshot now embeds the source-level
`ai-platform.skill-dependency-review-policy.v1` contract. That contract binds
the required review manifest schema to `ai-platform.skill-release-review.v1`,
requires the `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed` flags, requires SBOM/signed-package, license-policy,
and vulnerability-scan evidence categories, and keeps evidence references
matched to the Skill inventory while rejecting placeholder or secret-like
references. It also embeds the source-level
`ai-platform.skill-signed-package-evidence-contract.v1` as
`skill_signed_package_evidence_contract`. The contract defines required signed
package evidence fields such as package artifact reference, package digest,
signature artifact reference, signer identity, signing certificate or key
reference, transparency log or attestation reference, verification status, and
review status, while keeping evidence references bounded to relative or
artifact references. Source-level runtime validation now accepts only safe
signed-package wrapper JSON (`ai-platform-signed-package-evidence.json` or
`signed-package-evidence.json`) with the required fields, a 64-character
SHA-256 digest, final verified/reviewed statuses, and relative or `artifact://`
references. Raw cosign, in-toto, SLSA, or signature files are not accepted as
direct review evidence; the wrapper JSON must reference those artifacts through
its bounded attestation/signature fields.
This still does not close G6 without real reviewed evidence and runtime/Admin
acceptance.
`tools/skill_release_dashboard_readiness.py` now records a contract-only Admin
Skill release dashboard baseline with schema
`ai-platform.skill-release-dashboard-readiness.v1` and nested
`ai-platform.skill-release-dashboard-contract.v1`. Governance readiness records
this as `admin_skill_release_dashboard_contract`: the contract binds the
dashboard to existing admin-only Skill inventory, sync, upload, diff, promote,
and rollback routes; requires inventory summary, dependency policy, release
review evidence summary, version diff summary, promote/rollback policy, runtime
materialization status, filters, diff preview, confirmation controls, and review
evidence drilldown; and does not expose raw package internals, staging paths,
secret material, private runtime payloads, or sandbox working directories.
This replaces the previous coarse dashboard acceptance blocker with explicit
runtime, visual, and 211 acceptance gaps.
Therefore G6 remains blocked by
`signed_skill_package_or_sbom_release_gate` and
`dependency_vulnerability_or_license_policy`, plus real signed-package reviewed
evidence, `skill_dependency_review_policy_runtime_acceptance`,
`admin_skill_release_dashboard_runtime_acceptance`,
`admin_skill_release_dashboard_visual_acceptance`, and
`admin_skill_release_dashboard_211_acceptance` until the source-level policy and
dashboard are accepted through runtime/Admin release evidence. Ordinary users
must still not be exposed to raw Skill selection or staging internals.

The same CLI can now generate a pending review-manifest template:
`python tools/skill_release_readiness.py --review-template --skill-id <skill-id>
--format json`. By default this writes only to stdout; operators must pass
`--output skills/<skill-id>/ai-platform-skill-release-review.json` to create a
file. The template schema is `ai-platform.skill-release-review.v1`, starts with
`status = pending`, keeps `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed` false, and includes required evidence/checklist fields.
This entrypoint does not close the release gate by itself. A Skill remains
blocked until real SBOM evidence, license-policy evidence,
vulnerability-scan evidence, and a completed `passed` review manifest are
present, reviewed, and explicitly bound to those real evidence files. Empty
review evidence arrays, copied template placeholders, secret-like evidence
paths, or references that do not match the Skill evidence inventory keep the
readiness verdict fail-closed.
Signed-package evidence now has a source-level contract and validation in code
and tests, but it remains fail-closed until real package evidence and passed
review manifests are available and accepted through the runtime/Admin release
evidence path.

`tools/memory_erasure_readiness.py` now records code/test evidence for
ordinary-user session-scoped soft delete, admin same-tenant soft delete, admin
retention cleanup, worker retention cleanup across scopes, ordinary-user export
excluding deleted/expired rows, admin export using an operator projection
without content/metadata, no content/metadata returning in delete repository
tests, delete/cleanup audit allowlists, the admin-only
`POST /api/ai/admin/memory/redaction/preview` route, and the source-level
context snapshot public provenance projection contract. The preview route validates
same-tenant admin scope, returns only redacted preview fields, and writes an
audit payload that records policy scope, mode, change booleans, and redacted
reason without sample content or metadata. This does not close memory
governance by itself: office workflow context continuity now has source-level
context-pack persistence/versioning, user-visible context provenance API
projection source tests, source-level execution-tier routing tests, and the
sandbox latency split observability source contract, but still needs 211
executor context-pack acceptance, document-centric follow-up state, 211 sandbox
cold-start latency split acceptance, and frontend context provenance
acceptance.

`tools/office_context_readiness.py` now records the source-level #22
context-pack readiness contract for office-heavy workflows with schema
`ai-platform.office-context-pack-readiness.v1`. The contract keeps lightweight
writing/rewrite/summarization/translation follow-up work in the
`sdk_only_writing` tier by default, models document generation/conversion as a
separate `document_worker` tier, and reserves `heavy_sandbox` for script,
browser, risky tool, or complex multi-tool work. It defines allowed context
sources, user-visible context provenance fields, and forbidden projection terms
without reading `.env` values, raw storage keys, sandbox workdirs,
executor-private payloads, or secret-like runtime paths. The current
`run_context_snapshots.payload_json` and queued `context_snapshot` references now
carry source-level public provenance fields for `referenced_materials`,
`used_context_summary`, `latest_artifact_version`, `execution_tier`, and
`context_pack_version` / `context_pack_generated_at`; those fields contain
counts, safe input keys, tier, a bounded public context-pack version, and
generated time rather than raw message/file/artifact/memory IDs, and are
covered by source-level API projection tests. The
owner-scoped context snapshot API response no longer returns `included_*_ids`;
those IDs stay in the scoped database row and worker lookup path only to compute
public counts. Worker-side executor `context_ref` reconstruction resolves
the scoped DB snapshot by id and regenerates public provenance/counts instead of
trusting queue copies or stored payload provenance. The source tree now also has
a bounded `ai-platform.executor-context-pack.v1` executor prompt contract and
Claude SDK prompt-injection tests that pass only the generated safe summary, not
raw storage keys, sandbox workdirs, executor-private payloads, or long-term
memory reads. The implemented control
`source_level_context_pack_persistence_and_versioning`: it preserves the safe
public `context_pack_version` fact in source-level snapshot/projection paths
without adding a new database schema. Source-level tests now route lightweight
writing to `sdk_only_writing`, document generation/review/translation skills to
`document_worker`, and explicit sandbox/script/browser work to `heavy_sandbox`
without starting Docker during routing. This still does not provide 211
executor context-pack acceptance, enable long-term cross-session memory, start
Docker for lightweight office tasks, provide frontend context provenance
acceptance, or expand ordinary-user G8/G10 exposure.
The source-level sandbox latency split observability contract separates lease
acquisition, container cold start, healthcheck, executor dispatch/model work,
document processing, cleanup, and total runtime timings, but 211 sandbox
latency split acceptance is still required before closing the cold-start UX
gap.

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
evidence markers. The former bounded context-pack product-contract gap is now
split into source-level context-pack persistence/versioning plus explicit office
context-pack runtime, follow-up state, 211 sandbox latency acceptance, and
frontend acceptance gaps.

## Gate Rule

Do not close G6 until the remaining gaps above have code, focused tests,
documentation, review, and 211 smoke evidence where runtime behavior is
involved. Do not use this baseline to expand sandbox privilege, expose ordinary
users to raw Skill selection, or broaden multi-agent beta.
